# Audio DMA refactor

Replace the mixer's per-tap CRAM1 fetch loop with a DMA engine that
pre-streams each voice's sample window into on-chip BRAM.  Mixer
becomes a deterministic, counter-driven pipeline against the ring
BRAM.  FSM complexity moves into the DMA engine, which trades that
cost for uniform-latency mix loop + CRAM1 burst reads.

## Why

Today's mixer blocks on up to 4 CRAM1 reads per voice × 48 voices ×
22 kHz = 4M reads/sec worst case, each 15–30 cycles of async PSRAM
access.  That's ~80% of clk_cpu in the worst case, and the tap-fetch
FSM has to handle variable-latency waits, CPU bus collisions, and
cache hit/miss branching.

Prefetching into BRAM + sync burst reads:
- Average CRAM1 read cost drops from ~25 cycles/word to ~1.5 cycles/word
  (one setup for 32+ words).
- Mixer inner loop becomes fixed-latency — easier timing, easier debug.
- Lets us raise output rate to 44.1 kHz without saturating the bus.

## Architecture

```
 ┌──────────┐   voice params        ┌──────────┐
 │   CPU    │──────────────────────►│  vtbl    │◄─── port A: mixer
 └──────────┘                        └──────────┘
       │                                   │
       │   start / stop / addr / len       │   voice params
       ▼                                   ▼
 ┌──────────┐  refill requests   ┌──────────────┐  sample reads
 │   DMA    │◄──────────────────►│  ring BRAM   │◄─── mixer datapath
 │  engine  │  CRAM1 bursts      │ 48× slots    │
 └──────────┘                    └──────────────┘
       │
       ▼
   CRAM1 bus (shared with CPU, bridge)
```

Three independent agents, talking through BRAMs:
- **CPU** writes voice config into `vtbl`.
- **DMA engine** reads voice config from `vtbl`, streams samples from
  CRAM1 into the ring BRAM.
- **Mixer datapath** reads voice config from `vtbl` and samples from
  the ring BRAM, emits stereo samples to the audio FIFO.

## Ring BRAM layout

48 voices × 128 samples × 16-bit = 12 KiB ≈ 10 M10K blocks.

Per-voice slot:
- Single ring, power-of-two size (128 samples = 256 bytes)
- Two pointers tracked outside the ring:
  - `ring_head[v]` — next sample index DMA will write (in samples)
  - `ring_tail[v]` — next sample index mixer will read (== cur_pos_int mod ring size)
- Depth = `ring_head − ring_tail` (samples available)
- Underrun guard: mixer stalls the voice (or outputs silence for it)
  if depth < 4 (Hermite needs 4 taps).

BRAM geometry:
- True dual-port, 32-bit wide (2 samples per word for 16-bit format).
- 6144 words (48 × 128 samples / 2 samples-per-word).
- Address: `{voice[5:0], word_offset[6:0]}` = 13 bits.
- Port A — DMA writes (32-bit, 1 word per CRAM1 halfword pair).
- Port B — mixer reads (32-bit, mixer extracts the tap sample).

8-bit format uses 4 samples per word, so a 128-sample ring is 32 words.
The ring size stays 128 samples; halve the storage if desired, or keep
128 words (256 samples) for 8-bit at no extra BRAM cost.  **Decision:**
keep the ring 128 samples regardless, use format at read time.

## DMA engine

State per voice (48 slots, ~128 bits each):
- `active[v]`        — matches voice CTRL.active
- `base_addr[v]`     — CRAM1 word address (from vtbl ADDR)
- `length[v]`        — sample count (from vtbl LEN)
- `ring_head[v]`     — next sample to fetch (22-bit)
- `loop_start[v]`    — loop start (from vtbl)
- `loop_end[v]`      — loop end (from vtbl)
- `loop[v]`, `bidi[v]`, `fmt16[v]`, `dir[v]` — control bits
- `dirty[v]`         — set by CPU writes that invalidate the ring
                       (ADDR, LOOP_START, LOOP_END, CTRL)

Main loop (single FSM, round-robin scheduler):

