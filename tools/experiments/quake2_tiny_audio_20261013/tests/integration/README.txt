Tiny-audio integration qualification

Run against a staged selected core (Verilator+C++ required):
  python3 run_portable.py --core /path/to/staged-core --output /tmp/new-audio-proof
The output directory must be new. This performs no fit, SD write or deployment.
The fresh reproduction in portable-proof-v4/ passed against mixerbac9c69b,
cacheac018efd, OS30 peripheral2ad87cc9 and core_topbc1846e6.

Actual OS30 peripheral, cache-present and cache-absent configurations:
39/39 assertions each. Tests cover registered position readback, queued-write
backpressure, default-off CTRL, bit1 opt-in/readback, capability magicSCF1 at
0x48000834, capability read-only behavior, unchanged IRQ pending/W1C at
0x48000824, all512 voice/field addresses pulsing exactly once, and warm reset.
The wrapper is based on the existing peripheral bench. The audio-only entry
point avoids generic Analogizer/SNAC tests for features excluded from OS30.
An earlier full generic run retains its17 unrelated feature-mismatch failures
in periph/result-{0,1}.log; none were audio integration failures.

Actual mixer+cache connected as core_top, synthetic stalled SDRAM responder:
177 inherited checks including288 stop/rearm phases,18,432 independent sample
reference frames in96 scenarios/all32voices,64 completion/W1C races,
2048position readbacks, and343 capacity rows pass. PCM/position/volume/active/
completion hash46e30baca498b56b matches the uncached reference exactly.
The cache-enabled wrapper changes only AXI connectivity and the rearm test's
address observer: logical mixer requests must remain inside the programmed
sample; physical cache fills deliberately read the containing32-byte SDRAM
line. An initial unchanged physical-bound check rejected that legal prefetch
(addr0x9c versus last requested word0x84); the ownership/error/position checks
were retained and the bound is now observed upstream of the cache. Stream
requests bypass, so their physical producer-frontier bound is unchanged.

Read-only source audit:
- QSF selects the OS30 peripheral, mixer, cache and core_top sources, with
  INCLUDE_HW_MIXER and INCLUDE_AUDIO_SAMPLE_CACHE both enabled.
- Fetch metadata comes from cur_voice and captured cur_ctrl.stream, stable
  until the current mixer request is accepted; the cache captures metadata.
- Every actual voice MMIO write invalidates that slot. Group/master/IRQ writes
  do not alter sample contents. Invalidation wins over refill commit.
- Cache enable is CTRL.bit1 AND CTRL.bit0. Disable clears all line validity;
  reset clears state/validity. Media/core resets are synchronized replicas
  driven by the same reset source. Mixer and cache use the same media reset.
- Stream requests always bypass. Already accepted reads may finish after an
  invalidation; the mixer drains and discards a stopped/rearmed pass.
- Cache hits require full physical address tags and voice identity. The final
  crossing interpolation word bypasses without replacing the useful line.

Quake2/kernel lifecycle audit:
SDL_BackendInit calls of_audio_init and of_mixer_init BEFORE checkingSCF1 and
settingCTRL.bit1. Those two kernel initialization functions each writeCTRL=1.
They are the only initialization calls in the Quake2 backend. Later raw-music
rate changes call of_audio_stream_open/configure_stream_voice, and ongoing
writes/free/close and per-voice mixer calls do not writeCTRL. Stop-all writes
CTRL=0 per voice; it does not rewrite globalCTRL. Backend shutdown explicitly
clears cache bit1. Consequently stream/music restart does not disable the
cache. A future client calling global audio_init/mixer_init again must opt in
again; default-off for other clients is intentional.
SFX data is filled once by SDL_Cache and treated as immutable while playing;
slot/base/control writes invalidate reused voices. Mutable streaming uses
voice31 and bypasses. These findings refer to the pinned source files in
audit.json; they are not a binary reverse-engineering proof of arbitrary OSes.

No integration correctness issue remains in these gates. Shared-arbiter
throughput, physical fit/timing and Pocket audio/CDC are separate evidence.
Root's contention matrix retains an explicit worst-case limit for32 stereo
short-loop/up-to2x voices at90MHz with32-cycle memory delay; this test suite
does not erase that bound or claim device FPS.
