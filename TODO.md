# TODO

## Known workarounds & hacks

### Uncached alias (0x39/0x50) is unreliable
- The VexiiRiscv PMA does not reliably bypass the D-cache for "uncached" aliases
- **Workaround**: use cached alias + explicit D-cache clean (conflict eviction)
- **Affected code**:
  - `syscall.c:io_cache_data()` — uses cached 0x31 alias after DMA + invalidation
  - `mixer.c` — sample pool at 0x39 uncached, but `of_mixer_play` does a clean before starting
  - `audio.c:of_audio_write()` — writes to 0x39 scratch, flushes before playing
  - `video.c:of_video_init()` — clears FBs via 0x50 uncached (works at boot, terminal takes over)
  - `terminal.c` — writes glyphs to TERM_FB_BASE (0x50300000 uncached); may have coherency issues
- **Proper fix**: investigate VexiiRiscv PMA configuration for the 0x38-0x39/0x50 ranges

### Zicbom `cbo.clean` is extremely slow (~825 cycles per line)
- Measured: 1200 lines × 825 cycles = 990,000 cycles ≈ 9.9ms — unusable for per-frame flushes
- `cbo.inval` works fine (used in `fast_mem.S` memcpy) — invalidation path has no writeback, so no stall
- `cbo.zero` disabled due to CRAM1 stall — separate issue
- **Workaround**: `video.c` returns uncached FB pointer from `of_video_get_surface()`, flip does no flush
- **CBM Scala source** (`LsuL1Plugin.scala:1034-1056`):
  - `CBM_REDO := askCbm` — always replays on dirty lines (line 1035)
  - `doCbm` requires `!writeback.full` — blocks if writeback queue full (line 977)
  - Writeback queue is only 2 slots deep (`--lsu-l1-writeback-count=2` in `generate_vexii.sh`)
  - Each dirty cbo.clean: push writeback → REDO → replay → see clean → complete
- **The math doesn't add up**: 2-slot queue × ~60 cycles/writeback should give ~60 cy/line steady-state (0.7ms total), not 825 cy/line (9.9ms). The extra ~750 cycles/line is somewhere deeper:
  - AXI4 write adapter BVALID response path?
  - Bus arbitration stalls with video scanout bursts?
  - Writeback data read serializing with AXI4 write?
  - Pipeline replay overhead higher than expected?
- **Next step**: Verilator trace of a single `cbo.clean` writeback to find where the stalls are
- **Scala change to try**: increase `--lsu-l1-writeback-count` from 2 to 4 or 8 to see if throughput improves, or check if the pipeline replay cost dominates

### Mixer mix-down is a fixed ÷2 shift
- `audio_mixer.v` shifts accumulator right by 1 before clamping
- Prevents clipping with 2+ simultaneous voices but costs 6dB on single-voice playback
- **Proper fix**: dynamic gain based on active voice count, or let firmware set a mix level register

## Bugs to investigate

### Blank screen after first few frames (Duke3D)
- First frames display correctly, then screen goes blank
- NOT VRR (disabled, same issue) — NOT address mismatch (HW uses 16-bit word addresses)
- Possible causes:
  1. fb_swap_pending never clears after first few swaps
  2. Something writes TERM_FB_CTRL=1 → scanout switches to terminal FB
  3. Loader not configuring framebuffer correctly on app start

### Fmax below 100 MHz target
- CPU clock (mp_ram out0): ~95 MHz best (seed-dependent)
- clk_74a (bridge): ~67 MHz vs 74.25 MHz target
- Need seed sweep to find one that closes timing
- SDC constraints added (SDRAM I/O, clock groups) — helped from 89→95 MHz

### CRAM1 read contention between CPU and mixer
- Mixer and CPU share one CRAM1 read port via arbiter
- `cdc_cpu_rdata_valid` fires for BOTH — mixer can't distinguish its own reads from CPU's
- Works in practice (CPU rarely accesses CRAM1 during playback) but formally unsafe
- Could cause audio glitches under heavy CRAM1 load

## Completed

### fstat/stat via ecall (was: hanging on lseek)
- Jump table slots 88-89, musl fstat/stat via ecall
- `of_posix.c` uses 256-byte stack buffer to absorb kernel SYS_statx write
- Can't call kernel functions directly from app context (bridge ops need syscall trap)

### IRQ mask register
- `IRQ_MASK` at 0x400000FC, bits[2:0] = {mix_voice_end, link, uart_rx}
- ext_irq driven by masked OR in axi_periph_slave (reset: all masked)

### Clock/timing fixes
- SDC: RAM PLL outputs grouped together (were incorrectly async)
- SDC: SDRAM I/O timing constraints added (dram_clk_pin)
- VRR CDC: toggle handshake replaces bare 2-stage sync for 10-bit bus
- reset_n synchronized to clk_vid domain (reset_n_vid)

### Mixer bugs fixed
- `write_ctrl`: removed volume nibble from CTRL bits [7:4] (corrupted dir/bidi flags)
- `audio.c:of_audio_write`: same CTRL volume bug fixed, added cache flush
- Position auto-clear: only on inactive→active transition (was every CTRL write)
- Loop auto-init race: removed deferred LOOP_END/LOOP_START writes from LEN handler
- L/R channel swap: fixed mixer output to `{clamp_l, clamp_r}`
- Sample pool: uncached alias + D-cache clean before play
- `of_mixer_stop`: snaps volume to 0 before deactivating (reduces clicks)

### Video hardening
- `of_video_init`: resets vid_display_mode, clears FBs via uncached alias
- `of_video_wait_flip`: timeout prevents infinite hang if vsync stops
- `of_video_flip`: waits for pending swap before queuing new one

### I/O cache coherency
- `io_cache_data` returns cached alias (0x31) instead of unreliable uncached (0x39)
- After DMA + invalidation, first read misses and fetches fresh data

## Cleanup (lower priority)

### Remove dead bridge write FIFOs
- bridge_wr_fifo (SDRAM): ~38 ALMs, 28K BRAM — never used
- sram_wr_fifo (SRAM): ~35 ALMs, 24K BRAM — never used

### IRQ dispatcher testing
- kernel/irq.c untested on hardware
- Need to verify timer IRQ (mcause 7) and external IRQ (mcause 11) with mask register

### APP_BRAM_END mismatch
- Verify Duke3D BRAM usage doesn't exceed 0x7C00 limit after rebuild

### VRR re-enable
- VRR code exists but is disabled (`vrr_update()` commented out in of_video_flip)
- Toggle handshake CDC is ready — needs testing after blank screen bug is fixed