```
for ever:
  for v in 0..47:
    if !active[v]: continue
    depth = ring_head[v] - ring_tail[v]    // tail sourced from mixer
    if dirty[v]:
      ring_head[v] = cur_pos_int[v]        // reset to current pos
      ring_tail[v] = cur_pos_int[v]
      dirty[v] = 0
      skip this cycle, let mixer observe reset
    if depth < REFILL_THRESHOLD (e.g. 64):
      issue_burst(v)                       // blocks on CRAM1 mux
```

`issue_burst(v)`:
- Compute CRAM1 word address for `ring_head[v]`.
- Decide burst length: min(CHUNK_SIZE=32 words, samples-to-loop-wrap,
  samples-to-end-of-voice).
- Acquire CRAM1 via existing mux (same arbitration as today's per-tap
  reads — just fewer, longer transactions).
- Issue `sync_burst_en` with `sync_burst_len = length*2 - 1` (halfwords).
- Stream halfwords into ring BRAM at `ring_head[v]`, pair them into
  32-bit words, one write per 2 halfwords.
- On completion, update `ring_head[v]`, release CRAM1, advance v.

### CRAM1 bandwidth math

- PHY sync burst latency: ~20 cycles setup + 1 cycle per halfword.
- Burst of 32 words = 64 halfwords = 20 + 64 = ~84 cycles.
- DMA bandwidth: 32 words / 84 cycles = 38 MB/s peak.
- Mixer demand at 44.1 kHz output, 48 voices, rate≈1: ~8.5 MB/s.
- Headroom: 4.5×. Plenty for CPU + bridge coexistence.

Shared-bus arbitration:
- DMA is lower priority than CPU (CPU serves interactive code; DMA
  has hundreds of microseconds of ring headroom).
- DMA preempts bridge only on underrun risk (rare).
- Existing `cram1_mux` already arbitrates per-request; DMA just
  becomes one more requester with longer transactions.

### Loop / bidi / end handling

Straightforward but the scheduler owns them:
- Forward loop: on refill crossing `loop_end`, split into two bursts
  (pre-wrap + post-wrap starting at `loop_start`).
- Bidi: on direction change, invalidate ring from current pos, mixer
  sees a brief underrun (3–4 taps of silence), ring refills in reverse
  order.  **Or** store samples at absolute positions and let mixer
  walk backwards — simpler, no DMA reversal needed.  **Decision:** ring
  stores absolute sample values; mixer handles direction.
- One-shot end: DMA stops refilling when `ring_head >= length`.  Mixer
  deactivates the voice (voice_end IRQ) after it drains.

## Mixer datapath

No FSM in the sense of tap-fetch states.  One counter `v` steps 0→47,
sub-counter steps through pipeline stages:

```
stage 0:  read vtbl[v].CTRL, latch active bit
stage 1:  read vtbl[v].{POS_INT, POS_FRAC}
stage 2:  read vtbl[v].{RATE, LOOP_*, VOL_*}  (multi-cycle pipelined)
stage 3:  compute 4 ring addresses (pos-1, pos, pos+1, pos+2) with
          boundary wrap against loop_start/loop_end/len
stage 4:  issue 4 parallel reads on ring BRAM port B
          (BRAM is 32-bit; pair-pack 2 samples per word —
          worst case 2 reads if taps straddle a word boundary)
stage 5:  Hermite coefficients (pipelined 2 cycles — reuse today's 0A/0B)
stage 6:  Hermite Horner (3 cycles — reuse today's 1/2/3)
stage 7:  clamp + volume curve + volume mul + accumulate
stage 8:  volume ramp (writeback if changed)
stage 9:  position advance + writeback
```

Fixed 12–14 cycle per-voice iteration.  48 voices × 14 = 672 cycles
per sample period.  At 100 MHz / 44.1 kHz → 2268 cycles available.
Margin 3×.

### Control

- Inactive voices: still stepped through, but with `wren` suppressed —
  keeps the pipeline uniform.  Tiny datapath waste for big code
  simplification.
- No tap cache needed — ring has 128 samples resident, cache hit rate
  is effectively 100% for normal rate=1 playback.
- No branches on CRAM1 wait — ring is always ready (DMA guarantees
  depth > 4 except during explicit underrun).

### BRAM ports recap

- `vtbl` (TRUE_DUAL_PORT, as of the current refactor)
  - Port A: mixer reads + writes (unchanged)
  - Port B: CPU writes (unchanged)
- `ring` (TRUE_DUAL_PORT, new)
  - Port A: DMA writes
  - Port B: mixer reads
