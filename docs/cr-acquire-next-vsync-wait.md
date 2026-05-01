# Change request: `of_video_acquire_next` doesn't wait for vsync

## Status

**Open.**  Symptom is fully reproducible on `gpudemo` modes 1 / 2 (not
freezing, but visibly tearing), and the rate counters hit ~330–380 fps
with the VRR target at 60 Hz — proof the function is returning before
the queued swap has actually completed.  No RTL change required; this
is entirely a kernel-side fix in `firmware/os/targets/pocket/video.c`.

## Problem

`of_video_acquire_next(just_flipped_idx, fence_token)` is the new
GPU-triggered-flip acquire path (per `cr-gpu-triggered-flip.md`).  Its
public contract — both in the kernel comment and in the SDK header
(`firmware/api/of_video.h`) — is:

> The kernel waits for `fence_reached >= fence_token` (proves CMD_FLIP
> retired and the slave latched `fb_swap_pending=1`) **and then for
> `fb_swap_pending` to clear (proves the vsync swap completed)**, then
> returns the next free draw idx.

In practice, only the first wait exists.  Looking at the current
implementation in `firmware/os/targets/pocket/video.c:189-221`:

```c
int of_video_acquire_next(int just_flipped_idx, uint32_t fence_token) {
    vrr_update();

    if (just_flipped_idx < 0) {
        return buf_draw;
    }

    /* Bounded fence-wait fallback */
    {
        uint32_t spins = 500000u;
        while ((int32_t)(GPU_FENCE_REACHED_REG - fence_token) < 0) {
            if (--spins == 0) break;
        }
    }

    /* Idempotent FB_SWAP_CTRL write */
    FB_SWAP_CTRL = ((uint32_t)(just_flipped_idx & 0x3) << 1) | 1;

    buf_display = just_flipped_idx;          /* <-- premature */
    buf_ready = -1;
    swap_kicked = 0;
    buf_draw = (buf_display + 1) % 3;
    return buf_draw;                          /* returns to app */
}
```

After the `FB_SWAP_CTRL` write, `fb_swap_pending` is high and the
hardware will only consume the swap on the next vsync edge — anywhere
from ~0 µs to ~16.7 ms (at 60 Hz) away.  The function does not wait
for that edge; it updates `buf_display` immediately and returns.

Consequence: the app gets back the buffer that is *about to be
scanned out at the next vsync*.  It writes its next frame's pixels
into that buffer while scanout is already reading from it on the
following frame (or, at high frame rates, on the same frame).
That's the tearing.

The CPU-side `of_video_flip_wait()` and `of_video_vsync()` already do
the correct wait against `FB_SWAP_CTRL & 1` at lines 234-237 — the
GPU-triggered path just dropped that wait when it was written.

## Reproducer

`src/apps/gpudemo` on the current bitstream:

- Mode 0 (maze, ~800 spans/frame, GPU-bound): runs ~80 fps, no tearing
  visible because each frame's draw takes long enough to span a vsync.
- Mode 1 (perspective triangle, GPU-light): **~340 fps**, heavy tearing.
- Mode 2 (cube): **~340 fps**, heavy tearing.

Both fps numbers are 5–6× the panel rate.  The app calls `of_gpu_flip_to`
+ `of_gpu_kick` + `of_video_acquire_next` per frame; with the current
acquire implementation, none of those steps blocks long enough to lock
to vsync, so the loop free-runs at GPU + CPU rate.

## Fix

Restore the missing `fb_swap_pending` wait at the end of
`of_video_acquire_next`, mirroring the existing pattern in
`of_video_wait_flip` (line 175-180) and `of_video_vsync` (line 234-237):

```c
int of_video_acquire_next(int just_flipped_idx, uint32_t fence_token) {
    vrr_update();

    if (just_flipped_idx < 0) {
        return buf_draw;
    }

    /* Wait for CMD_FLIP's fence to retire — proves the GPU has
     * finished its m_wr drain and the slave saw the swap pulse. */
    {
        uint32_t spins = 500000u;            /* ~5 ms @ 100 MHz */
        while ((int32_t)(GPU_FENCE_REACHED_REG - fence_token) < 0) {
            if (--spins == 0) break;
        }
    }

    /* Idempotent fallback in case CMD_FLIP didn't fire (e.g. timeout
     * above): writing FB_SWAP_CTRL queues the swap for next vsync. */
    FB_SWAP_CTRL = ((uint32_t)(just_flipped_idx & 0x3) << 1) | 1;

    /* NEW: wait for the queued swap to complete on the vsync edge.
     * One vsync at the slowest VRR rate (~42 Hz) is ~24 ms, so we
     * give it 2 frames' worth of margin before declaring vsync gone.
     * Once fb_swap_pending clears, scanout has actually committed
     * the new buf_display — only THEN is it safe for the app to
     * write into the buffer we're about to hand back. */
    {
        uint64_t deadline = read_cycles() + (CPU_FREQ_HZ / 20);  /* 50 ms */
        while (FB_SWAP_CTRL & 1) {
            if (read_cycles() > deadline) break;
        }
    }

    buf_display = just_flipped_idx;
    buf_ready = -1;
    swap_kicked = 0;
    buf_draw = (buf_display + 1) % 3;
    return buf_draw;
}
```

Three things to highlight:

1. **Deadline choice: 50 ms = 2 frames at 40 Hz.**  Slightly bigger
   than the 33 ms used by `of_video_wait_flip` (which was sized for
   60 Hz) because VRR can drop to ~42 Hz under load, and a 1× period
   deadline can race the actual vsync.  Still small enough that a
   genuinely missing vsync (panel disconnected, scanout halted)
   degrades gracefully to a ~50 ms-per-frame freerun rather than
   freezing.

2. **`buf_display` update stays after the wait.**  Until the swap
   actually completes, the buffer at `just_flipped_idx` is still
   being read by scanout, so `buf_display` shouldn't advance to it.
   Updating after `fb_swap_pending` clears keeps software state in
   sync with hardware state.

3. **Fence wait stays as-is.**  It's a separate concern (proves GPU
   m_wr has drained); both waits are needed.  Order is
   `fence → swap-pending` because CMD_FLIP only sets `fb_swap_pending`
   *after* publishing `fence_reached`.

## Verification

After the fix, on `gpudemo`:

- All four modes should run at exactly the VRR target (~60 fps for
  panel-locked rendering, lower under load).  No rates over 60.
- No visible tearing in any mode.
- Mode 0 fps should be roughly unchanged (it was already vsync-bound
  via the GPU's own work duration); modes 1/2/3 should drop from
  ~340 fps to ~60 fps.

Optional regression: add a printf-based fps assertion in `gpudemo`
that fails if the per-second `fps_x10` exceeds `(VRR_HZ_MAX + 5) * 10`.
That catches future regressions that re-introduce freerunning.

## Workaround until fixed

Apps that need vsync pacing today can call `of_video_wait_flip()`
explicitly after `of_video_acquire_next()`:

```c
flip_token = of_gpu_flip_to(draw_idx);
of_gpu_kick();
draw_idx = of_video_acquire_next(draw_idx, flip_token);
of_video_wait_flip();   /* workaround until cr-acquire-next-vsync-wait lands */
```

Cost: one extra ecall per frame (~50 ns).  Once the kernel fix lands,
the explicit wait becomes redundant and can be removed.

## Estimated effort

- Kernel patch + build: **15 min** (~10 lines of C, no new dependencies).
- Re-flash + verify on hardware: **10 min**.
- Total: **30 min**.
