<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
-->

# The shared-ALU GPU: an architecture for a fragment-bound machine

## The thesis

The openfpgaOS GPU is **fragment-bound**: it produces ~1 pixel/cycle through a
6-stage pipeline, and on every real workload the RV32 CPU cannot feed it fast
enough. The GPU spends most of its life *waiting for commands*, not *waiting on
its own datapath*. host_speeds on Quake: `[gfx] wait` ≈ 0.1 ms while `alias`
(CPU) ≈ 23 ms — the GPU is idle 99.5% of a fight frame.

A machine that is never time-pressured should not spend silicon doing things
in parallel. Yet `gpu_core.v` grew the opposite way: each command
(param-span, perspective, triangle-derivation, clear, blend) accreted its
**own** datapath — its own multiply operands, its own address/strobe
generation, its own accumulator flush — all hardcoded into distinct FSM
states. The Pocket fit pays for that: a single shared register like `dsp_a`
becomes a **50:1 multiplexer** (≈495 LEs) purely because ~50 states each write
it. That fan-in, multiplied across `dsp_a/dsp_b/dsp2_a/dsp2_b` and the
duplicated address/accumulate cones, is the bulk of the reducible
~5,667 self-ALMs — and it is the reason the vert-tri feature overflows the
Pocket (5CEBA4F23C8) at 103%.

**The architecture:** collapse all *control-rate* datapath into a small set of
shared, microcoded functional units, trading the abundant idle cycles for
ALMs. The *pixel-rate* datapath stays exactly as it is.

## The one inviolable line

```
 ring ─► DECODE ─► per-record SETUP ─► [ p0a → p0 → p1 → p2 → p2b → p3 ] ─► FB
 ╰──────────── CONTROL-RATE: microcodeable, has slack ───────╯ ╰ PIXEL-RATE ╯
                                                                 SACRED — 1px/cyc
```

The fragment pipeline (`p0a..p3`, the tex-cache feed, the fbwq drain) **is**
the throughput. Fill rate is what keeps the GPU ahead of the CPU; serialize it
and you *become* the bottleneck. Every stage below leaves it byte-for-byte
untouched. The discipline rule is mechanical: **no edit may change a signal
inside p0a..p3 or the fbwq/tex datapath.** If a refactor needs to, it's wrong.

Everything else — command decode, plane setup, address/strobe synthesis,
accumulator flush — runs *between* pixel bursts and is fair game.

## The shared units

### 1. MUL — **CORRECTED** (the pilot disproved the first thesis)

**What I first proposed and why it was wrong.** The original plan was to
*share* one microcoded multiply path across PSS, the derivation, and spanprod —
an operand register file `mul_op[]` indexed by a microprogram, so `dsp_a`
becomes a small indexed read instead of a 50:1 mux. **The Stage-0 pilot
implemented it, measured it on real synthesis, and it REGRESSED +297 ALMs**
(215/215 bit-exact throughout, then reverted). Two findings killed it:

1. The 50:1 `dsp_a` mux is **not the derivation's** — it is dominated by PSS +
   spanprod + payload writers. The derivation is ~6 of 50 inputs. Microcoding
   it can't shrink a mux it barely feeds.
2. Indexing a register file **relocates** operand diversity into a 13:1 read
   mux + 512 new FFs — net-new logic on top of a bus it didn't shrink.

**The correction — the resource-scarcity inversion.** The fragment-bound thesis
holds for the *pixel pipe* (never serialize it). But for *scalar compute*, the
binding constraint is **ALMs** (the Pocket wall), while **DSPs (~36%) and M10Ks
have headroom**. Sharing a scalar resource through muxes *spends ALMs to save
DSPs* — exactly backwards. The right move is the opposite of sharing:

- **Give the derivation its own dedicated multiplier** (a 32×32 on +1–2 DSP
  blocks Pocket has to spare). This *removes* the derivation's ~6 inputs from
  the shared 50:1 bus, shrinking the mux that PSS/spanprod still use, and the
  dedicated multiplier's own fan-in is tiny. Spend the abundant resource (DSP)
  to relieve the scarce one (ALM).
- Do **not** route PSS/spanprod into a shared microsequencer — that is the same
  relocate-into-a-file mistake at larger scale, and would grow the mux further.

The earlier Stages 1–2 ("route PSS / spanprod through the shared MUL") are
**struck** — they were predicated on the disproven premise.

