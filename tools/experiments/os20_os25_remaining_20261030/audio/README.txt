OS20/OS25 generic mixer and RAM qualification, 2026-10-30
========================================================

Final decision
--------------
Do not enable the RAM-placement or nonstream-bypass candidates in os20/os25.
Their bounded functional checks passed, but the completed whole-core fits
regressed timing or failed hold checks. Generic memories and audio remain
unchanged. results/physical-decision.json pins the candidate/result files and
records all four-corner outcomes. These recipes are retained for future work.

Purpose
-------
These are self-contained, bounded experiments for the generic full 32-voice
mixer. They change no production source, configuration, SD card or game package.
Frozen fixtures are project-authored source/test files pinned in fixtures.json.
The complete peripheral test also references two already-tracked Analogizer
controller modules, pinned in references.json and checked before use. These
third-party RTL files are not duplicated here. The fixture origin strings are
provenance only; no runner loads ignored historical source/build recipes.
No Intel RAM model, game assets or simulation binaries are included.

Run from the repository root with Python 3, Verilator, make and a C++17 compiler:

  python3 tools/experiments/os20_os25_remaining_20261030/audio/run.py \
    --output build/os20-os25-audio-qualification

The output directory must be new, with no symlink in its path. Logs, build
commands, generated objects and raw PCM remain under that ignored build path.
--suite sources|mixer|wbuf|periph|contention runs a subset. Each check_*.py also
has --help and accepts a new --output directory. Two compile/run jobs are the
default; this does not run Quartus. The full runner performs:
* Four preprocessor combinations: full32/lite16, M10K macro on/off.
* INCLUDE_NONSTREAM_BYPASS compiler-define/source equivalence.
* Source identity of the Pocket controller twin except split-DQ simulation I/O.
* Four full32 mixers: baseline, M10K attribute, bypass, and their combination.
  Each runs 177 inherited checks, 18,432 independent PCM reference samples in
  96 scenarios, 64 IRQ races, 2,048 position reads and 343 capacity cases.
* Registered write-buffer collision poison checks with bank tracking 0 and 1.
* 159 peripheral/MMIO checks including position readback boundaries.
* Eighteen real-controller/arbiter/mixer contention cases: both baseline and
  bypass freshly rebuilt, 9/20/32 voices, quiet/CPU-only/mixed GPU+CPU work.

The bounded summaries in results/ preserve the initial qualification and
package-validation.json records the fresh portable-run verification. Actual
M10K parameter checks for os25-audio-ram s20 and os20-radix4-ram s35 are in
actual-mapping.json. Historical paths identify evidence, not runtime inputs.
Newly generated receipts supersede them for a rerun.

Candidate mechanics
-------------------
INCLUDE_AUDIO_POS_M10K changes only the full32 pos_latch storage attribute.
Its 32x22 synchronous read remains registered, with no extra scheduled cycle.
The lite16 declaration remains untouched. The write buffer keeps its 16x36
registered read and fill/drain schedule; only its instance RAMSTYLE_ATTRIBUTE
changes to M10K. Both require actual Quartus inference/fit checks because
Verilator ignores RAM attributes.

  python3 tools/experiments/os20_os25_remaining_20261030/audio/check_sources.py \
    --prepared-mixer PATH/frozen/src/fpga/common/audio_mixer.v \
    --output build/scoped-audio-source-check

  python3 tools/experiments/os20_os25_remaining_20261030/audio/check_mapping.py \
    PATH/ap_core.map.rpt --output build/mapped-audio-ram.json

The mapping checker requires M10K, expected width/depth, CLOCK0 registered
address, unregistered output and OLD_DATA mixed-port behavior for both inferred
memories. The variant QSF override must win in the actual map report.
The independent selection check can inspect the active checkout or a frozen
candidate without loading any historical build recipes:

  python3 tools/experiments/os20_os25_remaining_20261030/audio/check_selection.py \
    --source-root PATH/TO/ROOT --output build/audio-selection

It requires os20/os25 to select the generic mixer and os30 to retain its
separate source override and pinned shadow source. This check
does not establish routing, timing closure or same source placement superiority.
Do not substitute a saved os30 map for the actual os20/os25 candidate map.

