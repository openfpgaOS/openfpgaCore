//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// C++ harness for tb_system — full CPU boot sim.
//
// Usage: Vtb_system [os.bin path] [max_cycles]
// Defaults:
//   os.bin     = ../../firmware/os/os.bin
//   max_cycles = 5000000
//
// Flow:
//   1. Parse firmware.mif (Altera hex-in-ASCII format) and write the
//      words into the BRAM backdoor (altsyncram.mem[]).
//   2. Read os.bin into a buffer and write into cram0_sim_model's
//      mem[] backing store starting at word_addr=0 (byte offset
//      CRAM0_OS_OFFSET = 0).  Each 32-bit word is laid out little-endian
//      in the .bin, which matches the backing array's natural layout.
//   3. Assert reset_n=0 for 100 cycles.  clk_cpu tick alone runs the
//      reset state; clk_74a toggles at a slower rate to mimic the
//      100 MHz / 74.25 MHz split.
//   4. Release reset_n.  Every cycle after, if tb.uart_tx_dv pulses,
//      print tb.uart_tx_byte to stdout.
//   5. Stop on success (buffer contains "HAL init"), timeout
//      (max_cycles), or trap-stall heuristic.
//

#define private public
#include "Vtb_system.h"
#include "Vtb_system__Syms.h"
#undef private
#include "Vtb_system___024root.h"
#include "Vtb_system_tb_system.h"
#include <verilated.h>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <fstream>
#include <sstream>

static Vtb_system *tb;
static vluint64_t sim_time = 0;

// ========================================================================
// Clock handling — two asynchronous domains.
//
// clk_cpu runs every half-tick.  clk_74a toggles every ~1.35 clk_cpu
// cycles (74.25 MHz vs 100 MHz).  We drive them with an integer counter
// ratio (100:74 ≈ 27:20 with integer math), which is close enough for
// functional sim.
// ========================================================================

static uint64_t cpu_cycles = 0;   // full clk_cpu cycles
static uint64_t clk74_accum = 0;   // fractional accumulator
static bool clk_74a_state = false;

static void tick_half(bool cpu_edge_high) {
    tb->clk_cpu = cpu_edge_high ? 1 : 0;
    // Advance the 74.25 MHz clock: for every 100 half-ticks of clk_cpu,
    // clk_74a produces 74 half-ticks.  Use an integer accumulator: we
    // add 74 each half-tick of clk_cpu; every time it crosses a multiple
    // of 100 we flip clk_74a.
    clk74_accum += 74;
    while (clk74_accum >= 100) {
        clk74_accum -= 100;
        clk_74a_state = !clk_74a_state;
    }
    tb->clk_74a = clk_74a_state ? 1 : 0;
    tb->eval();
    sim_time++;
}

// Vsync generator — pulses high for 1 clk_cpu cycle every VSYNC_PERIOD
// cycles to model the display vsync edge that clears
// axi_periph_slave's fb_swap_pending. On real hardware vsync hits
// every ~16.67 ms (~1.67M clk_cpu cycles at 100 MHz); a tighter period
// keeps simulation tests short.
#define VSYNC_PERIOD 16667
static void tick_cycle() {
    tick_half(false);
    /* Pulse vsync high during the second half-tick of the period start so
     * vsync_rising's edge detector sees a clean 1-cycle-wide pulse. */
    tb->vsync = (cpu_cycles % VSYNC_PERIOD == 0) ? 1 : 0;
    tick_half(true);
    cpu_cycles++;
}

// ========================================================================
// Firmware.mif parser — populates BRAM backing store.
// ========================================================================

static bool load_firmware_mif(const char *path) {
    std::ifstream f(path);
    if (!f) {
        std::fprintf(stderr, "Can't open %s\n", path);
        return false;
    }

    // MIF format: ADDRESS : DATA;  (after "CONTENT BEGIN")
    std::string line;
    bool in_content = false;
    int count = 0;
    while (std::getline(f, line)) {
        if (!in_content) {
            if (line.find("CONTENT BEGIN") != std::string::npos) in_content = true;
            continue;
        }
        if (line.find("END;") != std::string::npos) break;

        // Trim leading whitespace
        size_t p = line.find_first_not_of(" \t");
        if (p == std::string::npos) continue;
        if (line[p] == '-') continue;  // comment line "-- ..."

        // Parse "ADDR : HEX;"
        size_t colon = line.find(':');
        size_t semi  = line.find(';');
        if (colon == std::string::npos || semi == std::string::npos) continue;

        std::string addr_str = line.substr(p, colon - p);
        std::string data_str = line.substr(colon + 1, semi - colon - 1);

        // Handle range form "[a..b] : VALUE;"
        if (addr_str.find('[') != std::string::npos) {
            size_t lb = addr_str.find('[');
            size_t dots = addr_str.find("..", lb);
            size_t rb = addr_str.find(']');
            if (dots == std::string::npos || rb == std::string::npos) continue;
            int lo = std::stoi(addr_str.substr(lb + 1, dots - lb - 1));
            int hi = std::stoi(addr_str.substr(dots + 2, rb - dots - 2));
            uint32_t val = std::stoul(data_str, nullptr, 16);
            for (int a = lo; a <= hi && a < 8192; a++) {
                tb->rootp->tb_system->periph__DOT__ram__DOT__mem[a] = val;
            }
            continue;
        }

        int addr = std::stoi(addr_str);
        uint32_t val = std::stoul(data_str, nullptr, 16);
        if (addr < 0 || addr >= 8192) continue;
        tb->rootp->tb_system->periph__DOT__ram__DOT__mem[addr] = val;
        count++;
    }
    std::printf("Loaded %d BRAM words from %s\n", count, path);
    return true;
}

