Private os30 dual-issue CPU validation

Current audited report:
  docs/measurements/os30-dual-20261004/validation/REPORT.txt
Machine-readable identities, coverage and individual log hashes:
  docs/measurements/os30-dual-20261004/validation/validation-inventory.json
Chronological notes are preserved as README-history.txt beside the report.

Scope
  Actual CPU, AXI, Pocket SDRAM and timer RTL; quiet and heavy scanout modes.
  Component cycles are not game FPS. No active GPU drawing/audio or deliberate
  AXI fetch-fault injection. FPGA resource and timing qualification is separate.
  Production RTL, app sources and deployed files are untouched by this suite.

Both corrected skid candidates pass all fourteen fixtures. The older unmasked
skid failed under interrupts and is explicitly rejected. The report preserves
its diagnosis, exact per-profile coverage and the immediate-lane pairing cost.
Earlier candidate profiles did not all receive the later frontend/FP tests;
consult the coverage matrix rather than assuming fourteen runs for every CPU.
The ready-only immediate candidate adds one directly simulated fourteen-run
profile. Its separate single-RS2-KEEP derivative shares that evidence because
an exact one-declaration reversal reproduces the tested source byte for byte;
it is recorded under shared_simulation_derivatives, with zero direct runs.
No extra simulated coverage is counted for synthesis-only attribute removal.
The eight-FP preselection, balanced SRC1 and all-match preselection CPUs each
have their own fourteen completed runs. The final all-match CPU0b4b291a has
byte-identical logs to SRC1, including33,363,543 skid-monitor cycles and
151,853 occupied-buffer flushes. Frozen proof/patch replay and independent
emitted-source audits are linked from the inventory; no game FPS claim follows
from these cycle-neutral timing changes. Current coverage lists 34 directly
simulated CPU builds, including rejected and partial historical profiles.
RAW-normalized CPU 06c79789 and combined RAW/hold/load CPU 1bce3882 each have
fourteen passing runs with byte-identical reference logs. The standalone
hold-only and byte-only sources have component proofs; their combined form
is the one that received the latter fourteen-case CPU run. Exact composition,
both patch orders and independent emitted audits are linked in the inventory.
The two-bank CPU d471c5da has fourteen passing runs. Filtering only its extra
PINGPONG COVERAGE line reproduces all parent1bce logs exactly. The new monitor
checks every physical bank update and all276 logical head bits against the old
StageLink recurrence, including invalid slots. It observes33,363,543 cycles and
zero errors. One pointer FF is added; no pipeline cycle is added. Reset cases
belong to the separate exhaustive refinement proof because runtime monitoring
starts after initial reset. The complete invalid-data consumer review is in
validation/pingpong-consumer-audit-v1/REVIEW.txt, with exact-source guard/census.
The subsequent aggregate-hit CPU4b01fdaa has fourteen logs byte-identical to
two-bank CPUd471, including both monitors. Its additional one-bit register uses
the original LSU hit-vector capture enable; no pipeline cycle is added.
The direct aligner-trap CPU ec69d8b6 then passes fourteen complete logs exactly
matching CPU4b01, including both monitors and 33,363,543 checked cycles. Only
two combinational ctx_trap classifications change; no register or cycle is
added. The actual 69-block proof exhausts 393,216 independent-lane cases,
including arbitrary masks and both trap bits. The runtime suite does not
deliberately inject fetch-AXI faults; that boundary is covered by the proof.
CPU 1aaeb857 applies those same two trap equations to d471 without the optional
sampled-hit FF. Its fourteen complete logs match d471, 4b01 and ec69 exactly;
the composition, reversals and emitted source have separate exact audits.
Its third trap/MMIO frozen integration retains the same peripheral sources
and ordinary AREA options. No further simulation was run for consolidation.
Combined CPU 9faabaee adds direct lastFromBuffer and grouped FP RS1 equations.
Its fourteen full logs match ec69/4b01/d471; exact composition, focused proofs
and independent emitted audits are bound. Standalone ef386 was compiled but
ran zero fixtures and is excluded from directly simulated profile counts.
The peripheral-integration-v1 audits bind both CPUs' full14-case evidence and
the separately qualified GPU/cache/audio/posted-write changes to104 exact
frozen inputs per fit, with unchanged100MHz PLL/phase/SDC and saved seed2.
This is source/evidence integration, not a new combined full-game simulation.
The trap-mmio-integration-v1 audits add the exact MMIO KEEP-only source and
qualified CPUec69 to the same peripheral components. Attribute reversal
restores production behavior byte for byte, so no extra tests are counted.
The CPU-SPEED comparison has a stored SPEED assignment and a uniquely mapped
target, while retaining global AREA. Map reports do not expose an effective
instance goal; identical entity resource tables do not prove it took effect.
The completed SPEED/AREA comparison produced identical final RBFs and timing
summaries, so this setting yielded no implementation benefit for that build.
The current fp-last-integration-v1 pair binds CPU9fa to six/seven overrides.
Only GPU/texture-cache source files differ between the104 audited inputs;
selected macros, AREA/combo/BRAM settings, seed2 and100MHz clocks/SDCs match.
Both GPU sides reuse the actual38x3 BRAM-queue test evidence. No tests rerun.

