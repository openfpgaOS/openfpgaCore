Private os30 dual-issue generator experiment, 2026-10-04
=====================================================

Focused first-buffer head-valid simplification on pair5209:
decode-head-valid-direct-v3/immediate,
SHAc4e3515079d5c7e0584982be72ccffcc692abfcda4107e6908d80c096709af41.
Only a constant-one default is inserted before the unchanged ready capture of
the existing head-valid FF. Actual ready includes !head-valid, so a blocked head
necessarily holds one. Source guards, all131072 actual ready patterns, extracted
FF/reset transitions, scoped four-state proof, independent emitted review, full
lint and clean recipe replay pass. No FF/cycle or lane-mask change. Arbitrary
unknown-initial valid state is expressly outside the proved reset-initialized
binary execution model; see four-state-scope.json. All14 retained CPU cases pass
with complete logs byte-identical to pair5209 and zero monitor errors over33.36M
cycles; no physical timing benefit is inferred from this behavioral evidence.
Recipe: simplify_head_valid.py.

Attribute-only packing trial on the second decoded buffer:
decode-second-skid-raw-unkeep-v1/immediate,
SHA0cc8133832fc0f36121c241d7f84efe9c2b291ea70b10f28dfe255d9145820f1.
Only KEEP/syn_keep annotations on thirteen aligner_raw_upper_sel scalar wires
are removed. All equations, state and cycles are unchanged; exact normalized
whole-source identity, inverse/replay, clean recipe replay and lint pass. Parent
f703 behavior qualification applies. No area/timing benefit is inferred before
fitting. Reproduce with unkeep_second_skid_raw.py into a new empty directory.

Behavior-qualified structural fallback, physical fit pending:
decode-second-skid-draft-v1/immediate, SHAf703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5.
Adds an age1025 decoded slot before dispatch using2x380 rotating data bits and
separate head/tail masks. Net+384 declaredFF, no added unstalled forward cycle;
backpressure/interrupt cycles may change. Four older flushes, blocked head
tombstones, partial-issue sent state, both trap aggregates and the earlier ready
helper are accounted for. Local114688 bit/mask transitions, a fresh actual-source
1340480-cycle sequential harness with146892 off-clock resets, independent emitted
and harness reviews, clean recipe replay and lint pass. All fourteen CPU fixtures
pass with zero monitor errors over33.37M cycles. Warm shift/branch uses1.161% more
cycles; vectors and private triangle cycles are essentially unchanged. The first
full fit failed by one LAB (1849/1848); timing remains unqualified. See the frozen
runtime-qualification.json, second_decode_skid.py and the detailed artifact README.
The original draft-generation manifest is retained unchanged. No production edit.

Focused two-register mapping experiment (whole-CPU suite held):
decode-head-mask-d-v1/rs12, SHA3669a306edbdffcbbfe1c832cdfdb92abcf0b486a482eb2ba33262f97de1e2e1.
Only the two existing head lane-mask FFs receive local CE-recognition-OFF
attributes and explicit self-hold else assignments. Reset, capture and later
cancellation priority are literal unchanged source; no additional state/cycles.
All256 actual-source state/input/ready/cancel/reset transitions, off-clock reset,
full lint and exact reverse/replay pass; root's independent emitted audit passes.
The pair5209 control2cdfa6a9 and rs12 composition commute with the original
operand-group patch in both directions. Root fits rs12 because its measured
head-mask paths end at ENA (-1.478ns). Pair head-valid already uses D, so no
head-valid variant was emitted. See the artifact README and head_mask_d.py.
The physical trial confirms both mask FFs use D pins; their local worst slack
improves from-1.478ns to-1.158ns. Other paths remain slower, so this profile was
not selected and no fourteen-case suite was run for it.

Earlier behavior-qualified frontend candidate:
second-usable-direct-v1/immediate,
SHA256856b139adcaf3147f0a791b2c1dfdd4b6b7b258a64a1b97c1e6a4151c652565a.
Only the seven-bit second usableMask assignment changes on ffed. It computes
remaining positions directly from the first usable mask and each adjacent
instruction length, bypassing first-selection/usage-mask muxes. All selectors,
current ownership/trap equations, FFs and every other source byte are retained.
Actual cones share the same16 unrestricted scanner-valid/required leaves;
all65536 combinations pass31 output bits plus an independent priority/remove
oracle, including3200 empty-second and56064 sparse-mask cases. Independent
emitted review, exact patch replay, clean script replay and full lint pass.
All fourteen complete CPU logs are byte-identical toffed,fb18 and5209, with
zero errors over33.36M monitored cycles. See qualification.json and
validation/rvc_skid_immediate_pingpong_second_usable. Script:
factor_second_usable.py. Root owns complete-core fitting with the existing
stage2-ready fanout32 constraint; no timing benefit is inferred from proof.

The original second-buffer/CE-off feasibility assessment is retained at
decode-second-buffer-feasibility-v1: a new stage3 slot is384 declared FF bits
before pruning, not276 raw bits, and requires careful flush-age/partial-issue
control treatment. The later source-only draft is described above; wide
D-feedback RTL remains unimplemented.

Companion GPU packing candidate:
fbwq-payload-pack-v1/gpu_core.v,
SHA256e7a1b8aacf523f87add3e030bbb2212b347cab29c35720e8f0a3d985d3fb3bba.
Only framebuffer queue data32/strobes4 storage is packed into one36-bit array.
Both writes and allthree unreset reads retain exact addresses/control/priority,
with no extra FFs/cycles.819200 actual-statement transitions, including
same-address old-data collisions, pass. Independent emitted audit and clean
script replay pass. The M10K hint deliberately omits no_rw_check to preserve
stronger old-data semantics. All38x3 retained workload cycles and entire GPU
logs are byte-identical, with42.55M observed cycles, including actual enqueue /
dequeue overlaps and pointer wraps. Runtime collision/swap/nonempty-reset
counters are zero; the focused proof covers collision/write priority/hold.
The expected one16x36 M10K inference and9 memoryLABs are confirmed, but the
complete-core fit fails1853/1848 LABs. There is no qualified placement/timing
or final physical block location, so this candidate is not selected.
Script: pack_fbwq_payload.py. See memory-packing-audit-v1 for the read-only
physical inventory: span arrays already share five blocks, so naive logical
RAM-row summation greatly overstates any packing opportunity.

Previous qualified operand composition:
all-operand-groups-v1/immediate,
SHA256 ffed6f6e299b8e7d3275b781d55d7094ae4d6c086b9f57d6553f794cd545082c.
This adds only the previous proved RS3 4+6 data grouping to fb18. Existing
integer RS1 2+7 and FP RS2 2+8 groups are retained. All six original component
patch orders/reversals on5209 commute; reversing RS3 alone restores fb18.
All three fresh full selector/data cones equal the qualified component proofs,
which rerun successfully; full lint and clean script replay pass. No register
or cycle change, only two additional kept combinational words versus fb18.
Independent composition review passes. All fourteen entire CPU logs are
byte-identical to fb18,5209 and9fa, with zero errors over33.36M monitored cycles.
See qualification.json and validation/rvc_skid_immediate_pingpong_all_operand_groups.
Script: compose_all_operand_groups.py. Root owns the separate stage2-ready
fanout constraint and physical fit; no timing benefit is inferred from proof.