INCLUDE_NONSTREAM_BYPASS already exists in the generic mixer. It skips two
stream bookkeeping states only when the voice is not streaming, saving exactly
two cycles per active ordinary voice per output in the capacity matrix. No new
cache, ABI, clock, voice limit or sample format is introduced. Stream-only
capacity rows must remain byte-identical. This is mixer overhead reduction,
not evidence of faster game rendering.

Observed results and limitations
--------------------------------
M10K-only: inherited mixer output was byte-identical; MMIO passed 159 checks.
Write-buffer schedules passed 491 checks per bank-tracking mode, including
32 poisoned collisions and 335 checked consumptions. This tests schedules,
not an FPGA primitive electrical model. Old full-fit headroom was 80 M10Ks
for os20 and 12 for os25; completed matching-source fits below decide promotion.

Bypass: reference processing cycles 23,557,171 -> 22,762,562 (-3.3731%),
same PCM hash 46e30baca498b56b. Capacity: 329 better, 14 identical stream-only,
zero worse. All 18 contention outputs matched exact quiet-reference PCM and
passed memory protocol checks. 9/20 voices had zero underruns. At 32 voices,
CPU-only overload still underruns 1,217 times and mixed overload 1,510 times
on BOTH sources. The comparison accepts no increased underruns; it does not
claim these overload cases provide correct real-time audio. The worst measured
CPU completion shift was +803/15,296,183 cycles (+0.00525%). Completion deltas
are retained explicitly; there is no measured game-FPS claim.

Rejected broader candidates
---------------------------
Do not enable bank row tracking on os20 just for the normal-case throughput
gain. The fixed-work row-tracking stress reduced CPU time but increased 32-voice
mixed audio underruns from 1,510 to 1,672; retain the failure. Both CPU-only
32-voice variants also overload. See results/rowtrack-rejected.json.

Do not port the full Quake2 compact mixer plus sample cache by default. The
controlled old component result saves 68 estimated ALMs and 401 registers;
it is a different mixer revision, not a controlled current whole-fit gain.
The complete mapped compact mixer plus cache used about 774 ALMs/7 M10Ks,
versus about 781-796 ALMs/4 M10Ks for generic builds (different placements,
so not a guaranteed delta). More importantly, default SDK MIX_CTRL=1 never
enables cache. Cache enable requires immutable nonstream sample lifetime and
has no CPU-store coherence. ScummVM's mutable stream must bypass. Existing
Quake2 tests do not establish all generic MIDI/stream/capacity requirements.
See results/mixer-cache-recommendation.json.

Completed physical qualification
--------------------------------
The following is the same comparison estimate used by the parent experiment:
1000 / (10 - worst setup slack in ns), from the configured 100 MHz/10 ns
period. It is not the clock-specific Quartus Fmax table or a timing guarantee.
All raw setup/hold/recovery/removal/pulse-width checks and hashes are retained
in results/physical-decision.json. Even the controls have negative setup
slack at configured 100 MHz; none of these figures asserts 100 MHz closure.

Candidate                    Seed  Estimate MHz  Worst hold ns  ALMs / M10Ks
os20 radix4 control            35       95.26          +0.042     16468 / 228
os20 radix4 + RAM              35       92.26          +0.036     16598 / 230
os20 radix4 + bypass           35       91.94          -0.338     16484 / 228
os20 radix4 + bypass           34       94.25          -0.104     16405 / 228
os25 control                   20       96.87          +0.078     15490 / 296
os25 fetch control             20       96.40          +0.006     15577 / 296
os25 fetch + RAM               20       93.44          +0.006     15372 / 298
os25 bypass                    20       94.19          +0.036     15435 / 296
os25 bypass + RAM              20       92.70          +0.069     15468 / 298
os25 bypass + clamp pipeline   20       90.59          +0.012     15481 / 296

M10K inference itself passed, but the resulting placement/routing regressed
whole-core timing. The mixer bypass's measured component cycle saving also
failed to yield an acceptable physical replacement. Both os20 bypass seeds
failed hold timing. Small ALM savings on some candidates do not meet the
requirement to preserve performance. No audio/RAM candidate is promoted.

This is a targeted-fit rejection, not a claim that every possible seed or
future implementation must fail. The independent os25 clamp-only experiment
is outside this audio decision; the bypass+clamp result must not decide it.
No audio/RAM optimization from this package is pending deployment.
