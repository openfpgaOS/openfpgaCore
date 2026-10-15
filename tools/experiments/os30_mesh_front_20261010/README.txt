os30 mesh-task front end — source-only experiment

Read REPORT.txt for conclusions, measured scope and remaining integration work.
This package contains component RTL, synthetic tests, a host producer mechanism,
analysis recipes and evidence receipts. It contains no game assets or bitstream.

Verify package hashes:
  python3 verify.py

Also verify original local evidence files (requires the original workspace):
  python3 verify.py --evidence

Run a portable clocked synthetic test into a NEW directory outside this package:
  python3 run_tests.py --test packet --out /tmp/os30-mesh-packet-test
  python3 run_tests.py --test fetch --out /tmp/os30-mesh-fetch-test
  python3 run_tests.py --test engine --out /tmp/os30-mesh-engine-test
  python3 run_tests.py --test chain --out /tmp/os30-mesh-chain-test
  python3 run_tests.py --test stream-control --out /tmp/os30-mesh-owner-test

These require Python3, Verilator, make and a C++17 compiler. AddressSanitizer and
UndefinedBehaviorSanitizer are enabled. LeakSanitizer is disabled because the
workspace sandbox uses ptrace. Exact build commands and input hashes are recorded.

Real-scene/controller/producer evidence depends on separately held, hash-bound
private fixtures and the exact local app ELF. Those fixtures are deliberately
absent. Their receipts and analysis sources document how they were generated;
the portable synthetic commands do not reproduce real-scene or full-frame tests.
Analysis recipes retain references to the original workspace and its frozen
dependencies; they are provenance sources, not self-contained portable builds.

rtl/ contains the tested component path. tests/ contains synthetic harnesses.
producer/ is a bounded host ownership/cache mechanism, not linked RV32 app code.
evidence/ contains source-bound test and measurement receipts.
analysis/ contains source-only capture, layout and task-compilation recipes.
source-bindings.json maps packaged files back to the tested originals.

Do not treat a host task handle as a hardware ABI. The real app integration must
bind PSRAM offsets, immutable material/viewport contexts, task-local repair and
source lifetimes. Hardware geometry acceptance alone does not establish material
eligibility, correct full-frame raster output, CPU overlap, physical fit or FPS.