Texture early-offer qualification is in validation/texture-offer-v1/. Original,
default-disabled and enabled 16KiB caches match all outputs for 244,148 cycles.
Actual os30 GPU38x3 logs match with selected BRAM queues and unchanged cycles;
42,548,619 monitored cycles include 387 hard and six soft resets. The source
FSM induction and all three producer-mode equations are separately proved.
Only prime-A uses the early offer; normal acceptance and port B are unchanged.

Reproduce with already-frozen firmware from repository root
  python3 tools/experiments/os30_dual_20261004/validation/run.py build \
    --label NEW_LABEL --netlist /absolute/path/to/frozen/cpu.v
  python3 tools/experiments/os30_dual_20261004/validation/run.py run --label NEW_LABEL
  python3 tools/experiments/os30_dual_20261004/validation/instruction_capacity.py run --label NEW_LABEL
  python3 tools/experiments/os30_dual_20261004/validation/frontend_hazards.py run --label NEW_LABEL
  python3 tools/experiments/os30_dual_20261004/validation/decode_traps.py run --label NEW_LABEL
  python3 tools/experiments/os30_dual_20261004/validation/fp_legality.py run --label NEW_LABEL

For the current corrected skid netlists, add --skid-monitor and the matching
validation/skid-masked-map.json or skid-immediate-map.json to the build command.
The map binds actual emitted hierarchical signal names to observation-only
testbench ports. It checks all 278 payload bits, flush lane cleanup, partial
frames, registered ready and cancellation. Then collect exercised corners:
  python3 tools/experiments/os30_dual_20261004/validation/skid_coverage.py --label NEW_LABEL
A new RTL profile must have its observation map reviewed before using it.
The two-bank CPU uses skid-pingpong-map.json, retaining the original skid checks
and enabling the optional physical-bank/StageLink-recurrence instrumentation.

Firmware preparation (only when deliberately creating a new fixture set)
  run.py prepare
  instruction_capacity.py prepare
  frontend_hazards.py prepare
  decode_traps.py prepare
  fp_legality.py prepare
These commands are scripts in this directory, require the existing local
firmware Docker image, and consume the saved captured-triangle inputs plus
local SM64 math source. Do not overwrite frozen firmware when comparing an
existing result. Each build records all input hashes and simulator SHA256.
Original three-program execution uses a 40M-cycle limit for D64 capacity cases.
--program/--mode and collect may reuse logs only with unchanged binaries and
simulators; the result collector verifies those identities.

Reports without any new simulation
  python3 tools/experiments/os30_dual_20261004/validation/audit_report.py
This checks all recorded simulator identities, frozen RTL and generated
testbench/C++ snapshots, result/log hashes, and available fixture coverage.
It refreshes REPORT.txt and validation-inventory.json. Historical source-tool
drift is limited to the monitor generator for rejected-v7 diagnosis and later
two-bank monitoring; its compiled
snapshots remain intact. Current candidate source identities are unchanged.

summarize.py accepts --profiles, --references and --output to collect paired
cycles from selected complete profiles. Use a fresh output name to preserve
historical checkpoints. summary-skid.json and summary-skid-immediate.json are
the current corrected-candidate comparisons. Build logs and all raw evidence
remain under docs/measurements/os30-dual-20261004/validation/PROFILE/.

GPU framebuffer queue derivatives
  gpu_fbwq_rendering.py --candidate /absolute/private/gpu_core.v --output /fresh/evidence
This compares against frozen countNZ GPU16059ac4, reusing its exact os30
parameters, helper RTL, retained C++ workloads and pixel oracles. It checks38
workloads/79 pixel checks across quiet, delayed and gapped memory modes,
per-workload cycles, qualified request/snoop payload and AXI activity hashes.
Observation-only counters/assertions cover hard/soft reset invalidation.
Unqualified stale request payload is allowed to differ after reset removal.
Existing logs can be identity-audited/recollected without simulation:
  gpu_fbwq_collect.py /existing/evidence