// ========================================================================
// os.bin loader — poke the whole file into CRAM0 mem[] at word_addr 0.
// ========================================================================

//
// os.bin preload.
//
// The sim boot path in targets/pocket/boot.c (shared via the
// targets/sim shim) short-circuits the APF bridge DMA + CRAM0→SDRAM
// memcpy and jumps straight to `start_os`, which expects the kernel
// image to already live at __os_entry (0x10320000 in SDRAM).  So we
// preload directly into SDRAM at byte-offset 0x320000 (word 0xC8000).
//
// We still mirror the image into CRAM0 so anything the kernel might
// poke through the bridge scratch window at boot (currently nothing
// on the sim target) sees a consistent snapshot.
//
static bool load_os_bin(const char *path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        std::fprintf(stderr, "Can't open %s\n", path);
        return false;
    }
    f.seekg(0, std::ios::end);
    size_t sz = f.tellg();
    f.seekg(0, std::ios::beg);
    std::vector<uint8_t> buf(sz);
    f.read(reinterpret_cast<char*>(buf.data()), sz);

    size_t words = (sz + 3) / 4;
    if (words > 4u * 1024u * 1024u) {
        std::fprintf(stderr, "os.bin too big for CRAM0 (16 MB): %zu bytes\n", sz);
        return false;
    }

    // SDRAM preload: byte address 0x10320000 → word index (0x320000/4).
    // The behavioural sim SDRAM is 16 Mword = 64 MB and wraps, so mask
    // accordingly.
    const uint32_t SDRAM_OS_WORD = 0x10320000u / 4u;
    const uint32_t SDRAM_MEM_MASK = (1u << 24) - 1u;  // must match sdram_fast_model.v
    for (size_t w = 0; w < words; w++) {
        uint32_t v = 0;
        for (int b = 0; b < 4; b++) {
            size_t idx = w * 4 + b;
            if (idx < sz) v |= ((uint32_t)buf[idx]) << (b * 8);
        }
        tb->rootp->tb_system->cram0_mem__DOT__mem[w]  = v;
        uint32_t sdram_idx = (SDRAM_OS_WORD + w) & SDRAM_MEM_MASK;
        tb->rootp->tb_system->sdram_mem__DOT__mem[sdram_idx] = v;
    }
    std::printf("Loaded %zu bytes (%zu words) of os.bin into CRAM0 and SDRAM@0x10320000\n",
                sz, words);
    return true;
}

