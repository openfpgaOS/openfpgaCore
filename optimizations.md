# openfpgaOS Core ALM and Fitter Flexibility Review

Date: 2026-05-07

This review is based on the current Pocket build reports under
`src/fpga/targets/pocket/output_files/` and a source review of the common RTL,
Pocket target RTL, Analogizer RTL, GPU, CPU interconnect, input, save/writeback,
audio, and video scanout paths.

The plan below assumes one feature-complete Pocket core. Existing GPU commands,
Analogizer modes, SNAC, input paths, audio features, save paths, video modes,
link/VRR behavior, debug registers, CPU ISA, and SDK-visible hardware features
remain present.

## Current Resource Pressure

The current fitted build is at the point where small RTL shape changes can make
or break seed quality.

From `ap_core.fit.rpt`:

| Resource | Current use | Notes |
| --- | ---: | --- |
| Logic utilization | 17,806 / 18,480 ALMs, 96% | Final placement uses 18,266 ALMs, 99%. |
| LABs | 1,847 / 1,848, 100% | Main fitter flexibility warning. |
| ALMs unavailable | 306 | 300 are due to LAB input limits. Wide muxes and high fan-in logic matter. |
| Dense-packing recovery | 766 ALMs | Packing headroom exists, but the fitter struggles to realize it. |
| Combinational ALUTs | 29,205 | Heavy logic duplication and route-through pressure. |
| Route-through ALUTs | 1,221 | Route pressure is high. |
| Registers | 22,658 | FF count is not the issue; spending FFs to reduce mux depth is reasonable. |
| M10K blocks | 269 / 308, 87% | M10K placement is meaningful even though ALMs are tighter. |
| DSP blocks | 32 / 66, 48% | DSP count is not limiting, but DSP placement affects local routing. |

Worst setup path remains in the 100 MHz CPU/RAM clock:

| Clock | Slow 85C slack | Slow 0C slack |
| --- | ---: | ---: |
| `ic|mp_ram|...general[0]...divclk` | -0.883 ns | -0.917 ns |
| `clk_74a` | +0.900 ns | +0.905 ns |
| `dram_clk_pin` | +1.267 ns | +1.409 ns |
| `cram0_clk_pin` | +2.296 ns | +2.788 ns |

The main conclusion is to reduce high-fan-in logic in the CPU/GPU/peripheral
region first, then reduce M10K placement pressure where it has no behavioral
cost. Do not globally force small memories into MLABs while final placement is
already at 99% ALMs.

## Top Area Consumers

Approximate fitted hierarchy from `ap_core.fit.rpt`:

| Block | ALMs | ALUTs | Registers | M10K | DSP |
| --- | ---: | ---: | ---: | ---: | ---: |
| `gpu_core:gpu` | 6,478.7 | 10,923 | 5,568 | 88 | 16 |
| `cpu_system:cpu` | 5,254.4 | 8,685 | 6,775 | 115 | 4 |
| `VexiiRiscv:cpu` | 4,288.8 | 7,231 | 5,142 | 115 | 4 |
| `axi_periph_slave:periph` | 1,770.9 | 2,636 | 2,951 | 34 | 0 |
| `audio_mixer:audio_mixer_inst` | 942.0 | 1,711 | 1,002 | 7 | 10 |
| `openFPGA_Pocket_Analogizer:analogizer` | 607.8 | 1,151 | 682 | 1 | 2 |
| `core_bridge_cmd:icb` | 293.9 | 547 | 389 | 4 | 0 |
| `cram0_bridge_prefetch:c0_bridge_prefetch` | 190.0 | 343 | 256 | 7 | 0 |
| `sync_fifo:bridge_cram0_write_fifo` | 60.9 | 120 | 110 | 6 | 0 |

Analogizer sub-blocks worth calling out:

| Analogizer sub-block | ALMs | M10K | DSP |
| --- | ---: | ---: | ---: |
| `yc_out:yc_out` | 287.6 | 1 | 2 |
| `vga_out:ybpr_video` | 135.0 | 0 | 0 |
| `scandoubler_2:sd` | 89.8 | 0 | 0 |
| `scanlines_analogizer` | 27.4 | 0 | 0 |

