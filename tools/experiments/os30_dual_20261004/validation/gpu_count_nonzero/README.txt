Private os30 GPU count-nonzero shadow

Candidate artifact:
  docs/measurements/os30-dual-20261004/validation/gpu-count-nonzero/gpu_core.v
SHA256:
  16059ac4b79bc6436885646e0cf6d88878bae56549dc22db1836f9a28b19dae9

The count RAM and its synchronous SELECT read are unchanged. Four flip-flops
record count!=0 under the existing final count_we/wa/wd write arbitration;
one additional flip-flop captures the selected predicate on the same SELECT
edge. The existing captured count-valid and frame-valid bits gate its output.
There is no additional cycle or FSM state, command change or reset requirement.
Payload/shadow bits remain unreset, protected by the existing validity masks.
This moves the wide nonzero reduction to the write side, away from the M10K
read-to-framebuffer-write-control path. Timing improvement requires a new fit.

Nonblocking assignments preserve RTL read-before-write behavior for coincident
same-address SELECT/write. Normal GPU FSM writes occur during PAY/TRI_FILL,
not SELECT; the original no_rw_check physical RAM collision contract is not
changed. The selected raw count still feeds sp_count exactly as before.
Generator agent independently reviewed write priority, reset and validity.

Verification completed
  focused/: Extracted original/candidate sequential statements, driven against
  an independent memory/capture oracle. All 65,536 count values x4 write lanes
  x4 selected lanes x16 validity masks, plus 1M mixed write/select/reset cycles:
  18,039,361 cycles; 61,408 same-address write/select overlaps; 1,928 reset+
  select requests. All effective counts and predicates match exactly.

  rendering/: Actual baseline and candidate GPU RTL with os30 definitions and
  parameters extracted from the current production top level. Existing retained
  pixel oracles cover 38 workloads in quiet, delayed and gapped memory modes.
  Each run reports 79 passing checks and zero failures. All 114 candidate
  workload cycle counts equal the matching baseline, and pixel checks pass.
  An observation-only assertion verifies the shadow predicate equals the
  effective 16-bit count nonzero at every falling edge throughout rendering.
  The private fixture, helper RTL, flags, commands, hashes and logs are frozen.

These checks establish component equivalence for the covered cases, not FPGA
clock closure or game FPS. Production gpu_core.v was not edited.

Reproduce in a fresh evidence directory (preserve existing logs):
  python3 tools/experiments/os30_dual_20261004/validation/gpu_count_nonzero/check.py focused --output /tmp/gpu-count-recheck
  python3 tools/experiments/os30_dual_20261004/validation/gpu_count_nonzero/check.py rendering --output /tmp/gpu-count-recheck

manifest.json preserves the exact guarded replacements from the frozen
baseline. focused/manifest.json binds extracted statements and simulator;
rendering/manifest.json binds production configuration and helper sources.
rendering/results.json contains per-workload cycle equality across all modes.
