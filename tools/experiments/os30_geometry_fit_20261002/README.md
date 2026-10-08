# os30 geometry footprint screen

The existing fixed-point transform and lighting hardware fits on the Pocket
alongside the current os30 core. This is a footprint experiment, not an
integrated SM64 accelerator or a measured game speedup.

Four structural candidates used the saved os30 seed **2**, Quartus 25.1std,
and the existing **100/100/100 MHz** CPU/GPU/SDRAM configuration. The full seed
sweep remains deferred. No production definitions, CPU generation, SM64
sources, deployment files or clocks were changed by this experiment.

## Measured fits

| Configuration | ALMs / 18,480 | M10Ks / 308 | DSPs / 66 | Minimum main-clock Fmax | Worst setup slack |
|---|---:|---:|---:|---:|---:|
| Current os30 baseline | 16,993 | 306 | 41 | 91.73 MHz | -0.902 ns |
| Transform; coordinates in RAM | 18,039 | 307 | 41 | 90.29 MHz | -1.076 ns |
| Transform; coordinates in registers | 18,094 | 304 | 41 | 89.61 MHz | -1.159 ns |
| Transform + lighting; coordinates in RAM | 18,162 | 307 | 41 | 91.28 MHz | -0.955 ns |
| Transform + lighting; coordinates in registers | 18,194* | 305* | 41* | Fit failed | — |

`*` Resource estimates from the failed fit. It requires 1,852 LABs; the device
has 1,848. The successful transform/light RAM configuration uses all 1,848
LABs: its nominal 318 unused ALMs are **not** guaranteed placement headroom.
It leaves one M10K and 25 DSP blocks. No configuration meets 100 MHz timing.

The combined RAM configuration adds **1,169 ALMs and one M10K**, with no
additional DSP blocks. The transform and lighting use the GPU's existing
multiplier, accumulator and divider; projection and the 32-slot vertex cache
are already present. Final RAM totals are measured fitter results, not a sum
of declared arrays: synthesis and packing change with the configuration.

These are single-seed results. The better timing of the larger lighting build
does not establish that lighting improves timing. Its worst reported path is
in the audio mixer (`S_WR_VOL_RAMP` to `nxt_l_r[5]`). The transform-only RAM
build's worst path is in CPU load/operand forwarding; the register variant's
is between the GPU render cache and the SDRAM arbiter.

The saved baseline comes from `os30-memory-opt-20261002/fit-clamp-q16-zdecode`.
Its CPU hash and resolved feature definitions match current normal os30.
All other source hashes match except comments/macro guards in the GPU and
arbiter and the variant Makefile; preprocessing confirms identical normal
GPU/arbiter tokens. Depth prefetch remains disabled for every candidate.

## What this establishes

Reusing the existing geometry path is a credible way to fit an accelerator.
Keep its matrix and temporary coordinate storage in RAM and reuse the command
ring and vertex cache. Moving the tiny coordinate arrays to registers frees
RAM but worsens logic packing here, enough to make the lighting build fail.

The next useful specialization is a batched **cached-vertex** path. SM64 can
then submit raw vertices and object state once, followed by indexed triangles.
Removing the restored direct whole-triangle matrix commands (0x51/0x52) is a
candidate for recovering logic before adding the SM64-specific interface.
That pruning and a batched interface have **not** been implemented or fitted
in this screen, so their savings are not claimed.

The measured engine shares the main GPU state machine with rasterization.
This is not evidence that an independent, concurrently running floating-point
geometry processor also fits. Its arithmetic and queues would need a separate
fit. The available DSP count alone cannot establish that.

## SM64 work still required

Simply restoring the old commands does not remove CPU geometry work. The
captured entrance frame has 1,484 clip-space vertex uploads (0x56), 854 indexed
triangles (0x54), and 332 fallback triangles (0x4E). The current renderer has
already transformed and lit those vertices before submission.

The legacy engine is not a drop-in replacement for that renderer:

- Matrix arithmetic is Q16.16, while SM64 currently transforms homogeneous
  x/y/z/w in floating point. The existing matrix path computes the projection
  triple x/y/w; CPU clipping also needs z and clip codes.
- Lighting implements one directional light plus ambient in RGB565. SM64
  computes 8-bit channels, supports multiple lights and texture generation,
  and applies material/combiner rules later.
- Matrix cache loads store divider-derived depth; the current clip-space
  command supplies explicit higher-precision depth. Mixing those scales would
  break depth ordering and repeat the distant-depth precision problem.
- CPU-side clipping, culling and fallback need a coherent design. Retaining
  all CPU transforms to obtain clipping inputs would erase much of the gain.

An integration should preserve these contracts, batch work to control CPU
submission cost, then compare actual Bob-omb Battlefield frames and CPU/GPU
work. A 30% throughput gain has not been demonstrated by these fits.

## Validation and reproduction

The RAM and register versions with lighting each pass **97 acceptance checks
under three memory schedules**. The RAM version additionally passes six
focused framebuffer/depth comparisons under each schedule: **600 successful
assertions in total**. The focused check calculates fixed-point lighting on
the CPU and compares lit cache loads with unlit loads carrying the expected
colors, including negative/fractional/saturated dot products, colored ambient
light and channel saturation. It checks the legacy command's behavior, not
equivalence to SM64's floating-point lighting.

From the repository root, with an unused campaign output directory:

```sh
python3 tools/experiments/os30_geometry_fit_20261002/screen.py fit matrix_ram
python3 tools/experiments/os30_geometry_fit_20261002/screen.py fit matrix_regs
python3 tools/experiments/os30_geometry_fit_20261002/screen.py fit matrix_light_ram
python3 tools/experiments/os30_geometry_fit_20261002/screen.py fit matrix_light_regs
python3 tools/experiments/os30_geometry_fit_20261002/screen.py accept matrix_light_regs
python3 tools/experiments/os30_geometry_fit_20261002/screen.py accept matrix_light_ram
python3 tools/experiments/os30_geometry_fit_20261002/lighting.py matrix_light_ram
```

Fit commands require the local Quartus Docker toolchain. Each freezes inputs
before compilation and changes only its private variant inventory. The
lighting check links against the compiled acceptance model, verifies source
and definition identity with the fit, and records hashes of its inputs.

The fitter driver records a failed fit in its status/results JSON even when
the driver process exits successfully. Inspect `valid` and `all_checks_pass`;
a generated bitstream does not imply timing closure.

[Machine-readable results](../../../docs/measurements/os30-geometry-fit-20261002/results.json)
include resource and timing results, validation summaries and source links.
[Bob command counts](../../../docs/measurements/os30-geometry-fit-20261002/bob-command-counts.json)
cover the 17 previously captured first-level frames. No hardware boot, FPS
test, seed sweep or deployment was performed.