### 2. ADDR — one address/strobe/lane unit

`{byte_addr} → {word_addr[25:0], 4-bit strobe, lane-aligned 32-bit data}` is
re-elaborated in ≥4 FSM arms (FBSS opaque, FBSS translucent, blend-apply, the
z-word path). One shared combinational unit, driven from a small source mux,
replaces the duplicated 4:1/4:32 decoders. (This is the prior audit's "A1".)

### 3. FLUSH — one accumulator-push datapath

The `z_acc / z_flush / fbwq-push` idiom ("if valid & can-issue → push
{addr,data,strb}, clear") is written verbatim in 4 states, one already subtly
drifted. One shared push task/datapath, sequenced by the FSM. (Audit "A2" —
and it removes a latent divergence bug.)

## Execution — sequential, gated, measured

This is one file; the campaign is **strictly sequential** (no parallel edits).
Each stage lands on the prior stage's committed state and must clear all gates
before the next begins:

| Stage | Change | Status |
|---|---|---|
| 0 | ~~Derivation microcode (shared MUL)~~ | **FAILED +297, reverted** — disproved the share-MUL premise |
| 1 | Derivation **dedicated** multiplier (un-share from the 50:1 bus) | re-aimed Stage 0 |
| 2 | ADDR shared unit (combinational cone dedup — different mechanism, still valid) | byte-exact wire-hoist |
| 3 | FLUSH shared unit (idiom dedup, kills a latent drift bug) | byte-exact + z-order subsets |
| — | Relief levers (variants, spanprod slots) as final margin | product decision |

Note Stages 2–3 (ADDR/FLUSH) are **combinational-cone / idiom dedup**, a
*different* mechanism from the failed operand-mux sharing — they remove
duplicated logic outright rather than relocating it, so the pilot's negative
result does not apply to them.

**The oracle:** the 215-test byte-exact acceptance suite. Every stage produces
pixel-identical output or it is reverted. This is the only reason a refactor of
this magnitude on a 5,600-line file is survivable — there is a tolerance-free
truth check after every edit.

**Timing per stage:** serialization can add combinational depth (the operand
mux) or remove it (fewer parallel cones) — it must be *measured*, not assumed.
The capture-register discipline (a flop between a DSP output and its next
consumer) from the vert-tri pipelining fix applies throughout; WNS is a gate at
every stage that touches a DSP feed.

## Verdict on the derivation (2026-06-07, after 4 measured attempts)

The derivation does **not** compress enough to fit the Pocket. Measured:
staging-dedup −110, cone-hoist −61 (both kept), microcode +297 (reverted),
dedicated-multiplier +221 (reverted — it shrank the mux 50:1→31:1 exactly as
predicted, but the new registers/DSP-integration outweighed the recovery on a
>97%-dense device). The ~1,000 ALMs is the real cost of a 4-plane fixed-point
perspective solve, and the 5CEBA4F23C8 — already at 97% with everything else —
has no room for it at 100 MHz. **This is a device-size reality, not a missing
optimization.** The full hardware triangle win is a MiSTer feature; the Pocket
keeps the 0x49 CPU-solve fallback plus this session's emission-core CPU wins.

What survives of this architecture: the **ADDR/FLUSH dedup (Stages 2–3)** is a
*different* mechanism (outright cone/idiom removal, not operand-mux relocation),
so the pilot failures don't bear on it. It's genuine cleanup the audits flagged
— it helps both targets (and improves MiSTer's vert-tri timing margin) — but it
reclaims hundreds, not ~1,000 ALMs, so it does not by itself put the derivation
on the Pocket. Worth doing on its own merits; not a path to the Pocket fit.

## What "done" looks like

- vert-tri fits the Pocket ungated, ≤95% with closeable timing — the feature
  reaches the platform it was built for.
- `gpu_core` is *smaller and clearer*: a handful of shared functional units
  microcoded by the command FSMs, instead of N parallel hardcoded datapaths.
- MiSTer benefits too (leaner, even though it isn't budget-bound).
- The pixel pipe is untouched — same fill rate, same byte-exact output, proven
  by the same 215 tests that passed on day one.

The measure of the art is not the ALMs saved. It's that the final machine
*reads* like what it is: a fragment engine with one sharp datapath, fed by a
small microcoded brain that does its setup work in the time it would otherwise
spend idle.