Previous qualified forwarding candidate:
integer-rs1-fp-rs2-combined-v1/immediate,
SHA256 fb18b1ca44bdb6ae500583c094d0c8859e3a7184457194fb40b738fe09337fdb.
On the measured5209 pair, integer RS1 is grouped2+7 and FP RS2 is grouped2+8.
All original selectors/live gates/FFs and the integer output KEEP are retained.
Exactly four kept combinational words are added, with no registers or cycles.
Both actual full selector/data cones match their proved components;262144
integer and1048576 FP exhaustive cases pass. Original patch orders commute,
reversals recover each parent, and lint passes. Independent component and
composition reviews pass. All fourteen complete CPU logs are byte-identical
to5209,9fa anda083, with zero errors over33.36M monitored cycles. Clean replay
matches the exact SHA; qualification.json binds all source/proof/test evidence.
compose_integer_rs1_fp_rs2.py reproduces the exact result. Complete-core timing
is still measured separately. The pair-based integer-only component b182970b
remains focused-only; its14-case suite is intentionally avoided unless selected.
The earlier a083-based integer6c147c75 and FP RS2 4+6 alternatives are unselected
focused artifacts; do not infer CPU qualification or measured benefit for them.

Best measured forwarding-group base, now whole-CPU qualified:
pointer-packer-combined-v1/immediate,
SHA256 5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa.
Its own fourteen entire logs are byte-identical to9fa and a083, including33.36M
monitored cycles with zero errors. Independent audits, clean script replay and
qualification.json bind its exact source. Parent's default full-core result is
87.65MHz; physical mapping confirms one pointer FF using D rather than ENA,
and the old packer-mode path is no longer limiting. This is still below90MHz.

Previous qualified RS3-group timing candidate:
pointer-packer-rs3-combined-v1/immediate,
SHA256 a083bdb084b9dfd7b8944287ef3411b4dc739bf366f7dfa3f42599d5edd0528b.
It removes only the redundant packer NORMAL qualifier from ROUNDING_INCR,
expresses the original ping-pong pointer toggle as unconditional XOR-D with
local AUTO_CLOCK_ENABLE_RECOGNITION OFF, and groups only the RS3 FP data mux
into two kept combinational words. No new FF or cycle. All six original patch
orders and reversals are exact; all three focused proofs rerun and full lint
passes. All fourteen entire CPU logs are byte-identical to9fa,ec69 andd471,
including33.36M cycles with zero skid/ping-pong assertion errors. The original
pointer next-state invariant and every276-bit head-payload recurrence remain
checked. Independent component/composition audits and clean script replay pass;
see its qualification.json and validation/rvc_skid_immediate_pingpong_pointer_packer_rs3.
Script: compose_pointer_packer_rs3.py. Timing and pointer D-versus-ENA mapping
require fitting; neither is inferred from these behavioral proofs.

Packer component: packer-round-mode-v1/immediate, b5002595. Its exact three
ROUND consumers terminate at packed data/NX/UF/OF; no internal value escapes
that proved boundary. NORMAL equations are literally identical for arbitrary
raw exponent/mantissa/all eight rounding codes, including signed wrap.417792
special-mode arithmetic-cutpoint/data-basis cases establish full packed-word
and all-five-flags independence;576 metadata cases and1524288 supplemental
full actual-cone boundary/random comparisons pass. No arithmetic behavior or
NaN/sign/quiet convention is changed. Script: remove_packer_mode_gate.py.

Previous qualified combined CPU: fp-last-combined-v1/immediate,
SHA256 9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377.
It combines direct frontend lastFromBuffer classification with RS1 FP data
priority split into two kept combinational groups. No additional FFs or cycles.
Original match/write-enable/lane-valid gates and selectors remain unchanged.
The actual focused cones pass exhaustive proofs and independent source audits;
both transformation/patch orders commute exactly. All14 entire CPU logs are
byte-identical to ec69,4b01 andd471, including33.36M cycles of skid/pingpong
assertions with zero errors. See qualification.json and compose_fp_last.py.
Timing/area benefit still requires complete-core fitting.

Proof-only fallback, NOT whole-CPU-qualified or selected for fitting:
fp-cmp-abs-factor-v1/immediate,
SHA256 67ad5bafec4d5fd654469a820c00ac5c3599878b8d9703141c10ff62e1a59bb6.
Only the FP rs1AbsSmaller special-class override block is factored; all32
independent class/base conditions and128 exact seven-input cone conditions
pass. No FFs, cycles or kept boundaries are added. All other source is exact,
including NaN/equality/sign/final-result/flags behavior and sequential blocks.
Patch replay and lint pass. The parent explicitly holds its14-case suite and
fit pending the current9fa results. The compiler may already infer this logic,
so this proof makes no timing claim. Script: factor_fp_abs_compare.py.

Its sampled-hit ancestor is lsu-hit-sampled-v1/immediate,
SHA256 4b01fdaa39fce594a8e0bb46cb756cd55d6613b8775c72207b5c7be3c77ecf09.
One additional unreset FF samples the two-way LSU hit OR beside the existing
hit-vector capture, using the exact same advance. The vector and all aggregate
consumers remain unchanged. Boolean and four-state induction, independent
emitted audit, exact replay and complete-netlist lint pass. Recipe: sample_lsu_hit.py.

The decode-pingpong parent d471c5da removes the wide late-ready capture enable
with the same payload FF budget plus one pointer FF;
original occupancy/lane-mask/flush equations remain unchanged. All276 logical
head bits match the original StageLink recurrence over33.36M monitored cycles,
with zero errors. Exhaustive inductive refinement, independent emitted-source
audit, exact patch replay and complete-netlist lint also pass. This is functional
qualification; complete-core area/setup/hold measurement remains separate.
Production is unchanged.

Reproduce into a new private directory:
  python3 tools/experiments/os30_dual_20261004/generator/pingpong_decode.py --output <new-directory>
The adjacent recipe.json pins source, patch, proof, script and output hashes.
The original 1bce3882 reference and all earlier measured candidates remain frozen.

Companion private audio timing candidate: audio-ramp-balance-v1/rtl/audio_mixer.v,
SHA256 7dd77c3e2ed727b931a30b6e716d7b918c801c8e4c60bd08a246fb656884ea97.
Only the shared volume-ramp combinational function is factored. All16,777,216
inputs match old RTL and an independent oracle; existing mixer PCM/timestamp
regression logs and36 throughput cases are byte-identical. No sequential
register, cycle, channel or feature changes. Script: audio_ramp_balance.py.

