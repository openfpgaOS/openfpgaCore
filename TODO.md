# TODO — Post-session fixes

## Critical (blocking Duke3D)

### fstat/stat hanging
- Current: of_posix.c uses JT->lseek(SEEK_END) to get file size — may hang on some fds
- Real fix: Add `fstat` and `stat` to the libc jump table (entries 89-90)
  - `of_libc.h`: add `int (*fstat)(int, void *)` and `int (*stat)(const char *, void *)` to struct
  - `libc_table.c`: populate with musl's fstat/stat
  - `of_posix.c`: `int fstat(int fd, struct stat *buf) { return JT->fstat(fd, buf); }`
  - Musl handles the kernel struct layout internally — no ABI mismatch
- Fallback: revert to inline stubs returning -1 if jump table change is too risky

### Blank screen after first few frames
- First frames display correctly, then screen goes blank
- NOT VRR — disabled VRR, same issue
- Possible causes to investigate:
  1. Something writes TERM_FB_CTRL=1 (0x4000000C) after a few frames → scanout switches to terminal FB
  2. fb_swap_pending never clears after first few swaps — vsync stops firing or hold counter stuck
  3. PLL with 5 outputs may intermittently lose lock
  4. The overlay composite code reads TERM_FB_BASE through cached alias — if those cache lines have stale term FB data that's nonzero, it overwrites app pixels (but vid_display_mode should be FRAMEBUFFER not OVERLAY)
- Debug approach:
  - Add a vsync counter register readable by firmware — check if it keeps incrementing
  - Add a readable TERM_FB_CTRL shadow — verify it stays 0 during gameplay
  - Try reverting to 3-output PLL (remove clk_vid, use clk_core_12288 for video) to isolate PLL issue
  - Check if vid_display_mode is somehow OVERLAY (2) instead of FRAMEBUFFER (1)

### ext_irq disabled (tied to 0)
- We added link_irq + uart_rx_irq + mix_voice_end_irq but they're level-triggered
- Causes IRQ storm when any source is high (UART RX FIFO has stale data)
- Real fix: Add IRQ enable mask register in axi_periph_slave.v
  - New register at e.g. 0x400000F0: bits[2:0] = {mix, link, uart} enable
  - `ext_irq = (uart_rx_irq & mask[0]) | (link_irq & mask[1]) | (mix_voice_end_irq & mask[2])`
  - Reset default: 0 (all masked)
  - Firmware unmasks after registering handler that drains the source

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

## Cleanup (lower priority)

### Remove dead bridge write FIFOs
- bridge_wr_fifo (SDRAM): ~38 ALMs, 28K BRAM — never used, no firmware DMAs to SDRAM via bridge
- sram_wr_fifo (SRAM): ~35 ALMs, 24K BRAM — never used
- See savings.md for details

### fstat/stat via jump table (proper fix)
- Add entries 89-90 to of_libc_table for fstat/stat
- Update OF_LIBC_COUNT from 88 to 90
- Eliminates struct layout assumptions in of_posix.c

### IRQ dispatcher testing
- kernel/irq.c is new, untested on hardware
- Need to verify timer IRQ still works (mcause 7 dispatch)
- Need to test external IRQ (mcause 11) once mask register is added
