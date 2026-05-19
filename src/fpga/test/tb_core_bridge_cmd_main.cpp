#include "Vtb_core_bridge_cmd.h"
#include <verilated.h>
#include <cstdio>
#include <cstdint>

static Vtb_core_bridge_cmd *tb;
static vluint64_t sim_time = 0;
static int passes = 0;
static int fails = 0;

static constexpr uint32_t HOST_CMD = 0xF8000000u;
static constexpr uint32_t TARGET_CMD = 0xF8001000u;

static void tick() {
    tb->clk = 0;
    tb->eval();
    sim_time++;
    tb->clk = 1;
    tb->eval();
    sim_time++;
}

static void check_eq(const char *tag, uint32_t got, uint32_t expect) {
    if (got == expect) {
        passes++;
        std::printf("  OK  %s\n", tag);
    } else {
        fails++;
        std::printf("  FAIL %s got=0x%08x exp=0x%08x\n", tag, got, expect);
    }
}

static void write32(uint32_t addr, uint32_t data) {
    tb->bridge_addr = addr;
    tb->bridge_wr_data = data;
    tb->bridge_wr = 1;
    tick();
    tb->bridge_wr = 0;
    tick();
}

static uint32_t read32(uint32_t addr) {
    tb->bridge_addr = addr;
    tb->bridge_rd = 1;
    tick();
    tb->bridge_rd = 0;
    tick();
    return tb->bridge_rd_data;
}

static bool wait_target_cmd(uint32_t expect, int max_cycles = 64) {
    while (max_cycles-- > 0) {
        if (read32(TARGET_CMD) == expect)
            return true;
        tick();
    }
    return false;
}

static bool saw_target_cmd(uint32_t expect, int cycles = 32) {
    while (cycles-- > 0) {
        if (read32(TARGET_CMD) == expect)
            return true;
        tick();
    }
    return false;
}

static void ack_target_cmd() {
    write32(TARGET_CMD, 0x6F6B0000u);
    for (int i = 0; i < 4; i++)
        tick();
}

static void host_cmd(uint16_t cmd) {
    write32(HOST_CMD, 0x434D0000u | cmd);
    for (int i = 0; i < 8; i++)
        tick();
}

static void init_inputs() {
    tb->clk = 0;
    tb->bridge_endian_little = 0;
    tb->bridge_addr = 0;
    tb->bridge_rd = 0;
    tb->bridge_wr = 0;
    tb->bridge_wr_data = 0;
    tb->status_boot_done = 1;
    tb->status_setup_done = 0;
    tb->status_running = 0;
    tb->shutdown_ack_s = 1;
    for (int i = 0; i < 8; i++)
        tick();
}

static void test_ready_reissued_after_warm_reset() {
    std::printf("test_ready_reissued_after_warm_reset:\n");
    init_inputs();

    tb->status_setup_done = 1;
    check_eq("initial-ready", wait_target_cmd(0x636D0140u) ? 1u : 0u, 1u);
    ack_target_cmd();

    host_cmd(0x0011);  // Reset Exit into the running state
    check_eq("initial-reset-exit-drives-reset-high", tb->reset_n, 1u);
    check_eq("initial-reset-exit-no-extra-ready", saw_target_cmd(0x636D0140u) ? 1u : 0u, 0u);

    host_cmd(0x0010);  // Reset Enter
    check_eq("reset-enter-drives-reset-low", tb->reset_n, 0u);
    check_eq("warm-reset-ready", wait_target_cmd(0x636D0140u) ? 1u : 0u, 1u);
    ack_target_cmd();

    host_cmd(0x0011);  // Reset Exit
    check_eq("reset-exit-drives-reset-high", tb->reset_n, 1u);
    check_eq("reset-exit-no-extra-ready", saw_target_cmd(0x636D0140u) ? 1u : 0u, 0u);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_core_bridge_cmd;

    std::printf("=== core_bridge_cmd warm-reset test ===\n");
    test_ready_reissued_after_warm_reset();

    std::printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);
    delete tb;
    return fails == 0 ? 0 : 1;
}
