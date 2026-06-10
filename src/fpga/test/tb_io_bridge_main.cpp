//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_io_bridge_main.cpp — phase-sweep harness for io_bridge_peripheral.
//
// Both bridge_spiclk and clk_74a run at 74.25 MHz on hardware; their
// relative phase is latched at power-up and then FIXED.  This harness
// drives the host (Aristotle) side at full-rate bulk-write pacing and
// sweeps the spiclk-vs-clk phase offset in PHASES (default 64) sub-ns
// steps across one 13.468 ns period, using a 1 ps timebase.
//
// Per phase step a FRESH model is created (power-up determinism), a
// burst of back-to-back write words is played, and the checker
// verifies that every transmitted (addr, word) appears exactly once,
// in order, on the pmp_wr interface.
//
// Pacing profiles model the host's per-word framing:
//   lead = SS-fall  -> first spiclk rising edge
//   hold = last spiclk rising edge -> SS-rise
//   gap  = SS-high time between words (this IS the inter-word spacing:
//          sparse traffic == large gap, full-rate bulk == minimal gap)
// all in units of the 13.468 ns clock period P (fractions allowed).
// The module header says worst-case word pacing is 88 clk cycles; the
// "spec88" profile reproduces exactly that.  Writes are documented as
// "immediate", so bulk write streams may frame tighter — the remaining
// profiles probe how much framing margin the receiver really has.
//
// A read-path sweep (host releases the bus, peripheral transmits the
// word back, self-clocked) runs as well, to pin the TX semantics.
//
// Exit code 0 only if every profile passes at every phase.
//

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <array>
#include <string>
#include <algorithm>
#include <memory>
#include "Vtb_io_bridge.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static const uint64_t P = 13468;   // 74.25 MHz period, ps (1 ps timebase)
static const uint64_t H = P / 2;   // half period

static const int PHASES = 64;

// ── host event scheduling ───────────────────────────────────────────

enum Sig { SIG_SS, SIG_SCK, SIG_MOSI, SIG_MISO, SIG_OE };

struct Ev {
    uint64_t t;
    uint8_t  sig;
    uint8_t  val;
};

struct Profile {
    const char *name;
    double      lead_p;     // SS fall -> first rising edge, in P
    double      hold_p;     // last rising edge -> SS rise, in P
    double      gap_p;      // SS high between words, in P
    int         words;
};

struct WordRec {
    uint32_t addr;
    uint32_t data;
    bool operator==(const WordRec &o) const {
        return addr == o.addr && data == o.data;
    }
};

static uint32_t bswap32(uint32_t v)
{
    return (v >> 24) | ((v >> 8) & 0xff00) | ((v << 8) & 0xff0000) | (v << 24);
}

static uint32_t pat_data(int w)
{
    // distinct byte values per word so rotations are identifiable
    uint32_t b0 = (0x10 + w * 4 + 0) & 0xff;
    uint32_t b1 = (0x10 + w * 4 + 1) & 0xff;
    uint32_t b2 = (0x10 + w * 4 + 2) & 0xff;
    uint32_t b3 = (0x10 + w * 4 + 3) & 0xff;
    return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3;
}

