# OS Cleanup Plan

Findings from the structural review of boot/kernel/syscall + services/HAL/target,
ordered by impact and prerequisite chain. Each item lists what to change, where,
how to verify, and the risk of regressing other code.

## Phase 1 — Real bugs

### 1. Audio ring: switch to uncached alias, drop cbo.clean
- **Where:** `src/firmware/os/targets/pocket/audio.c:29, 65, 109`
- **What:** `audio_ring = (int16_t *)SAMPLE_POOL_BASE` puts the ring at `0x13700000`
  (cached alias). `of_audio_init` and `of_audio_write` call
  `of_cache_clean_range`. Both are wrong for this hot path:
  - `cbo.clean` is unreliable on this VexiiRiscv config (existing memory note);
    only `cbo.flush` is, and even that doesn't guarantee writes have reached
    SDRAM by the time the mixer reads them sub-ms later.
  - `cache.c:91-94` already documents the fix: write through the **uncached**
    SDRAM alias from the start so each store stalls on its AXI B-response.
- **Change:** add an uncached alias of `OF_TARGET_SAMPLE_BASE` to
  `target_platform.h` (or per-target header), point `audio_ring` at it, delete
  both `of_cache_clean_range` calls.
- **Verify:**
  - Run an audio stream test; confirm no glitches at high voice counts.
  - Check Duke3D / any music app for the historical crackle pattern.
  - Confirm mixer SFX uploads (which use `of_cache_flush_range` on the cached
    alias) still work — those are not in the hot path and are correct as-is.
- **Risk:** low. Only `audio_ring` moves; the rest of `SAMPLE_POOL_BASE` (used
  by mixer for SFX upload) stays cached. No app-visible API change.

### 2 + 3. Save close/truncate: surface HAL failures
- **Where:** `src/firmware/os/kernel/syscall.c:691-714` (open with O_TRUNC),
  `:832-849` (close).
- **What:**
  - O_TRUNC path sets `f->size = 0; f->dirty = 1` *before* the HAL call, then
    ignores `of_save_set_size` failure. A later `close` re-flushes through the
    broken path.
  - `sys_close` on a save fd unconditionally marks the fd closed and updates
    the size cache even if `of_save_flush_size` returned an error.
- **Change:**
  - In O_TRUNC: only mark dirty after `of_save_set_size` succeeds; on failure
    return `-EIO` from `open` (or whatever the contract says).
  - In `sys_close`: propagate flush failure as the close return value; do not
    update the size cache on failure.
- **Verify:**
  - Force a HAL failure (test stub) and confirm `open`/`close` return errors.
  - Run the existing save tests in `tools/` if any.
  - **Likely investigates the post-PSRAM save regression.** Re-test on hardware
    after this lands.
- **Risk:** medium. Apps may not have been checking `close()` return values;
  surfacing failures could change observable behavior. Worth checking
  PocketDuke-SDK + Quake save paths.

### 4. setitimer: validate `a2` (old itimerval pointer)
- **Where:** `src/firmware/os/kernel/syscall.c:1425`
- **What:** unconditionally writes 16 bytes to user-supplied pointer if
  non-NULL. App can pass a kernel/MMIO address.
- **Change:** add a bounds check against the app's writable region (same kind
  of check used elsewhere for user pointers — find the helper or add one).
- **Verify:** unit test with a deliberately bad pointer; expect `-EFAULT`.
- **Risk:** low.

## Phase 2 — Brittle, easy wins

### 5. Mixer alloc_voice: respect SCRATCH (voice 31) in steal path
- **Where:** `src/firmware/os/hal/mixer.c:164-225`
- **What:** `alloc_voice_grouped` excludes voice 31; basic `alloc_voice` does
  not. Under heavy SFX load (30 voices busy) `of_mixer_play` will steal the
  audio-streaming voice.
- **Change:** mirror the `MIXER_SCRATCH_VOICE` exclusion into the basic
  `alloc_voice` steal loop.
- **Verify:** stress test with 32+ concurrent SFX while audio stream is active
  (or just inspect the loop); confirm voice 31 is never picked.
- **Risk:** very low. Trims the candidate set by one.

### 6. ELF loader: log unsupported reloc types instead of silently skipping
- **Where:** `src/firmware/os/kernel/loader.c:312-344`
- **What:** loop only handles `R_RISCV_RELATIVE`; anything else falls through
  with no diagnostic.
- **Change:** on unknown reloc type, print to UART (loader-stage trap log) and
  fail the load with a clear error code.
