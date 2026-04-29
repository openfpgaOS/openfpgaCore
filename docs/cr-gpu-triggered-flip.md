# Change request: GPU-triggered display flip (`CMD_FLIP`)

## Status

**Fixed.**  Landed in a single combined RTL change that also closes
`cr-gpu-fence-write-completion.md` (the two share the same
`m_wr_inflight` drain primitive in `gpu_core.v`).  Implementation
notes:

- New CMD_FLIP opcode `0x42` with 2-word payload `{idx[1:0],
  fence_token}`.  CMD_FENCE was upgraded at the same time to use
  the same drain wait — both stall in S_EXECUTE while
  `m_wr_inflight != 0`, then publish `fence_reached` (and CMD_FLIP
  additionally pulses the swap side-port).
- API design choice: 2-bit index into the existing fb_ready_idx
  mux (FB_ADDR_{0,1,2}) instead of a full base address — keeps the
  RTL minimal and matches the existing kernel sysreg path.
- GPU → swap register: dedicated side-port (`gpu_swap_req`,
  `gpu_swap_idx[1:0]`) from `gpu_core` into `axi_periph_slave`,
  not AXI MMIO.  Single-cycle pulse, ~10 ALMs, no `m_wr_*`
  bandwidth cost.  GPU has priority on the rare same-cycle
  conflict (later non-blocking write wins).
- Buffer count: 3 (no growth).  `of_video_acquire_next()` blocks
  if a previous flip is still pending — caps the CPU at exactly
  one frame ahead of the display.
- Fence token in the payload was made mandatory in v1 (not
  optional) — apps poll completion via the same
  `of_gpu_fence_reached()` they use today.

Verification: tb_gpu's `test_cmd_fence_drain` and
`test_cmd_flip_drain_and_pulse` confirm the drain ordering and
exactly-one-cycle pulse contract.  PocketDukeNukem-SDK's
`_nextpage()` was ported to the new path; the `STATUS_BUSY`
workaround poll in `d3d_gpu_flush()` has been removed.

## Problem

Today the page-flip pipeline is CPU-driven and serial:

```
CPU: render frame N
CPU: d3d_gpu_flush()              — drain GPU writes (≈274 µs spin)
CPU: of_video_flip()              — kernel call queues swap (≈79 µs)
CPU: retarget back buffer
CPU: render frame N+1 ...
```

The two CPU-side waits (`d3d_gpu_flush` + `of_video_flip`) cost ~350 µs
per frame on the duke3d core today.  More importantly they're a hard
serialization point: the CPU cannot start the next frame's work until
both the GPU and the kernel have acknowledged the current frame.

The architectural reason for the wait is correctness, not lack of
information: each span carries its own `fb_addr`, so the GPU knows
exactly which back buffer it's writing.  But if the CPU queues the
display flip while the GPU's `m_wr_*` writes for that buffer are
still in flight, the display will scan garbage pixels mid-frame.
The CPU spin in `gpu_finish` is purely defensive against that race.

## Fix

Add a `CMD_FLIP` command to the GPU's command set.  Semantics:

1. Stall the command processor until all prior `m_wr_*` writes to
   the target buffer have committed (same drain semantics as the
   already-landed `CMD_FENCE` fix).
2. Write the target buffer's base address into the display
   controller's `next_present` register.
3. The display controller picks it up at the next vsync edge and
   swaps scanout — exactly as it does today when the kernel writes
   that register from `of_video_flip()`.

The CPU side becomes:

```
CPU: render frame N
CPU: d3d_gpu_flush_batch()        — ship pending span_buf to GPU ring
CPU: of_gpu_flip_to(slot_C)       — appends CMD_FLIP(slot_C) to ring
CPU: of_video_acquire_next()      — non-blocking; returns next free back buffer
CPU: retarget back buffer
CPU: render frame N+1 ...
```

No CPU-side spin on `GPU_STATUS_BUSY`, no kernel `of_video_flip` call.
The CPU is always one frame ahead of the GPU; the GPU drains and flips
asynchronously.

## API

### RTL

- New command `CMD_FLIP` (suggested opcode `0x42`).  Payload: 1 word =
  buffer base address.  Decoder: stall on outstanding `m_wr_*`, then
  write the address to the display controller's swap register.