Companion private MMIO synthesis candidate: mmio-read-boundary-v1/rtl/axi_periph_slave.v,
SHA256 0af14f6dc9daee62b6b565f3dab8af66f1afdcb18bbae481a765851fd125a8b6.
Exactly one KEEP attribute preserves the existing SYSREG32 read-data boundary.
Removing it restores the entire reference source byte for byte, including all
address/default/feature branches and read-side effects. No register or cycle
changes. This targets the measured local-SYSREG-to-regional-mux path; only fitting
can determine its resource/physical benefit. Behavioral simulations are not
repeated for an attribute-only edit. Script: keep_mmio_boundary.py.

The independently emitted CPU trap-direct candidate ec69d8b6 is maintained by
the fit-review agent in aligner-trap-direct-v1/immediate. Its two ctx_trap
expressions classify buffer/fetch faults directly from each usable mask, leaving
selection, payload, flush and sequential state unchanged. Our independent source
audit passed all393,216 mask/required/fault cases and the actual proof binary;
see aligner-trap-emitted-audit-v1. Whole-CPU validation is owned by the validator.

Controlled placement alternate: aligner-trap-no-sampled-hit-v1/immediate,
SHA256 1aaeb857d1ceec36b89d8b459365c0a2ee2b76ab0c9b7afc865a81c1aae8e616.
This applies only the two trap equations to d471, equivalently reversing only
the three sampled-hit edits from ec69. Both composition orders and exact
component reversals are verified; every d471 sequential block and all original
hit-vector consumers are unchanged. Independent emitted audit, proof rerun,
lint and clean script reproduction pass. All14 entire CPU logs are byte-identical
to d471,4b01 andec69, with zero errors over33.36M skid/pingpong-monitored cycles.
See its qualification.json and validation/rvc_skid_immediate_pingpong_trap_nohit.
This is a physical-placement control, not a claim that the sampled hit logic is
incorrect or locally slow. Root owns comparison fits. Script: without_sampled_hit.py.

Current qualified candidates and reproduction
--------------------------------------------

The current full-ALU candidate retains I32/D128, BTB256/GShare1KiB, the complete
lane-zero ISA/FPU and a full integer-ALU second lane. Its raw-align/skid/RVC/decode
pipeline is qualified as an exact os30 configuration, not as a generic change to
every upstream Vexii configuration. Production source/RTL has not been changed.

  Full ALU: timing-rescue-v4/rvc-skid-masked-control/VexiiRiscv.v
    SHA256 80b6f51d805e4909d8814ee742d1041c387e2c9a66438985062e40271428697f
  Immediate-only fallback: timing-rescue-v5/rvc-skid-immediate-control/VexiiRiscv.v
    SHA256 26f237c8ca540356f156691ff1f7a6940c583e4dfef149cf0687e81eaf0d792e

Paths above are relative to docs/measurements/os30-dual-20261004. Both passed the
14 whole-CPU program/traffic-mode cases with payload and cancellation assertions.
See validation/summary-skid.json and summary-skid-immediate.json. The full-ALU
fit used 18,146 ALMs, 1,848 LABs and 308 M10Ks, but reached only 82.95 MHz; fitting
is not evidence of the requested 92 MHz target. The immediate-only fallback loses
about 6.7% triangle throughput compared with the full ALU lane and remains optional.

The initial generator-v7 / timing-rescue-v3 skid is FAILED, even though its isolated
CPU fit was fast. Flush cleared frame valid while stored lane-select bits leaked
into decode1. IRQ-enabled stress/frontend tests reproduced architectural corruption.
The separate v8 correction masks BOTH outgoing lane-select bits on flush. Keep the
failed artifact as a regression reference; do not deploy or reuse it as the base.

To verify the frozen source/helper bundle without generating anything:
  python3 tools/experiments/os30_dual_20261004/generator/reproduce_snapshot.py \
    --snapshot docs/measurements/os30-dual-20261004/generator-qualified-v1

To reproduce into a NEW output directory (full-alu or immediate):
  python3 tools/experiments/os30_dual_20261004/generator/reproduce_snapshot.py \
    --snapshot docs/measurements/os30-dual-20261004/generator-qualified-v1 \
    --output /tmp/os30-new-replay --cache tools/.vexii-home --profile full-alu

The replay script refuses an existing output directory, verifies all frozen hashes,
uses the pinned Docker image and exact source archive/flags/helpers, and checks the
raw, postprocessed and final RTL hashes. It restores only the generated provenance
comment header omitted by archiving without .git; no logic is normalized. A clean
full-ALU replay matched all three hashes; evidence is in generator-qualified-v1/
replay-full-alu/reproduction.json. Docker needs access to the local Docker daemon.

The newer timing derivatives are separate immutable outputs:
  fpu-match-v1/full-skid: 27 precomputed FPU ID matches, no extra pipeline cycle.
  tag-select-v1/full-skid-fpu: same plus early D-cache tag DATA selectors only;
    write masks/address/arbitration remain unchanged. Initializer-invalid tag
    address/fault bits are allowed to differ while loaded=false.
  integer-rs2-v2/full-skid-fpu-tag: same plus eight lane-zero integer RS2
    match/write-enable FFs; previous DOWN_RD_ENABLE is captured, while current
    UP_LANE_SEL cancellation, bypass priority and operand KEEP stay unchanged.
The corresponding immediate-only alternatives are tag-select-v2/immediate-skid-fpu
and integer-rs2-v3/immediate-skid-fpu-tag. All three full-ALU derivatives and both
immediate alternatives passed all 14 CPU cases with byte-identical logs/cycles to
their respective skid baseline. These are timing changes, not cycle-count gains.
Their adjacent manifests and extracted proofs identify exact source and boundaries.
Whole-CPU validation is recorded separately, so a creation manifest marked pending
is not silently rewritten after testing. These transformations do not change ISA/ports.

The three postgeneration transforms also have a frozen exact-hash replay bundle:
  python3 tools/experiments/os30_dual_20261004/generator/reproduce_timing.py \
    --snapshot docs/measurements/os30-dual-20261004/postprocess-qualified-v1 \
    --profile full-alu \
    --base /tmp/os30-new-replay/VexiiRiscv.v --output /tmp/os30-new-timing-replay
Use the qualified RTL path emitted by the first replay as --base. Alternatively,
pass the original timing-rescue-v4/.../VexiiRiscv.v, or the matching immediate base
with --profile immediate. Every input/intermediate/output SHA is checked, output
must be new, and no text normalization occurs. Both timing replays were exercised;
evidence is in postprocess-qualified-v1-replays. The first integer-rs2-v1 proof
draft failed due to a harness renaming collision; v2 corrects the proof, preserving
identical CPU RTL. Use the v2 proof and manifest.

Reproduction scope: exact single-hart os30 M-mode configuration, debug disabled,
no MMU/supervisor/user configuration. Debug stepping, debug-origin flushes and
other generator option combinations have not been qualified. The skid source
asserts the actual flush ages and whole-frame flush contract at elaboration.

