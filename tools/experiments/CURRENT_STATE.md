# Selected core and game performance work

This index records the selected state as of 2026-10-08. The dated directories
contain experiment recipes, test fixtures and historical reports, including
rejected candidates. A directory name or successful isolated test does not
mean that candidate is enabled in the current core.

## Selected products

- Pocket os30 uses the source replacements in
  [os30.sources](../../src/fpga/targets/pocket/variants/os30.sources), together
  with the variant QSF and SDC. The copies under `common/os30` and
  `targets/pocket/os30` are active sources, not redundant build output.
- The selected CPU is single issue, with 64 KiB instruction and data caches,
  one refill/writeback slot and a 1/4 store buffer. The historical
  "cache capacities unchanged" comment in `configs/os30.cfg` predates this
  selection. Its qualified source bytes are preserved.
- The deployed os30 bitstream has SHA256
  `b1083c26b99c063684b411af1299e3b820c69f025ce6ee0003f6d7d4ab93a8e4`.
  It is configured at 100 MHz; the selected timing estimate is 90.33 MHz hot
  and 91.75 MHz cold. This is not 100 MHz timing closure.
- os20 retains affine/color-mapped spans and translucency for the retained
  Diablo/ScummVM paths, while excluding perspective/depth span features.
  Its documented seed-35 estimate is 94.24 MHz at a configured 100 MHz.
- Current game sources live in the sibling [Quake2](../../../Quake2) and
  [SM64](../../../SM64) repositories. Their runtime images are intentionally
  tracked there; application build outputs remain ignored.

## Experiment groups

| Directories | Purpose |
| --- | --- |
| `os30_dual_20261004`, `os30_frontend_20261004`, `os30_parallel_20261004`, `os30_simple_20261005`, `os30_sm64_ab_20261005` | CPU/fit alternatives and comparisons; dual issue is not the current selection. |
| `os30_fps25_20261005` | Captured workloads, CPU/GPU overlap analysis, compact-core integration, renderer PGO and ordinary-build provenance. `PHASE10.txt` records the progression and rejected alternatives. |
| `os30_depth_prefetch_20261002`, `os30_geometry_fit_20261002`, `os30_worker_20261003`, `os30_mesh_20261004` | Optional or private hardware experiments, not blanket production recommendations. |
| `os30_feature_audit_20261003`, `os30_overlap_20261006`, `gpu_edge_overflow_20261004` | Feature, overlap and triangle-coordinate correctness analysis. |
| `sm64_*_20261002` through `sm64_*_20261005` | SM64 capture, profiling, batching, geometry and fallback experiments. |
| `quake2_sm64_transfer_20261006` | Transfer of retained rendering lessons and compatible runtime/SDK support to Quake2. |
| `quake2_optimize_20261008`, `quake2_optimize_pass2_20261008`, `quake2_comprehensive_20261008`, `quake2_batch_memory_20261008` | Successive Quake2 profiling, exact rendering optimizations and guarded deployments. |

## Latest Quake2 result

The selected final application SHA256 is
`5c21ef893f016aeeb56d6c3f5f0ceab5bf66489a6a8f9877f676cffff86cade9`.
The last pass resolves attribute formats once per draw, shares model position
transforms across UV seams, and immediately publishes the post-flip clear.
It reduces modeled CPU work by 2.36–2.94% across three fixed workloads relative
to the preceding application. Model position transforms fall by 33.07%.

The final pass preserves all command streams and CPU framebuffer bytes over
602 captured frames. The sampled RTL replays preserve complete color and depth
output. These CPU and isolated GPU model results are not measured Pocket FPS.
The final application was copied to the Pocket SD card and verified on
2026-10-08; this pass changes no FPGA resources or clocks.

## Evidence and reproduction

Large captures, immutable build trees, outputs, backups and deployment receipts
remain under the ignored `docs/measurements` directory. They are deliberately
retained locally, not included in Git or deleted as cleanup. Useful entry points:

- `os30-fps25-20261005/phase10/normal-build-endpoint-control-v1`: selected
  source-map, QSF closure and CPU provenance.
- `os30-fps25-20261005/phase10/renderer-pgo-delivery-v7`: selected SM64
  renderer profile inputs and normal-build reproduction.
- `os30-fps25-20261005/phase10/selected-performance-summary-v3/REPORT.txt`:
  retained SM64 model results and their limits.
- `quake2-batch-memory-20261008/acceptance.json`: final Quake2 composition
  and 395 bound evidence files.
- `quake2-batch-memory-20261008/delivery-v1/sd-20261008-v1/complete.json`:
  verified one-file SD update, with the previous executable backed up.

Historical experiment scripts commonly pin their original workspace, input
hashes and one-time output directories. They are evidence recipes, not commands
to rerun blindly against the latest tree. ROMs, game data and private captures
must be supplied separately. Committed fixtures contain no complete game ROM.
Frozen source and patch bytes, including historical whitespace, are preserved
so their recorded hashes and patch context remain valid.

Some general GPU/transport checkers preprocess the common source tree rather
than applying `os30.sources`; their docstrings now state that limit. Use the
selected-core provenance above when assessing that build. Shell/Perl/Python
syntax, CPU freshness/publication, variant-timing and transport tests passed
during cleanup. No new FPGA build or seed sweep was run.