- Optional refinement: a 2-word payload that also carries a fence
  token so the SDK can poll completion the same way it polls fences
  today.

### Display controller

- Already has a `next_present` register written by the kernel today.
  Expose the same register to the GPU's MMIO bus, OR add a side-port
  the GPU writes through.  Arbitrate writes if both kernel and GPU
  may write (kernel still owns init / mode-set).

### Kernel

- New service: `of_video_acquire_next()` returns the address of the
  next free back buffer without queuing a flip.  CPU uses it to
  retarget rendering.  Internal book-keeping advances which slot is
  "free", "queued via GPU", "displayed".
- Existing `of_video_flip()` stays as the CPU-driven path for apps
  that don't use the GPU dispatch helpers.

### SDK

```c
/* Append a CMD_FLIP to the GPU command ring.  GPU drains its m_wr_*
 * writes for buffer_addr first, then triggers a page swap to that
 * buffer at the next vsync.  Non-blocking — returns immediately. */
static inline void of_gpu_flip_to(uint32_t buffer_addr);
```

Plus optionally:

```c
/* Returns the buffer the next CMD_FLIP will swap to (queried for
 * book-keeping).  Same as of_video_acquire_next() but in the GPU
 * helper namespace. */
uint32_t of_gpu_pending_flip_buffer(void);
```

## Savings

Per-frame, on the duke3d core (33 ms frame today):

- `gpu_fin = 274 µs` (the spin in `d3d_gpu_flush`'s `STATUS_BUSY` poll)
  → drops to ~0; the wait moves into the GPU command processor where
  it overlaps with CPU work for the next frame.
- `vid_flip = 79 µs` → drops to ~10 µs (just the cheap
  `acquire_next()` book-keeping call).

Direct CPU savings: ~340 µs/frame, ≈1% at 30 fps.

Indirect savings unlock follow-up wins:

- Multiple frames can be in flight concurrently — currently each
  frame's CPU work is gated on the previous frame's GPU drain.
  With this CR, CPU is always 1 frame ahead.
- Removes the architectural reason for the `STATUS_BUSY` workaround
  in `d3d_gpu_flush`; the wait moves into RTL where it belongs.
- Latency-vs-throughput trade: input-to-photon latency grows by ~1
  frame (CPU is ahead of display by one extra step), but throughput
  improves and frame-pacing variance drops.

## Verification

1. SDK helper compiles, links, runs in `gpudemo` without changing
   visual output.
2. Duke3D `_nextpage()` ported to the new API renders identically
   to the current path.  The `STATUS_BUSY` poll in
   `d3d_gpu_flush` can be removed; if any flashing-pixel artifacts
   reappear, `CMD_FLIP`'s drain semantics aren't tight enough.
3. `[perf-flip]` line should show `gpu_fin` near 0 and total `flip`
   close to `np_perm_pre + np_perm_post + np_video_flip` (the
   non-GPU permfifo + kernel call costs only).
4. Stress: kill GPU enable mid-CMD_FLIP — display should freeze on
   the last good frame, not show garbage.

## Why not just rely on `of_gpu_finish()` as today?

`of_gpu_finish` is a CPU spin.  Even if the spin is short (~140 µs)
it's pure CPU stall — no useful work happens during it.  A
GPU-triggered flip moves the wait from CPU spin to a GPU pipeline
stage that overlaps with the *next* frame's CPU work.  Same ordering
guarantee, free CPU cycles.

It also makes pipelining tractable: today every frame is gated on
"GPU finishes everything for this frame" before the CPU even thinks
about the next.  With CMD_FLIP, multiple frames can be in flight
and each one self-paces against vsync via its own CMD_FLIP.

## Order of operations (summary)

1. Land `CMD_FLIP` in RTL (with `m_wr_*` drain semantics).
2. Expose display swap register on GPU MMIO bus.
3. Add kernel `of_video_acquire_next()` non-blocking acquire.
4. Add SDK `of_gpu_flip_to()` helper.
5. Port duke3d `_nextpage()` to use the new path; drop the
   `STATUS_BUSY` poll workaround in `d3d_gpu_flush`.
6. Verify on hardware (E1L1 + perf-flip line + stress kill).