- DMA needs its own vtbl snapshot (reads active/ADDR/LEN/LOOP*).  Options:
  - Give DMA a third view of vtbl via a small per-voice shadow that
    the DMA latches whenever CPU writes trigger `dirty[v]` (cheap,
    ~200 flops).
  - Or: dedicate a second BRAM as "DMA vtbl shadow" kept in sync.
  - **Decision:** per-voice shadow in flops.  DMA snoops CPU writes
    the same way the mixer snoops for cache_valid today.

## CPU interface changes

Minimal.  The MMIO register set stays the same.  New behavior:
- Writing ADDR/LOOP_START/LOOP_END/CTRL sets `dirty[v]` in BOTH the
  mixer's `cache_valid` (existing) AND the DMA's `dirty[v]` (new).
- `MIX_VOICE_POS` readback unchanged (per-voice latch from mixer).
- `MIX_VOICE_POS_WR` sets CPU-visible position; mixer picks it up,
  DMA sees `dirty[v]`, refetches.

## Implementation plan

Each bullet is a focused change, testable in isolation.

1. **Ring BRAM module** — standalone, true-dual-port, 6144×32-bit.
   Write a unit test that validates 2-sample-per-word packing and
   address mapping.  Verilator sim-only.

2. **DMA engine, passive mode** — implement the scheduler FSM but
   keep the existing tap-fetch path in the mixer.  Wire DMA to the
   ring BRAM on one side, CRAM1 bus on the other.  Verify it fills
   rings correctly under synthetic voice configs.  Use an existing
   Verilator test harness — spawn 1, 4, 8, 48 voices, check ring
   depth stays bounded and CRAM1 burst counts match expectations.

3. **Mixer read path cutover** — replace `cram1_rd/addr/busy/rdata`
   in the tap-fetch states with ring BRAM reads.  Keep the FSM
   temporarily to minimize scope.  Tap cache logic can stay (now
   always hits); delete after #4 proves stable.

4. **Mixer datapath rewrite** — replace the FSM with the
   counter-driven pipeline.  Delete `cram1_rd/addr/busy/rdata/rdata_valid`
   ports from the mixer module entirely.  Delete tap cache BRAM.

5. **CRAM1 mux simplification** — mixer no longer requests, only DMA
   does.  Two-way mux (DMA + CPU) instead of three-way.  Delete the
   `cram1_mix_*` wires.

6. **Hardware validation** — full testdemo suite on Pocket, audio
   stress test with 48 simultaneous voices at 44.1 kHz source rate.

7. **Raise output rate** (optional follow-on) — bump
   `MIXER_OUTPUT_RATE` to 44100, update HAL constants, verify DMA
   still keeps up.

## Risks / open questions

- **BRAM pressure.** +10 M10Ks is fine on the Pocket (~3% of total),
  but adds to the fitter's placement work.  Could push critical
  paths longer.  Watch Fmax after #4 lands.
- **CRAM1 burst length calibration.** The PHY's sync burst was off
  today because of a word-32 IOB skew bug (noted in the earlier
  `core_top.v` history).  The bug manifests at the row boundary of
  32-word bursts.  Need to either: (a) cap burst length to 16 words,
  (b) root-cause the skew and fix at the PHY level, (c) split any
  burst that crosses a row boundary into two.  **Decision TBD.**
- **DMA underrun on rapid voice retrigger.** If CPU retriggers a
  voice with new ADDR, the ring is invalidated and depth drops to
  zero.  Mixer outputs 3–4 samples of silence for that voice.
  Acceptable — matches the current tap-cache-miss penalty.
- **Voice_sel_rd readback.** Still served from the mixer's per-voice
  pos latch — unchanged.
- **DMA vtbl snapshot coherence.** If CPU writes LEN and DMA reads
  LEN on the same cycle, snoop semantics must match the vtbl port B
  write.  Same race as the existing `cache_valid` snoop, same fix.

## Est. effort

- Ring BRAM + test: ½ day
- DMA engine (passive): 1½ days
- Mixer read cutover: ½ day
- Mixer datapath rewrite: 1½ days
- Mux simplification + cleanup: ½ day
- HW validation + bug chase: 1 day (optimistic)

Total: ~5 focused days of RTL + test work.
