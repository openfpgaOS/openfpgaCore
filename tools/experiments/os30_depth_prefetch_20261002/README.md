# os30 span-start depth prefetch

**Status:** opt-in with `OS30_DEPTH_PREFETCH=1`; saved-seed timing regresses.
The full seed sweep is deferred. See the campaign report for the test bitstream.

`INCLUDE_Z_PREFETCH` requests the first depth line of a span while its
perspective/source setup warms up. It reuses the eight-word depth window,
its fill collector, accumulator forwarding, write barrier and snoop kill mask.
A flag set at span emission proves that the pixel/texture pipeline has drained.
The idle P3 address register holds the upcoming first pixel address; invalid
pipeline shifts preserve it. Real fragments overwrite it on arrival.

Only os30 is evaluated. CPU/GPU/SDRAM remain at 100/100/100 MHz. No capabilities,
rendering features or numerical precision change. Complete results and source
snapshots are under `docs/measurements/os30-depth-prefetch-20261002`.

## Measurements and limits

The private SDK simulator executes the current SM64 ELF with one fixed game
tick per frame, disabled audio and current triangle/combiner capabilities.
Its raster model does not implement os30 triangles. It captures every GPU
command and CPU memory update; **all rendering and comparisons use os30 RTL**.
The SM64 backend allocates/uploads its textures in SDRAM directly, so this
fixture exercises that path. The capture's fast-texture capability is disabled.

The replay includes the production 8 KiB render cache, 16-word GPU write queue,
SDRAM arbiter/slave, Pocket controller, refresh and physical command timing
checks. Background scanout/audio/CPU reads are repeatable traffic proxies.
It waits for idle after each CPU batch and flushes the render cache there.
CPU submission overlap, actual audio mixing and presentation pacing are not
modeled. Reported cycles and throughput changes are **GPU workload measurements,
not whole-game FPS or a demonstrated 30% game speedup**.

26 captured head, castle-intro and Bowser frames run under each of three traffic
schedules. Compare displayed framebuffer, fence and a hash of every SDRAM byte.
The 38 existing pixel-oracle workloads and an additional ordered depth/blend
oracle cover transparent texels, rejected/equal depth, read-only depth,
partially written words and delayed/gapped read beats. RTL assertions check
that prefetch has no older fragment and its address equals the source pixel.

## Reproduction

Run from the repository root. Generated data stays in the campaign directory;
no sibling SM64 or SDK files are edited. Local SM64 ELF/assets, the sibling SDK
qsim sources, Verilator and the existing Quartus Docker toolchain are required.
The campaign retains frozen baseline/candidate sources and manifests; use those
when reproducing the recorded comparison.

```sh
python3 tools/experiments/os30_depth_prefetch_20261002/prepare_capture.py
python3 tools/experiments/os30_depth_prefetch_20261002/build_replay.py replay-custom \
  --common src/fpga/common \
  --manifest docs/measurements/os30-depth-prefetch-20261002/gpu-shared/os30/manifest.json
python3 tools/experiments/os30_depth_prefetch_20261002/run_replay.py replay-custom
python3 tools/experiments/os30_depth_prefetch_20261002/run_replay.py --extra replay-custom
python3 tools/experiments/os30_depth_prefetch_20261002/build_ordered.py gpu-shared memory-shared
python3 tools/experiments/os30_depth_prefetch_20261002/analyze.py shared
```

Capture runs use `--app CAMPAIGN/sm64.app`, `--data-dir` pointing to the local
SM64 common assets, `--gpuvec` with the desired presented-frame numbers and
`--frames` beyond their maximum. Set `SM64_INTRO=1` for the deterministic Start/X
input sequence. The normal sequence remains in attract mode. Inputs and hashes
are recorded in `capture-inputs.json` and each capture's `summary.json`.

Build labels must be fresh; the helpers refuse to overwrite frozen models.
Only structural A/B comparisons at saved seed 2 are authorized for this campaign.
The full seed sweep remains deferred until hardware testing.
