Retain the qualified baseline. AJ01 is correct in native simulation but slower:
the selected F22 JOB3 trial uses 12.15% more total cycles than its same-ELF
control, and 19.57% more than the prior qualified app/core. Its warm
interval still uses 10.19% more cycles with zero CPU clipping repairs.
No physical compile or deployment was performed.

This source-only bundle selects app v3 plus frozen native-v2 RTL, preserves
app v1/v2 source patches and all six paired strict receipts, and binds all
181 native input files and all three prepared fixtures. REPORT.txt and
results.json contain the measured decision, exact intervals and limitations.
The physical recipe is prepared-only; no area, timing or sustained FPS claim.

Verify without altering the baseline:
  python3 verify.py --patches --evidence
Run portable actual-RTL tests into a new external directory:
  python3 run_tests.py --output /tmp/aj01-rtl-tests
Run portable repair bounds/format/cancellation/FENV checks:
  python3 run_repair_host.py --output /tmp/aj01-repair-tests
Exact repair arithmetic was checked separately on the actual native CPU.
Full frame and scene replay also requires separately held game assets/tools;
those inputs are hash-bound, not redistributed in this source bundle.

native.patch targets the exact cached-CPU native baseline. core.patch targets
the qualified physical frozen tree; recipes/prepare_physical.py records the
isolated QSF/source integration. sm64.patch targets the explicit app baseline;
sm64-from-predecessor.patch targets app v2. Historical variants are in apps/.
Original workspace recipes and paths are retained for audit. Assembly uses
build_bundle.py followed by finalize_bundle.py; manifest records both stages.
No script automatically fits, deploys or changes the installed core.