Final narrow-lane area fallback (generator-v10)
----------------------------------------------
The additional --lane1-address-only profile supports only ADDI/LUI/AUIPC on lane
one; all other instructions retain their lane-zero implementation. Its bitwise
and SLT result datapaths disappear, ALU_RESULT is the direct adder result, and
add/sub reversal decoding becomes constant zero. This is a concrete area cut,
not a cache reduction. The full I32/D128 caches, predictors and corrected skid
remain identical in configuration. Only private Param/IntAlu Scala files change.

Final RTL: integer-rs2-v4/address-skid-fpu-tag/VexiiRiscv.v
SHA256: 374e4333a011f34f92d5c3dbd6f1da20f99b445a58a75f14775ef827bf1a3c15
All 14 actual CPU cases pass, including 33.48M monitored skid cycles. Relative to
the broader immediate lane at equal clock, vectors lose 1.07% throughput and the
private triangle fixture loses 0.60% quiet / 0.69% scanout; shift/branch is unchanged.
These component measurements are not game FPS. The complete-core result follows.

Final comparison rejected this tiny profile: it fitted at 82.04 MHz with 18,073
ALMs / 1,843 LABs, versus the broader immediate profile at 86.45 MHz with 18,042
ALMs / 1,843 LABs under the corresponding complete-core experiments. It brought
no LAB saving and lost both timing and pairing throughput. Keep it as evidence,
not as the selected CPU. Further work uses the broader immediate profile.

Source recipe: prepare_tiny.py --output NEW_DIRECTORY extracts the hash-verified
frozen immediate archive and applies only generator-v10/address-only.patch. Exact
commands, flags, modified source hashes and original/final RTL hashes are saved
alongside it. Never use an existing output directory. The three timing transforms
are frozen in postprocess-qualified-v2-address, replayable with reproduce_timing.py
--profile address and the generator-v10 base RTL. That replay matches all three
intermediate/final hashes; evidence is in postprocess-qualified-v2-address-replay.
Earlier source and timing snapshots were preserved. No further CPU profiles are
being generated while the complete-core timing/area results are evaluated.

Measured-path follow-up on the broader immediate lane (decode-ready-v1)
---------------------------------------------------------------------
The stage1 capture-ready equation is factored around the measured late candidate0
lane0-availability bit. Two retained early Boolean terms feed the final AND/OR.
All 262,144 input combinations of the actual original ready cone match, including
empty/stale frames, sent slots, priority, freezes and dispatch halts. No storage,
pipeline latency or per-lane flush logic changes. Its 14 actual CPU logs are
byte-identical to the immediate all-fixes base, with 33.36M monitored cycles.

  decode-ready-v1/immediate/VexiiRiscv.v: ff0bd8cf20e0fc0fd85ec91c70b5122a5fabd8d9a185a33b12cc40e9be1baf55
  decode-ready-v1/immediate-rs2-unkept/VexiiRiscv.v: c7606f8dbcdd343e88b9d6d7074cd7cd7e13358e4457820143d35fb387ead6f0

The second candidate removes only the lane0 RS2 combinational KEEP attributes;
normalized behavioral RTL is identical, so it shares functional qualification.
These candidates require separate fitting. The parent also applies a targeted
LCELL/MAX_FANOUT64 assignment to decode_skid_gate_down_ready, whose old node had
302 fanout and a 2.115ns route. Actual acceptance/duplication must be checked in
the mapping report. Proof, hashes, exact patches and replay script are preserved
under decode-ready-v1. No second skid buffer or additional lane cut was added.

The ready-only complete-core fit reached 87.31 MHz, 18,089 ALMs / 1,847 LABs /
308 M10Ks, with +0.034 ns hold slack. The RS2-unkept alternative regressed to
85.61 MHz and negative hold slack and is rejected. The ready-only CPU remains
the base for subsequent work; the user's >90 MHz objective is not yet achieved.

Measured FP comparison placement (fp-preselect-v1)
------------------------------------------------
The eight FP match-register endpoints below the >90 MHz setup threshold are
RS1_10, RS2_4/7/8/10 and RS3_6/10/11. Their producer/consumer ID equality tests
are moved before dispatch's late candidate selector. Sixteen retained equality
wires feed the existing eight match FFs; no register, pipeline cycle, port,
operand data mux, current validity gate or RD_ENABLE gate changes. The other
19 FP match equations are untouched. This is an area-conscious subset, not a
claim that duplicating every comparison is free.

  fp-preselect-v1/immediate/VexiiRiscv.v
  SHA256 a85cdf3621b3e0257f7fd2ceeb5288c554cf50bc76f1b7bf728006ca726370ad

The original OR-masked selected ID is zero when neither candidate is selected.
The replacement explicitly retains producer-ID-zero equality in that case.
The actual scheduler cone is exhausted across all 2,097,152 control inputs to
prove the two selectors cannot both be asserted; this invariant is not assumed.
All 786,432 combinations of three six-bit IDs and three legal selector states
then compare the original selected-ID equations and emitted early comparisons
for each endpoint. Both proofs pass, with no implicit or undriven proof inputs.
Reversing the eight RHS changes and added wire declarations yields the input
RTL byte for byte, including unchanged FF enables and downstream live gating.

Reproduce with generator/fp_preselect.py --output NEW_DIRECTORY; this checks the
base/checker hashes and reruns both exhaustive proofs. The exact patch, script,
checker snapshot, manifest and proof sources/logs are adjacent to the RTL.
Whole-CPU simulation and complete-core area/timing remain separate qualification.

All 14 whole-CPU program/traffic-mode cases now pass and their logs are byte-for-
byte identical to ready-only, including cycle counts. The skid monitor covers
33,363,543 cycles, 7,791,624 captures and 151,853 occupied flushes with zero errors.
Evidence: validation/rvc_skid_immediate_ready_fp_preselect/cycle-equivalence.json.
No performance gain is claimed until the complete-core fit establishes the clock.

Local integer SRC1 OR fallback (src1-balance-v1)
----------------------------------------------
On top of the eight-endpoint FP preselect candidate, src1_balance.py changes only
the lane-zero integer RS1 data-output block: the eight-term OR plus selector0
override becomes one balanced nine-term masked OR. All priority/select equations
and the existing output KEEP remain byte-identical. There are no new FFs/cycles.

  src1-balance-v1/immediate/VexiiRiscv.v
  SHA256 6077cfd99d013f3d1e21892e00f3782c14f35385747f1da6cc66a6eec0e2d80c

The actual priority and old/new mux cones pass all 512 enable masks x 512 input-bit
patterns x 32 output positions (8,388,608 comparisons). Original zero/one-hot and
fallback selection are asserted. Both guarded mux expressions are strictly
bitwise, so per-position exhaustive checks cover arbitrary complete data words.
Proof, exact patch and frozen script/checker are adjacent; reproduce with
generator/src1_balance.py --output NEW_DIR. Complete-core fit and whole-CPU
validation determine whether this local fallback should replace its parent.

