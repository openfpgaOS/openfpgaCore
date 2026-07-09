//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// C++ harness for tb_system_sdram — full CPU boot sim over the REAL
// MiSTer SDRAM chain (axi_sdram_slave → pulse adapter → io_sdram →
// sdram_model_full) with concurrent scanout burst traffic.
//
// Purpose: reproduce the MiSTer kernel-layout boot lottery (suspected
// i-fetch read corruption or protocol hang under scanout contention).
//
// On top of the plain tb_system boot flow this harness adds:
//   1. SDRAM preload through the chip model's clocked backdoor port.
//   2. A scanout generator: one burst_rd of SCAN_LEN 32-bit words per
//      SCAN_PERIOD cycles for SCAN_ACTIVE of every SCAN_TOTAL lines,
//      walking line-by-line through the terminal FB — the MiSTer video
//      cadence (320w @8bpp, 31.5 kHz line rate at 100 MHz ≈ 3172cyc).
//   3. An R-beat data checker: every beat delivered on the CPU→arbiter
//      M1 read channel is compared against the chip model's CURRENT
//      content (combinational backdoor peek).  Any mismatch = read-path
//      corruption, reported with full context.  rlast-vs-count protocol
//      slips and orphan beats are reported separately.
//   4. A scanout-data checker (same peek) — catches cross-contamination
//      between the word-op stream and the burst_rd stream.
//   5. A written-address bitmap fed by the chip model's write-beat tap,
//      plus a periodic scan of the kernel image region: any word that
//      was NEVER legally written but no longer matches the loaded image
//      is a DRAM strike (write-path corruption), distinguishing
//      fetch-path corruption (model content clean) from strikes.
//
// Usage: Vtb_system [os.bin path] [max_cycles]
// Env knobs: SCAN_PERIOD (3172), SCAN_LEN (80), SCAN_BASE_HW (0x180000),
//            SCAN_ACTIVE (480), SCAN_TOTAL (525), SCAN_ENABLE (1),
//            SCAN_PHASE (0), STRIKE_SCAN_PERIOD (500000)
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

static uint64_t cpu_cycles = 0;
static uint64_t clk74_accum = 0;
static bool clk_74a_state = false;

static void tick_half(bool cpu_edge_high) {
    tb->clk_cpu = cpu_edge_high ? 1 : 0;
    clk74_accum += 74;
    while (clk74_accum >= 100) {
        clk74_accum -= 100;
        clk_74a_state = !clk_74a_state;
    }
    tb->clk_74a = clk_74a_state ? 1 : 0;
    tb->eval();
    sim_time++;
}

#define VSYNC_PERIOD 16667
static void tick_cycle() {
    tick_half(false);
    tb->vsync = (cpu_cycles % VSYNC_PERIOD == 0) ? 1 : 0;
    tick_half(true);
    cpu_cycles++;
}

// Combinational chip-model peek: word address (byte>>2), returns the
// model's CURRENT 32-bit content.  Extra evals are safe (no clock edge).
static uint32_t model_peek(uint32_t word_addr) {
    tb->bd_sdram_rd_word_addr = word_addr & 0xFFFFFF;
    tb->eval();
    return tb->bd_sdram_rd_data;
}