GPU memory worth calling out:

| GPU memory | M10K |
| --- | ---: |
| Command ring, `ring_bram` | 16 |
| Translucency LUT, `transluc_bram` | 32 |
| Texture cache data, two copies | 32 |
| Texture cache tags and valid bits | 6 |
| Reciprocal LUT | 2 |

## Optimization Principles

1. Preserve the public hardware contract.
2. Prefer pipeline registers over large combinational muxes.
3. Prefer balanced local decode over repeated global address/opcode compares.
4. Keep CDC attributes where they protect synchronizers; remove preservation only
   from ordinary mux/pipeline/debug nets.
5. Use targeted memory packing. Do not move broad RAM classes into ALMs.
6. Validate each area change with the runtime behavior that previously failed:
   Duke save/load, GPU spans/textures, Analogizer output, keyboard/mouse input,
   menu music, and launcher boot.

## Priority 1: Split and Register the Peripheral Read Mux

Files:

- `src/fpga/common/axi_periph_slave.v:1098`
- `src/fpga/common/axi_periph_slave.v:1224`
- `src/fpga/common/axi_periph_slave.v:1303`
- `src/fpga/common/axi_periph_slave.v:1345`
- `src/fpga/common/axi_periph_slave.v:1357`

Problem:

`axi_periph_slave` is about 1,771 ALMs. The read path contains a wide sysreg
case followed by a second peripheral-region mux:

```verilog
wire [31:0] periph_rd_mux = reg_sysreg ? sysreg_rdata :
                             reg_audio  ? audio_rdata :
                             reg_cram0  ? cram0_mode_rdata :
                             reg_link   ? link_reg_rdata :
                             reg_uart   ? uart_rdata :
                             reg_gpu    ? gpu_reg_rdata :
                             reg_mixer  ? mixer_rdata :
                             32'h0;
```

This is exactly the kind of high-input mux that hurts LAB input packing. The fit
report says 300 ALMs are unavailable due to LAB input limits.

Recommendation:

Use a two-stage read pipeline while keeping the same MMIO map and readback data:

1. Cycle N: latch read region, page, and low address.
2. Cycle N+1: select inside a smaller page-local mux.
3. Cycle N+2 if needed: return data through the AXI R channel.

The current FSM already has `S_PERIPH_RD_WAIT`, so there is a natural place to
absorb one extra local-read cycle. Split sysregs into pages:

- Display/dataslot/palette page.
- Input page.
- Analogizer/SNAC page.
- IRQ/timer/debug page.
- GPU/audio/link external page.

Expected effect:

Moderate ALM savings and better packing. More importantly, the high-fan-in mux
should become easier to place away from the VexiiRiscv/CPU cluster.

Risk:

Medium. Firmware should tolerate MMIO read latency, but every register group
must be verified.

Validation:

- `tb_axi_periph`.
- Boot test.
- UART, input, timer, dataslot, GPU register readbacks.
- Duke settings and save-slot readbacks.

## Priority 1: Factor GPU Decode and Stage Control Muxes

Files:

- `src/fpga/common/gpu_core.v`
- `src/fpga/common/gpu_tex_cache.v`
- `src/fpga/targets/pocket/core_top.v:2519`
- `src/fpga/common/axi_periph_slave.v:409`

Problem:

The GPU is the largest block in the design at about 6,479 ALMs, 88 M10K, and 16
DSP. It contains span rendering, batching, span4, triangles, perspective setup,
translucent blending, SDRAM/cache access, diagnostics, and register readback.

The area risk is not only the amount of logic; it is that command decode,
state bits, span flags, blend controls, cache responses, and debug readbacks fan
out into large muxes. This hurts the same LAB-input limit that shows up in the
fit report.

Recommendation:

Keep every GPU command and feature, but reshape the control logic:

- Latch a compact decoded command class once when a command is accepted.
- Replace repeated opcode compares with registered one-hot command-family bits.
- Split the command FSM into smaller local sub-FSMs for command ingest, span
  setup, triangle setup, fragment issue, framebuffer read/modify/write, and
  completion.
- Register the span/triangle setup outputs before they enter the fragment pipe.
- Register diagnostic/readback mux output one level earlier so debug registers
  do not sit on the same path as active rendering control.