- **Verify:** synthesize a test ELF with a bogus reloc, confirm clean failure.
- **Risk:** low. Only changes behavior for malformed inputs that already
  produce broken apps.

### 7. Misaligned trap counter: don't cap at 5
- **Where:** `src/firmware/os/kernel/misaligned.c:168-172`
- **What:** stops printing after 5 traps, masking pathological misaligned
  patterns.
- **Change:** keep counting; emit a one-line summary when the count crosses
  thresholds (10, 100, 1000, …) instead of suppressing.
- **Verify:** trace under a known-misaligned workload.
- **Risk:** none — purely diagnostic.

## Phase 3 — Contract / API hygiene

### 8. Timer Hz parameter: pick a side
- **Where:** `src/firmware/os/kernel/services_table.c:56-72`,
  `src/firmware/api/of_*.h` declaration.
- **What:** `svc_timer_set_callback(cb, hz)` ignores `hz` and pins to 1 kHz.
  SDK MIDI tables require 1 kHz, but the silently-ignored parameter is a
  contract violation.
- **Options:**
  - **A:** drop the `hz` parameter; document that the timer is fixed-rate.
    Requires bumping the services table version or appending a new slot.
  - **B:** validate `hz`: accept only 1000 (or N divisors of 1000), return
    `-EINVAL` otherwise.
- **Recommendation:** Option B is non-breaking and surfaces misuse.
- **Risk:** medium (API touch). Sweep all callers (Quake, Duke, gpudemo, SDK)
  before changing.

### 9. Save CRC scope: document or shrink
- **Where:** `src/firmware/os/targets/pocket/save.c:159`
- **What:** CRC covers the full 256 KB slot regardless of logical save size,
  so corruption in the unused tail flips the CRC even though no app data
  changed.
- **Options:**
  - **A:** keep current behavior, add a header comment + memory note
    explaining the trade-off (it's a save-slot integrity check, not a
    save-data integrity check).
  - **B:** change CRC to cover only the logical size (read from save metadata
    header). Requires bumping the save format version.
- **Recommendation:** A unless we're already touching the save format for the
  PSRAM regression fix.
- **Risk:** B is a save-format break; A is just a doc change.

### 10. caps_table comment: stop saying "CRAM0"
- **Where:** `src/firmware/os/kernel/caps_table.c:1-76`
- **What:** comment claims caps_table lives in CRAM0; the linker actually puts
  it in OSDATA/SDRAM. Stale from the v1 architecture.
- **Change:** one-line comment fix.
- **Risk:** none.

## Phase 4 — Defensive cleanup (only if touching the area)

These are brittle but currently working. Don't pull on these threads in
isolation; address opportunistically when in the area.

- **Double `syscall_init`** (`kernel/main.c:106, 143`) — the second call
  resets `fd_table`; safe today only because nothing opens files between OS
  init and app load. If you ever add OS-side file opens, this fires.
- **I/O cache hard-coded layout** (`syscall.c:81-91`) — sandwiched between
  `TERM_FB` and `INTERACT_BASE` with no compile-time overlap check. Add a
  `_Static_assert` or move the layout into `target_platform.h` next to the
  other addresses.
- **Video buffer state** (`targets/pocket/video.c:19-26`) — `of_video_flip`
  before vsync silently drops the queued buffer. Document or return an error.
- **`awe_voice_t` opaque type** (`api/of_services.h:34`) — forward-declared,
  never defined. Either define it (so apps can sizeof) or document why it's
  intentionally opaque.
- **Dead `play_counter_diag`** (`hal/mixer.c:236`) — increments a counter
  that's never exported. Delete.

## What I'm explicitly not touching

These were inspected and look correct as-is:

- Boot chain (chip32 loader → bootloader → kernel handoff, stack/BSS init,
  .data copy).
- Trap handler register save/restore in `start.S`.
- `auxv` setup (AT_OF_SVC, AT_OF_CAPS, AT_PAGESZ).
- Services table population (every slot is non-NULL by app launch).
- Linker assertions (BRAM fasttext+fastdata fit, OSDATA fits).
- ELF validation (magic, arch, PT_LOAD vs APP_VMAP_V1).
- mmap/brk strict-inequality isolation.

## Suggested order of work

1. **Phase 1.1 (audio cache)** — fastest visible win, low risk.
2. **Phase 1.2/1.3 (save close/truncate)** — most likely root of post-PSRAM
   save bug. Pair with hardware test.
3. **Phase 1.4 (setitimer pointer)** — quick safety fix.
4. **Phase 2** — small, parallelizable.
5. **Phase 3** — needs design discussion before code.
6. **Phase 4** — only if you're already in those files.
