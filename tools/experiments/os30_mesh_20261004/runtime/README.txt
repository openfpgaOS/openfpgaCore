Private mesh application transport and complete-frame timing model
================================================================

This folder never deploys a core or replaces the installed SM64 directory.
stage.py modifies only docs/measurements/os30-mesh-20261004/SM64.

mesh_transport.h publishes immutable raw vertex/index pools and four fenced
mutable record pages. A successful whole-job submission invalidates the SDK
span-state memo and marks the exact submitted texture slot's epoch. The private
mesh_tri_state.h emits ordinary 0x4A state even though removed legacy triangle
capability bits are clear; projected fallback still uses the ordered mesh job
queue. The actual renderer callback lives in mesh_state_changed.h and is shared
with the host lifetime test.

Reviewed app snapshot runtime-reviewed-v3.elf:
473ff9ed890ce6578524c74fd4ee1fded163a699eaa6d52cd021509b9ef012ae
The subsequent source-only fog-toggle fix preserves vertex LOAD-time fog
configuration until DRAW; check_fog_state.py tests the real material helper.
That change is not in the frozen v3 binary; a rebuilt capture must validate it
before any future deployment.

Reproducible full-frame comparison:

  python3 runtime/build_coupled.py --label coupled-v1
  python3 runtime/run_coupled.py mesh-v3-360-t1 --elf <runtime-reviewed-v3.elf>
  python3 runtime/run_coupled.py baseline-p4-360-t1 --baseline --elf <attributes-p4.elf>
  python3 runtime/build_coupled_cache.py --base coupled-v1 --label coupled-v1-dc64
  python3 runtime/run_coupled.py mesh-v3-dc64-360-t1 --build coupled-v1-dc64 \
      --dcache-kib 64 --elf <runtime-reviewed-v3.elf>
  python3 runtime/analyze_coupled.py

Run from tools/experiments/os30_mesh_20261004. Each label creates a new output
directory; old executables, inputs, logs and source hashes remain unchanged.
The baseline is the untouched attributes-v6 RTL executable and attributes-p4
ELF, not an older worker generation. Hardware settings: 1024-word output FIFO,
two 256-word instruction pages; default CPU model32KiB I$/128KiB D$. The cache
sensitivity relinks exactly the original frozen RTL, changing only estimated
CPU D-cache sets1024->512 before warmup, and preserves the128KiB executable.

At the warmup handoff the coupling restores the ELF's uploaded microprogram,
GPU settings, fence token and ring-pointer translation, then switches the CPU
to the physical SDRAM memory image. GPU MMIO/backpressure, DMA, mesh arithmetic,
rasterization, fences, SDRAM protocol/timing and60Hz presentation execute RTL.
Game updates remain one fixed step per submitted frame so scenes are identical.
CPU cache/instruction timing is estimated. External memory traffic is synthetic;
CPU memory-stall feedback, kernel interrupts and PCM mixing cost are not modeled.
These results are neither CPU RTL results nor Pocket FPS measurements.

Frozen pre-optimization v3 results, frames360..367, traffic schedule1:
  attributes-v6/P4:        36.07371ms mean submission interval
  mesh128KiB:              40.61932ms (11.19% less throughput)
  same mesh64KiB:          40.63919ms (0.049% greater frame time)
The cache change preserves all8 complete color/depth frames. The coupled mesh
frames also exactly match the independent complete GPU capture replay.
Compared with production, however,7013..8668 of76800 color pixels differ and
depth differs too. This prototype is not qualified for deployment.
See docs/measurements/os30-mesh-20261004/coupled-comparison-v1.json.

Corrected hybrid v3, using the optimized410-word engine,64KiB CPU D-cache,
M10K texture queues and the same8 frames/traffic schedule, now matches EVERY
production color and depth byte. Original guardband eligibility, fixed lazy
recovery and cached fallback normalization are all required for this equality.
Its38.64558ms mean interval is6.65% less throughput than the36.07371ms baseline,
so correctness does not establish a performance gain. This is not a deployment
candidate. Shader/material cache optimizations are measured in later frozen
ELFs without changing the hardware model.
See coupled-comparison-hybrid-v3.json; compare_coupled.py independently verifies
manifest hashes, scene equality, complete framebuffer bytes and profile deltas.

Producer coverage audit (separate diagnostic ELF, never a timing candidate):
runtime-coverage-v1 counts frames360..367 before face culling. It submits15810
faces in1719jobs;608 accepted-load fallback faces are272decals and336texture-
alpha blends.4006 further fallback faces use nonaccepted loads and reference
zero accepted slots. There are no parser, mixed-load, capacity, LOD or shade
rejections.3856 CPU-recovered vertices include2848 recovered only to preserve
surviving slots across partial LOADs; actual fallback faces request1008. The
context-pool experiment targets that avoidable preservation work.

Current material exclusions preserve exact rendering. Decals require float32
depth*1.001 followed by per-face max(|dzdx|,|dzdy|)*0.5 depth bias. Existing mesh
state/PROJA has no equivalent operation. Texture-alpha interpolation can emit
two ordered passes (complementary shade mask then white texel), while the mesh
job currently owns one material/pass. Both original flattened paths additionally
use continuous per-face zi normalization rather than coarse cached zi followed
by power-of-two normalization. Simply removing eligibility checks changes
texture sampling or depth. No material eligibility changes were made.

Final checkpoint selects hybrid-reviewed-v5.elf:35.92107ms versus36.07371ms
baseline (+0.425% modeled throughput, not a substantial game improvement).
Every v3..v9 candidate produces identical complete color/depth for all8frames.
Retained-context variants v7/v8/v9 measured36.54517/36.36945/36.20471ms and
were not retained in normal private source. Their shaders/tests remain under
kernel/retained_contexts. Normal source was restored to v5, then rebuilt to the
EXACT frozen ELF SHA9c4f2ccb1fab13381b33af343b5df59000e7efab4cd9de1078817cf33fd43276.
performance-checkpoint-v1.json records all paired versions; the separate
selected-v5-source-manifest.json hashes3892 dependency/build/library inputs.
Nothing was deployed and no seed sweep was run for this experiment.

analyze_reuse.py found zero exact shader/geometry job repeats within/across
those8 frames. All-inside jobs would permit18192 unique projections instead of
35832 face projections, but caching/extra transfer overhead is not implemented
and the theoretical saving is only1.72ms/frame at the old78cycle projection.

Host lifetime validation (from repository root):
  cc -std=c11 -O2 -g -Wall -Wextra -no-pie -fsanitize=address,undefined \
      -fno-omit-frame-pointer -Itools/experiments/os30_mesh_20261004/runtime \
      tools/experiments/os30_mesh_20261004/runtime/test_transport.c -o /tmp/mesh_transport_test
  ASAN_OPTIONS=detect_leaks=0 /tmp/mesh_transport_test