// ========================================================================
// Main
// ========================================================================

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);

    const char *os_bin_path = "../../firmware/os/os.bin";
    uint64_t max_cycles = 8000000;
    if (argc > 1) os_bin_path = argv[1];
    if (argc > 2) max_cycles = std::strtoull(argv[2], nullptr, 0);

    tb = new Vtb_system;

    // Initialise inputs
    tb->clk_cpu = 0;
    tb->clk_74a = 0;
    tb->reset_n = 0;
    tb->vsync   = 0;
    tb->bd_cram0_wr = 0;
    tb->bd_cram0_addr = 0;
    tb->bd_cram0_wdata = 0;

    // Run a few cycles in reset to let initial blocks settle (memory
    // array zeroing runs during `initial`, which Verilator executes
    // once at construction time).
    for (int i = 0; i < 5; i++) tick_cycle();

    // Preload BRAM from firmware.mif (either next to the binary or
    // inside src/firmware/os).
    const char *mif_candidates[] = {
        "firmware.mif",
        "../../firmware/os/firmware.mif",
        "../targets/pocket/firmware.mif"
    };
    bool bram_loaded = false;
    for (const char *p : mif_candidates) {
        std::ifstream f(p);
        if (f) { bram_loaded = load_firmware_mif(p); break; }
    }
    if (!bram_loaded) {
        std::fprintf(stderr, "Failed to locate firmware.mif — continuing "
                              "with NOP-filled BRAM; boot will hang early.\n");
    }

    // Preload os.bin into CRAM0 mem[]
    if (!load_os_bin(os_bin_path)) {
        std::fprintf(stderr, "os.bin load failed\n");
        return 2;
    }

    // Hold reset for 100 cycles
    std::printf("Asserting reset for 100 cycles\n");
    for (int i = 0; i < 100; i++) tick_cycle();

    // Release reset
    tb->reset_n = 1;
    std::printf("Reset released, running up to %llu cycles\n",
                (unsigned long long)max_cycles);

    std::string uart_buf;
    uint64_t last_uart_cycle = 0;
    uint32_t last_sdram_addr = 0xFFFFFFFFu;
    uint32_t last_cram0_addr = 0xFFFFFFFFu;
    uint32_t last_periph_addr = 0xFFFFFFFFu;
    uint64_t last_bus_activity = 0;
    (void)last_bus_activity;
    uint64_t last_report = 0;

    int exit_code = 1;  // timeout default

    // Log the first N AR/AW events.  Generous budget so we can see if
    // the BSS-clear loop is making bus progress.
    int ar_log_budget = 3000;
    uint32_t prev_sdram_araddr = 0xFFFFFFFF;
    uint32_t prev_sdram_awaddr = 0xFFFFFFFF;
    uint32_t prev_periph_araddr = 0xFFFFFFFF;
    uint32_t prev_periph_awaddr = 0xFFFFFFFF;
    uint32_t prev_cram0_araddr = 0xFFFFFFFF;
    (void)prev_cram0_araddr;

    for (uint64_t c = 0; c < max_cycles; c++) {
        tick_cycle();

        if (tb->uart_tx_dv) {
            char ch = (char)(tb->uart_tx_byte & 0xFF);
            std::fputc(ch, stdout);
            std::fflush(stdout);
            uart_buf.push_back(ch);
            if (uart_buf.size() > 4096) uart_buf.erase(0, 2048);
            last_uart_cycle = c;
        }

        // Track bus activity — if nothing happens for 50k cycles, abort.
        if (tb->dbg_sdram_arvalid || tb->dbg_cram0_arvalid || tb->dbg_periph_arvalid
            || tb->dbg_sdram_araddr != last_sdram_addr
            || tb->dbg_cram0_araddr != last_cram0_addr
            || tb->dbg_periph_araddr != last_periph_addr) {
            last_bus_activity = c;
            last_sdram_addr = tb->dbg_sdram_araddr;
            last_cram0_addr = tb->dbg_cram0_araddr;
            last_periph_addr = tb->dbg_periph_araddr;
        }

        // Log first N AR/AW events — collapse repeats on the same
        // address to keep the output readable.
        if (ar_log_budget > 0) {
            if (tb->dbg_sdram_arvalid && tb->dbg_sdram_araddr != prev_sdram_araddr) {
                std::printf("[c=%llu] AR sdram=0x%08x arlen=%d\n",
                            (unsigned long long)c, tb->dbg_sdram_araddr,
                            tb->dbg_word_burst_len_sd);
                prev_sdram_araddr = tb->dbg_sdram_araddr;
                ar_log_budget--;
            }
            if (tb->dbg_cram0_arvalid && tb->dbg_cram0_araddr != prev_cram0_araddr) {
                std::printf("[c=%llu] AR cram0=0x%08x\n",
                            (unsigned long long)c, tb->dbg_cram0_araddr);
                prev_cram0_araddr = tb->dbg_cram0_araddr;
                ar_log_budget--;
            }
            if (tb->dbg_periph_arvalid && tb->dbg_periph_araddr != prev_periph_araddr) {
                std::printf("[c=%llu] AR periph=0x%08x\n",
                            (unsigned long long)c, tb->dbg_periph_araddr);
                prev_periph_araddr = tb->dbg_periph_araddr;
                ar_log_budget--;
            }
            if (tb->dbg_sdram_awvalid && tb->dbg_sdram_awaddr != prev_sdram_awaddr) {
                std::printf("[c=%llu] AW sdram=0x%08x\n",
                            (unsigned long long)c, tb->dbg_sdram_awaddr);
                prev_sdram_awaddr = tb->dbg_sdram_awaddr;
                ar_log_budget--;
            }
            if (tb->dbg_periph_awvalid && tb->dbg_periph_awaddr != prev_periph_awaddr) {
                std::printf("[c=%llu] AW periph=0x%08x\n",
                            (unsigned long long)c, tb->dbg_periph_awaddr);
                prev_periph_awaddr = tb->dbg_periph_awaddr;
                ar_log_budget--;
            }
        }
        // Reset prev trackers outside the budget gate so we always see
        // new addresses.  Pre-existing addresses that stay valid are
        // collapsed until the next toggle.
        if (!tb->dbg_sdram_arvalid)  prev_sdram_araddr = 0xFFFFFFFF;
        if (!tb->dbg_sdram_awvalid)  prev_sdram_awaddr = 0xFFFFFFFF;
        if (!tb->dbg_periph_arvalid) prev_periph_araddr = 0xFFFFFFFF;
        if (!tb->dbg_periph_awvalid) prev_periph_awaddr = 0xFFFFFFFF;
        if (!tb->dbg_cram0_arvalid)  prev_cram0_araddr = 0xFFFFFFFF;
        if (c - last_report >= 500000) {
            last_report = c;
            uint32_t current_pc = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu.__PVT__PcPlugin_logic_harts_0_self_state;
            uint32_t a0 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[10];
            uint32_t a1 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[11];
            uint32_t a3 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[13];
            uint32_t a4 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[14];
            uint32_t a5 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[15];
            std::printf("[c=%llu] PC=0x%08x a0=0x%08x a1=0x%08x a3=0x%08x a4=0x%08x a5=0x%08x\n"
                        "    periph_ar=0x%08x/%d  sdram_ar=0x%08x/%d  cram0_ar=0x%08x/%d  mode=%d  uart_bytes=%zu\n"
                        "    sdram slave_st=%d model_st=%d busy=%d  "
                        "arb: st=%d grant_cpu=%d  cmd_fwd=%d accepted=%d\n",
                        (unsigned long long)c, current_pc, a0, a1, a3, a4, a5,
                        tb->dbg_periph_araddr, tb->dbg_periph_arvalid,
                        tb->dbg_sdram_araddr, tb->dbg_sdram_arvalid,
                        tb->dbg_cram0_araddr, tb->dbg_cram0_arvalid,
                        tb->dbg_cram0_mode, uart_buf.size(),
                        tb->dbg_sdram_slave_state,
                        tb->dbg_sdram_model_state,
                        tb->dbg_sdram_model_busy,
                        tb->dbg_arb_state,
                        tb->dbg_arb_grant_cpu,
                        tb->dbg_cmd_forwarded,
                        tb->dbg_accepted);
        }

        if (uart_buf.find("HAL init") != std::string::npos) {
            std::printf("\n\n=== PASS: observed 'HAL init' at cycle %llu ===\n",
                        (unsigned long long)c);
            exit_code = 0;
            break;
        }

        // Note: we don't fail on "no bus activity" alone — the CPU may
        // legitimately be executing cached code in tight loops and not
        // issuing any AR for thousands of cycles.  Only timeout on
        // max_cycles.
     }

    if (exit_code == 1) {
        uint32_t current_pc = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu.__PVT__PcPlugin_logic_harts_0_self_state;
        uint32_t a0 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[10];
        uint32_t a1 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[11];
        uint32_t a3 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[13];
        uint32_t a4 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[14];
        uint32_t a5 = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu__integer_RegFilePlugin_logic_regfile_fpga.asMem_ram[15];
        std::printf("\n\n=== TIMEOUT at %llu cycles ===\n",
                    (unsigned long long)max_cycles);
        std::printf("  Current PC: 0x%08x\n", current_pc);
        std::printf("  Registers: a0=0x%08x a1=0x%08x a3=0x%08x a4=0x%08x a5=0x%08x\n",
                    a0, a1, a3, a4, a5);
        std::printf("  UART bytes observed: %zu\n", uart_buf.size());
        if (!uart_buf.empty()) {
            size_t take = uart_buf.size() > 200 ? 200 : uart_buf.size();
            std::printf("  Last %zu bytes: ", take);
            for (size_t i = uart_buf.size() - take; i < uart_buf.size(); i++) {
                char ch = uart_buf[i];
                if (ch >= 0x20 && ch < 0x7f) std::fputc(ch, stdout);
                else                          std::printf("\\x%02x", (uint8_t)ch);
            }
            std::fputc('\n', stdout);
        }
        std::printf("  last periph AR: 0x%08x valid=%d  state=%d\n",
                    tb->dbg_periph_araddr, tb->dbg_periph_arvalid, tb->dbg_periph_state);
        std::printf("  last sdram  AR: 0x%08x valid=%d\n",
                    tb->dbg_sdram_araddr, tb->dbg_sdram_arvalid);
        std::printf("  last cram0  AR: 0x%08x valid=%d  mode=%d\n",
                    tb->dbg_cram0_araddr, tb->dbg_cram0_arvalid, tb->dbg_cram0_mode);
    }

    delete tb;
    return exit_code;
}