// ========================================================================
// firmware.mif parser (identical to tb_system_main.cpp)
// ========================================================================
static bool load_firmware_mif(const char *path) {
    std::ifstream f(path);
    if (!f) { std::fprintf(stderr, "Can't open %s\n", path); return false; }
    std::string line;
    bool in_content = false;
    int count = 0;
    while (std::getline(f, line)) {
        if (!in_content) {
            if (line.find("CONTENT BEGIN") != std::string::npos) in_content = true;
            continue;
        }
        if (line.find("END;") != std::string::npos) break;
        size_t p = line.find_first_not_of(" \t");
        if (p == std::string::npos) continue;
        if (line[p] == '-') continue;
        size_t colon = line.find(':');
        size_t semi  = line.find(';');
        if (colon == std::string::npos || semi == std::string::npos) continue;
        std::string addr_str = line.substr(p, colon - p);
        std::string data_str = line.substr(colon + 1, semi - colon - 1);
        if (addr_str.find('[') != std::string::npos) {
            size_t lb = addr_str.find('[');
            size_t dots = addr_str.find("..", lb);
            size_t rb = addr_str.find(']');
            if (dots == std::string::npos || rb == std::string::npos) continue;
            int lo = std::stoi(addr_str.substr(lb + 1, dots - lb - 1));
            int hi = std::stoi(addr_str.substr(dots + 2, rb - dots - 2));
            uint32_t val = std::stoul(data_str, nullptr, 16);
            for (int a = lo; a <= hi && a < 8192; a++)
                tb->rootp->tb_system->periph__DOT__ram__DOT__mem[a] = val;
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
// os.bin loader — CRAM0 poke + SDRAM chip-model backdoor (clocked).
// ========================================================================
static const uint32_t SDRAM_OS_WORD = 0x10320000u / 4u;   // 0x40C8000 → masked
static std::vector<uint32_t> g_image;   // loaded image words (strike scan)

static bool load_os_bin(const char *path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) { std::fprintf(stderr, "Can't open %s\n", path); return false; }
    f.seekg(0, std::ios::end);
    size_t sz = f.tellg();
    f.seekg(0, std::ios::beg);
    std::vector<uint8_t> buf(sz);
    f.read(reinterpret_cast<char*>(buf.data()), sz);

    size_t words = (sz + 3) / 4;
    g_image.resize(words);
    for (size_t w = 0; w < words; w++) {
        uint32_t v = 0;
        for (int b = 0; b < 4; b++) {
            size_t idx = w * 4 + b;
            if (idx < sz) v |= ((uint32_t)buf[idx]) << (b * 8);
        }
        g_image[w] = v;
        tb->rootp->tb_system->cram0_mem__DOT__mem[w] = v;
    }
    // SDRAM preload through the clocked backdoor (reset still asserted).
    for (size_t w = 0; w < words; w++) {
        tb->bd_sdram_we = 1;
        tb->bd_sdram_word_addr = (SDRAM_OS_WORD + (uint32_t)w) & 0xFFFFFF;
        tb->bd_sdram_wdata = g_image[w];
        tick_cycle();
    }
    // Sim boot mailbox at 0x13FFFFF0: 'OSSZ' magic + true image size so
    // the bootloader can locate the OSE2 metadata block (the sim path
    // has no slot/HPS size source).
    tb->bd_sdram_we = 1;
    tb->bd_sdram_word_addr = (0x13FFFFF0u - 0x10000000u) >> 2;
    tb->bd_sdram_wdata = 0x5A53534Fu;
    tick_cycle();
    tb->bd_sdram_word_addr = (0x13FFFFF4u - 0x10000000u) >> 2;
    tb->bd_sdram_wdata = (uint32_t)sz;
    tick_cycle();
    tb->bd_sdram_we = 0;
    tick_cycle();
    // Verify a few spots through the peek port
    bool ok = true;
    for (size_t w = 0; w < words; w += (words / 7) + 1) {
        uint32_t got = model_peek((SDRAM_OS_WORD + (uint32_t)w) & 0xFFFFFF);
        if (got != g_image[w]) {
            std::fprintf(stderr, "PRELOAD VERIFY FAIL word %zu: got %08x want %08x\n",
                         w, got, g_image[w]);
            ok = false;
        }
    }
    std::printf("Loaded %zu bytes (%zu words) of os.bin into CRAM0 + chip model @0x10320000%s\n",
                sz, words, ok ? " (verified)" : " (VERIFY FAILED)");
    return ok;
}

// ========================================================================
// Main
// ========================================================================
static uint32_t env_u32(const char *name, uint32_t dflt) {
    const char *s = getenv(name);
    return s ? (uint32_t)strtoul(s, nullptr, 0) : dflt;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);

    const char *os_bin_path = "../../firmware/os/bld/sim/os.bin";
    uint64_t max_cycles = 40000000;
    if (argc > 1) os_bin_path = argv[1];
    if (argc > 2) max_cycles = std::strtoull(argv[2], nullptr, 0);

    // Scanout cadence knobs (defaults = MiSTer boot: 320w 8bpp term FB,
    // 31.5 kHz lines at 100 MHz)
    const uint32_t SCAN_PERIOD  = env_u32("SCAN_PERIOD", 3172);
    const uint32_t SCAN_LEN     = env_u32("SCAN_LEN", 80);        // 32-bit words
    const uint32_t SCAN_BASE_HW = env_u32("SCAN_BASE_HW", 0x180000); // TERM FB halfword addr
    const uint32_t SCAN_ACTIVE  = env_u32("SCAN_ACTIVE", 480);
    const uint32_t SCAN_TOTAL   = env_u32("SCAN_TOTAL", 525);
    const uint32_t SCAN_ENABLE  = env_u32("SCAN_ENABLE", 1);
    const uint32_t SCAN_PHASE   = env_u32("SCAN_PHASE", 0);
    const uint64_t STRIKE_SCAN_PERIOD = env_u32("STRIKE_SCAN_PERIOD", 500000);

    std::printf("scanout: enable=%u period=%u len=%u base_hw=0x%x active=%u/%u phase=%u\n",
                SCAN_ENABLE, SCAN_PERIOD, SCAN_LEN, SCAN_BASE_HW,
                SCAN_ACTIVE, SCAN_TOTAL, SCAN_PHASE);

    tb = new Vtb_system;

    tb->clk_cpu = 0; tb->clk_74a = 0; tb->reset_n = 0; tb->vsync = 0;
    tb->bd_cram0_wr = 0; tb->bd_cram0_addr = 0; tb->bd_cram0_wdata = 0;
    tb->bd_sdram_we = 0; tb->bd_sdram_word_addr = 0; tb->bd_sdram_wdata = 0;
    tb->bd_sdram_rd_word_addr = 0;
    tb->inj_burst_rd = 0; tb->inj_burst_addr = 0; tb->inj_burst_len = 0;

    for (int i = 0; i < 5; i++) tick_cycle();

    const char *mif_candidates[] = {
        "firmware.mif",
        "../../firmware/os/bld/sim/firmware.mif",
        "../targets/pocket/firmware.mif"
    };
    bool bram_loaded = false;
    for (const char *p : mif_candidates) {
        std::ifstream f(p);
        if (f) { bram_loaded = load_firmware_mif(p); break; }
    }
    if (!bram_loaded)
        std::fprintf(stderr, "No firmware.mif — boot will hang early.\n");

    if (!load_os_bin(os_bin_path)) return 2;

    std::printf("Asserting reset for 100 cycles\n");
    for (int i = 0; i < 100; i++) tick_cycle();

    tb->reset_n = 1;
    std::printf("Reset released, running up to %llu cycles\n",
                (unsigned long long)max_cycles);

    // ---------- checker state ----------
    std::string uart_buf;
    uint64_t r_mismatches = 0, scan_mismatches = 0, protocol_slips = 0,
             orphan_beats = 0, strike_words = 0;
    uint64_t r_beats_total = 0, m1_bursts_total = 0, scan_bursts_total = 0;
    int detail_budget = 40;

    // Active M1 read burst
    bool     rd_active = false;
    uint32_t rd_base = 0, rd_len = 0, rd_beat = 0;
    // One pending AR (pre-grant can accept AR before prior beats drain)
    bool     ar_pending = false;
    uint32_t ar_base = 0, ar_len = 0;

    // Scanout burst bookkeeping
    bool     scan_active_burst = false;
    uint32_t scan_base_hw_cur = 0, scan_beat = 0;
    uint32_t scan_line = 0;
    uint64_t scan_next_cycle = SCAN_PHASE ? SCAN_PHASE : SCAN_PERIOD;

    // ---- per-master CPU-boundary trackers (i / mem / per) ----
    struct Burst { uint32_t base, len, id, beat; bool sdram; };
    struct MasterTrk {
        const char *name;
        std::vector<Burst> pending;   // accepted ARs not yet fully returned
        uint64_t beats = 0, mism = 0, rid_mism = 0, rlast_slips = 0, orphans = 0;
    };
    MasterTrk trk_i{"I"}, trk_mem{"MEM"}, trk_per{"PER"};
    uint64_t master_mism_total = 0;
    auto is_sdram_addr = [](uint32_t a){ return a >= 0x10000000u && a < 0x14000000u; };
    auto track_ar = [&](MasterTrk &t, uint32_t addr, uint32_t len, uint32_t id) {
        t.pending.push_back({addr, len, id, 0, is_sdram_addr(addr)});
    };
    auto track_r = [&](MasterTrk &t, uint32_t data, uint32_t rid, bool rlast,
                       uint64_t c, int &budget) {
        if (t.pending.empty()) { t.orphans++; return; }
        Burst &b = t.pending.front();
        if (b.sdram) {
            uint32_t beat_addr = b.base + b.beat * 4;
            uint32_t expect = model_peek((beat_addr >> 2) & 0xFFFFFF);
            if (data != expect) {
                t.mism++; master_mism_total++;
                if (budget > 0) { budget--;
                    std::printf("[c=%llu] %s-SIDE MISMATCH addr=0x%08x (burst 0x%08x+%u/%u id=%u) got=%08x want=%08x rlast=%d rid=%u\n",
                        (unsigned long long)c, t.name, beat_addr, b.base, b.beat,
                        b.len + 1, b.id, data, expect, rlast, rid);
                }
            }
        }
        if (rid != b.id) {
            t.rid_mism++; master_mism_total++;
            if (budget > 0) { budget--;
                std::printf("[c=%llu] %s RID MISMATCH burst 0x%08x beat %u: rid=%u want=%u\n",
                    (unsigned long long)c, t.name, b.base, b.beat, rid, b.id);
            }
        }
        bool count_last = (b.beat == b.len);
        if (count_last != rlast) {
            t.rlast_slips++; master_mism_total++;
            if (budget > 0) { budget--;
                std::printf("[c=%llu] %s RLAST SLIP burst 0x%08x beat %u/%u rlast=%d\n",
                    (unsigned long long)c, t.name, b.base, b.beat, b.len + 1, rlast);
            }
        }
        b.beat++;
        t.beats++;
        if (count_last || rlast) t.pending.erase(t.pending.begin());
    };

    // ---- fabric-wedge detector + signal ring buffer ----
    struct Ring {
        uint64_t c; uint8_t slave_st, arb_st, io_dbg;
        uint8_t busy, cmd_fwd, acc, word_rd, word_wr, slave_rd, slave_wr,
                m1_awv, m1_awr, m1_arv, m1_arr, scan_act;
    };
    std::vector<Ring> ring(512);
    size_t ring_pos = 0;
    uint64_t wedge_start = 0;   // first cycle of current suspicious window
    auto dump_ring = [&](uint64_t c) {
        std::printf("--- last %zu cycles of fabric state (wedge at c=%llu) ---\n",
                    ring.size(), (unsigned long long)c);
        std::printf("cycle slave arb io busy fwd acc wrd wwr srd swr awv awr arv arr scan\n");
        for (size_t k = 0; k < ring.size(); k++) {
            const Ring &r = ring[(ring_pos + k) % ring.size()];
            if (r.c == 0) continue;
            std::printf("%llu %u %u %u %u %u %u %u %u %u %u %u %u %u %u %u\n",
                (unsigned long long)r.c, r.slave_st, r.arb_st, r.io_dbg, r.busy,
                r.cmd_fwd, r.acc, r.word_rd, r.word_wr, r.slave_rd, r.slave_wr,
                r.m1_awv, r.m1_awr, r.m1_arv, r.m1_arr, r.scan_act);
        }
    };

    // ---- ordered write-stream conformance checker ----
    // Every W beat accepted at the arbiter→slave boundary must produce
    // exactly two chip halfword write beats, in order, at the burst's
    // sequential addresses, with dqm = ~wstrb pair and matching data.
    // Any divergence = write-path corruption caught at the exact beat.
    struct HwBeat { uint32_t hw_addr; uint16_t data; uint8_t dqm; uint64_t c; };
    struct AwRec { uint32_t base_word, len; };
    std::vector<AwRec> aw_fifo;
    std::vector<HwBeat> exp_fifo;
    uint32_t wr_run_word = 0, wr_run_left = 0;
    uint64_t wconf_mism = 0, wconf_spurious = 0;
    auto push_w_beat = [&](uint32_t wdata, uint32_t wstrb, uint64_t c) {
        if (wr_run_left == 0) {
            if (aw_fifo.empty()) { wconf_spurious++; return; }
            wr_run_word = aw_fifo.front().base_word;
            wr_run_left = aw_fifo.front().len + 1;
            aw_fifo.erase(aw_fifo.begin());
        }
        uint32_t hw = ((wr_run_word << 1) & 0x1FFFFFF);
        exp_fifo.push_back({hw,     (uint16_t)(wdata & 0xFFFF),
                            (uint8_t)(~wstrb & 0x3), c});
        exp_fifo.push_back({hw + 1, (uint16_t)(wdata >> 16),
                            (uint8_t)((~wstrb >> 2) & 0x3), c});
        wr_run_word++;
        wr_run_left--;
    };
    auto check_wr_evt = [&](uint32_t hw_addr, uint16_t data, uint8_t dqm,
                            uint64_t c, int &budget) {
        if (exp_fifo.empty()) {
            wconf_spurious++;
            if (budget > 0) { budget--;
                std::printf("[c=%llu] WCONF SPURIOUS chip write @hw 0x%x data=%04x dqm=%u (no W beat pending)\n",
                            (unsigned long long)c, hw_addr, data, dqm);
            }
            return;
        }
        HwBeat e = exp_fifo.front();
        exp_fifo.erase(exp_fifo.begin());
        bool addr_bad = (e.hw_addr != hw_addr);
        bool dqm_bad  = (e.dqm != dqm);
        // data comparable only on unmasked byte lanes
        uint16_t lane_mask = ((dqm & 1) ? 0 : 0x00FF) | ((dqm & 2) ? 0 : 0xFF00);
        bool data_bad = ((e.data ^ data) & lane_mask) != 0;
        if (addr_bad || dqm_bad || data_bad) {
            wconf_mism++;
            if (budget > 0) { budget--;
                std::printf("[c=%llu] WCONF MISMATCH: chip wrote hw=0x%x data=%04x dqm=%u; expected hw=0x%x data=%04x dqm=%u (W accepted c=%llu)\n",
                            (unsigned long long)c, hw_addr, data, dqm,
                            e.hw_addr, e.data, e.dqm,
                            (unsigned long long)e.c);
            }
        }
    };

    // Written-halfword bitmap (32M halfwords)
    std::vector<uint64_t> written((32u * 1024 * 1024) / 64, 0);
    auto mark_written = [&](uint32_t hw) {
        written[(hw & 0x1FFFFFF) >> 6] |= 1ull << (hw & 63);
    };
    auto was_written = [&](uint32_t word_addr) {
        uint32_t hw = (word_addr << 1) & 0x1FFFFFF;
        return ((written[hw >> 6] >> (hw & 63)) & 1) ||
               ((written[(hw + 1) >> 6] >> ((hw + 1) & 63)) & 1);
    };

    uint64_t last_report = 0, last_strike_scan = 0, last_m1_activity = 0;
    int exit_code = 1;

    for (uint64_t c = 0; c < max_cycles; c++) {
        // ---- scanout generator: pulse burst_rd for 1 cycle ----
        if (SCAN_ENABLE && c == scan_next_cycle) {
            if (scan_line < SCAN_ACTIVE && !scan_active_burst) {
                tb->inj_burst_addr = SCAN_BASE_HW + scan_line * (SCAN_LEN * 2);
                tb->inj_burst_len  = SCAN_LEN;
                tb->inj_burst_rd   = 1;
                scan_active_burst  = true;
                scan_base_hw_cur   = tb->inj_burst_addr;
                scan_beat          = 0;
                scan_bursts_total++;
            }
            scan_line = (scan_line + 1) % SCAN_TOTAL;
            scan_next_cycle += SCAN_PERIOD;
        }

        tick_cycle();
        if (tb->inj_burst_rd) tb->inj_burst_rd = 0;   // 1-cycle pulse

        // ---- scanout data checker ----
        if (scan_active_burst && tb->inj_burst_data_valid) {
            uint32_t widx = (scan_base_hw_cur >> 1) + scan_beat;
            uint32_t expect = model_peek(widx);
            if (tb->inj_burst_data != expect) {
                scan_mismatches++;
                if (detail_budget > 0) {
                    detail_budget--;
                    std::printf("[c=%llu] SCANOUT MISMATCH beat %u @hw 0x%x: got %08x want %08x\n",
                                (unsigned long long)c, scan_beat,
                                scan_base_hw_cur + scan_beat * 2,
                                tb->inj_burst_data, expect);
                }
            }
            scan_beat++;
        }
        if (scan_active_burst && tb->inj_burst_data_done)
            scan_active_burst = false;

        // ---- M1 AR accept ----
        if (tb->dbg_m1_arvalid && tb->dbg_m1_arready) {
            if (ar_pending && detail_budget > 0) {
                detail_budget--;
                std::printf("[c=%llu] NOTE: 2nd AR accepted while one pending (base=0x%08x)\n",
                            (unsigned long long)c, tb->dbg_m1_araddr);
            }
            ar_pending = true;
            ar_base = tb->dbg_m1_araddr;
            ar_len  = tb->dbg_m1_arlen;
            m1_bursts_total++;
            last_m1_activity = c;
        }

        // ---- M1 R beat checker ----
        if (tb->dbg_m1_rvalid && tb->dbg_m1_rready) {
            if (!rd_active) {
                if (ar_pending) {
                    rd_active = true; rd_base = ar_base; rd_len = ar_len;
                    rd_beat = 0; ar_pending = false;
                } else {
                    orphan_beats++;
                    if (detail_budget > 0) {
                        detail_budget--;
                        std::printf("[c=%llu] ORPHAN M1 R BEAT: data=%08x rlast=%d (no AR!)\n",
                                    (unsigned long long)c, tb->dbg_m1_rdata,
                                    tb->dbg_m1_rlast);
                    }
                }
            }
            if (rd_active) {
                uint32_t beat_addr = rd_base + rd_beat * 4;
                uint32_t widx = (beat_addr >> 2) & 0xFFFFFF;
                uint32_t expect = model_peek(widx);
                if (tb->dbg_m1_rdata != expect) {
                    r_mismatches++;
                    if (detail_budget > 0) {
                        detail_budget--;
                        std::printf("[c=%llu] M1 R MISMATCH: addr=0x%08x (burst 0x%08x+%u/%u) got=%08x want=%08x rlast=%d\n",
                                    (unsigned long long)c, beat_addr, rd_base,
                                    rd_beat, rd_len + 1, tb->dbg_m1_rdata, expect,
                                    tb->dbg_m1_rlast);
                    }
                }
                bool count_last = (rd_beat == rd_len);
                if (count_last != (bool)tb->dbg_m1_rlast) {
                    protocol_slips++;
                    if (detail_budget > 0) {
                        detail_budget--;
                        std::printf("[c=%llu] RLAST SLIP: burst 0x%08x beat %u/%u rlast=%d\n",
                                    (unsigned long long)c, rd_base, rd_beat,
                                    rd_len + 1, tb->dbg_m1_rlast);
                    }
                }
                rd_beat++;
                if (count_last || tb->dbg_m1_rlast) rd_active = false;
                r_beats_total++;
                last_m1_activity = c;
            }
        }

        // ---- per-master CPU-boundary checkers ----
        if (tb->dbg_i_arvalid && tb->dbg_i_arready)
            track_ar(trk_i, tb->dbg_i_araddr, tb->dbg_i_arlen, tb->dbg_i_arid);
        if (tb->dbg_mem_arvalid && tb->dbg_mem_arready)
            track_ar(trk_mem, tb->dbg_mem_araddr, tb->dbg_mem_arlen, tb->dbg_mem_arid);
        if (tb->dbg_per_arvalid && tb->dbg_per_arready)
            track_ar(trk_per, tb->dbg_per_araddr, tb->dbg_per_arlen, 0);
        if (tb->dbg_i_rvalid && tb->dbg_i_rready)
            track_r(trk_i, tb->dbg_i_rdata, tb->dbg_i_rid, tb->dbg_i_rlast, c, detail_budget);
        if (tb->dbg_mem_rvalid && tb->dbg_mem_rready)
            track_r(trk_mem, tb->dbg_mem_rdata, tb->dbg_mem_rid, tb->dbg_mem_rlast, c, detail_budget);
        if (tb->dbg_per_rvalid && tb->dbg_per_rready)
            track_r(trk_per, tb->dbg_per_rdata, 0, tb->dbg_per_rlast, c, detail_budget);

        // ---- fabric ring buffer + wedge detector ----
        ring[ring_pos] = { c, (uint8_t)tb->dbg_sdram_slave_state,
            (uint8_t)tb->dbg_arb_state, (uint8_t)tb->dbg_sdram_model_state,
            (uint8_t)tb->dbg_sdram_model_busy, (uint8_t)tb->dbg_cmd_forwarded,
            (uint8_t)tb->dbg_accepted, (uint8_t)tb->dbg_word_rd_sd,
            (uint8_t)tb->dbg_word_wr_sd, (uint8_t)tb->dbg_slave_rd,
            (uint8_t)tb->dbg_slave_wr, (uint8_t)tb->dbg_m1_awvalid,
            (uint8_t)tb->dbg_m1_awready, (uint8_t)tb->dbg_m1_arvalid,
            (uint8_t)tb->dbg_m1_arready, (uint8_t)scan_active_burst };
        ring_pos = (ring_pos + 1) % ring.size();
        // slave stuck mid-transaction (S_RD_CMD/S_WR_CMD wait-accepted or
        // S_WR_DON wait-!busy) with no accepted pulse = fabric wedge
        {
            uint8_t st = tb->dbg_sdram_slave_state;
            bool suspicious = (st == 1 || st == 3 || st == 4) && !tb->dbg_accepted;
            if (!suspicious) wedge_start = c;
            else if (c - wedge_start > 20000) {
                std::printf("\n=== FABRIC WEDGE: slave_st=%u stuck %llu cycles (c=%llu) ===\n",
                            st, (unsigned long long)(c - wedge_start),
                            (unsigned long long)c);
                dump_ring(c);
                exit_code = 6;
                break;
            }
        }

        // ---- write-stream conformance ----
        if (tb->dbg_aw_valid && tb->dbg_aw_ready)
            aw_fifo.push_back({tb->dbg_aw_addr >> 2, tb->dbg_aw_len});
        if (tb->dbg_w_valid && tb->dbg_w_ready)
            push_w_beat(tb->dbg_w_data, tb->dbg_w_strb, c);
        if (tb->dbg_model_wr_evt)
            check_wr_evt(tb->dbg_model_wr_hw_addr, tb->dbg_model_wr_data,
                         tb->dbg_model_wr_dqm, c, detail_budget);
        if (wconf_mism + wconf_spurious > 30) {
            std::printf("\n=== ABORT: write-conformance threshold at c=%llu ===\n",
                        (unsigned long long)c);
            exit_code = 4;
            break;
        }

        // ---- written bitmap ----
        if (tb->dbg_model_wr_evt && tb->dbg_model_wr_dqm != 3)
            mark_written(tb->dbg_model_wr_hw_addr);

        // ---- M1 write activity (liveness only) ----
        if (tb->dbg_m1_awvalid && tb->dbg_m1_awready)
            last_m1_activity = c;

        // per-master mismatch abort
        if (master_mism_total > 30) {
            std::printf("\n=== ABORT: per-master corruption threshold at c=%llu ===\n",
                        (unsigned long long)c);
            exit_code = 4;
            break;
        }

        // ---- UART ----
        if (tb->uart_tx_dv) {
            char ch = (char)(tb->uart_tx_byte & 0xFF);
            std::fputc(ch, stdout);
            std::fflush(stdout);
            uart_buf.push_back(ch);
            if (uart_buf.size() > 8192) uart_buf.erase(0, 4096);
        }

        // ---- periodic DRAM-strike scan of the kernel image ----
        if (c - last_strike_scan >= STRIKE_SCAN_PERIOD) {
            last_strike_scan = c;
            uint64_t found = 0;
            for (size_t w = 0; w < g_image.size(); w++) {
                uint32_t widx = (SDRAM_OS_WORD + (uint32_t)w) & 0xFFFFFF;
                if (was_written(widx)) continue;
                uint32_t got = model_peek(widx);
                if (got != g_image[w]) {
                    found++;
                    if (detail_budget > 0) {
                        detail_budget--;
                        std::printf("[c=%llu] DRAM STRIKE @0x%08x: got %08x want %08x (never legally written)\n",
                                    (unsigned long long)c,
                                    0x10320000u + (uint32_t)w * 4, got, g_image[w]);
                    }
                }
            }
            if (found > strike_words) strike_words = found;
        }

        // ---- progress report ----
        if (c - last_report >= 500000) {
            last_report = c;
            uint32_t current_pc = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu.__PVT__PcPlugin_logic_harts_0_self_state;
            std::printf("[c=%llu] PC=0x%08x uart=%zu m1_bursts=%llu r_beats=%llu scan_bursts=%llu | mism=%llu scan_mism=%llu slips=%llu orphans=%llu strikes=%llu\n",
                        (unsigned long long)c, current_pc, uart_buf.size(),
                        (unsigned long long)m1_bursts_total,
                        (unsigned long long)r_beats_total,
                        (unsigned long long)scan_bursts_total,
                        (unsigned long long)r_mismatches,
                        (unsigned long long)scan_mismatches,
                        (unsigned long long)protocol_slips,
                        (unsigned long long)orphan_beats,
                        (unsigned long long)strike_words);
            std::printf("    m1_ar: v=%d r=%d addr=0x%08x | shim_ar: v=%d addr=0x%08x | periph_ar: v=%d addr=0x%08x\n"
                        "    arb_st=%d grant_cpu=%d slave_st=%d io_dbg=0x%x busy=%d cmd_fwd=%d acc=%d word_rd=%d word_wr=%d\n",
                        tb->dbg_m1_arvalid, tb->dbg_m1_arready, tb->dbg_m1_araddr,
                        tb->dbg_sdram_arvalid, tb->dbg_sdram_araddr,
                        tb->dbg_periph_arvalid, tb->dbg_periph_araddr,
                        tb->dbg_arb_state, tb->dbg_arb_grant_cpu,
                        tb->dbg_sdram_slave_state, tb->dbg_sdram_model_state,
                        tb->dbg_sdram_model_busy, tb->dbg_cmd_forwarded,
                        tb->dbg_accepted, tb->dbg_word_rd_sd, tb->dbg_word_wr_sd);
        }

        // ---- verdicts ----
        if (uart_buf.find("HAL init") != std::string::npos) {
            std::printf("\n\n=== BOOTED: 'HAL init' at cycle %llu ===\n",
                        (unsigned long long)c);
            exit_code = (r_mismatches || scan_mismatches || protocol_slips ||
                         orphan_beats || strike_words) ? 3 : 0;
            break;
        }
        if (r_mismatches + protocol_slips + orphan_beats > 100) {
            std::printf("\n=== ABORT: corruption threshold exceeded at cycle %llu ===\n",
                        (unsigned long long)c);
            exit_code = 4;
            break;
        }
        if (c - last_m1_activity > 1500000) {
            std::printf("\n=== M1 DEAD: no CPU SDRAM traffic for 1.5M cycles (at %llu) ===\n",
                        (unsigned long long)c);
            exit_code = 5;
            break;
        }
    }

    uint32_t final_pc = tb->vlSymsp->TOP__tb_system__cpu_sys__cpu.__PVT__PcPlugin_logic_harts_0_self_state;
    std::printf("\n=== SUMMARY ===\n");
    std::printf("cycles=%llu PC=0x%08x exit=%d\n",
                (unsigned long long)cpu_cycles, final_pc, exit_code);
    std::printf("m1_bursts=%llu r_beats=%llu scan_bursts=%llu\n",
                (unsigned long long)m1_bursts_total,
                (unsigned long long)r_beats_total,
                (unsigned long long)scan_bursts_total);
    std::printf("R mismatches=%llu  scanout mismatches=%llu  rlast slips=%llu  orphan beats=%llu  DRAM strikes=%llu\n",
                (unsigned long long)r_mismatches,
                (unsigned long long)scan_mismatches,
                (unsigned long long)protocol_slips,
                (unsigned long long)orphan_beats,
                (unsigned long long)strike_words);
    for (MasterTrk *t : {&trk_i, &trk_mem, &trk_per})
        std::printf("%s: beats=%llu mism=%llu rid_mism=%llu rlast_slips=%llu orphans=%llu pending=%zu\n",
                    t->name, (unsigned long long)t->beats,
                    (unsigned long long)t->mism,
                    (unsigned long long)t->rid_mism,
                    (unsigned long long)t->rlast_slips,
                    (unsigned long long)t->orphans, t->pending.size());
    std::printf("WCONF: mism=%llu spurious=%llu exp_fifo=%zu aw_fifo=%zu run_left=%u\n",
                (unsigned long long)wconf_mism,
                (unsigned long long)wconf_spurious,
                exp_fifo.size(), aw_fifo.size(), wr_run_left);
    if (!exp_fifo.empty())
        std::printf("WCONF: oldest undelivered W beat: hw=0x%x data=%04x dqm=%u accepted at c=%llu\n",
                    exp_fifo.front().hw_addr, exp_fifo.front().data,
                    exp_fifo.front().dqm,
                    (unsigned long long)exp_fifo.front().c);
    if (exit_code == 1) {
        std::printf("=== TIMEOUT ===\n");
        std::printf("  last sdram AR: 0x%08x valid=%d\n",
                    tb->dbg_sdram_araddr, tb->dbg_sdram_arvalid);
        std::printf("  slave_st=%d io_dbg=%d arb: st=%d grant_cpu=%d cmd_fwd=%d\n",
                    tb->dbg_sdram_slave_state, tb->dbg_sdram_model_state,
                    tb->dbg_arb_state, tb->dbg_arb_grant_cpu,
                    tb->dbg_cmd_forwarded);
        if (!uart_buf.empty()) {
            size_t take = uart_buf.size() > 300 ? 300 : uart_buf.size();
            std::printf("  last %zu uart bytes: ", take);
            for (size_t i = uart_buf.size() - take; i < uart_buf.size(); i++) {
                char ch = uart_buf[i];
                if (ch >= 0x20 && ch < 0x7f) std::fputc(ch, stdout);
                else                          std::printf("\\x%02x", (uint8_t)ch);
            }
            std::fputc('\n', stdout);
        }
    }

    delete tb;
    return exit_code;
}