All fourteen CPU program/mode cases now pass, with logs and cycles byte-identical
to FP-preselect alone. The skid monitor again covers 33.36M cycles with zero errors.
Evidence is in validation/rvc_skid_immediate_preselect_src1/cycle-equivalence.json.
Independent emitted-source audit and repeated exhaustive proof also pass under
src1-balance-emitted-audit-v1. Complete-core fitting remains the selection gate.

Complete match-precompute selection rewrite (match-preselect-all-v1)
-----------------------------------------------------------------
The partial FP rewrite moved the dominant fitted path to untouched FP RS2_5 and
integer match5_0 endpoints. match_preselect_all.py completes the existing family:
all 27 FP and all eight integer RS2 match FFs now select early candidate equality
bits instead of comparing a late selected ID. The original eight FP equations
stay unchanged; 19 FP and eight integer RHS expressions change. The exact previous
DOWN_RD_ENABLE remains in every integer match, with all current live validity,
FP write-enable gates, capture enables, register counts and cycle counts preserved.

  match-preselect-all-v1/immediate/VexiiRiscv.v
  SHA256 0b4b291a6d8d13d9e4b9fe1ba3eb58a2bf2e69f73308b8d936bcfee25e05e846

Fifty early equality wires are added (66 total); two integer endpoints reuse the
identical FP RS2 equality pairs. The actual scheduler passes all 2,097,152 control
states; every endpoint passes all 1,572,864 combinations of producer/candidate IDs,
legal selectors and RD_ENABLE. Exact per-endpoint dependency checks prevent an
incorrect stage/lane/enable from being hidden by shared parallel harness inputs.
No-selection ID-zero behavior is preserved. Source reverse-patch is byte exact.
Reproduce with generator/match_preselect_all.py --output NEW_DIR; proofs, script,
checker, patch and manifest are frozen beside the RTL. Whole CPU and fitted area,
setup and hold remain separate qualification; no new FF or cycle is introduced.

All fourteen CPU program/mode cases pass with byte-identical logs and cycle counts
to the SRC1 reference. Skid assertions again cover 33.36M cycles with zero errors.
Evidence: validation/rvc_skid_immediate_preselect_all/cycle-equivalence.json.
Independent emitted audit and proof reruns pass under all-match-emitted-audit-v1.

RAW normalization and aggregate/load follow-ups
----------------------------------------------
raw_normalize.py moves compressed-instruction upper-half zeroing ahead of the
aligner's late one-hot choice. Only the two RAW assembly blocks and thirteen
one-bit qualifiers change; low16, selectors, controls, FFs and cycles stay exact.
All32,768 mask pairs prove the actual zero/one-hot invariant;150,994,944 extracted
data/selector comparisons exhaust every output bit's local truth table under
guarded bitwise dependencies. Independent emitted audit and all14 CPU cases pass,
with byte-identical all-match logs. Evidence: raw-normalize-v1 and validation/
rvc_skid_immediate_raw_normalize. RAW SHA06c797895939e09a2a63a0eb921f2ae73beb8e8e55ef4ac98521c8c75f65d048.

inflight_hold.py changes only aggregate inflightTrap accounting. Decode0 already
counts an empty skid's bypass trap. A held skid needs an extra occupancy-qualified
stored-payload term, with LSU cancellation absorbed by the already present
execute pending term. All other flush qualifications and every actual skid
signal remain exact. Three actual-cone proofs establish LSU=>execute pending,
the decode OR decomposition, and full held/bypass aggregate equivalence. Its
standalone creation is SHA5e9affab..., without a separate whole-CPU run.

The selected combined source applies that hold patch and validator's byte-only
load factoring to RAW. Both patch orders are byte-identical and both reverse to
RAW exactly. No additional registers/cycles. Exact source:
  raw-hold-load-v1/immediate/VexiiRiscv.v
  SHA256 1bce3882b721c09e4a5091cc3ef00360366df9b1b0b79ffb0b1938b820105569
Reproduce with generator/compose_raw_hold_load.py --output NEW_DIR. Component
proofs, patches, snapshots and composition identities are retained separately.
Whole-CPU test label is rvc_skid_immediate_raw_hold_load; fitting remains separate.

The combined source passed all fourteen program/mode cases, byte-identical to RAW
including cycles, with 33,363,543 monitored cycles and zero errors. The independent
held/combined emitted audit passes in inflight-hold-emitted-audit-v1. Direct logs
and comparison are in validation/rvc_skid_immediate_raw_hold_load/cycle-equivalence.json.

Historical experiments and convenience generator
------------------------------------------------

The commands below describe the recorded exploration. prepare.py copies CURRENT
vendor files and can overwrite its existing profile output; do not rerun --profile
all over these immutable artifacts. Use the frozen replay recipe above for the
qualified current candidate. Historical one-off generation command:
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile all

The script copies the current vendor generator, including its already-applied
production patches, into docs/measurements/os30-dual-20261004/generator-v1/source.
It applies lean-lane.patch only to this private copy. Docker uses the existing
local openfpgaos-vexii image and SBT cache. It does not fetch/update vendor source,
edit production RTL/config, run Quartus, deploy a core, or perform a seed sweep.

Original profiles and output names (under generator-v1):
  lean        VexiiRiscv_os30_dual_lean.v
  stock-dual  VexiiRiscv_os30_dual_stock.v
  single64    VexiiRiscv_os30_single64.v

Optional fourth profile (separate generator-v2 directory):
  lean-immediate  VexiiRiscv_os30_dual_immediate.v
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile lean-immediate
This applies immediate-lane.patch after lean-lane.patch. Lane one then supports
only ADDI/SLTI/SLTIU/XORI/ORI/ANDI/LUI/AUIPC; all register-register ALU operations
stay on lane zero. This trades pairing opportunities for removal of the second
lane's RS2 integer-register-file read and forwarding path. The explicit
withRegisterOperands IntAluPlugin parameter defaults to true; lane-zero behavior
is unchanged. Source staging and generated RTL are separate from generator-v1.
The --profile all command includes this optional profile after the original three.

Alternative cache budget (separate generator-v3 directory):
  lean-i16-d128    VexiiRiscv_os30_dual_lean_i16_d128.v
  single-i16-d128  VexiiRiscv_os30_single_i16_d128.v
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile lean-i16-d128
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile single-i16-d128
These trade half the instruction cache for preserving the original 128 KiB data
cache: fetch sets128 (16 KiB), LSU sets1024 (128 KiB). The lean profile retains the
full ALU-only second lane; its single-issue control isolates instruction-cache cost.
All other flags/postprocessors are unchanged. This is a performance-screening
alternative, not the selected configuration; instruction-cache capacity can matter.
The --profile all command includes these alternatives after the earlier profiles.

Final cache-preserving predictor alternative (generator-v4 directory):
  lean-smallpred  VexiiRiscv_os30_dual_lean_smallpred.v
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile lean-smallpred
This keeps I32/D128 and the full ALU-only second lane, reducing BTB1024/GShare4KiB
to BTB256/GShare1KiB. All other flags stay unchanged. It trades predictor accuracy
for memory budget and must be screened against the original CPU before full fit.