// Build the full write burst.  Returns the byte stream actually shifted
// out (for rotation forensics) and the expected pmp-side words.
static void build_burst(const Profile &pr, uint64_t t0, bool endian_little,
                        std::vector<Ev> &ev,
                        std::vector<WordRec> &expected,
                        std::vector<uint8_t> &stream)
{
    uint64_t lead = (uint64_t)(pr.lead_p * P + 0.5);
    uint64_t hold = (uint64_t)(pr.hold_p * P + 0.5);
    uint64_t gap  = (uint64_t)(pr.gap_p  * P + 0.5);

    uint64_t t = t0;
    for (int w = 0; w < pr.words; w++) {
        uint32_t addr = 0x40000000u + (uint32_t)w * 4;
        uint32_t data = pat_data(w);

        uint8_t bytes[8] = {
            (uint8_t)(addr >> 24), (uint8_t)(addr >> 16),
            (uint8_t)(addr >> 8),  (uint8_t)((addr & 0xfc) | 1), // write flag
            (uint8_t)(data >> 24), (uint8_t)(data >> 16),
            (uint8_t)(data >> 8),  (uint8_t)data
        };
        for (int i = 0; i < 8; i++) stream.push_back(bytes[i]);

        WordRec ex;
        ex.addr = addr & 0xfffffffcu;
        ex.data = endian_little ? bswap32(data) : data; // latch is byte-swapped
        expected.push_back(ex);

        ev.push_back({t, SIG_SS, 0});
        // 32 rising edges, 2 bits (mosi=high bit, miso=low bit) each
        uint64_t te_last = 0;
        for (int k = 0; k < 32; k++) {
            uint64_t te = t + lead + (uint64_t)k * P;
            uint8_t byte = bytes[k / 4];
            int sh = (k % 4) * 2;
            uint8_t hi = (byte >> (7 - sh)) & 1;
            uint8_t lo = (byte >> (6 - sh)) & 1;
            ev.push_back({te - H, SIG_SCK, 0});
            ev.push_back({te - H, SIG_MOSI, hi});
            ev.push_back({te - H, SIG_MISO, lo});
            ev.push_back({te, SIG_SCK, 1});
            te_last = te;
        }
        ev.push_back({te_last + H, SIG_SCK, 0});
        ev.push_back({te_last + hold, SIG_SS, 1});
        t = te_last + hold + gap;
    }

    std::stable_sort(ev.begin(), ev.end(),
                     [](const Ev &a, const Ev &b) { return a.t < b.t; });
}

// ── model run ───────────────────────────────────────────────────────

static void apply_ev(Vtb_io_bridge *top, const Ev &e)
{
    switch (e.sig) {
    case SIG_SS:   top->host_ss     = e.val; break;
    case SIG_SCK:  top->host_spiclk = e.val; break;
    case SIG_MOSI: top->host_mosi   = e.val; break;
    case SIG_MISO: top->host_miso   = e.val; break;
    case SIG_OE:   top->host_oe     = e.val; break;
    }
}

struct RunOut {
    std::vector<WordRec> got;
};

static RunOut run_write_phase(const Profile &pr, uint64_t phase,
                              bool endian_little,
                              std::vector<WordRec> &expected,
                              std::vector<uint8_t> &stream,
                              const char *vcd_path)
{
    auto ctx = std::make_unique<VerilatedContext>();
    auto top = std::make_unique<Vtb_io_bridge>(ctx.get());
    std::unique_ptr<VerilatedVcdC> vcd;
    vluint64_t now = 0;

    if (vcd_path) {
        ctx->traceEverOn(true);
        vcd.reset(new VerilatedVcdC);
        top->trace(vcd.get(), 99);
        vcd->open(vcd_path);
    }

    top->reset_n       = 1;
    top->endian_little = endian_little;
    top->host_oe       = 1;
    top->host_ss       = 1;     // idle: deselected
    top->host_spiclk   = 0;
    top->host_mosi     = 0;
    top->host_miso     = 0;
    top->clk           = 0;
    top->pmp_rd_data   = 0;
    top->eval();
    if (vcd) vcd->dump(now);

    std::vector<Ev> ev;
    expected.clear();
    stream.clear();
    build_burst(pr, 40 * P, endian_little, ev, expected, stream);
    uint64_t t_end = ev.back().t + 200 * P;

    // clk edges: rising at phase + k*P (phase 0 mapped to P so the
    // schedule still starts in the preamble; mod P it is identical and
    // produces exact same-timestep edge coincidence with spiclk).
    uint64_t next_clk = phase ? phase : P;
    bool clk_lvl = false;
    size_t ei = 0;
    int prev_wr = 0;
    RunOut out;

    uint64_t t = 0;
    while (t < t_end || ei < ev.size()) {
        uint64_t tn = next_clk;
        if (ei < ev.size() && ev[ei].t < tn) tn = ev[ei].t;
        t = tn;

        while (ei < ev.size() && ev[ei].t == t) {
            apply_ev(top.get(), ev[ei]);
            ei++;
        }
        bool clk_rise = false;
        if (t == next_clk) {
            clk_lvl = !clk_lvl;
            top->clk = clk_lvl;
            clk_rise = clk_lvl;
            next_clk += H;
        }
        top->eval();
        now = t;
        if (vcd) vcd->dump(now);

        if (clk_rise) {
            if (top->pmp_wr && !prev_wr)
                out.got.push_back({top->pmp_addr, top->pmp_wr_data});
            prev_wr = top->pmp_wr;
        }
    }
    if (vcd) vcd->close();
    return out;
}

