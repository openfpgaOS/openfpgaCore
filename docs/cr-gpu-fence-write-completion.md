# Change request: `CMD_FENCE` should wait for `m_wr_*` write completion

## Status

**Open.** Worked around app-side in PocketDukeNukem-SDK (`d3d_gpu.c::d3d_gpu_flush`
adds a `while (GPU_STATUS & GPU_STATUS_BUSY)` poll after `of_gpu_finish()`).
The right place for this is the RTL fence implementation.

## Problem

`GPU_FENCE_REACHED` is updated when the GPU's command processor walks
past the `CMD_FENCE` op.  It does **not** wait for the rasteriser's
pending `m_wr_*` AXI master writes to commit to SDRAM.  So
`of_gpu_finish()` (which spins on `fence_reached`) can return while
the last batch's pixel writes are still draining through the AXI
write fabric.

Concretely, in PocketDukeNukem-SDK we observed:

1. App emits N spans for frame N targeting back buffer FB(A).
2. App calls `of_gpu_finish()` — fence reaches, return.
3. App calls `of_video_flip()` — display starts scanning FB(A).
4. Rasteriser's last few `m_wr_*` beats for frame N now land in FB(A)
   *during* scanout — visible as flashing horizontal artifacts where
   "late" pixels race the display electron-beam equivalent.

The mcause=2 / unmapped-mepc traps we saw earlier had a different
root cause (cache-flush race, fixed by `_gpu_batch_buf` via uncached
SDRAM alias) but the same class of "fence completes before writes
commit" symptom.

## Fix

Hold off on updating `fence_reached` inside `CMD_FENCE` handling until
the rasteriser's outstanding write count is zero — i.e.

```
m_wr_bvalid_count == m_wr_awvalid_count_at_fence
```

(or whatever the equivalent counter is on this fabric).  The fence
op then becomes a true write-side barrier, and `of_gpu_finish()`
alone is sufficient pre-flip ordering.

Equivalently, `GPU_STATUS.busy` could be made to reflect "any write
in flight on m_wr_*", and the SDK's `of_gpu_wait` could be extended
to poll `busy` after `fence_reached`.  But that just moves the SDK
workaround into every app — landing the wait inside `CMD_FENCE` is
strictly cleaner since the fence semantics already promise "all
prior commands complete".

## Verification

After landing, drop the `STATUS_BUSY` poll in
`PocketDukeNukem-SDK/src/duke3d/d3d_gpu.c::d3d_gpu_flush` (search for
"Promote 'fence reached' to 'pipeline fully drained'").  Run E1L1 of
Duke3D for a few minutes — flashing horizontal artifacts at mid-frame
positions should be gone.
