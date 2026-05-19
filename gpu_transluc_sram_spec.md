# GPU Translucency LUT In External SRAM

## Goal

Move the GPU `transluc[]` lookup table out of on-chip BRAM/M10K and into the
Pocket external SRAM while preserving byte-identical translucent rendering.
The later BRAM framebuffer experiment was implemented and measured, but did
not improve rendering performance; that 64 KB M10K budget is now reassigned
to the CPU D-cache instead.

## Current State

`src/fpga/common/gpu_core.v` stores the translucency LUT as:

```verilog
reg [31:0] transluc_bram [0:8191];
```

Properties:

- Size: 32 KB.
- Shape: 8192 32-bit words.
- Upload path: `GPU_TRANSLUC_ADDR` and `GPU_TRANSLUC_DATA`.
- Lookup timing: 1-cycle BRAM read.
- Lookup address: `{shaded_src[7:1], fb_byte[7:0]}`.
- Output: one selected byte from the returned 32-bit word.

The external SRAM controller already exists in `src/fpga/targets/pocket/core_top.v`,
but SRAM is currently tied off because the old Z-buffer path was removed.

## Target End State

The active architecture keeps the translucency LUT in external SRAM and renders
directly to SDRAM.  The intermediate framebuffer section below is retained only
as historical design context for the measured experiment.

## SRAM Layout

Keep SRAM GPU-private. Do not reintroduce a CPU AXI surface or general-purpose
SRAM bus.

```text
0x00000..0x07fff  translucency LUT, 32 KB
0x08000..end      reserved for future GPU scratch/framebuffer experiments
```

## GPU SRAM Interface

Add a direct SRAM word interface to `gpu_core`:

```verilog
output reg         sram_rd,
output reg         sram_wr,
output reg  [21:0] sram_addr,
output reg  [31:0] sram_wdata,
output reg  [3:0]  sram_wstrb,
input  wire [31:0] sram_rdata,
input  wire        sram_busy,
input  wire        sram_rdata_valid
```

Wire these ports directly to the existing `sram_controller` instance in
`core_top.v`.

## Upload Path

Replace the BRAM write:

```verilog
transluc_bram[transluc_wr_addr[14:2]] <= reg_wdata;
```

with an SRAM write:

```text
sram_addr  = TRANSLUC_SRAM_BASE_WORD + transluc_wr_addr[14:2]
sram_wdata = reg_wdata
sram_wstrb = 4'b1111
sram_wr    = 1
```

The CPU upload path must not silently drop writes if `GPU_TRANSLUC_DATA` arrives
while SRAM is busy. Preferred implementation:

- Add a 1-entry pending upload register.
- Accept the MMIO write into that register.
- Issue it to SRAM once `!sram_busy`.
- Increment `transluc_wr_addr` only when the pending write is accepted or
  explicitly when the write is captured, as long as ordering is preserved.

The SDK upload sequence should not need to change.

## Lookup Path

Replace the BRAM lookup in the blend flow with an SRAM read sub-FSM.

Current conceptual flow:

```text
BLEND_R_WAIT
  set transluc_rd_addr

BLEND_LUT_WAIT
  wait for BRAM read latency

BLEND_APPLY
  use transluc_rd_data
```

New flow:

```text
BLEND_R_WAIT
  compute transluc key and word address
  issue SRAM read when !sram_busy

BLEND_LUT_WAIT
  wait for sram_rdata_valid

BLEND_APPLY
  select byte lane from sram_rdata
  apply blend result
```

Address construction:

```verilog
wire [14:0] transluc_key       = {src_byte[7:1], fb_byte};
wire [21:0] transluc_word_addr = TRANSLUC_SRAM_BASE_WORD + transluc_key[14:2];
wire [1:0]  transluc_lane      = transluc_key[1:0];
```

Lane select:

```verilog
case (transluc_lane)
  2'd0: transluc_rd_data = sram_rdata[7:0];
  2'd1: transluc_rd_data = sram_rdata[15:8];
  2'd2: transluc_rd_data = sram_rdata[23:16];
  default: transluc_rd_data = sram_rdata[31:24];
endcase
```

## Constraints

- SRAM remains GPU-private.
- Do not add CPU AXI access to SRAM.
- Do not add a general SRAM arbiter unless a second active SRAM user is
  introduced.
