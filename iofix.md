# File I/O 64KB Read Hang — Investigation Status

## Symptom

After ~35 fopen/fclose cycles, the CPU hard-stalls. No trap fires, no
ecall executes. The stall occurs between two syscalls in user-mode code
(musl's __fdopen → malloc). A mock `open()+close()` also hangs — the
`close()` ecall never fires after `open()` succeeds.

## What we proved

### Time-based (confirmed)
Adding a 5-second delay at the start of the test shifts the hang EARLIER
(from close() hanging to open() hanging). The Pocket does something at
a fixed time after boot (~2-3 seconds) that stalls the CPU permanently.

### NOT shutdown (confirmed)
- SHUTDOWN_PENDING never asserts (debug print in handler never fires)
- Auto-ack in FPGA (shutdown_ack = ack_cpu | pending_cpu) doesn't help
- The bridge Reset Enter timeout (56ms) was a valid concern but not the
  active cause — the Pocket isn't sending Reset Enter during the test

### NOT memory type (confirmed)
Tested with all combinations — same hang at same point:
- I/O cache in CRAM1 (D-cached 0x31, uncached 0x39)
- I/O cache in SDRAM (kernel BSS)
- Original ra_buf_storage (single 32KB SDRAM buffer)
- Direct of_file_read (no cache at all)

### NOT the mixer/drain/shutdown in wait loops (confirmed earlier)
These were real bugs (caused iteration-2 hang) but were fixed. The
assertion-17 hang is a different, deeper issue.

### NOT interrupts (confirmed)
The hang existed before start.S IRQ enable changes were added.

### NOT the memset size (confirmed)
Reducing the 64KB memset to 4KB doesn't help.

## Confirmed fixes (committed and pushed)

### Mixer pump removal
- Removed from file_wait_complete (caused SDRAM contention with bridge DMA)
- Removed from syscall_dispatch (same issue on every syscall entry)
- Root cause of the original iteration-2 hang at assertion 5

### Audio ring buffer removal
- of_audio_drain in file_wait_complete caused AXI contention
- Ring buffer (SRAM) was unnecessary — apps use of_audio_write or mixer

### of_file_inval_cram fix
- SDRAM bridge addresses (0x00xxxxxx) incorrectly treated as CRAM0
- Fixed to check 0x20xxxxxx for CRAM0, 0x30xxxxxx for CRAM1

### PSRAM1 CDC ownership tracking (verified in Verilator)
- Response ownership register prevents bridge stealing CDC's rdata_valid
- Inflight guard prevents overlapping PSRAM reads
- bridge_requesting is combinatorial (wire) for same-cycle holdoff
- Verilator test passes: 8192 CPU reads + 16384 bridge reads, 0 errors

### DS_STATUS enhancements
- Bit 6: bridge_wr_idle (all bridge writes drained to memory)
- file_wait_complete waits for WR_IDLE after READY
- DS_DEBUG register at 0x94 (internal latch state)

### Auto-ack shutdown in FPGA
- shutdown_ack = shutdown_ack_cpu | shutdown_pending_cpu
- Bridge never times out regardless of CPU state

### VexiiRiscv PMA
- CRAM1 (0x31+): main=1 exe=0 (D-cacheable, not executable)

## What we don't know

1. **What does the Pocket do at ~2-3 seconds after boot?**
   - Not Reset Enter (SHUTDOWN_PENDING never asserts)
   - Not a data slot command (those go through target state machine)
   - Could be a bridge read/write that affects AXI bus state
   - Could be a host command we're not monitoring

2. **Why does the CPU hard-stall?**
   - The stall is on a user-mode instruction (load/store in malloc)
   - No trap fires — the CPU simply stops executing
   - This requires either:
     a. AXI bus locked by a pending request that never completes
     b. D-cache deadlock (fill + eviction interlock)
     c. External signal (reset_n, clock) being deasserted

3. **Can we catch the event in real-time?**
   - SignalTap on bridge_rd, bridge_wr, reset_n, shutdown_pending
   - Would show exactly what the Pocket sends at the critical moment
   - The DS_DEBUG register can't help because the CPU stalls

## Next steps

- **SignalTap**: Capture bridge bus activity around the time of the hang.
  Monitor: bridge_rd, bridge_wr, bridge_addr, reset_n, shutdown_pending,
  bridge_cram1_active, psram1_busy, cpu_psram_busy.

- **Scope the hang window**: Use a GPIO pin toggled by the CPU every
  syscall. When it stops toggling, that's the hang. Correlate with
  bridge activity on another channel.

- **Check reset_n**: The auto-ack should prevent reset, but verify
  reset_n stays HIGH throughout the test with SignalTap.

- **Check other host commands**: Monitor hstate in core_bridge_cmd
  to see which Pocket commands arrive and when.

## Files

### OS repo (openfpgaOS, branch feature/psram-32bit)
- `src/firmware/os/kernel/syscall.c` — I/O cache, debug traces
- `src/firmware/os/targets/pocket/file.c` — file_wait_complete, of_file_read_raw
- `src/firmware/os/targets/pocket/audio.c` — ring buffer removed, IRQ handler
- `src/firmware/os/hal/regs.h` — DS_DEBUG, DS_STATUS_WR_IDLE, timer regs
- `src/fpga/common/axi_periph_slave.v` — DS_DEBUG, WR_IDLE, ds_done fix
- `src/fpga/common/cpu_psram1_cdc.v` — mutual exclusion, cdc_inflight
- `src/fpga/targets/pocket/core_top.v` — ownership tracking, deferred bridge,
  auto-ack shutdown
- `src/fpga/vendor/vexriscv/generate_vexii.sh` — PMA
- `src/fpga/test/tb_cram1_cdc.v` — Verilator collision test
- `iofix.md` — this file

### SDK repo (openfpgaOS-SDK, branch main)
- `src/apps/testdemo/test_file.c` — debug markers, mock fopen
- `src/apps/testdemo/test_psram.c` — CRAM1 via 0x39
- `src/apps/testdemo/test_audio.c` — direct FIFO test (no ring buffer)
- `src/sdk/include/of_audio.h` — ring buffer API removed