// classification: '.' pass, 'D' in-order word drops, 'R' corruption
// (data that matches the transmitted byte stream at a rotated/shifted
// offset, or arbitrary garbage)
static char classify(const std::vector<WordRec> &expected,
                     const std::vector<WordRec> &got)
{
    if (expected == got) return '.';
    size_t i = 0;
    bool subseq = true;
    for (const auto &g : got) {
        while (i < expected.size() && !(expected[i] == g)) i++;
        if (i == expected.size()) { subseq = false; break; }
        i++;
    }
    return subseq ? 'D' : 'R';
}

// rotation forensics: does this received word exist as 4 consecutive
// bytes in the transmitted stream at a non-word-aligned offset?
static int find_rot_offset(const std::vector<uint8_t> &stream,
                           uint32_t data, bool endian_little)
{
    uint32_t latch = endian_little ? bswap32(data) : data;
    uint8_t b[4] = { (uint8_t)(latch >> 24), (uint8_t)(latch >> 16),
                     (uint8_t)(latch >> 8),  (uint8_t)latch };
    for (size_t i = 0; i + 4 <= stream.size(); i++) {
        if (!memcmp(&stream[i], b, 4))
            return (int)(i % 8); // 8-byte word frame; 4 = aligned data field
    }
    return -1;
}

// ── read path ───────────────────────────────────────────────────────
//
// host sends a 4-byte address with bit0 clear, releases the bus, then
// receives 16 spiclk cycles (2 bits each) self-clocked by the
// peripheral.  Returns true on success; reports the returned word and
// the TX-start latency (ps from last addr rising edge to the
// peripheral's drive-high).

static bool run_read_phase(uint64_t phase, uint32_t rd_data, bool endian_little,
                           uint32_t &word_out, int64_t &tx_latency_ps)
{
    auto ctx = std::make_unique<VerilatedContext>();
    auto top = std::make_unique<Vtb_io_bridge>(ctx.get());
    std::unique_ptr<VerilatedVcdC> vcd;
    const char *rd_vcd = getenv("BRX_READ_VCD");
    if (rd_vcd && *rd_vcd) {
        ctx->traceEverOn(true);
        vcd.reset(new VerilatedVcdC);
        top->trace(vcd.get(), 99);
        vcd->open(rd_vcd);
        setenv("BRX_READ_VCD", "", 1);  // only the first read run
    } else {
        rd_vcd = nullptr;
    }

    top->reset_n       = 1;
    top->endian_little = endian_little;
    top->host_oe       = 1;
    top->host_ss       = 1;
    top->host_spiclk   = 0;
    top->host_mosi     = 0;
    top->host_miso     = 0;
    top->clk           = 0;
    top->pmp_rd_data   = rd_data;
    top->eval();
    if (vcd) vcd->dump(0);

    uint32_t addr = 0x40001000u; // bit0 clear: read
    uint8_t bytes[4] = { (uint8_t)(addr >> 24), (uint8_t)(addr >> 16),
                         (uint8_t)(addr >> 8),  (uint8_t)(addr & 0xfc) };

    std::vector<Ev> ev;
    uint64_t t0 = 40 * P, lead = 2 * P;
    ev.push_back({t0, SIG_SS, 0});
    uint64_t te_last = 0;
    for (int k = 0; k < 16; k++) {
        uint64_t te = t0 + lead + (uint64_t)k * P;
        uint8_t byte = bytes[k / 4];
        int sh = (k % 4) * 2;
        ev.push_back({te - H, SIG_SCK, 0});
        ev.push_back({te - H, SIG_MOSI, (uint8_t)((byte >> (7 - sh)) & 1)});
        ev.push_back({te - H, SIG_MISO, (uint8_t)((byte >> (6 - sh)) & 1)});
        ev.push_back({te, SIG_SCK, 1});
        te_last = te;
    }
    ev.push_back({te_last + H, SIG_SCK, 0});
    ev.push_back({te_last + P, SIG_OE, 0});   // release the bus
    std::stable_sort(ev.begin(), ev.end(),
                     [](const Ev &a, const Ev &b) { return a.t < b.t; });

    uint64_t t_end = te_last + 400 * P;
    uint64_t next_clk = phase ? phase : P;
    bool clk_lvl = false;
    size_t ei = 0;

    int prev_pad = 1;        // host left it... track resolved pad
    bool released = false, seen_fall = false, first_rise_seen = false;
    int pairs = 0;
    uint32_t word = 0;
    tx_latency_ps = -1;

    uint64_t t = 0;
    while ((t < t_end) && pairs < 16) {
        uint64_t tn = next_clk;
        if (ei < ev.size() && ev[ei].t < tn) tn = ev[ei].t;
        t = tn;
        while (ei < ev.size() && ev[ei].t == t) {
            if (ev[ei].sig == SIG_OE && ev[ei].val == 0) {
                released = true;
                prev_pad = 0; // pad pulls low once released (sck was left 0)
            }
            apply_ev(top.get(), ev[ei]);
            ei++;
        }
        if (t == next_clk) {
            clk_lvl = !clk_lvl;
            top->clk = clk_lvl;
            next_clk += H;
        }
        top->eval();
        if (vcd) vcd->dump(t);

        if (released) {
            int pad = top->pad_spiclk;
            if (pad && !prev_pad) {                 // rising
                if (!first_rise_seen) {
                    first_rise_seen = true;          // ST_SEND_N drive-high
                    tx_latency_ps = (int64_t)t - (int64_t)te_last;
                } else if (seen_fall) {
                    word = (word << 2) |
                           ((uint32_t)top->pad_spimosi << 1) |
                           (uint32_t)top->pad_spimiso;
                    pairs++;
                }
            }
            if (!pad && prev_pad) seen_fall = true;
            prev_pad = pad;
        }
    }

    if (vcd) vcd->close();
    if (getenv("BRX_DEBUG_READ"))
        printf("  [dbg] pairs=%d seen_fall=%d first_rise=%d t=%llu word=%08x\n",
               pairs, (int)seen_fall, (int)first_rise_seen,
               (unsigned long long)t, word);

    word_out = word;
    uint32_t expect = endian_little ? bswap32(rd_data) : rd_data;
    return (pairs == 16) && (word == expect);
}