- Preserve the existing quantized table format.
- Preserve byte output for all existing translucent span cases.
- Opaque and masked span paths must remain unchanged.
- Keep existing generic span and triangle behavior unchanged except for
  transluc lookup latency.

## Retired Intermediate BRAM Framebuffer State

After the translucency table was moved to SRAM, the freed M10Ks were tested as
a GPU-local render target. Hardware validation showed no useful performance
gain, so this mode has been retired and the recovered 64 KB M10K budget is now
reserved for the CPU D-cache.

### Motivation

Duke-style vertical spans create scattered SDRAM writes. A BRAM framebuffer
turns those writes into local memory updates, then performs one sequential
copy-back to SDRAM at frame end. The copy-back can use the existing SDRAM burst
write path, making the expensive part predictable and linear.

The experiment implemented the feature as a generic GPU capability:

- Span renderers can target BRAM instead of SDRAM.
- Triangle renderers can target BRAM instead of SDRAM.
- Final presentation still uses the normal SDRAM framebuffer and scanout path.

### Memory Sizing

8-bit indexed framebuffer sizes:

```text
320x200 = 64,000 bytes = 16,000 32-bit words
320x240 = 76,800 bytes = 19,200 32-bit words
```

The current translucency BRAM is 32 KB. Moving it to SRAM frees enough M10K for
roughly 102 full-width 320-pixel lines, but not a complete 320x200 or 320x240
framebuffer by itself.

Measured implementation shape:

```text
BRAM FB width:      320 pixels
BRAM FB height:     configurable, start with 100 or 128 lines
BRAM FB format:     8-bit indexed
BRAM FB stride:     320 bytes
Copy-back target:   SDRAM framebuffer base + y_offset * 320
```

The useful target was a full 320x200 Duke-sized framebuffer, but even with the
larger 64 KB buffer the measured result did not improve Duke or gpudemo enough
to justify the extra GPU control logic.

### Rendering Model

The retired design added a GPU render-target mode:

```text
TARGET_SDRAM_FB   existing behavior
TARGET_BRAM_FB    write GPU fragments into intermediate BRAM
```

The SDK exposed this as a policy instead of forcing apps to manually sequence
the copy-back every frame:

```c
of_gpu_set_framebuffer_policy(OF_GPU_FB_POLICY_DIRECT);     // default
of_gpu_set_framebuffer_policy(OF_GPU_FB_POLICY_BRAM_AUTO);  // transparent BRAM when the window fits
of_gpu_configure_bram_framebuffer(width, height, src_off, src_stride, dst_stride);
```

In the current code, these APIs are compatibility shims only:
`OF_GPU_FB_POLICY_BRAM_AUTO` is accepted as direct SDRAM rendering, and
`OF_GPU_BRAM_FB_BYTES` reports zero.

When `TARGET_BRAM_FB` was enabled in the experiment:

- Opaque writes update BRAM.
- Masked writes skip transparent pixels and update BRAM.
- Translucent writes read destination from BRAM, use the SRAM transluc table,
  then write the blended byte back to BRAM.
- GPU commands that address rows outside the configured BRAM window either:
  - fall back to SDRAM writes, or
  - are clipped/rejected by command setup.

The production path keeps direct SDRAM targeting for all spans and triangles.

### Copy-Back Command

The retired design used a GPU command to copy the BRAM framebuffer window to
SDRAM:

```text
CMD_COPY_BRAM_FB_TO_SDRAM
  word 0: opcode / flags
  word 1: SDRAM framebuffer byte base
  word 2: source BRAM byte offset
  word 3: byte count, rounded down to 32-bit words
```

The command and its decoder state have been removed from the active RTL.

Copy ordering:

1. GPU renders into BRAM.
2. GPU copies BRAM window to SDRAM.
3. GPU fence/flip only retires after the copy-back writes have committed.
4. CPU HUD/menu overlays may then draw directly to SDRAM, or the app may
   include them in the BRAM window if they fit.

The active SDK does not queue copy-back work because the BRAM target is retired.

For CPU framebuffer access, transparency still requires an app/engine hook.
An engine that reads the framebuffer or draws CPU overlays after GPU work must
call:

