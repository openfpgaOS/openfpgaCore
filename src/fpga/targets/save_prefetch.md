# Save prefetch buffer

Fix for the APF save-read latency bug: `bridge_rd` at a save slot
address has a ~4 clk_74a cycle capture window, but CRAM1 async read
takes ~25–30 cycles.  Every save currently persists garbage for every
word.

This design places a small streaming BRAM between the bridge and
`psram1_b`, and uses CRAM1 sync burst reads to refill it.  APF always
reads from BRAM (single-cycle), BRAM is pre-filled before APF starts
strobing — `dataslot_requestread` is the pre-trigger.

## Architecture

```
     APF bridge (clk_74a)                         CRAM1 chip
          │                                            │
     bridge_rd ──┐                                     │
     bridge_addr ─┐                              ┌─────┘
                  ▼                              │
     ┌──────────────────────┐        ┌───────────┴──────┐
     │  save_prefetch       │ burst  │  psram1_b        │
     │  ┌────────────────┐  │◄──────►│  (clk_74a)       │
     │  │ 32-word BRAM   │  │  req   │   + burst iface  │
     │  │  + head/tail   │  │  resp  └──────────────────┘
     │  │  + base_addr   │  │
     │  └────────────────┘  │
     │  dataslot_requestread │
     │  ┌──► trigger prefill │
     └──────────────────────┘
             │
             ▼
        bridge_rd_data
```

- `save_prefetch` lives on clk_74a (same as psram1_b and the bridge).
- Owns a 32-word BRAM + pointers + FSM.
- Drives a **new burst-read interface** exposed by `cram1_controller`
  (alongside existing word_rd/word_wr).
- Replaces the current `bridge_cram1_rd_detect` single-word FSM.
- Writes (`bridge_cram1_wr_detect` path) are unchanged.

## cram1_controller burst interface (new)

Parallel to the existing word_rd/word_wr interface:

```verilog
// Burst read interface (32-bit word-aligned)
input  wire         burst_rd,       // 1-cycle pulse
input  wire  [21:0] burst_addr,     // word address (start)
input  wire  [4:0]  burst_len,      // word count minus 1 (max 32 words)
output reg   [31:0] burst_q,
output reg          burst_q_valid,  // 1-cycle pulse per 32-bit word
output reg          burst_busy
```

New states (added to existing FSM):
- `ST_CFG_START / ST_CFG_BSY / ST_CFG_WAI` — write BCR to configure
  CRAM1 for sync burst mode.  Runs once at reset, before accepting any
  word_rd/word_wr/burst_rd.
- `ST_BURST_START / ST_BURST_BSY / ST_BURST_DATA / ST_BURST_DONE` —
  issues `sync_burst_en` with `sync_burst_len = {burst_len, 1'b1}`
  (halfwords = 2×words - 1), captures halfword pairs from the phy
  into 32-bit words, pulses `burst_q_valid`.

BCR value: same as CRAM0 (0x9D1F — sync burst mode, code 3 latency, 32-word max burst).

Writes still work: BCR only affects reads.  Writes remain async.

## save_prefetch module

### Ports

```verilog
module save_prefetch (
    input wire clk,       // clk_74a
    input wire reset_n,

    // Bridge side
    input  wire         bridge_rd,
    input  wire [31:0]  bridge_addr,
    output reg  [31:0]  bridge_rd_data,

    // Dataslot pre-trigger (from APF)
    input  wire         dataslot_requestread,
    input  wire [15:0]  dataslot_requestread_id,

    // Per-slot base address table — CPU writes via MMIO at boot
    input  wire [21:0]  slot_base_addr [0:9],

    // Burst read interface to cram1_controller (psram1_b)
    output reg          burst_rd,
    output reg  [21:0]  burst_addr,
    output reg  [4:0]   burst_len,
    input  wire [31:0]  burst_q,
    input  wire         burst_q_valid,
    input  wire         burst_busy
);
```

### BRAM

32-word ring, 32-bit wide = 128 bytes = 1 M10K block.

```verilog
altsyncram (
    operation_mode = "BIDIR_DUAL_PORT",
    width_a = 32, widthad_a = 5,  // 32 entries
    width_b = 32, widthad_b = 5,
    outdata_reg_b = "UNREGISTERED",
    ...
)
```

Port A: prefetch writes (addr = tail[4:0], data = burst_q).
Port B: bridge reads (addr = bridge_addr_offset[4:0], q = bridge_rd_data source).

### Pointers (5-bit + 1 wrap-detect bit each)

- `write_ptr[5:0]` — next BRAM slot to be filled by burst.
- `read_ptr[5:0]`  — next BRAM slot APF will consume.
- `base_addr[21:0]` — word address of `bram[read_ptr[4:0]]`.
- `depth = write_ptr - read_ptr`  // how many words ready.
- `space = 32 - depth`            // how many words can be fetched.

### FSM

```
ST_IDLE:
  if dataslot_requestread && slot_id in save-slot range:
    base_addr <= slot_base_addr[slot_id]
    read_ptr <= 0; write_ptr <= 0
    → ST_PREFETCH

ST_PREFETCH:
  if space >= 16:
    burst_addr <= base_addr + write_ptr
    burst_len <= min(16 - 1, remaining)
    burst_rd <= 1 (1 cycle pulse)
    → ST_FILL

ST_FILL:
  while burst_q_valid:
    bram[write_ptr[4:0]] <= burst_q
    write_ptr <= write_ptr + 1
  when !burst_busy:
    → ST_SERVE

ST_SERVE:
  if bridge_rd && addr in expected_range:
    // bridge_rd_data already driven combinationally from bram[read_ptr]
    read_ptr <= read_ptr + 1
  if space >= 16 && more_to_prefetch:
    → ST_PREFETCH  (refill bottom half while APF drains top half)
  if bridge_rd to out-of-range addr:
    → ST_IDLE  (reset — APF moved to a different slot or restarted)
```

