# Change request: `CMD_CLEAR_RECT` should carry stride per command

## Status

**Fixed.**  Word 2 of the CMD_CLEAR_RECT payload now carries a 16-bit
stride at bits [31:16] (the slot previously used for `pad`).  When the
stride field is 0, the decoder falls back to the SET_FB-resident
global stride for backwards compatibility — every existing caller that
emits a zeroed pad keeps working bit-identically.  RTL changes in
`src/fpga/common/gpu_core.v` (cr_stride register + S_CLEAR_RECT_WAIT
row-advance mux); SDK helper `of_gpu_clear_rect_strided(addr, w, h,
stride, color)` in `openfpgaOS-SDK/src/sdk/include/of_gpu.h`; the
prior 4-arg `of_gpu_clear_rect()` is retained as a wrapper that
emits stride=0.  Verilator coverage:
`test_clear_rect_per_command_stride` exercises both the per-command
stride path and the legacy fallback.

PocketDukeNukem-SDK's `d3d_gpu_clear_rect_fb` workaround
(an `of_gpu_set_framebuffer()` resync in front of every clear)
has been removed in favour of the strided helper.

## Problem

`CMD_CLEAR_RECT` walks `h` rows × `w` bytes from `start_addr`,
advancing each row by **whatever stride was last set globally via
`CMD_SET_FB`**.  The active stride is GPU-global state, not part of
the clear command payload.

Span dispatch (`CMD_DRAW_SPAN` / `CMD_DRAW_SPANS_BATCH`) already
solved the equivalent problem by carrying `fb_stride` per span — each
span is fully self-describing about which buffer/stride it writes.
Clears didn't get the same treatment, so apps that use multiple
buffers with different strides (BUILD's `setviewtotile`, anything
rendering to off-screen tiles, paletted scratch surfaces, etc.) have
to chase global SET_FB state and reset it before every clear.

In PocketDukeNukem-SDK this manifested as a real bug: BUILD's
`setviewtotile` flips `bytesperline` from 320 (screen) to 160 (tile)
mid-frame.  Vlines (carrying their own stride) handled it fine after
a one-line fix.  Clears didn't — `clearview` issued during tile
rendering walked 320 bytes/row across a tile whose rows are 160
bytes apart, leaving every other tile row holding stale pixels (most
visibly: previous menu content showing through where the clear
should have wiped it).

## Fix

Add a stride word to `CMD_CLEAR_RECT`'s payload:

```
CMD_CLEAR_RECT (header) | start_addr | {w[31:16], h[15:0]} | {pad, color}
                       to:
CMD_CLEAR_RECT (header) | start_addr | {w[31:16], h[15:0]} | stride[31:0] | {pad, color}
```

Or pack `stride` into the existing `{pad, color}` slot if there are
spare bits there (a 16-bit stride covers up to 65535 — plenty for
the 320×240 + tile sizes we run).

The decoder uses the per-command stride for the row advance instead
of the SET_FB-resident value.  Spans already do exactly this for
their own stride; clear should mirror the pattern.

SDK side: extend `of_gpu_clear_rect()` to take a stride argument.
Keep the current 4-arg signature as a thin wrapper that pulls
stride from `CMD_SET_FB`-style state for source compatibility, but
make the strided variant the one apps should call.

## Why not just SET_FB before each clear

That's the band-aid we'd otherwise apply.  Two reasons it's worse:

1. Adds a ring command per clear (header + 2 payload words for SET_FB
   = 12 bytes, vs ~8 bytes saved in a header-only clear command).
2. Leaves the GPU's global stride in an unexpected state after the
   clear; later code that *assumes* a particular global stride breaks
   subtly.  Spans are insulated (they carry their own stride), but
   any future op that uses SET_FB stride globally is exposed.

Per-command stride decouples each clear from FB state and prevents
this whole class of bug.

## Verification

A Duke-side workaround is in place at
`PocketDukeNukem-SDK/src/duke3d/d3d_gpu.c::d3d_gpu_clear_rect_fb`:

```c
of_gpu_set_framebuffer((uint32_t)(uintptr_t)dest, (uint16_t)bytesperline);
of_gpu_clear_rect((uint32_t)(uintptr_t)dest, w, h, color);
```

The two-line resync makes the clear walk the right row spacing for
whatever buffer BUILD currently targets.  Once the per-command
stride lands in the RTL:

1. Drop the `of_gpu_set_framebuffer` line directly above the
   `of_gpu_clear_rect` call (search for "Workaround for
   openfpgaOS/docs/cr-gpu-clear-rect-stride.md").
2. Update the SDK helper signature to
   `of_gpu_clear_rect(addr, w, h, stride, color)` and have d3d_gpu
   pass `bytesperline`.
3. Run Duke3D in low-detail mode (`ud.detail == 0`).  Horizontal
   stripes of stale content where the floor "is missing" should not
   reappear after the workaround is removed.
