# GPU refactor proposal - reviewed plan

**Status:** reviewed / updated after second-pass feedback.  
**Component:** `src/fpga/common/gpu_core.v`, `src/fpga/common/gpu_tex_cache.v`.  
**Goal:** reduce ALM/LAB pressure and improve timing flexibility without changing the public command stream, production MMIO map, AXI protocol, or pixel output. The only exception is B2's optional debug-only instrumentation, which must be removed before merge and must not become a public API/MMIO surface.

## Current fitter context

Latest build artifact checked: `src/fpga/targets/pocket/output_files/ap_core.fit.summary`, completed May 15 2026 09:00, seed 15.

| Resource | Used | Available | Utilization |
|---|---:|---:|---:|
| ALMs | 18,302 | 18,480 | 99% |
| LABs | 1,847 | 1,848 | 100% |
| Registers | 22,060 | -- | N/A |
| Pins | 224 | 224 | 100% |
| Block memory bits | 2,094,028 | 3,153,920 | 66% |
| M10K blocks | 274 | 308 | 89% |
| DSP blocks | 33 | 66 | 50% |
| PLLs | 2 | 4 | 50% |

The main constraint is not just ALMs; it is LAB packing. Treat every estimated saving as a hypothesis until a before/after fit confirms both ALMs and LABs improve.

Several items rely on how Quartus shares logic. If `fit.rpt` shows the current build already shares a cone or optimizes an expression, drop that item rather than forcing a rewrite. The savings estimates assume worst-case fabric synthesis.

## Verification policy

Use these targets for every change:

1. `make -C src/fpga/test gpu`
2. `make -C src/fpga/test gpu-acceptance`
3. `make -C src/fpga/test gpu-chain` only for bus/fabric interaction coverage, not as a byte-exact renderer oracle.

For triangle changes, explicitly check the triangle and perspective tests inside `tb_gpu_main.cpp`. For framebuffer write path changes, explicitly check cross-word span tests, clear-rect tests, and translucency tests.

For any Tier A change, compare:

- `ap_core.fit.summary`
- relevant hierarchy rows in `ap_core.fit.rpt`
- critical paths in `ap_core.sta.rpt`

Acceptance requires no pixel regression and no meaningful timing regression. Because LAB pressure is extreme, a change that saves ALMs but worsens LAB packing is not a win.

## Sequencing

Recommended order:

1. A4, C3, C2
2. C4, C5, C7
3. A1 alone
4. A3 with shared edge-init / next-row wires
5. A2 only after a quick synth/fitter comparison
6. B2 instrumentation as debug-only local/fitter-run instrumentation, removed before merge
7. B2 burst-2 removal only if measurement shows it is safe for gpudemo, Duke, and Quake
8. B1 only if width narrowing and a CE-compatible source-register plan both land; otherwise keep the DSP shadow regs

Do not batch A1/A2/A3 together. They touch hot triangle/writeback logic and should remain independently bisectable.
The C4/C5/C7 cleanups are intentionally grouped in step 2 because they are small, mechanical changes.

---

## Tier A - Primary candidates

### A1 - Collapse triangle row-walk inside/outside duplication

**Where:** `gpu_core.v`, `S_TRI_PIX`, inside/outside row-walk branches.

The inside and outside branches perform the same row-walk arithmetic:

- `tri_cur_x <= tri_cur_x + 1`
- `row_done_r <= ((tri_cur_x + 1) > tri_xmax)`
- `tri_e[i] <= tri_e[i] + (tri_A[i] <<< 4)`
- `tri_s/t/w/r <= tri_s/t/w/r + (grad_*_dx <<< 4)`

They differ only in whether the current pixel contributes to the current span.

**Refactor:** in non-`row_done_r` scan cycles, compute one `inside_tri` wire and perform the row-walk once. Gate only:

- `tri_span_x_start`
- `tri_span_s/t/w/r_start`
- `tri_span_count`

Do not advance row-walk state on the `row_done_r` path. That path emits or skips the completed row.

