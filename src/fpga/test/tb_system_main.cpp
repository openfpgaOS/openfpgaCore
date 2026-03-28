/*
 * Verilator System Test: VexiiRiscv CPU + SDRAM + BRAM + UART
 *
 * Loads firmware binary into BRAM, runs the CPU, captures UART output.
 * Can load either:
 *   1. A raw binary file (firmware.bin) at address 0x00000000
 *   2. Built-in self-test firmware if no file specified
 *
 * Usage: ./Vtb_system [firmware.bin] [max_cycles]
 */

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include "Vtb_system.h"
#include "verilated.h"

static Vtb_system *tb;
static uint64_t sim_time = 0;
static uint64_t cycle_count = 0;

static void tick() {
    tb->clk = 0;
    tb->eval();
    sim_time++;
    tb->clk = 1;
    tb->eval();
    sim_time++;
    cycle_count++;

    // Capture UART output
    if (tb->uart_tx_valid) {
        putchar(tb->uart_tx_byte);
        fflush(stdout);
    }
}

static void bram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1;
    tb->bd_addr = word_addr;
    tb->bd_wdata = data;
    tick();
    tb->bd_we = 0;
}

static uint32_t bram_read(uint32_t word_addr) {
    tb->bd_addr = word_addr;
    tb->eval();
    return tb->bd_rdata;
}

// Load raw binary into BRAM
static int load_binary(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { printf("ERROR: can't open %s\n", path); return -1; }

    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);

    printf("Loading %s (%ld bytes, %ld words)\n", path, size, size / 4);
    if (size > 256 * 1024) {
        printf("WARNING: binary exceeds 256KB BRAM\n");
        size = 256 * 1024;
    }

    uint8_t *buf = (uint8_t *)malloc(size);
    fread(buf, 1, size, f);
    fclose(f);

    // Load as little-endian 32-bit words
    for (long i = 0; i < size; i += 4) {
        uint32_t word = 0;
        for (int b = 0; b < 4 && (i + b) < size; b++)
            word |= (uint32_t)buf[i + b] << (b * 8);
        bram_write(i / 4, word);
    }

    free(buf);
    return 0;
}

// Built-in self-test: write to SDRAM, read back, print result via UART
static const uint32_t fw_selftest[] = {
    // UART putchar helper: write byte in a0 to 0x4F000004
    // Address: 0x00000000 (function entry, called via jalr)

    // main entry point at 0x00000000:
    // Step 1: Write 0xCAFEBABE to SDRAM[0x10000000]
    0x10000537,  //  0: lui   a0, 0x10000     # a0 = 0x10000000
    0xCAFEC5B7,  //  4: lui   a1, 0xCAFEC
    0xABE58593,  //  8: addi  a1, a1, -1346   # a1 = 0xCAFEBABE
    0x00B52023,  //  C: sw    a1, 0(a0)       # SDRAM[0] = 0xCAFEBABE
    0x0FF0000F,  // 10: fence

    // Step 2: Read back
    0x00052603,  // 14: lw    a2, 0(a0)       # a2 = SDRAM[0]

    // Step 3: Compare and print result via UART
    0x4F000737,  // 18: lui   a4, 0x4F000     # a4 = 0x4F000000 (UART base)

    // Print 'O' if pass, 'X' if fail
    0x04F00693,  // 1C: li    a3, 'O'
    0x00C58463,  // 20: beq   a1, a2, +8
    0x05800693,  // 24: li    a3, 'X'         # fail

    // 0x28: Write char to UART TX (0x4F000004)
    0x00D72223,  // 28: sw    a3, 4(a4)       # UART TX = char

    // Print 'K' or '!'
    0x04B00693,  // 2C: li    a3, 'K'
    0x00C58463,  // 30: beq   a1, a2, +8
    0x02100693,  // 34: li    a3, '!'

    0x00D72223,  // 38: sw    a3, 4(a4)       # UART TX

    // Print newline
    0x00A00693,  // 3C: li    a3, '\n'
    0x00D72223,  // 40: sw    a3, 4(a4)

    // Write pass/fail to BRAM result register (0x0003FF00)
    0x00040837,  // 44: lui   a6, 0x00040
    0xF0080813,  // 48: addi  a6, a6, -256    # a6 = 0x0003FF00

    0x00100693,  // 4C: li    a3, 1           # pass
    0x00C58463,  // 50: beq   a1, a2, +8
    0x00200693,  // 54: li    a3, 2           # fail

    0x00D82023,  // 58: sw    a3, 0(a6)       # result status
    0x00C82223,  // 5C: sw    a2, 4(a6)       # result value

    // Halt
    0x0000006F,  // 60: j     .               # infinite loop
};

#define RESULT_STATUS_ADDR  (0x3FF00 / 4)
#define RESULT_VALUE_ADDR   (0x3FF04 / 4)

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_system;

    const char *firmware_path = NULL;
    int max_cycles = 500000;  // Default 500K cycles

    if (argc >= 2 && argv[1][0] != '-')
        firmware_path = argv[1];
    if (argc >= 3)
        max_cycles = atoi(argv[2]);

    printf("=== VexiiRiscv System Simulation ===\n\n");

    // Hold reset
    tb->reset_n = 0;
    tb->bd_we = 0;
    tb->bd_addr = 0;
    tb->bd_wdata = 0;
    for (int i = 0; i < 10; i++) tick();

    // Load firmware
    if (firmware_path) {
        if (load_binary(firmware_path) < 0) return 1;
    } else {
        printf("Loading built-in self-test (%lu words)\n",
               (unsigned long)(sizeof(fw_selftest) / sizeof(fw_selftest[0])));
        for (unsigned i = 0; i < sizeof(fw_selftest) / sizeof(fw_selftest[0]); i++)
            bram_write(i, fw_selftest[i]);
    }

    // Clear result
    bram_write(RESULT_STATUS_ADDR, 0);

    // Release reset
    printf("Starting CPU...\n");
    printf("--- UART output ---\n");
    tb->reset_n = 1;

    // Run until result or timeout
    int status = 0;
    for (int i = 0; i < max_cycles; i++) {
        tick();
        if (i % 1000 == 999) {
            status = bram_read(RESULT_STATUS_ADDR);
            if (status != 0) break;
        }
    }

    printf("--- end UART ---\n");
    status = bram_read(RESULT_STATUS_ADDR);
    uint32_t value = bram_read(RESULT_VALUE_ADDR);

    printf("\nCPU ran for %lu cycles\n", (unsigned long)cycle_count);

    if (status == 1) {
        printf("PASS (value=0x%08x)\n", value);
    } else if (status == 2) {
        printf("FAIL (value=0x%08x)\n", value);
    } else {
        printf("TIMEOUT after %d cycles (status=%d)\n", max_cycles, status);
    }

    printf("\n=== Result: %s ===\n", status == 1 ? "PASS" : "FAIL");
    delete tb;
    return (status == 1) ? 0 : 1;
}