Important M10K accounting from actual fitter RAM tables, before fitting this profile:
  Lean I32/D64 CPU: 122 M10Ks. Single I32/D64 CPU: 116 M10Ks.
  D64 -> D128 adds 66 blocks: 64 data plus two additional tag blocks.
  Dual BTB1024: two 512x51 banks, 3 blocks each = 6.
  Dual BTB256 (existing os20 fit): two 128x51 banks, 2 blocks each = 4.
  GShare4KiB: 4096x8, 4 blocks; GShare1KiB: 1024x8, 1 block.
  Predictor saving is therefore 5 blocks, not 7. Estimated final CPU = 183 M10Ks.
  The current full lean I32/D64 experiment uses 247 M10Ks, giving an estimated 308
  for this full-cache/small-predictor alternative, exactly the device limit.
These are inference estimates from measured component geometries, not a completed
fit of this variant. Extra FIFO-to-M10K area optimizations would exceed that budget
and must be excluded or funded separately. The final fit remains authoritative.

All profiles retain the production os30 RV32IMAF+C+Zicbom ISA, reduced FMA,
ignored subnormals, pipelined integer-to-float, radix-4 divider, full integer
bypass, 2-slot/16-op store buffer, and production fetch/D-cache retiming.
Original profiles use I32/D64; the instruction-cache alternatives use I16/D128;
lean-smallpred uses I32/D128. Predictors remain BTB1024/GShare4KiB except for
lean-smallpred's explicit BTB256/GShare1KiB reduction. Their top module and
AXI interfaces remain VexiiRiscv and the existing os30 AXI interfaces.

The lean profile has two decode slots and two execution lanes. The second lane
has SrcPlugin, IntAluPlugin, IntFormatPlugin and integer WriteBackPlugin. Its
registered micro-ops are ADD/SUB/SLT/SLTU/XOR/OR/AND, their immediate variants,
LUI and AUIPC. It has no barrel shifter, branch plugin, Zb plugins, LSU, multiplier,
divider or FPU. Lane zero retains all the original instructions. The patch rejects
incompatible single-lane/decoder or late-ALU configurations instead of quietly
creating a different design. The stock-dual profile is a controlled area/throughput
comparison with normal lane-one shift and branch hardware; single64 isolates the
data-cache reduction from the dual-issue change.

Scheduling and correctness rationale (does not replace simulation):
  DispatchPlugin constructs LANES_LAYER_HIT from each layer's registered micro-ops.
  Removing lane-one shift/branch plugins removes their lane-one decode eligibility;
  their lane-zero implementations still determine the ISA legality. Dispatch is
  ordered: failure to issue an older candidate blocks younger candidates. Candidate
  RAW/WAW checks, in-flight bypass/hazard checks, resource reservation checks and
  branch/trap flush age remain upstream logic. The higher-priority simple lane is
  selected when available, leaving lane zero free for specialized operations.
  CsrAccessPlugin continues executing through lane zero and sharing its asynchronous
  integer register-file write port with lane one's writeback, as upstream dual issue
  does. Do not change that to lane zero without evaluating write-port conflicts.

Evidence:
  source-manifest.json records copied Scala source/helper hashes and vendor HEAD.
  Each profile-manifest.json records full command/flags and final RTL SHA-256.
  Each profile-generate.log records SpinalHDL elaboration and generated warnings.
  The final RTL receives the same maxfan=16 and AUTO_SHIFT_REGISTER_RECOGNITION OFF
  synthesis hints and the same four postgeneration transforms as production os30.

This first configuration deliberately avoids os20-only frontend/FPU timing rewrites:
those are additional changes, and some rely on the exact normal dual-lane structure.
Any timing rescue must be separately measured and checked against this lean netlist.

Potential next area experiments, only after a measured fit:
  * Inspect integer register-file replication, operand forwarding, and dispatch logic;
    these remain the unavoidable cost of the second lane after its units are cut.
  * The optional immediate-only second lane narrows pairing opportunities and must
    be evaluated as a separate benchmark/configuration.
  * Do not subtract D-cache area a second time from these D64 CPU measurements.
  * Do not assume disabling pipeline shift-register recognition is free: production
    os30 selected it to avoid a measured slow M10K writeback/bypass path.

Frontend timing rescue
----------------------
Standalone lean CPU's measured worst path is aligner buffer_mask[0] through second
instruction selection/decompression to decode instruction bit 16 (11.035 ns data,
9 logic levels, -1.300 ns slack). Nearby paths feed GShare/branch-history captures
through dispatch readiness. This motivates frontend-specific combinational changes.

Run (output directory must not already contain the requested profile):
  python3 tools/experiments/os30_dual_20261004/generator/timing_rescue.py --validate

This creates timing-rescue-v1/{lean,immediate}-{control,frontend,grouped}/VexiiRiscv.v.
The source RTL, generator-v1/v2, and production helpers remain unchanged. The script
uses the existing optimize_os20_timing.pl subroutines with their complete aligner
and dispatch SHA-256 contract checks, selecting only these transformations:
  control: local_usedmask + early_valid + direct_dispatch_grants.
  frontend: control plus expansion of each possible compressed instruction before
            selection (8 nonconstant decompressor instances, versus the original 2).
  grouped: control plus two decompression alternatives per issue slot; each half
           window selects its raw instruction first, then expands it, followed by
           a final selection. This uses 4 nonconstant decompressor instances.
The grouped bank is private code; it reuses the extracted actual RV32 decoder and
exact upstream selection connections. An additional zero-input decoder in the
frontend/grouped bank is combinational constant logic, not a fifth/ninth useful unit.

None changes instruction legality, instruction ordering, pipeline registers, FPU
matches, cache capture holds, or integer-forwarding keep attributes. None promises
an ALM or Fmax result before a real fit. Grouped expansion trades some parallelism
for fewer duplicated decoders; the original timing path is mostly routing, so its
effect must be measured. Control-only aims at simpler Boolean logic with no added
decompressors or pipeline state.

Each --validate run extracts the actual original/candidate frontend cones and checks
all 65,536 compressed encodings in 8 positions, all mask/length/control combinations,
and 1,000,000 random states. It also exhausts all 2,097,152 dispatch control states,
including both issue decisions, fires, and instruction selectors. The reused test
extractor accepts either zero or two RVC helper-module definitions, and removes only
the maxfan declaration annotation from its test input so os30's freeze wire is
recognized. The candidate netlist retains that annotation. Source/input/candidate
hashes and a passed flag are recorded in each manifest only after tests succeed.
Full CPU boot/interrupt/kernel tests are still required for a selected rescue.