- Keep the existing fallback paths and exact command semantics.

Suggested shape:

```verilog
reg cmd_is_span_r;
reg cmd_is_span4_r;
reg cmd_is_triangle_r;
reg cmd_uses_persp_r;
reg cmd_uses_blend_r;

always @(posedge clk) begin
    if (cmd_accept) begin
        cmd_is_span_r     <= opcode_is_span;
        cmd_is_span4_r    <= opcode_is_span4;
        cmd_is_triangle_r <= opcode_is_triangle;
        cmd_uses_persp_r  <= decoded_perspective;
        cmd_uses_blend_r  <= decoded_translucent;
    end
end
```

The point is not the exact register names; it is to keep wide decode from being
recomputed across several large always blocks.

Expected effect:

Potentially moderate ALM improvement, but the larger benefit is fitter freedom
and less seed sensitivity near the CPU/GPU region.

Risk:

Medium. GPU command ordering and completion flags are sensitive. This needs GPU
acceptance tests and Duke rendering tests after each stage.

Validation:

- GPU span, batch-span, span4, colormap, masked, translucent, triangle, and
  perspective acceptance tests.
- Duke vline/mvline hot path.
- Texture corruption repro scenes, especially doors/screens where localized
  wrong texels were observed.
- GPU-only-first-frame startup repro.

## Priority 1: Remove Non-CDC `keep` / `preserve` Attributes

Files:

- `src/fpga/targets/pocket/core_top.v:258`
- `src/fpga/targets/pocket/core_top.v:259`
- `src/fpga/targets/pocket/core_top.v:282`
- `src/fpga/targets/pocket/analogizer/openFPGA_Pocket_Analogizer.v:128`
- `src/fpga/targets/pocket/analogizer/openFPGA_Pocket_Analogizer.v:130`
- `src/fpga/targets/pocket/analogizer/openFPGA_Pocket_Analogizer.v:230`
- `src/fpga/targets/pocket/analogizer/openFPGA_Pocket_Analogizer.v:248`
- `src/fpga/targets/pocket/analogizer/openFPGA_Pocket_Analogizer_SNAC.sv`
- `src/fpga/targets/pocket/analogizer/dualshock_controller.v`

Problem:

There are many `/* synthesis keep */` and `/* synthesis preserve */` attributes.
Many are legitimate CDC markers in the SNAC/DualShock paths, but several are on
ordinary output mux and pipeline nets. Those attributes restrict Quartus'
ability to merge, retime, duplicate, or pack logic.

Recommendation:

Keep CDC synchronizer protection. Remove preservation from ordinary mux,
pipeline, and output-select nets unless there is a documented Quartus issue or
active SignalTap dependency.

Clear candidates:

- Bulk mux preserves at `core_top.v:258`, `core_top.v:259`, `core_top.v:282`.
- Analogizer output mux preserves at
  `openFPGA_Pocket_Analogizer.v:128`, `130`, `230`, `232`, `248`.

Do not touch CDC markers clustered in:

- `openFPGA_Pocket_Analogizer_SNAC.sv:107-116`, `313`, `337-338`,
  `362-365`, `385-387`.
- `dualshock_controller.v:81`, `83`, `100`, `107`, `124-131`, `174-221`.

Expected effect:

Probably modest direct ALM savings, but meaningful fitter freedom. The current
report loses about 300 ALMs to LAB input limits, which is exactly where
unnecessary preservation hurts.

Risk:

Low if CDC attributes stay intact. Medium if a preserved net was being used for
debug capture.

## Priority 1: Combine Texture Cache Tag and Valid Memories

Files:

- `src/fpga/common/gpu_tex_cache.v:97`
- `src/fpga/common/gpu_tex_cache.v:109`
- `src/fpga/common/gpu_tex_cache.v:110`
- `src/fpga/common/gpu_tex_cache.v:373`

Problem:

The texture cache stores tags and valid bits in separate M10K memories:

```verilog
(* ramstyle = "M10K" *) reg                 valid_mem [0:SETS-1];
(* ramstyle = "M10K" *) reg [TAG_BITS-1:0]  tag_mem   [0:SETS-1];
```