**Expected benefit:** medium, but fit-dependent. The current if/else branches are mutually exclusive, so Quartus may already share some of the arithmetic and only duplicate enable/mux logic. Conservative estimate is about 20-40 ALMs; optimistic estimate is about 60 ALMs if the current fit is not sharing well.

**Risk:** low-medium. This is mechanically simple but in a sensitive triangle state machine.

**Verification:** all `test_triangle_*`, perspective triangle tests, Gouraud/flat-light tests, and gpudemo triangle replay tests.

### A2 - Factor framebuffer byte-lane insertion

**Where:** four repeated lane-write cases in FB accumulator paths:

- normal same-word write
- normal cross-word re-arm
- translucent same-word write
- translucent cross-word re-arm

Each block does the same "write one byte into `fb_acc_data` and set one bit in `fb_acc_mask`" logic.

**Refactor:** factor into a small helper pattern, but be careful. A Verilog task may inline into four copies, and a dynamic shifter can become larger than the current case statements. Prefer a small function/wire form only if synthesis proves it helps.

Potential implementation style:

```verilog
wire [31:0] lane_byte_mask = 32'h000000ff << {lane, 3'b0};
wire [31:0] color_shifted  = {24'b0, color} << {lane, 3'b0};
wire [3:0]  lane_bit_mask  = 4'b0001 << lane;
```

Then apply:

```verilog
fb_acc_data <= (clear_word ? 32'b0 : fb_acc_data & ~lane_byte_mask)
             | color_shifted;
fb_acc_mask <= (clear_word ? 4'b0 : fb_acc_mask) | lane_bit_mask;
```

Caller sets `clear_word = 1` on the cross-word re-arm paths and `clear_word = 0` on the same-word fast paths.

**Expected benefit:** uncertain. This may improve readability; ALM/LAB savings must be proven by fit.

**Risk:** low if tests cover all four paths.

**Verification:** scalar span cross-word tests, reverse-stride tests, translucency overdraw, clear/drain tests.

### A3 - Remove duplicate triangle edge-init adders safely

**Where:** `S_TRI_ROW` initializes both `tri_e[]` and `tri_row_e[]` with:

```verilog
tri_e_init_Apx[i] + tri_e_init_Bpy[i] + tri_C[i]
```

`S_TRI_ROW_NEXT` similarly computes next-row values for both `tri_row_e[]` and `tri_e[]`.

**Corrected refactor:** do not use `tri_e <= tri_row_e` in the same clock edge as updating `tri_row_e`; nonblocking semantics would read the old value. Do not add an extra pipeline cycle unless needed.

Use shared combinational wires:

```verilog
wire signed [31:0] tri_init_e0 =
    tri_e_init_Apx[0] + tri_e_init_Bpy[0] + tri_C[0];
...
wire signed [31:0] tri_next_row_e0 =
    tri_row_e[0] + (tri_B[0] <<< 4);
```

Replicate the shared init and next-row wires for all three edges, `i = 0..2`.

Then assign both destinations from the same wire:

```verilog
tri_e[0]     <= tri_init_e0;
tri_row_e[0] <= tri_init_e0;
```

and on row advance:

```verilog
tri_e[0]     <= tri_next_row_e0;
tri_row_e[0] <= tri_next_row_e0;
```

**Expected benefit:** medium if Quartus currently duplicates the adders. It may already share some fabric, so confirm in fit.

**Risk:** low-medium. Edge init is correctness-critical.

**Verification:** all triangle tests, especially bbox-origin, CW winding, thin sliver, sharp apex, and fuzz tests.

### A4 - Single drain-complete wire for FENCE/FLIP

**Where:** `S_EXECUTE`, FENCE and FLIP branches.

Both branches test:

```verilog
fbwq_empty && (m_wr_inflight == 4'b0) && !m_wr_chan_busy
```

FLIP additionally checks `!slave_swap_pending`.

**Refactor:**

```verilog
wire drain_complete =
    fbwq_empty && (m_wr_inflight == 4'b0) && !m_wr_chan_busy;
```