```c
of_gpu_prepare_framebuffer_for_cpu();
```

In the active direct-SDRAM path this is equivalent to `of_gpu_finish()`.

### Clear/Load Policy

The BRAM target needs explicit contents.

Supported policies:

- `CLEAR_BRAM_FB`: clear the BRAM window to a color before rendering.
- `LOAD_SDRAM_TO_BRAM`: optional future command for effects that need the
  previous SDRAM framebuffer contents.
- `UNDEFINED`: fastest path, only valid when every destination pixel in the
  BRAM window is overwritten before copy-back.

This state machine is not present in the active RTL.

### Hazards And Constraints

- Do not let scanout read BRAM directly; scanout remains SDRAM-only.
- Do not add CPU access to BRAM FB.
- Do not route BRAM FB through the existing command ring BRAM.
- Do not require a full-screen BRAM framebuffer for correctness.
- Preserve direct SDRAM target behavior as the production default; the active
  SDK accepts `OF_GPU_FB_POLICY_BRAM_AUTO` as a no-op compatibility value.
- Fence and flip semantics must still mean all pixels are visible in SDRAM.
- If translucent rendering targets BRAM, destination readback must come from
  BRAM, not SDRAM.

### Expected Performance

Best case:

- Large reduction in scattered SDRAM writes for vertical-span-heavy scenes.
- More predictable frame-end SDRAM traffic.
- Potential to use SDRAM bursts during copy-back in a later optimized copy
  engine.

Costs:

- Fixed copy-back bandwidth every frame.
- Extra BRAM write/read muxing in GPU.
- Extra copy FSM pressure.
- If the BRAM window is small, scenes with many pixels outside the window still
  use SDRAM writes and gain less.

Measured result: the extra muxing and copy-back machinery were not a win.  The
current design favors a simpler direct-SDRAM GPU and spends the recovered M10K
on CPU cache capacity.

## Expected Tradeoffs

Benefits:

- Frees roughly 32 KB of M10K currently used by `transluc_bram`.
- Keeps transluc table reads off SDRAM.
- Creates a GPU-private SRAM scratch area for later experiments.

Costs:

- Transluc lookup latency increases from a 1-cycle BRAM read to external SRAM
  controller latency.
- Translucency-heavy scenes may slow down.
- Adds some ALMs for SRAM request/write state and top-level wiring.

Expected game impact:

- Duke overall should change little because opaque and masked spans dominate.
- Translucency-heavy scenes may regress and should be measured.

## Validation

Run the GPU tests:

```sh
make -C src/fpga/test gpu-acceptance
make -C src/fpga/test gpu gpu-chain
```

Add or update tests for:

- Transluc table upload through `GPU_TRANSLUC_ADDR` / `GPU_TRANSLUC_DATA`.
- Translucent span output equals the current BRAM-backed implementation.
- Consecutive upload writes while SRAM is busy.
- Multiple byte lanes in the transluc table word.
- Opaque and masked spans unchanged.

Hardware validation:

- Build the Pocket core.
- Confirm M10K count drops by the expected amount.
- Confirm LAB increase remains acceptable.
- Test Duke normal gameplay and translucent effects.
- Keep BRAM framebuffer compatibility shims no-op unless the experiment is
  deliberately revived.

## Implementation Order

Stage 1, SRAM translucency:

1. Add SRAM ports to `gpu_core`.
2. Wire GPU SRAM ports to `sram_controller` in `core_top.v`.
3. Replace transluc BRAM upload with the SRAM write queue.
4. Replace transluc BRAM lookup with the SRAM read FSM.
5. Delete `transluc_bram`.
6. Update acceptance tests.
7. Build and compare resource usage.

Historical stage 2, intermediate BRAM framebuffer:

1. Implemented a parameterized BRAM framebuffer block.
2. Added GPU render-target mode selection.
3. Routed opaque/masked/translucent FB writes to BRAM in BRAM mode.
4. Added `CMD_COPY_BRAM_FB_TO_SDRAM`.
5. Preserved fence/flip ordering through copy-back completion.
6. Added byte-exact tests comparing SDRAM direct render vs BRAM render plus
   copy-back.
7. Measured Duke and gpudemo.
8. Retired the path after it failed to improve performance.
