Direct accelerator BRAM to GPU: completed transfer-only experiment

The BIM2 prototype works, but the final warm replay is 0.767% slower than
its same-executable control. Keep the current deployed core. This package
is a reproducible experimental handoff, not a production optimization.

core.patch implements two immutable 256x32 result snapshots, a nonblocking
claim mailbox, direct GPU vertex imports and ordered releases. The raw
producer is reserved only during copying. Full banks use the CPU fallback.
sm64.patch implements the final BRAM7 application, including capability
negotiation, early capacity checks and deferred submission during geometry.

The CPU still copies canonical vertices and performs clipping, culling,
material handling and command construction. Removing those costs requires
a separate triangle-front-end experiment; it is not implemented here.

Patches target the exact preimages identified in manifest.json. They were
applied in temporary directories without fuzz and compared byte-for-byte
with the compiled/tested sources. They are not patches against an arbitrary
checkout. The firmware, generated CPU, clocks and memory configuration are
unchanged. The physical project additionally registers the three new RTL
files and defines INCLUDE_GPU_BRAM_IMPORT, only for os30.

Detailed evidence and isolated physical-project preparation:
docs/measurements/os30-psram32-20261009/bram-import-v1/

No FPGA fit, timing qualification, deployment, commit or seed sweep was
performed for this candidate. The physical preparation retains the known
90 MHz baseline, seed 3 and placement effort 3.0. Snapshot RAM should use
two of the four free M10Ks; added ALM use and timing are unqualified.

REPORT.txt and results.json contain the paired measurements and limitations.
abi.json describes the BIM2 register and packet interfaces.

Verify this package and its locally available evidence:
  python3 tools/experiments/os30_bram_import_20261010/verify.py --evidence

Recheck patch application against the exact local preimages:
  python3 tools/experiments/os30_bram_import_20261010/verify.py --patches

These commands verify artifacts/provenance; they do not rerun RTL or fit.