Use `drain_complete` in both branches.

**Expected benefit:** small but safe. Also improves readability.

**Risk:** zero.

**Verification:** fence drain tests, flip pulse tests, clear-drain tests.

### A5 - Shared FB accumulator match predicate: defer

**Original idea:** share the 32-bit equality cone used by normal writes and translucent blend writes.

**Reviewer update:** do not include this in the main cleanup batch. The current blend path deliberately precomputes `blend_p3_match_r` in `FBSS_BLEND_LUT_WAIT` because this compare was on a bad path. A shared mux-before-compare can easily worsen timing even if it saves a few ALMs.

**Recommendation:** keep A5 as an experiment only. Try it after easier cleanups and accept only if both fit and STA improve.

---

## Tier B - Conditional / measurement-gated

### B1 - DSP shadow input regs: keep unless CE packing is solved

**Where:** triangle DSP input shadow registers such as `tri_A_dsp_in[]`, `tri_B_dsp_in[]`, `tri_xmin_sub_dsp_in`, `tri_ymin_sub_dsp_in`.

These registers are load-bearing for two separate reasons:

- They narrow 32-bit state into DSP-packable inputs.
- They decouple clock-enable/control sets. Source regs such as `tri_A[]`, `tri_B[]`, clamp-derived inputs, and setup state are gated by triangle state or clamp logic. The shadow regs are always-clocked so Quartus can pack input FFs into DSP blocks. The current RTL comment notes that state-gated CEs previously prevented input-FF packing and left a `-0.858 ns` routing slack path.

Width narrowing alone is not enough. Removing the shadow regs after a width-narrowing refactor while keeping state-gated writes on the source regs can regress DSP input FF packing and timing.

**Safe future options:**

- Keep the shadow regs.
- After width narrowing, make the narrowed source regs CE-compatible / always-clocked and verify DSP input FF packing.
- Selectively remove only a shadow reg whose source already has a matching always-clocked control set, verified by fitter reports.

**Acceptance condition:** fitter report must show DSP input FFs still packed into DSP blocks and STA must not regress. If FFs spill into fabric, revert.

**Expected benefit:** mostly FFs, not necessarily ALMs/LABs.

### B2 - Measure burst-2 framebuffer write usefulness before removal

**Where:** `fbwq_pop_burst2`, `m_wr_w2_*`, and the central AXI write drain.

The path creates two-beat AXI write bursts for adjacent full-word triangle writes. It may be rare for BUILD-style spans because `fb_acc` merges bytes before the write queue, but it may matter for Quake / large-triangle workloads.

**Instrumentation rule:** do not reintroduce permanent debug MMIO/API surfaces. Instrument in one of these ways:

- simulation-only counters in the testbench
- debug-only local build with private MMIO removed before merge
- `ifdef GPU_DEBUG_BURST2` excluded from production

Measure:

- burst-2 pops
- total write pops
- full-word triangle queue entries
- before/after FPS/render time for gpudemo, Duke, Quake

**B2-specific verification:** run `make -C src/fpga/test gpu-acceptance-burst2` in addition to the standard GPU tests before and after any burst-2 removal PR.

**Removal threshold:** 5% is a reasonable starting point, but not absolute. If burst-2 materially helps Quake or large triangle paths, keep it even if Duke does not use it heavily.

**Risk of removal:** functionally low, performance uncertain.

---

## Tier C - Small cleanups

### C1 - Inline `finish_fragment_stream_after_flush`

**Correction:** this task currently has two call sites, not one. It is still tiny and can be inlined if it improves readability, but do not expect measurable savings.

### C2 - Drop dead reset values for command-latched data

Candidates:

- `pending_fence_token`
- `pending_swap_idx`

Only remove resets for fields that are guaranteed to be consumed only after their command payload has latched. Keep reset for externally visible outputs and valid bits. For example, keep `gpu_swap_idx` reset unless a dedicated audit proves every downstream consumer samples it only when `gpu_swap_req` pulses.