The fit report shows two 1024 x 1 valid memories, each consuming a whole M10K.
This wastes two M10Ks and adds hard-block placement constraints.

Recommendation:

Pack valid into the tag word:

```verilog
(* ramstyle = "M10K" *) reg [TAG_BITS:0] tagv_mem [0:SETS-1];
wire rd_valid = rd_tagv[TAG_BITS];
wire [TAG_BITS-1:0] rd_tag = rd_tagv[TAG_BITS-1:0];
```

Because 1024 x 13 should fit in the same two-M10K geometry as 1024 x 12, this
should save two M10K blocks with little or no ALM increase.

Expected effect:

Saves about 2 M10K and improves GPU-local hard-block placement.

Risk:

Low. Must preserve reset/flush walk behavior and port A/B read timing.

Validation:

- Texture cache miss/hit tests.
- Colormap via port B tests.
- GPU texture flush mid-flight test.
- Duke localized texture corruption repros.

## Priority 1: Keep Dual-Port Texture Cache, Improve Its Timing Shape

Files:

- `src/fpga/common/gpu_tex_cache.v`
- `src/fpga/common/gpu_core.v`

Problem:

The current texture cache supports independent texture and colormap clients.
That is likely the right performance choice for Duke's colormapped span path,
but it creates a dense block around the GPU with two data copies, tag compare,
miss handling, and response muxing.

Recommendation:

Keep both clients and the current behavior, but reduce fanout and mux depth:

- Register port A and port B request metadata at cache entry.
- Keep tag compare local to each port and register hit/miss results before
  they feed command-state transitions.
- Separate miss-fill state from response-selection muxes.
- Keep colormap and texture response ordering explicitly documented in comments
  so future fixes do not reintroduce stale-response bugs.

Expected effect:

Likely small ALM change, but better fitter freedom around the GPU/M10K cluster.

Risk:

Medium because texture corruption bugs can be localized and angle-dependent.
Regression tests need real Duke scenes, not just synthetic spans.

## Priority 1: Reshape Analogizer Output Selection While Keeping All Modes

Files:

- `src/fpga/targets/pocket/core_top.v:196`
- `src/fpga/targets/pocket/core_top.v:397`
- `src/fpga/targets/pocket/analogizer/openFPGA_Pocket_Analogizer.v`
- `src/fpga/targets/pocket/analogizer/yc_out.sv`
- `src/fpga/targets/pocket/analogizer/scandoubler_2.v`

Problem:

Analogizer costs about 608 ALMs, 1 M10K, and 2 DSP. All video paths are active,
and the final output selection is a fairly wide mux across RGBS, RGsB, YPbPr,
Y/C, scandoubler, scanlines, GPIO, UART, and SNAC ownership.

Recommendation:

Retain every output mode, but make selection cheaper:

- Predecode `analog_video_type` into one-hot registered mode bits.
- Separate pin ownership selection from video-format selection.
- Register the selected RGB/Y/C path before the final pin mux.
- Remove non-CDC `preserve` attributes from normal output-select nets.
- Keep UART/SNAC idle behavior explicit in the final mux.

This should let Quartus pack the encoder outputs and final pin mux more freely
without changing supported modes.

Expected effect:

Modest ALM savings plus better routing. The main win is reducing high-fan-in
logic near the cart-pin output path.

Risk:

Medium. A pin mux mistake can break console input, Analogizer output, or SNAC.

Validation:

- Console controls.
- UART boot/logging.
- Analogizer output for every configured mode.
- SNAC adapter.
- Dock keyboard/mouse input.

## Priority 1: Avoid Tiny Shift Registers Becoming M10Ks

Files:

- `src/fpga/targets/pocket/analogizer/yc_out.sv:66`
- `src/fpga/targets/pocket/analogizer/yc_out.sv:114-117`
- `src/fpga/common/video_CRT_scanout_indexed_BRAM.v:171`
- `src/fpga/common/video_CRT_scanout_indexed_BRAM.v:195`

Problem:

The fit report confirms two inferred `altshift_taps` blocks each consuming a
whole M10K:

- `altshift_taps:phase[1].y_rtl_0` — width 36, depth 6.
- `altshift_taps:vactive_q1_rtl_0` — width 21, depth 3.

Both are far below M10K break-even and should not consume hard blocks.

Recommendation:

Apply shift-register extraction control locally:

```verilog
(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
phase_t phase[MAX_PHASES];

(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)
reg vactive_q1, vactive_q2, vactive_q3;
```

Alternatively, rewrite each as explicit named pipeline registers.

Expected effect:

Saves 1-2 M10K. May add a small number of FFs/ALMs, which is acceptable if it
improves hard-block placement.

Risk:

Low if `clk_vid` timing remains positive.

## Priority 2: Stage Video Scanout Mode Logic

Files:

- `src/fpga/common/video_CRT_scanout_indexed_BRAM.v:46`
- `src/fpga/common/video_CRT_scanout_indexed_BRAM.v:175`
- `src/fpga/common/video_CRT_scanout_indexed_BRAM.v:181`
- `src/fpga/common/video_CRT_scanout_indexed_BRAM.v:197`

Problem:

The scanout block supports 8-bit, 4-bit, 2-bit, RGB565, RGB555, and RGBA5551 at
runtime. That is needed for the SDK, but the current implementation puts mode
decode, address selection, byte/nibble extraction, direct-color unpack, and
alpha blacking close together.

Recommendation:

Keep every mode, but pipeline the mode-dependent work:

- Synchronize and register a one-hot mode decode.
- Stage line-buffer address selection separately from pixel extraction.
- Use a small registered extraction stage for 8/4/2-bit indexed modes.
- Keep direct-color unpack in a separate stage.
- Verify the low-video-settings path, because the alternate-line black issue
  suggests mode/timing interaction around screenshot/save-preview capture.

Expected effect:

Small to moderate ALM savings and less LAB input pressure in video timing logic.

Risk:

Medium because screenshot/save-preview and low-video-settings behavior are
already sensitive.

## Priority 2: Keep Four Input Slots, Simplify the Input Hub

Files:

- `src/fpga/common/axi_periph_slave.v:555-566`
- `src/fpga/common/axi_periph_slave.v:643`

Problem:

The input hub synchronizes four APF slots into the CPU domain:

- 4 x 32-bit key.
- 4 x 32-bit joy.
- 4 x 16-bit trigger.

There are about 12 `synch_3` instances plus previous-state tracking, event
detection, FIFO push logic, and readback mux rows.

Recommendation:

Keep all four slots, but make the structure more regular and easier to pack:

- Generate synchronizers and previous-state registers with arrays and `genvar`.
- Build per-slot change vectors locally, then OR-reduce a small vector instead
  of comparing everything in one block.
- Register the selected event slot before forming the FIFO payload.
- Stage input register readback through a slot-local mux followed by a final
  page mux.

Expected effect:

Likely modest ALM savings and better placement. The larger benefit is reducing
wide read/event muxing in `axi_periph_slave`.

Risk:

Medium. Input regressions are user-visible immediately.

Validation:

- Console controls.
- Duke keyboard.
- Dock keyboard/mouse/controller.
- Analogizer/SNAC input path.
- IRQ/event FIFO behavior for every slot.

## Priority 2: Reshape SNAC Logic Without Changing SNAC Support

Files:

- `src/fpga/common/axi_periph_slave.v:235`
- `src/fpga/common/axi_periph_slave.v:264`
- `src/fpga/common/snac_shifter.v`
- `src/fpga/targets/pocket/core_top.v:344`

Problem:

`snac_shifter` costs about 121 ALMs by itself, plus GPIO and read mux support.
Some SNAC and DualShock nets are correctly preserved for CDC, but the surrounding
register decode and pin muxing can still be simplified.

Recommendation:

- Keep SNAC support present.
- Keep CDC preservation on synchronizers.
- Stage SNAC register readback through the Analogizer/SNAC page mux.
- Check whether shift counters and output-enable decode can be factored into
  local registered signals.
- Keep UART cart-pin behavior and SNAC pin ownership as separate registered
  decisions before the final pin mux.

Expected effect:

Small to moderate. The goal is less mux pressure around `axi_periph_slave` and
the cart-pin output path, not a large standalone ALM win.