Existing helper compatibility (tested only on /tmp copies):
  retime_fpu_matches.pl applies to lean and immediate; it adds 27 comparison FFs.
  retime_fetch_pipeline.pl applies to both; it adds approximately 229 raw/held/enable
  FFs before synthesis. These were deliberately excluded from frontend-only rescue.
  The complete optimize_os20_timing.pl applies to lean. For immediate it rejects
  the final forwarding keep removal because lane-one RS2 no longer exists; all
  preceding contract checks and transformations succeed. The complete helper also
  duplicates the full early RVC bank and previously added 281 ALMs just by relaxing
  forwarding keep boundaries in os20. Do not apply that full package speculatively
  to an area-constrained os30 design.

Structural RVC pipeline candidates, generator-v5
------------------------------------------------
The later full-core small-predictor fit exposed a fetch-valid -> second expanded
instruction path at about 82 MHz. Unlike the combinational alternatives above,
rvc-pipeline.patch relocates decompression across an existing register boundary.
It adds a private --rvc-decompress-at parameter (default 0), leaving all lane-zero
instructions, both original caches and the small predictors intact.

  rvc-stage1  VexiiRiscv_os30_dual_rvc_stage1.v
    Align/select and capture INSTRUCTION_RAW at decode0->1, then expand RVC and
    perform ISA decode in stage1; dispatch remains stage2. No new frontend cycle.
    Expanded instructions no longer need their own stage1 capture registers.
  rvc-pipeline  VexiiRiscv_os30_dual_rvc_pipeline.v
    Same raw capture and stage1 expansion; ISA decode moves to stage2, dispatch3.
    This adds one frontend stage and increases redirect recovery latency.

Reproduction uses prepare.py --profile rvc-stage1 or --profile rvc-pipeline.
Source and outputs are isolated in generator-v5, with manifests and structure-check.
The patch preserves AlignerPlugin.scala line counts so existing postprocessor
guard names remain valid. All four production postprocessors passed unchanged for
both candidates; top-level ports are identical to production.

Control semantics: upstream DecodePipelinePlugin StageLinks transport raw
instruction, PC, prediction metadata, trap state and registered lane-valid bits.
Its existing age-based cancellation handles the relocated/deeper stages. The
DecoderPlugin computes trap/flush ages from its configured stage, so the deeper
candidate also moves these ages consistently. Aligner instruction length, slice
consumption, prediction-boundary handling, and original raw mtval masking are
unchanged. Decompression reads the registered raw instruction; compressed upper
16 bits remain zero for trap reporting. It still expands the exact same low16.

Validation: generator-v5/decompression-equivalence checks the actual extracted old
and relocated expansion cones for all 65,536 low16 encodings and 1,000,000 random
instruction words, including raw-upper-half masking; passed. Validation agent's
full CPU suite passes both A/B with exact expected outputs: stress, I2F, kernels,
instruction-cache capacity, mixed RVC/full32 line boundaries, wrong-path store and
ebreak guards, FP stalls, timer IRQs, and 48 exact illegal-instruction mepc/mtval
traps per memory mode. All 12 A logs are byte-identical to previous lean-smallpred,
including cycles; B adds the expected branch-sensitive overhead. See
validation/summary-frontend.json for measured per-workload differences.

These are timing experiments, not a claim of fitted 92 MHz. Root owns whole-core
fit/timing evidence; generator tests establish semantics and stage structure only.

Immediate fallback and control factoring
----------------------------------------
generator-v6/rvc-immediate combines A's stage1 RVC expansion with the immediate-only
second lane, full I32/D128 caches and small predictors. Lane zero retains all ISA
capabilities. Full CPU validation passes, but the narrower second lane reduces
triangle and shift/branch throughput versus A; see validation/summary-immediate.json.

timing-rescue-v2 contains rvc-stage1-control, rvc-pipeline-control and
rvc-immediate-control. These apply only local_usedmask, early_valid and
direct_dispatch_grants to the corresponding generated CPU. The raw-instruction
aligner cone and complete dispatch cone are checked against pinned source contracts
and exercised exhaustively plus randomized states. All sequential blocks remain
unchanged. The test-only adaptation follows the registered RAW operand for the
relocated decompressor; it does not put expansion back before the register.

Direct RAW floating-point classification
----------------------------------------
A-control's subsequent measured path ran from RAW instruction bits through RVC
expansion and dynamic FP legality into trap capture. raw_fp.py replaces the final
NEED_FPU and NEED_RM assignments and the decoder-local fp_instRm source with exact
RAW-based classification, for both lanes. Its inputs are the already-qualified
timing-rescue-v2 rvc-stage1-control and rvc-immediate-control CPUs. It writes new
raw-fp-v1 directories and refuses to overwrite an existing profile directory.

Reproduction in a fresh artifact workspace:
  python3 tools/experiments/os30_dual_20261004/generator/raw_fp.py

Prerequisites are the generator-v5 reference, its raw-fp-flags-audit-v1 audit, and
the two timing-rescue-v2 inputs. Run Python normally, not with assertions disabled.
The adjacent manifests pin these input/output hashes and the generator script.
raw-fp-v1/raw_fp.snapshot.py preserves the exact script used; source-audit.json
also verifies reapplying transform() reproduces both frozen RTL files byte for
byte, confirms exactly six changed assignment lines, and records the unchanged
sequential block counts (57 for A, 55 for C, including helper modules).

For compressed instructions NEED_FPU is true only for the audited five encoding
families {quadrant,funct3} = {3,5,7,19,23}; NEED_RM is false. For ordinary 32-bit
instructions the original generated Boolean classifiers operate directly on RAW.
The decoder-local rounding-mode selector also reads RAW, removing its dependence
on expansion. This selector is used only by dynamic FP legality. It does not
change the execution unit's rounding input, ISA capability or CSR behavior.

Static ISA legality and decompression fault detection remain unchanged. In
particular, an expanded C.FSD must still fail this RV32F CPU's static ISA test;
absence of a decompression fault does not establish ISA support. The generated
NEED_FPU/RM source cones are canonicalized and compared to the independently
audited reference for both lanes. Their final flags have exactly one consumer,
fp_triggered, which in turn only suppresses LEGAL. fp_instRm only feeds this
local dynamic-legality calculation.

Each candidate's actual emitted cones are compared with its actual reference for
all 65,536 low16 encodings and 1,000,000 random instruction words, each under all
16 FP-enabled/frm states, in both lanes. The comparison is fault OR fp_triggered:
decompressor-rejected encodings have irrelevant expanded outputs, and the unchanged
fault gate already forces those instructions illegal. Both candidates pass; each
manifest is written only after this test finishes. All pipeline registers, port
interfaces, static legality, trap payload logic and execution hardware are intact.

This adds no registers or cycles. Whole CPU tests and fitted timing remain separate
requirements. It specifically removes expansion from dynamic FP classification;
other expanded-instruction static-legality and aligner-ready paths may still limit
the whole-core clock. No timing improvement is asserted from cone equivalence alone.

Registered ready through a flush-aware skid, generator-v7
--------------------------------------------------------
WARNING: This first skid version failed whole CPU interrupt tests. Its frame
flush did not clear the stored per-lane LANE_SEL payload before stage1 capture.
The original artifact remains intact for diagnosis; use the separate corrected
generator-v8 candidate below for further qualification. Its 94.51 MHz standalone
timing result does not qualify the failed design for deployment.