### C3 - Collapse `ring_a_we` / `ring_a_wdata` aliases

`ring_a_we` and `ring_a_wdata` are aliases of `dma_ring_wr_raw` / `dma_ring_wdata_raw` from the retired CPU MMIO command path.

This is primarily readability cleanup. It may not save ALMs.

### C4 - Rewrite palookup address composition as OR of disjoint fields

Current form:

```verilog
cmap_req_addr_reg <= PALOOKUP_BASE
                   + {8'b0, sp_colormap_id, 14'b0}
                   + {12'b0, p1_light, tex_resp_data[7:0]};
```

`PALOOKUP_BASE` is slot-aligned and the slot/light/texel fields are disjoint. This can be expressed without a carry chain:

```verilog
cmap_req_addr_reg <= PALOOKUP_BASE
                   | {8'b0, sp_colormap_id, 14'b0}
                   | {12'b0, p1_light, tex_resp_data[7:0]};
```

**Expected benefit:** small, but this is cheap and easy to validate. Drop it if Quartus already removes the carry chain.

### C5 - Share cmap request valid prefix

Current cmap request/issue logic repeats:

```verilog
p2_valid && p2_flags[SPAN_COLORMAP] &&
(fbss == FBSS_IDLE) &&
!cmap_pipe_wait &&
!fp_pipe_shift_blocked
```

Factor that into one wire and derive `cmap_req_valid_b` and `cmap_issue_wait` from it.

**Expected benefit:** small. Low risk. Drop it if the fitter already shares the common predicate.

### C6 - DMA starvation threshold narrowing: do not include as identity cleanup

Changing `DMA_STARVE_THRESHOLD` from 1024 to 512 changes behavior. It may be fine, but it is not a functional-identity refactor. Defer unless there is a specific measured reason.

### C7 - Simplify `spg_load_source_only`

The manual case over lanes can likely become indexed array reads:

```verilog
sp_tex_addr <= spg_tex_addr[lane];
sp_t        <= spg_t[lane];
sp_tstep    <= spg_tstep[lane];
sp_light_q  <= {2'b0, spg_light[lane], 16'b0};
```

Quartus may already produce equivalent muxes, so treat as readability plus possible minor packing improvement.

### C8 - Translucency lane mux: track only with translucency refactor

Fold into the existing translucency refactor work if that work happens. Do not track this as a standalone implementation task in this proposal; expected benefit is too small and it would otherwise be double-counted.

---

## Do not touch in this cleanup

- `gpu_tex_cache.v` prime/read structure. It is tuned for TDP M10K inference.
- PSS Newton-Raphson stages. They are needed for Quake perspective quality.
- The existing `FBSS_BLEND_LUT_WAIT` compare hoist unless an STA experiment proves a replacement is better.
- DSP pipeline staging around texture address, triangle gradients, and perspective setup unless part of the explicit width-narrowing work.
- Permanent debug/API surfaces for one-off measurement.

---

## Revised savings estimate

These are deliberately conservative:

| Group | Conservative | Optimistic |
|---|---:|---:|
| A1 | 20-40 ALMs | 60 ALMs |
| A2 | 0-30 ALMs | 50 ALMs |
| A3 | 20-50 ALMs | 70 ALMs |
| A4 | 5-15 ALMs | 20 ALMs |
| C cleanups | 10-30 ALMs | 50 ALMs |
| B2 removal, if measured safe | ~30 ALMs + ~40 FFs | ~40 ALMs + ~40 FFs |

The practical target is not a headline ALM number; it is getting below roughly 98% ALMs and freeing several LABs so the fitter has room to breathe.

---

## Open decisions

1. Should A2 be attempted before or after A1/A3? It touches the framebuffer path rather than triangle setup, so after A1/A3 may be easier to bisect.
2. What workloads should define B2 removal? Recommended minimum: gpudemo mode 5/6, Duke E1L1, Quake textured view.
3. Should A5 be removed from the proposal entirely? Current recommendation: keep as "defer/experiment only", not an implementation item.