Risk:

Medium because SNAC and UART share sensitive external pins.

## Priority 2: Clean Up Audio Mixer Hot Muxes

Files:

- `src/fpga/common/audio_mixer.v:46`
- `src/fpga/common/audio_mixer.v:216`
- `src/fpga/common/audio_mixer.v:348-354`
- `src/fpga/common/audio_mixer.v:357-359`
- `src/fpga/common/audio_mixer.v:372-417`
- `src/fpga/common/audio_mixer.v:1087-1138`
- `src/fpga/common/axi_periph_slave.v:1325-1351`

Problem:

The mixer costs about 942 ALMs, 10 DSP, and 7 M10K. It keeps 32 voices, linear
interpolation, stereo handling, group/master volume, volume ramping, per-voice
readback, end IRQs, and CPU write queue.

The active-count calculation is already registered/staged, so it is not the
main target. Better targets are:

- Per-voice position readback at `audio_mixer.v:357-359`
  (`pos_readback = pos_latch[voice_sel_rd]`).
- Volume-ramp pipeline and `gxm_*_r` group×master composition around
  `audio_mixer.v:372-417` and `audio_mixer.v:1087-1138`.
- Mixer register readback case rows folded into the wide periph mux at
  `axi_periph_slave.v:1325-1351`.

Recommendation:

Keep the full mixer behavior, but stage heavy mux/add paths:

- Latch selected voice index before readback.
- Return per-voice fields through a two-stage mux.
- Factor common volume terms and register them before per-sample multiply/add.
- Keep IRQ bitmask behavior identical.

Expected effect:

Probably tens to low hundreds of ALMs depending on mux sharing and register
placement.

Risk:

Medium. The previous music-note issue was timing-sensitive, so validate with
menu music and high-pitched-note regressions.

## Priority 2: Tune CRAM0 Save/Bridge Buffers By Measurement

Files:

- `src/fpga/targets/pocket/core_top.v:638`
- `src/fpga/targets/pocket/core_top.v:662`
- `src/fpga/targets/pocket/core_top.v:1365`
- `src/fpga/common/sync_fifo.v:11`
- `src/fpga/common/cram0_bridge_prefetch.v`

Problem:

The CRAM0 bridge write FIFO is 1024 x 54 and consumes 6 M10K. The prefetcher
buffer consumes 7 M10K. These blocks were added for save correctness, so changes
must be measurement-driven.

Recommendation:

Add instrumentation for maximum observed FIFO occupancy during:

- SDK save stress test.
- Duke save.
- Launcher SaveA/SaveB.
- Core exit automatic writeback.

If measured occupancy leaves large margin, reduce buffer depth conservatively and
rerun save persistence tests. The external behavior must remain the same.

Expected effect:

Reducing 1024 x 54 to 512 x 54 should save roughly 3 M10K. ALM impact is small.

Risk:

High if guessed. Save corruption bugs were already subtle.

## Priority 2: Keep Debug Registers, Stage Counter Logic

Files:

- `src/fpga/common/gpu_tex_cache.v:83`
- `src/fpga/common/gpu_tex_cache.v:352`
- `src/fpga/common/gpu_core.v:1201`
- `src/fpga/common/gpu_core.v:2180`
- `src/fpga/common/axi_periph_slave.v:437`
- `src/fpga/common/axi_periph_slave.v:1082`
- `src/fpga/targets/pocket/core_top.v:2485`
- `src/fpga/targets/pocket/core_top.v:2501`

Problem:

Several 32-bit diagnostic counters and debug buses are synthesized:

- Texture cache request/miss counters.
- CMD_FLIP counters.
- Peripheral AW/W/B/stall counters.
- SDRAM/GPU debug bus composition and synchronizers.
- Audio mixer sample/debug readbacks.

These are useful, but saturating 32-bit incrementers and wide debug muxes can
spread control fanout into hot logic.

Recommendation:

Keep register addresses and readback behavior, but stage the implementation:

- Keep counter increments local to each block.
- Register event pulses before the saturating increment.
- Group debug readback into local pages before the final peripheral mux.
- Avoid feeding raw FSM state vectors directly into top-level debug buses.

