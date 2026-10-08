// SPDX-License-Identifier: Apache-2.0
// Check word ordering, masked writes, ring wrap and commit-based B responses
// while GPU/CPU/audio reads contend and the slave delays every write response.
#include "Vtb_arbiter.h"
#include <verilated.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <vector>

struct Word { uint32_t addr, data; unsigned strb; bool last; };
struct Burst { std::vector<Word> words; };
static uint32_t rng = 0x796132ae;
static uint32_t random_word() { rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng; }
static unsigned cycle;
static void require(bool good, const char *what) {
    if (!good) { std::fprintf(stderr, "FAIL cycle=%u: %s\n", cycle, what); std::exit(1); }
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    require(argc == 2, "expected queue depth argument");
    const unsigned depth = std::strtoul(argv[1], nullptr, 10), count = 4000;
    std::vector<Burst> input;
    unsigned total_words = 0;
    for (unsigned i = 0; i < count; ++i) {
        Burst burst;
        unsigned length = i < 128 ? 8 : 1 + random_word() % 8;
        uint32_t addr = 0x10000000 + (random_word() & 0x3ffc);
        for (unsigned b = 0; b < length; ++b)
            burst.words.push_back({addr + 4*b, random_word(),
                i < 128 || i % 3 ? 15u : random_word() % 16, b + 1 == length});
        total_words += length;
        input.push_back(burst);
    }
    Vtb_arbiter tb;
    tb.reset_n = 0;
    tb.m0_awvalid = tb.m0_wvalid = tb.m0_arvalid = 0;
    tb.m1_awvalid = tb.m1_wvalid = tb.m1_arvalid = 0;
    tb.m3_arvalid = 0; tb.m1_rready = 1;
    for (unsigned i = 0; i < 8; ++i) { tb.clk=0; tb.eval(); tb.clk=1; tb.eval(); }
    tb.reset_n = 1;
    tb.m0_araddr = 0x10200000; tb.m0_arlen = 3;
    tb.m1_araddr = 0x10300000; tb.m1_arlen = 7;
    tb.m3_araddr = 0x10400000; tb.m3_arlen = 3;
    std::deque<Word> expected;
    unsigned submitted=0, wi=0, received=0, accepted=0, committed=0, credits=0;
    unsigned max_occupancy=0, downstream_left=0, downstream_size=0;
    unsigned downstream_beats=0, next_audio=0, gpu_reads=0, cpu_reads=0, audio_reads=0;
    unsigned full_cycles=0, pending_peak=0, masked=0, idle_tail=0;
    uint32_t downstream_addr=0;
    bool address_pending=false, data_pending=false, downstream_active=false;
    bool downstream_completes_aw=false;
    for (cycle=0; cycle<2000000; ++cycle) {
        if (!address_pending && !data_pending && submitted < count) {
            address_pending=true; tb.m0_awvalid=1;
            tb.m0_awaddr=input[submitted].words[0].addr;
            tb.m0_awlen=input[submitted].words.size()-1;
        }
        if (data_pending && !tb.m0_wvalid &&
            (submitted < 128 || random_word()%4)) {
            const auto &w=input[submitted].words[wi];
            tb.m0_wvalid=1; tb.m0_wdata=w.data; tb.m0_wstrb=w.strb; tb.m0_wlast=w.last;
        }
        tb.m0_arvalid = received < count;
        tb.m1_arvalid = received < count;
        if (received < count && cycle >= next_audio) tb.m3_arvalid=1;
        tb.clk=0; tb.eval();
        const bool aw=tb.m0_awvalid&&tb.m0_awready, w=tb.m0_wvalid&&tb.m0_wready;
        const bool audio=tb.m3_arvalid&&tb.m3_arready;
        gpu_reads += tb.m0_arvalid&&tb.m0_arready;
        cpu_reads += tb.m1_arvalid&&tb.m1_arready;
        audio_reads += audio;
        require(tb.dbg_gpu_wq_count == accepted-committed, "occupancy excludes uncommitted words");
        require(tb.dbg_gpu_wq_count <= depth, "queue overflow");
        max_occupancy=std::max(max_occupancy,unsigned(tb.dbg_gpu_wq_count));
        full_cycles += tb.dbg_gpu_wq_count == depth;
        if (tb.dbg_s_awvalid&&tb.dbg_s_awready) {
            require(!downstream_active, "second downstream AW before B");
            require(!expected.empty(), "downstream AW without posted word");
            downstream_active=true; downstream_addr=tb.dbg_s_awaddr;
            downstream_size=downstream_left=unsigned(tb.dbg_s_awlen)+1;
            require(downstream_size <= 8, "native burst exceeds eight words");
            require(downstream_addr==expected.front().addr, "downstream start address");
        }
        if (tb.dbg_s_wvalid&&tb.dbg_s_wready) {
            require(downstream_active&&downstream_left&&!expected.empty(), "duplicate or unsolicited W");
            const auto e=expected.front(); expected.pop_front();
            require(e.addr==downstream_addr, "word address/order");
            require(e.data==tb.dbg_s_wdata, "word data/order");
            require(e.strb==tb.dbg_s_wstrb, "byte strobes");
            require(bool(tb.dbg_s_wlast)==(downstream_left==1), "downstream WLAST");
            require(!e.last||downstream_left==1, "burst crossed upstream AW boundary");
            downstream_completes_aw=e.last; --downstream_left; downstream_addr+=4;
            ++downstream_beats; masked += e.strb!=15;
        }
        if (tb.dbg_s_bvalid) {
            require(downstream_active&&!downstream_left, "B before all W beats");
            committed+=downstream_size; credits+=downstream_completes_aw;
            downstream_active=false;
        }
        if (tb.m0_bvalid) {
            require(credits>0, "upstream B before commit or duplicate B");
            --credits; ++received;
        }
        tb.clk=1; tb.eval();
        if (aw) { tb.m0_awvalid=0; address_pending=false; data_pending=true; wi=0; }
        if (w) {
            expected.push_back(input[submitted].words[wi]); ++accepted;
            tb.m0_wvalid=0;
            if (++wi==input[submitted].words.size()) { data_pending=false; ++submitted; }
        }
        if (audio) { tb.m3_arvalid=0; next_audio=cycle+97; }
        pending_peak=std::max(pending_peak,submitted-received);
        if (received==count && ++idle_tail==128) break;
    }
    require(received==count&&submitted==count, "bounded progress / one B per AW");
    require(expected.empty()&&!downstream_active&&!credits, "all writes retired");
    require(accepted==total_words&&committed==total_words&&downstream_beats==total_words, "word conservation");
    require(max_occupancy==depth&&full_cycles&&pending_peak>1, "full and multiple pending AW coverage");
    require(masked>1000&&gpu_reads>1000&&cpu_reads>1000&&audio_reads>1000, "masked writes and reader progress coverage");
    std::printf("PASS depth=%u cycles=%u transactions=%u words=%u masked=%u full_cycles=%u pending_peak=%u reads=%u/%u/%u\n",
        depth,cycle,count,total_words,masked,full_cycles,pending_peak,gpu_reads,cpu_reads,audio_reads);
}
