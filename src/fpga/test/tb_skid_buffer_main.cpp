//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator cosim for skid_buffer.v (full-throughput valid/ready register
// slice).  Proves the three properties an AXI register slice must hold:
//   1. Data integrity / FIFO order: every beat that leaves equals, in order,
//      a beat that was accepted (no loss, no duplication, no reorder).
//   2. Handshake safety: m_valid is never asserted without a real beat behind
//      it; in/out beat counts balance.
//   3. Full throughput: with s_valid=1 and m_ready=1 it accepts AND emits one
//      beat every cycle (the slice adds latency, not a throughput penalty).
//
// Build/run: make skid   (test/Makefile target)
//
#include "Vskid_buffer.h"
#include "verilated.h"
#include <deque>
#include <cstdio>
#include <cstdlib>

static Vskid_buffer *dut;

static void tick() {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vskid_buffer;

    int passed = 0, failed = 0;
    auto CHECK = [&](bool c, const char *m) {
        if (c) { passed++; printf("  OK   %s\n", m); }
        else   { failed++; printf("  FAIL %s\n", m); }
    };

    printf("=== skid_buffer cosim (register-slice correctness) ===\n\n");

    // Reset.
    dut->reset_n = 0; dut->s_valid = 0; dut->m_ready = 0; dut->s_data = 0;
    for (int i = 0; i < 4; i++) tick();
    dut->reset_n = 1;

    // ---- Randomized cosim against a reference FIFO ----
    std::deque<uint32_t> model;
    uint32_t next = 1;
    long in_count = 0, out_count = 0;
    bool order_ok = true, novalid_empty_ok = true;
    srand(0xC0FFEE);

    for (int cyc = 0; cyc < 40000; cyc++) {
        dut->s_valid = (rand() % 100) < 70;   // 70% offer upstream
        dut->m_ready = (rand() % 100) < 60;   // 60% accept downstream
        uint32_t din = next;
        dut->s_data  = din;
        dut->eval();                          // settle combinational outputs

        bool in_fire  = dut->s_valid && dut->s_ready;
        bool out_fire = dut->m_valid && dut->m_ready;

        if (dut->m_valid && model.empty()) novalid_empty_ok = false;

        if (out_fire) {                       // oldest beat leaves first
            if (model.empty() || (uint32_t)dut->m_data != model.front()) order_ok = false;
            else model.pop_front();
            out_count++;
        }
        if (in_fire) { model.push_back(din); next++; in_count++; }

        tick();
    }

    // Drain: stop offering, keep accepting until empty.
    dut->s_valid = 0; dut->m_ready = 1;
    for (int i = 0; i < 16 && !model.empty(); i++) {
        dut->eval();
        if (dut->m_valid && dut->m_ready) {
            if (!model.empty() && (uint32_t)dut->m_data == model.front()) model.pop_front();
            else order_ok = false;
            out_count++;
        }
        tick();
    }

    CHECK(order_ok,          "random cosim: FIFO data order preserved (no reorder/corruption)");
    CHECK(novalid_empty_ok,  "m_valid never asserted without a backing beat");
    CHECK(model.empty(),     "all accepted beats eventually drained out");
    CHECK(in_count == out_count, "in_count == out_count (no loss, no duplication)");
    printf("  [cosim] accepted=%ld emitted=%ld\n\n", in_count, out_count);

    // ---- Full-throughput burst ----
    dut->s_valid = 1; dut->m_ready = 1;
    uint32_t v = next;
    for (int i = 0; i < 8; i++) { dut->s_data = v++; dut->eval(); tick(); }  // fill
    long acc = 0, emit = 0;
    for (int c = 0; c < 1000; c++) {
        dut->s_data = v++;
        dut->eval();
        if (dut->s_valid && dut->s_ready) acc++;
        if (dut->m_valid && dut->m_ready) emit++;
        tick();
    }
    CHECK(acc  == 1000, "full throughput: accepts 1 beat/cycle (s_ready held high)");
    CHECK(emit == 1000, "full throughput: emits 1 beat/cycle (m_valid held high)");
    printf("  [throughput] accepted=%ld emitted=%ld over 1000 cycles\n", acc, emit);

    printf("\n=== Results: %d passed, %d failed ===\n", passed, failed);
    delete dut;
    return failed ? 1 : 0;
}