Expected effect:

Likely small to moderate ALM improvement, mostly from reduced mux/fanout.

Risk:

Low if readback values remain semantically identical.

## Priority 2: Revisit QSF Fitter Settings During Sweeps

Files:

- `src/fpga/targets/pocket/ap_core.qsf:276`
- `src/fpga/targets/pocket/ap_core.qsf:285`
- `src/fpga/targets/pocket/ap_core.qsf:622`
- `src/fpga/targets/pocket/ap_core.qsf:656`

Problem:

The QSF has:

```tcl
set_global_assignment -name MUX_RESTRUCTURE OFF
set_global_assignment -name FITTER_EFFORT "STANDARD FIT"
set_global_assignment -name PARTITION_FITTER_PRESERVATION_LEVEL PLACEMENT_AND_ROUTING -section_id Top
```

Placement/routing preservation on the root partition can reduce the fitter's
freedom during significant RTL changes. It is useful only after selecting a
known-good placement for a small follow-up change.

Recommendation:

For exploration builds:

- Disable root placement/routing preservation.
- Keep the seed explicit.
- Re-run seed sweeps.
- Test `MUX_RESTRUCTURE ON` again after the peripheral read mux is split.

For release:

- Use the setting combination that reproduces the selected fit cleanly.

Risk:

Low. This affects build quality and reproducibility, not RTL behavior.

## Priority 3: CPU/VexiiRiscv Placement and Interface Hygiene

Files:

- `src/fpga/common/cpu_system.v:123`
- `src/fpga/common/cpu_system.v:741`
- `src/fpga/vendor/vexriscv/generate_vexii.sh`

Problem:

The CPU cluster is the second largest area consumer. VexiiRiscv alone is about
4,289 ALMs and 115 M10K. The worst timing path is on the 100 MHz CPU/RAM clock.

Recommendation:

Keep CPU ISA and cache behavior stable. Focus on placement and interface shape:

- Do not remove the AXI register slices; `cpu_system.v:124` documents why they
  improve placement and timing.
- Keep CPU-facing target muxes registered.
- Check whether CPU SRAM/cache RAMs are packed into hard blocks with sensible
  locality relative to the AXI target ports.
- Re-evaluate floorplan/LogicLock only after the peripheral and GPU mux changes,
  because those may alter the best CPU placement.

Expected effect:

This is more likely to improve slack and seed stability than to directly save
many ALMs.

Risk:

Low if ISA/cache configuration is unchanged.

## Priority 3: Link and VRR Readback Shape

Files:

- `src/fpga/targets/pocket/core_top.v:2255`
- `src/fpga/common/link_lite.v`
- `src/fpga/common/vrr_controller.v`
- `src/fpga/common/axi_periph_slave.v:1172`

Problem:

Link and VRR are not top area consumers, but they add control and readback paths
to the already large peripheral mux.

Recommendation:

Keep behavior present, but stage readback and status paths:

- Register Link status before it enters `axi_periph_slave`.
- Register VRR status before the sysreg read mux.
- Keep external timing behavior unchanged.

Expected effect:

Small. Useful mostly as part of the broader peripheral-read-mux cleanup.

## Things To Avoid

Do not globally force small inferred memories to MLAB.

Reason: final placement is already 99% ALMs and total MLAB memory bits are
currently zero. Moving many RAMs into ALMs can make the real limiter worse.

Do not touch CDC preservation casually.

Reason: many SNAC/DualShock `keep`/`preserve` attributes are protecting real
clock-domain crossings.

Do not remove CPU AXI register slices.

Reason: they isolate VexiiRiscv from long target-port routes and help the worst
clock domain.

Do not shrink save/writeback buffers without occupancy data.

Reason: Duke save failures were caused by subtle APF/CRAM0 persistence timing.

Do not change SDK-visible hardware behavior to solve fit.

Reason: the target is one feature-complete core, so optimization must come from
better RTL shape, packing, and fitter freedom.

## Suggested Implementation Order