// ── main ────────────────────────────────────────────────────────────

int main(int argc, char **argv)
{
    Verilated::commandArgs(argc, argv);

    // optional: --vcd <profile-name> <phase-index> dumps one run
    const char *vcd_profile = nullptr;
    int vcd_phase = -1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--vcd") && i + 2 < argc) {
            vcd_profile = argv[i + 1];
            vcd_phase = atoi(argv[i + 2]);
        }
    }

    const bool endian_little = true;   // matches core_top (bridge_endian_little=1)

    // lead/hold/gap in periods of 13.468 ns.  "spec88" is the 88-cycle
    // worst-case word pacing from the module header (4+31+4+49=88).
    const Profile profiles[] = {
        // name            lead  hold  gap   words
        { "spec88",         4.0,  4.0, 49.0,  24 },
        { "comfort",        4.0,  4.0,  8.0,  48 },
        { "hold3",          2.0,  3.0,  6.0,  48 },
        { "hold2.5",        2.0,  2.5,  6.0,  48 },
        { "hold2",          2.0,  2.0,  6.0,  48 },
        { "hold0.5",        2.0,  0.5,  6.0,  48 },
        { "gap1",           2.0,  4.0,  1.0,  48 },
        { "gap0.6",         2.0,  4.0,  0.6,  48 },
        { "minmin",         2.0,  0.9,  0.4,  48 },
        { "sparse-hold0.5", 2.0,  0.5,200.0,  16 },
    };

    int bad_phases = 0;
    printf("io_bridge_peripheral RX phase sweep — %d phases / %.3f ns period\n",
           PHASES, P / 1000.0);
    printf("phase map legend: '.'=pass  D=word drop(s)  R=rotation/corruption\n\n");

    for (const auto &pr : profiles) {
        std::string map(PHASES, '?');
        int fails = 0;
        std::string detail;

        for (int ph = 0; ph < PHASES; ph++) {
            uint64_t phase = (uint64_t)((__int128)P * ph / PHASES);
            std::vector<WordRec> expected;
            std::vector<uint8_t> stream;
            const char *vcd = nullptr;
            char path[128];
            if (vcd_profile && !strcmp(vcd_profile, pr.name) && ph == vcd_phase) {
                snprintf(path, sizeof path, "bridge_rx_%s_ph%02d.vcd", pr.name, ph);
                vcd = path;
            }
            RunOut out = run_write_phase(pr, phase, endian_little,
                                         expected, stream, vcd);
            char c = classify(expected, out.got);
            map[ph] = c;
            if (getenv("BRX_VERBOSE") && c != '.')
                printf("    %s phase %2d: got %zu/%zu (%c)\n",
                       pr.name, ph, out.got.size(), expected.size(), c);
            if (c != '.') {
                fails++;
                bad_phases++;
                if (detail.empty()) {
                    char buf[256];
                    snprintf(buf, sizeof buf,
                             "    first fail @phase %d (%.0f ps): sent %zu words, got %zu",
                             ph, (double)phase, expected.size(), out.got.size());
                    detail = buf;
                    // first divergence
                    size_t i = 0;
                    while (i < out.got.size() && i < expected.size() &&
                           expected[i] == out.got[i]) i++;
                    if (i < out.got.size()) {
                        snprintf(buf, sizeof buf,
                                 "\n    first divergent rx: addr=%08x data=%08x"
                                 " (expected[%zu]: addr=%08x data=%08x)",
                                 out.got[i].addr, out.got[i].data, i,
                                 i < expected.size() ? expected[i].addr : 0,
                                 i < expected.size() ? expected[i].data : 0);
                        detail += buf;
                        int rot = find_rot_offset(stream, out.got[i].data,
                                                  endian_little);
                        if (rot >= 0 && rot != 4) {
                            snprintf(buf, sizeof buf,
                                     "\n    -> matches tx byte stream at frame offset"
                                     " %d (aligned=4): BYTE ROTATION", rot);
                            detail += buf;
                        }
                    }
                }
            }
        }

        printf("%-16s lead=%-4.1f hold=%-4.1f gap=%-5.1f words=%-3d [%s] %s\n",
               pr.name, pr.lead_p, pr.hold_p, pr.gap_p, pr.words,
               map.c_str(), fails ? "FAIL" : "ok");
        if (!detail.empty()) printf("%s\n", detail.c_str());
    }

    // read path sweep.
    //
    // NOTE: the original (pre-hardening) io_bridge_peripheral drove its
    // pads from procedural `inout reg` statements; Verilator lowers each
    // driving statement to a sticky per-statement strong-out flag and
    // ORs them, so the self-clocked TX waveform is unobservable (pad
    // sticks high after ST_SEND_N).  BRX_SKIP_READ=1 skips the read
    // sweep for that baseline; the hardened module uses the supported
    // single continuous tristate assign and is fully checked here.
    if (getenv("BRX_SKIP_READ")) {
        printf("\nread path sweep SKIPPED (BRX_SKIP_READ set: original inout-reg\n"
               "TX pads are not Verilator-observable; write-path results above\n"
               "are unaffected — the peripheral tristates its pads during writes)\n");
        printf("\n%s — %d failing phase points total\n",
               bad_phases ? "RESULT: FAIL" : "RESULT: ALL PHASES PASS", bad_phases);
        return bad_phases ? 1 : 0;
    }
    printf("\nread path sweep (TX is self-clocked by the peripheral):\n");
    int read_fails = 0;
    std::string rmap(PHASES, '?');
    int64_t lat_min = INT64_MAX, lat_max = INT64_MIN;
    for (int ph = 0; ph < PHASES; ph++) {
        uint64_t phase = (uint64_t)((__int128)P * ph / PHASES);
        uint32_t w = 0;
        int64_t lat = -1;
        bool ok = run_read_phase(phase, 0xA5C3F00Du, endian_little, w, lat);
        rmap[ph] = ok ? '.' : 'X';
        if (!ok) {
            read_fails++;
            bad_phases++;
            printf("  read FAIL @phase %d: got %08x\n", ph, w);
        }
        if (lat >= 0) { lat_min = std::min(lat_min, lat); lat_max = std::max(lat_max, lat); }
    }
    printf("read             [%s] %s\n", rmap.c_str(), read_fails ? "FAIL" : "ok");
    if (lat_min != INT64_MAX)
        printf("read TX-start latency (last addr edge -> drive-high): %.1f..%.1f clk\n",
               lat_min / (double)P, lat_max / (double)P);

    printf("\n%s — %d failing phase points total\n",
           bad_phases ? "RESULT: FAIL" : "RESULT: ALL PHASES PASS", bad_phases);
    return bad_phases ? 1 : 0;
}