Both helpers reside in this directory. Reports/results are preserved under
gpu-fbwq-single-read-v2 and gpu-fbwq-payload-reset in the validation artifacts.
Each candidate is separate; this does not validate their combination.
The later combined candidate is qualified separately in gpu-fbwq-combined-v1/.
combine_fbwq.py reproduces the qualified source edits and proves both orders
give identical bytes; --output selects a fresh directory. Its rendering uses
single-read GPU as reference and combined GPU as candidate, with hot cache
03e4d625 in both builds via --cache-reference and --cache-candidate.

Render-cache hot comparator simplification
  gpu_fbwq_rendering.py --reference /private/single-read-gpu.v \
    --candidate /private/single-read-gpu.v --cache-candidate /private/cache.sv \
    --output /fresh/rendering/evidence
Both builds use the same GPU and frozen helpers; only candidate cache changes.
This adds actual cache IDLE-equivalence/hot-hit/reset assertions to the fixture.
The cache must use the frozen os30 geometry: ADDR_W26, two ways, 64 sets,
16 words per line. Dedicated hostile-collision scoreboards were frozen by
render_cache_hot.py, which intentionally refuses to overwrite its evidence.
Completed proof limits, hashes and reset coverage are in render-cache-hot-v1/.

Ring RAM write data selection
The private gpu-ring-data-v1 candidate changes only the enabled ring RAM DATA
mux; the original write enable/address and both CPU/DMA controls stay intact.
Use gpu_fbwq_rendering.py with --ring-monitor, explicit reference/candidate
GPUs and the same hot cache via both --cache-reference/--cache-candidate.
The additional observation-only monitor checks actual RAM contents after
every write against the original selected-data expression and compares writer,
wrap and full-rejection counters plus accepted-write address/data hashes.
The frozen os30 configuration disables command DMA, so runtime coverage is
CPU ring uploads. Arbitrary DMA controls are covered separately by the actual
equation proof. Evidence is retained under validation/gpu-ring-data-v1/;
the independent source audit proves only one RHS changed and binds the proof
equations to the actual GPU. No register or cycle was added.

Packed framebuffer data/strobe RAM
gpu_payload_pack_rendering.py derives the retained renderer without modifying
the previously frozen runner. It adds --texture-cache-reference so both GPUs
can use the qualified early-offer cache, plus observation-only framebuffer FIFO
port, wrap, simultaneous read/write and partial-strobe counters. Pass identical
render-cache and texture-cache files to both reference/candidate options, and
--texture-queues-bram --ring-monitor --texture-offer-monitor for selected os30.
This runner requires all three entire logs to match, including cycles and AXI
activity. audit_payload_pack.py binds exact source/proof/replay identities and
retained logs without rerunning simulation. Evidence is under
validation/fbwq-payload-pack-v1/{qualification.json,REPORT.txt,rendering/}.
Actual runtime counters are kept separate from exhaustive collision/reset
proof coverage; physical memory mapping and timing require the full fit.

Existing-register GPU coordinate staging
stage_gpu_coordinates.py --output /fresh/source/evidence emits the private
PROD_L0 coordinate capture using the existing DDS bank, exact source/phase
contracts, extracted arithmetic checks, lint and patch replay. Source scope
is six captures and two DELTA input lines; no new FF or cycle. Universal
arithmetic equality uses exact low16 operand substitution and unchanged
17-bit arithmetic. Supporting simulations are not exhaustive96-bit inputs.
gpu_coordinate_rendering.py derives the retained runner with an additional
observation-only capture/hold/DELTA/consumer monitor. Use explicit qualified
reference/candidate GPUs, identical texture/render-cache files on both sides,
and --texture-queues-bram --ring-monitor --texture-offer-monitor. The baseline
checks original-coordinate results; the candidate additionally checks scratch
contents before DELTA. All three entire logs must match. Independent review,
38x3 results and exact source/configuration identities are consolidated by
audit_gpu_coordinates.py under validation/gpu-coordinate-stage-v1/. Runtime
normalization and reset-during-scratch counters are reported separately from
the source/phase proof; the latter establishes reset/abort observability.

Anchor-only GPU coordinate staging
stage_gpu_anchor.py --output /fresh/source/evidence derives a separate
variant from the same qualified GPU 2d4870c5, staging only dd_x0/dd_y0.
All four edge-register writers and raw slot1/2 reads remain unchanged.
Use gpu_anchor_rendering.py with the same explicit source/cache arguments
and --texture-queues-bram --ring-monitor --texture-offer-monitor. The
additional monitor checks both captured 16-bit anchors and every original
DELTA/result consumer. audit_gpu_anchor.py binds the independent phase
review, exact two-operand substitution, replay and retained 38x3 results
under validation/gpu-anchor-stage-v1/. No new simulator or fit is implied
by refreshing these evidence manifests.