1. Remove non-CDC `keep`/`preserve` attributes.
2. Pack GPU texture-cache valid bits into tag memories.
3. Add local shift-register extraction controls for the two tiny `altshift_taps`.
4. Split/register the peripheral read mux.
5. Reshape Analogizer output selection while keeping every mode.
6. Stage input hub event/readback muxes while keeping four slots.
7. Stage GPU command decode and debug/readback muxing.
8. Stage scanout mode logic and verify low-video-settings screenshot behavior.
9. Clean up audio mixer per-voice readback and volume paths.
10. Add save FIFO occupancy instrumentation before any buffer-depth change.
11. Re-run seed sweeps with root placement/routing preservation relaxed during
    exploration, then lock the selected release fit.

## Minimal Validation Matrix

Every area-reduction step should be checked with:

- `make -C src/fpga/test` or the narrow relevant FPGA tests.
- GPU acceptance tests for span, batch, span4, colormap, masked, texture cache,
  translucency, triangle, and perspective.
- Boot test.
- Duke build and runtime smoke test.
- Duke save/load within same run and after core exit.
- Duke settings save/load.
- Duke menu/music stress test.
- Console input and Duke keyboard input.
- Dock keyboard/mouse/controller.
- Analogizer output mode test for every mode.
- SNAC adapter test.
- Screenshot/save-preview path, especially low-video-settings mode.
- Quartus fit summary comparison: ALMs, final-placement ALMs, LABs, M10K, worst
  slack, route-through ALUTs, and unavailable ALMs due to LAB input limits.

## Expected Best Wins

The realistic savings from a single feature-complete core are still worthwhile
because the current build is limited by packing and high-fan-in muxes.

| Change | ALM impact | M10K impact | Main benefit |
| --- | --- | --- | --- |
| Peripheral read-mux split + paging | moderate | 0 | Lower LAB input pressure and better CPU-region placement. |
| GPU decode/control staging | moderate | 0 | Less fanout, better seed stability, possible timing improvement. |
| Remove non-CDC preservation | small to moderate | 0 | Better retiming, duplication, and packing freedom. |
| Analogizer output-select staging | small to moderate | 0 | Less cart-pin mux pressure. |
| Input hub staging/generate cleanup | small to moderate | 0 | Cleaner event/readback muxing. |
| Audio mixer readback/volume staging | small to moderate | 0 | Less per-voice mux/add pressure. |
| Texture-cache valid+tag packing | tiny ALM delta | ~2 M10K | Better GPU M10K placement. |
| Tiny altshift_taps to FFs | small ALM cost | ~1-2 M10K | Avoid wasting hard RAMs. |
| Save FIFO depth tuning with data | minor | up to ~3 M10K | Only if occupancy proves margin. |
| QSF preservation/sweep cleanup | no RTL change | no RTL change | Better fitter freedom and reproducibility. |

Expected aggregate impact: likely hundreds of ALMs plus 3-7 M10K from safe
memory packing/tuning, with a bigger improvement in fitter flexibility than raw
ALM count suggests. The key metrics to track are final-placement ALMs, LAB use,
route-through ALUTs, unavailable ALMs due to LAB input limits, and CPU-clock
slack across seeds.

## Additional Findings

1. `transluc_bram` at `gpu_core.v:421` dominates GPU M10K usage at about
   32 M10K. It should remain present, but its placement can influence the rest
   of the GPU. If texture/cache changes affect placement, compare whether this
   block moves and whether route-through ALUTs change.

2. `cmap_bram` is referenced in comments at `gpu_core.v:429`, `487`, and
   `1236`, but the actual cmap path is merged into `gpu_tex_cache` port B.
   Future work should update stale comments so optimization work does not chase
   a dead block.

3. `HW_FEATURES` already exists at `axi_periph_slave.v:409`. Keep it as the
   source of truth for SDK probes, but this optimization plan does not require
   changing advertised feature bits.

4. `MUX_RESTRUCTURE OFF` was probably set intentionally for the current mux
   topology. Retest it only after the large muxes are split; otherwise it can
   make an already dense design harder to place.

5. Several recent bugs were caused by extra gating in FSM transitions: slave
   `S_WR_DON`, GPU `count=0`, burst completion, and texture-cache pipe-B
   starvation. Favor explicit registered handshakes over clever combinational
   gating when reshaping the GPU, save, and peripheral paths.
