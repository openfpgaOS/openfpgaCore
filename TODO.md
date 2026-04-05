# TODO — Post-session fixes

## Critical (blocking Duke3D)

### ~~fstat/stat hanging~~ DONE
- Added fstat/stat to jump table (slots 88-89, OF_LIBC_COUNT=90) pointing to musl's implementations
- of_posix.c calls JT->fstat() with a 256-byte stack buffer, then extracts st_size from offset 40
- Flow: app → of_posix.c (stack buffer) → musl fstat → ecall → kernel SYS_statx → writes 256B to stack buffer
- Can't call kernel functions directly from app context (bridge ops need syscall trap)

### Blank screen after first few frames
- First frames display correctly, then screen goes blank
- NOT VRR — disabled VRR, same issue
- NOT address mismatch — HW uses 16-bit word addresses, values are correct
- Possible causes to investigate:
  1. Loader not using framebuffer correctly
  2. fb_swap_pending never clears after first few swaps
  3. Something writes TERM_FB_CTRL=1 after a few frames → scanout switches to terminal FB

### ~~ext_irq disabled~~ DONE
- Fixed: added IRQ_MASK register at 0x400000FC, bits[2:0] = {mix, link, uart}
- ext_irq now driven by masked OR in axi_periph_slave.v (reset default: all masked)
- Firmware unmasks via IRQ_MASK after registering handler

## Important (not blocking but should fix)

### APP_BRAM_END mismatch
- OS regs.h: APP_BRAM_END = 0x7C00 (libc table at 0x7C00)
- PocketDukeNukem-SDK app.ld: was 0x7E00, fixed to 0x5A00 (= 0x7C00 end)
- openfpgaOS-SDK app.ld: already correct at 0x5C00
- Verify Duke3D BRAM usage doesn't exceed new limit after rebuild

### of_video_init display mode switch
- of_video_init() calls of_video_set_display_mode(FRAMEBUFFER) to switch from terminal FB to app FB
- Changed to direct TERM_FB_CTRL = 0 to avoid side effects
- Need to verify this actually switches the scanout on hardware

### Investigate uncached alias overuse for framebuffers
- of_video_init() currently clears FBs via uncached alias (0x50000000)
  - Uncached writes bypass D-cache → go directly to SDRAM
  - But this defeats write-combining and is very slow for large fills (76800 bytes × 3)
  - The SDRAM controller sees single-word writes instead of burst-friendly cache-line flushes
- of_video_flip() flushes via conflict eviction (cache.c) — correct but expensive
- Questions to investigate:
  1. Are uncached FB writes causing SDRAM bus contention with the video scanout?
  2. Should FBs always be written cached + flushed (better throughput via write-back bursts)?
  3. Terminal FB (TERM_FB_BASE = 0x50300000) uses uncached alias for all glyph blits —
     is this causing visible tearing or SDRAM bandwidth starvation?
  4. Could the uncached alias region (0x50000000) be misconfigured in the CPU's PMA
     or address decoder, causing bus errors or stalls?
  5. Profile: cached memset + flush vs uncached memset — which is faster for 76KB fill?
- If uncached alias is the problem, switch of_video_init() back to cached writes + flush
  (using of_cache_clean_range or a simpler full dcache flush)

## Cleanup (lower priority)

### Remove dead bridge write FIFOs
- bridge_wr_fifo (SDRAM): ~38 ALMs, 28K BRAM — never used, no firmware DMAs to SDRAM via bridge
- sram_wr_fifo (SRAM): ~35 ALMs, 24K BRAM — never used
- See savings.md for details

### IRQ dispatcher testing
- kernel/irq.c is new, untested on hardware
- Need to verify timer IRQ still works (mcause 7 dispatch)
- Need to test external IRQ (mcause 11) now that mask register is added
