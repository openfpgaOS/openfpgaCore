# Potential ALM/BRAM Savings

## Pending

### Remove dead bridge write FIFOs
- **bridge_wr_fifo** (SDRAM): 512×56-bit dcfifo + skid buffer — no firmware ever DMA-writes to SDRAM via bridge
- **sram_wr_fifo** (SRAM): 512×56-bit dcfifo + skid buffer + sram_controller write path — SRAM never used as bridge DMA target
- **Est. savings**: ~118 ALMs, 53K BRAM bits (5 M10K blocks)
- **Files**: `core_top.v` (lines ~930-1015 bridge_wr_fifo, ~1435-1540 sram_wr_fifo), `sram_controller.v` write path
- **Risk**: Low — verify no future bridge write use case before removing

### Log volume LUT in BRAM instead of MLAB
- Current: 256-byte LUT inferred as distributed MLAB (~16 ALMs)
- Could move to a spare M10K slot if ALMs are more scarce than BRAM
- **Est. savings**: ~16 ALMs, +1 M10K block
- **Risk**: None

### Reduce link_mmio FIFOs
- Current: 256-word TX + RX FIFOs (16K BRAM bits, 2 M10K)
- Typical usage is a few words at a time — 64-word FIFOs would suffice
- **Est. savings**: 0 ALMs, ~12K BRAM bits (may free 1 M10K)
- **Risk**: Low — verify max burst size in link protocol

## Completed (this session)

| Change | ALMs saved | BRAM saved | Timing impact |
|--------|-----------|------------|---------------|
| Software terminal (removed text_terminal.v) | ~491 | 2 M10K + font ROM | — |
| Removed display_mode register | ~2 | — | — |
| Removed tile/sprite stub ports (16 outputs) | ~10 | — | — |
| Removed dead VID_* localparams + clk wire | ~1 | — | — |
| Mixer log volume: LUT replaces 2 multipliers | -18 ALMs (MLAB) | — | +0.24 ns slack, freed 2 DSP |
| Mixer 3-stage pipeline (LUT→mul→accum) | — | — | +0.24 ns SDRAM slack |
| Mixer ramp simplification + pos_wrapped dedup | ~10 | — | reduced comb depth |
| **Total** | **~528 ALMs (4.1%)** | **~60K bits** | **SDRAM: -0.91→-0.67 ns** |
