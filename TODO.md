# TODO — Post-session fixes

## Critical (blocking Duke3D)

### ~~fstat/stat hanging~~ DONE
- Fixed: added fstat/stat to jump table (slots 88-89), OF_LIBC_COUNT=90
- of_posix.c now delegates to musl via JT->fstat/JT->stat instead of lseek workaround

### ~~Blank screen after first few frames~~ DONE
- Root cause: FB address mismatch between hardware and firmware
  - Hardware used 512KB spacing (0x80000) but firmware uses 1MB spacing (0x100000)
  - After first swap, scanout read from address firmware never wrote to → blank
  - Terminal FB address was also wrong (0x180000 vs 0x300000)
- Fixed: aligned hardware localparams to 1MB spacing matching firmware defines

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

## Cleanup (lower priority)

### Remove dead bridge write FIFOs
- bridge_wr_fifo (SDRAM): ~38 ALMs, 28K BRAM — never used, no firmware DMAs to SDRAM via bridge
- sram_wr_fifo (SRAM): ~35 ALMs, 24K BRAM — never used
- See savings.md for details

### IRQ dispatcher testing
- kernel/irq.c is new, untested on hardware
- Need to verify timer IRQ still works (mcause 7 dispatch)
- Need to test external IRQ (mcause 11) now that mask register is added