Key invariant: by the time `bridge_rd` fires, the word APF wants is
already in BRAM at `bram[read_ptr]`.  `dataslot_requestread` gives us
~100+ clk_74a cycles of head-start (APF's SD setup time) — easily
enough to fill 16 words via sync burst (~50 cycles total).

### bridge_rd_data mux

For addresses in 0x30000000–0x3000XXXX (save slot range):
```verilog
always_comb begin
    if (bridge_rd && addr_matches_bram) begin
        // Combinational read from BRAM port B, indexed by read_ptr
        bridge_rd_data = bram_q_b;
    end else begin
        bridge_rd_data = 32'h00000000;
    end
end
```

With `outdata_reg_b = "UNREGISTERED"` and port B address presented one
cycle earlier (when we noted that APF's bridge_rd would arrive), q_b is
valid combinationally when bridge_rd fires.  Actually: since we know
the next read address after each consume, we can pre-present the BRAM
address and have q_b already settled — fits the 4-cycle window.

## MMIO — slot base address table

New register range in `axi_periph_slave.v`:

```
0x110: SAVE_SLOT_BASE[0]  // word address in CRAM1 (22 bits, right-justified)
0x114: SAVE_SLOT_BASE[1]
...
0x134: SAVE_SLOT_BASE[9]  // 10 save slots
```

CPU writes these at boot from the known save slot layout (memory
`project_memory_layout.md` — saves at CRAM1 0x000000–0x27FFFF, 256 KB
slots).

Expose as an output array from `axi_periph_slave` to `save_prefetch`
via the existing periph register fanout.

## core_top.v wiring changes

- Remove lines 1196–1214 (old bridge_cram1_rd FSM).
- Instantiate `save_prefetch` on clk_74a.
- Wire `save_prefetch.bridge_rd` = bridge_rd.
- Wire `save_prefetch.bridge_addr` = bridge_addr.
- `bridge_rd_data` mux gains a save_prefetch input:
  ```
  bridge_rd_data <= (addr[31:24] == 8'h30)
                    ? save_prefetch_rd_data
                    : existing_mux;
  ```
- Wire `save_prefetch.dataslot_requestread` = apf_top.dataslot_requestread.
- Wire `save_prefetch.burst_*` to new ports on `psram1_b` (cram1_controller
  with burst interface).
- Keep `bridge_cram1_wr_detect` path (writes) intact.

## Firmware — populate slot base table

At boot, after the existing save system init:

```c
// src/firmware/os/hal/hal.c  (or wherever save init lives)
for (int i = 0; i < 10; i++) {
    // Slot N at CRAM1 word offset N * 0x10000 (256KB / 4 bytes = 64K words)
    SAVE_SLOT_BASE(i) = i * 0x10000;
}
```

One-time write, no runtime overhead.

## Implementation plan

1. **Add BCR config + burst interface to `cram1_controller.v`** (~150 LOC).
   Reset sequence writes BCR 0x9D1F via CRE, then enters ST_IDLE.
   New burst_* ports + states streaming halfword pairs into 32-bit words.
   Test in Verilator against a cram1 chip model (tb_cram1_burst.v).

2. **Write `save_prefetch.v`** (~200 LOC).
   Standalone module, testable in isolation.
   Benchtest: simulate dataslot_requestread + bridge_rd pattern,
   verify BRAM contents match CRAM1 at each bridge_rd.

3. **Add SAVE_SLOT_BASE[0..9] registers to `axi_periph_slave.v`** (~30 LOC).
   Mirror the pattern of other register arrays.

4. **Rewire `core_top.v`** (~40 LOC).
   Instantiate save_prefetch, delete old bridge_cram1_rd FSM, add
   bridge_rd_data mux.

5. **Firmware: populate SAVE_SLOT_BASE at boot** (~10 LOC).

6. **Hardware validation:**
   - Test: save to slot 0, power cycle, verify save persists (binary
     comparison of SD file against known-good data).
   - Test: save to each of slots 0..9, verify distinct content.
   - Test: alternating save/load cycles don't corrupt.

## Risks / open questions

- **Sync burst row-boundary bug.** CRAM0 had a word-32 IOB skew issue
  that made us nervous about long bursts.  Cap burst at 16 words to stay
  within one row.  If 16-word works reliably, we're fine — saves don't
  need full 32-word bursts.
- **APF's bridge_rd timing.** Assumed 1 word/cycle continuous.  If APF
  paces slower (likely, bottlenecked by SD write), our ~50-cycle fill
  time is comfortably ahead of each 16-word consumption.  If APF bursts
  faster than we can refill, we need larger BRAM or multiple outstanding
  bursts.  Measure first, adjust if needed.
- **Non-sequential reads.** APF reads save slots sequentially, but if
  there are edge cases (rewind, retry), the prefetcher falls back to
  ST_IDLE → ST_PREFETCH on any address mismatch.  First word of the
  new range will be stale; this is acceptable for rare edge cases.
- **Multiple slots in flight.** Pocket only saves one slot at a time
  via the normal UI.  Not a concern.
- **BCR write for CRAM1 is new.** CRAM0 has precedent (commit c80b23c)
  so the pattern is proven; just needs adapting to psram1_b (clk_74a).

## Est. effort

- cram1_controller burst + BCR: ½ day (includes bench)
- save_prefetch module + bench: 1 day
- core_top rewiring: ¼ day
- MMIO + firmware: ¼ day
- Hardware validation: ½ day

Total: ~2.5 focused days.
