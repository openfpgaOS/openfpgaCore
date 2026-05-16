# GPU Translucency LUT In External SRAM

## Goal

Move the GPU `transluc[]` lookup table out of on-chip BRAM/M10K and into the
Pocket external SRAM. The purpose is to free the current 32 KB M10K allocation
for future GPU scratch experiments, especially BRAM framebuffer or stripe
buffering, while preserving byte-identical translucent rendering.

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

## Implementation Order

1. Add SRAM ports to `gpu_core`.
2. Wire GPU SRAM ports to `sram_controller` in `core_top.v`.
3. Replace transluc BRAM upload with the SRAM write queue.
4. Replace transluc BRAM lookup with the SRAM read FSM.
5. Delete `transluc_bram`.
6. Update acceptance tests.
7. Build and compare resource usage.