B's fitted full-core CPU paths remained limited by dispatch hazard checks through
the entire decode ready chain into aligner mask/fetch state. rvc-skid keeps B's
registered RVC/decode separation and inserts an upstream S2MLink plus a flush
CtrlLink before the existing decode0->1 StageLink. The input ready is now exactly
!decode_skid_rValid. The extra buffer captures one 278-bit raw-instruction frame
(32 payload fields) plus one occupancy bit. It preserves the existing stage1
registers and adds no unstalled forward cycle. When downstream resumes, a full
skid drains while upstream waits; the upstream frame can bypass on the next cycle,
so this recovery does not impose an output bubble. No execution or cache change.

Reproduce:
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile rvc-skid
  python3 tools/experiments/os30_dual_20261004/generator/timing_rescue.py --lane rvc-skid --profile control --validate
  python3 tools/experiments/os30_dual_20261004/generator/audit_skid.py

Final candidate is timing-rescue-v3/rvc-skid-control/VexiiRiscv.v; original generated
RTL is retained in generator-v7. Control factoring uses the same pinned aligner
and dispatch contracts and passes the same extracted-cone tests. These checks
compare skid versus skid-with-factoring; they are not a claim of cycle identity
between the old CPU and the structurally buffered CPU. CPU validation separately
checks workload outputs, interrupt/flush behavior and measured cycle differences.

The skid's age is 1005, between decode0 age1001 and decode1 age1011. Elaboration
explicitly requires every decode-origin flush source to be strictly older than
1005; actual sources are 21 (fetch prediction), 1021, 2020, 2030 and 2040. Thus a
decode/execute flush cancels the entire skid frame; younger fetch prediction
flushes do not discard accepted instructions. CtrlLink.throwWhen emits both
forgetOne and cancel: S2MLink forgets a held frame, and only while bypassing does
it propagate those requests upstream. Its output valid is suppressed during the
flush. Existing decode1 per-lane flush clearing remains on the original register.

The custom aligner already consumes on ready OR cancel and its own global flush
clears saved mask/last. It now explicitly advertises forgetOne support, matching
Node.arbitrateFrom(Stream)'s adapter contract. This declaration is justified by
the actual paired cancellation path, not by ignoring an unsupported request.
The new skid trapPending is gated by the unflushed output node valid and each
lane's LANE_SEL/TRAP, so stale drained buffer bits cannot keep the PC halted.
Validation should assert that any decode0 forget request also asserts cancel.

Production factor_fetch_ready.pl is deliberately excluded for this new profile:
its guard and expression assume a direct stage0->1 ready chain, which the skid
replaces. The generator's registered-ready handshake is used instead. The three
remaining production helpers pass their unchanged contracts. retime_aligner_pc.pl
only changes the original fetched-word buffer PC, not the new skid/decode PC mux.
No FP retiming helpers or forwarding attribute changes are included.

generator-v7/skid-structure.json records the exact RTL SHA, all input/saved/output
payload mappings, handshake taps, flush-source ages and passed structural checks.
audit_skid.py verifies that upstream ready depends only on occupancy, paired live
cancellation reaches the existing aligner, all payload fields capture under the
same ready and retain their original stage1 registers, flush drops output valid,
and trapPending is qualified. It also reapplies the saved source patch in a
temporary directory and checks byte identity with the elaborated private Scala.
Whole CPU occupancy/stall/flush coverage and fitted area/timing are still required.

Skid lane cancellation correction, generator-v8
----------------------------------------------
Actual CPU monitoring traced the initial failure to an occupied skid flushed
while stage1 was ready: frame valid became zero, but stored lane selects remained
one and were captured into the next stage. Decode lane consumers use LANE_SEL
directly, so this violated the original pipeline's flush contract and corrupted
interrupt handling. Disabling IRQ in the otherwise identical frontend fixture
removed the failure. The trace is retained in validation/rvc_skid_debug.

decode-skid-mask.patch is a separate patch on top of the frozen v7 recipe. It
masks both gate downstream lane selects with !flushIt, matching the original
decode0 bypass's behavior even when the payload comes from the skid storage.
It does not change any stored bits, add registers, or affect an unflushed frame.
Existing v7 source, RTL, manifests, patch and failure logs remain unchanged.

Reproduce the corrected candidate:
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile rvc-skid-masked
  python3 tools/experiments/os30_dual_20261004/generator/timing_rescue.py --lane rvc-skid-masked --profile control --validate
  python3 tools/experiments/os30_dual_20261004/generator/audit_skid.py --masked

Output is timing-rescue-v4/rvc-skid-masked-control/VexiiRiscv.v. Validation must
include the retained failing IRQ fixture and assert zero downstream lane selects
during a skid flush, in addition to whole-frame valid/payload/occupancy checks.
generator-v8/skid-correction-audit.json confirms that all sequential blocks and
procedural combinational logic are identical to v7 after normalizing generated
source-line names. Only the two lane-select continuous assignments are redirected
through the new flush masks. This narrows the fix to the traced failing contract.

Corrected v8 whole-CPU qualification passes all 14 fixed programs/traffic modes.
The monitor exercised 32.32 million cycles, 6.89 million captures, 143,634 occupied
flushes (91,043 while blocked), and 840,334 partial-lane captures. All 278 payload
bits, held/bypass behavior, canceled stage1 lane cleanup, and aligner forget=>cancel
assertions pass. Vector cycles are effectively unchanged versus B; the private
triangle kernel improves about 0.30% from elasticity. See validation/summary-skid.json.
These results qualify behavior; the corrected whole-core fit remains separate.

Immediate-only corrected-skid fallback, generator-v9
---------------------------------------------------
rvc-skid-immediate uses the same corrected skid, registered RVC stage1, ISA decode2,
dispatch3, I32/D128 caches and smaller predictors. Only lane1's ALU pairing set is
narrowed to immediate operations/LUI/AUIPC using the previously tested private
IntAluPlugin option. Lane0 retains all register-register instructions, and lane1's
unused RS2 register-file read/forwarding port disappears. This is an area fallback,
not the preferred full-ALU candidate; pairing losses require direct measurements.

Reproduce:
  python3 tools/experiments/os30_dual_20261004/generator/prepare.py --profile rvc-skid-immediate
  python3 tools/experiments/os30_dual_20261004/generator/timing_rescue.py --lane rvc-skid-immediate --profile control --validate
  python3 tools/experiments/os30_dual_20261004/generator/audit_skid.py --immediate

Final RTL: timing-rescue-v5/rvc-skid-immediate-control/VexiiRiscv.v. The same complete
control-cone tests and source/payload/flush checks pass, including the corrected
per-lane masks. generator-v9/skid-structure.json provides all unchanged skid taps
and payload mappings. Whole CPU validation compares this fallback directly against
the corrected full-ALU skid before any area/timing decision.
