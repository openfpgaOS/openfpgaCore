Focused silent-audio review — native MD2 experiment, 2026-10-12

Run: python3 run.py
Requires Python3, Verilator, C/C++ compiler and ASan/UBSan. No game assets,
Quartus, sibling checkout or FPGA are needed. Source snapshots and their
original paths/SHA256 values are in sources/ and source-pins.json. The tested
core_top.v is byte-identical to the fit-silent-v3 input. Logs and result.json
record the completed test; generated executables live only in a temporary dir.

The test extracts the EXACT NATIVE_MESH_SILENT_AUDIO conditional from the
pinned core_top.v. Its unselected audio_output instance matches the unchanged
baseline instance byte-for-byte, and both normal HW/SW branches preprocess
identically to the baseline. Simulations also check each normal branch's
selected PCM producer through a transparent audio_output test double; this is
not a new test of the existing FIFO or serializer internals.

Three Verilator runs each execute 8,212 audio clock cycles: 9 cold reset,
4,096 active, 11 warm reset, then 4,096 active. For the silent branch MCLK
follows the input at both edges, LRCK exactly follows the expected /256
divider (32 rising and 32 falling edges across the two active windows), DAC
stays zero, and FIFO level/full remain 1023/1 even while CPU/mixer PCM inputs
change. At the specified 12.288MHz MCLK this yields a 48kHz LRCK. The actual
AXI status packing and firmware masks are pinned and checked: status is0x7ff.

The host harness includes the FULL unchanged sw_mixer.c. It substitutes only
register access/platform address definitions, derives the status masks from
the actual regs.h, and instruments actual function entries. ASan/UBSan pass:

- 1,000 cold-start software-pump calls: no render and no PCM writes.
- 1,000 calls with a stale active voice/inaccessible sample pointer: no
  render, no PCM writes and no mutation of the voice-register backing store.
- 1,000 calls after resetting the firmware initialization flag: no render
  and no PCM writes.
- Positive control with exactly one free FIFO slot: one real render call
  and one PCM write, proving the counters detect the removed work.
- 1,000 normal hardware-mixer calls: no FIFO reads, renders or PCM writes.

The first pump after each cold/reset epoch still performs one backing-store
initialization before polling FIFO status. Each later ISR call pays the
existing pump guard/status-read overhead. The test establishes removal of
sample generation, not removal of every audio-related CPU instruction.

The native-v3 app separately returns false from SDL_BackendInit before any
audio/mixer service initialization when native bit12 is set and HW mixer
capability is absent. That app gate and this silent-core status behavior are
both necessary: the OS invokes its software pump even without app startup.
This configuration deliberately has no sound. Existing hardware-mixer audio
and generic software-mixer cores keep their existing branch and behavior.

No issue found in the reviewed block. Physical timing, core fit and Pocket
operation remain separate checks; these focused tests do not establish FPS.
