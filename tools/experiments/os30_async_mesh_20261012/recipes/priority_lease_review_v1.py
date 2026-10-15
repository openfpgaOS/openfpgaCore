#!/usr/bin/env python3
"""Read-only trace analysis of a proposed, UNIMPLEMENTED priority lease.

The report observes old-schedule boundaries. It does not simulate granting a
lease at those boundaries or subtract them from measured frame times.
"""
import csv
import hashlib
import json
from pathlib import Path

A = Path(__file__).resolve().parents[1]
OUT = A / "analysis" / "priority-lease-next-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


inputs = [Path(__file__), A / "analysis/native-appv3.json",
          A / "design/abi.json"]
for name in ("geometry_async_mesh.v", "gpu_mesh_job_front.v"):
    inputs.append(A / "native-v2/frozen/src/fpga/common" / name)
for name in ("gfx_gpu.c", "gfx_pc.c"):
    inputs.append(A / "work-app-v3/src/sm64/sm64/src/pc/gfx" / name)
inputs.append(A / "work-app-v3/src/sdk/include/of_gpu.h")
inputs.append(A / "work-app-v3/evidence/app-build-v3/build-result.json")
inputs.append(A / "validation/async_hooks_v2.py")


def analyze(name):
    base = A / "validation" / name
    paths = [base / "run" / n for n in
             ("async-events.tsv", "async-overlap.tsv", "boundaries.tsv",
              "result.json", "manifest.json")]
    paths.append(base / "check-independent-v1.json")
    for p in paths:
        if p.exists():
            inputs.append(p)
    rows = [{k: int(v) for k, v in r.items()} for r in csv.DictReader(
        paths[0].open(), delimiter="\t")]
    current = previous = None
    context_open = False
    leases = []
    phases = {}
    epoch = rows[0]["epoch"]
    for r in rows:
        # No reset in these captured intervals. A reset would need to clear
        # the inferred context-bundle latch, not inherit its preceding state.
        assert r["epoch"] == epoch
        phase = phases.setdefault(r["phase"], {"jobs": 0, "context_words": 0})
        phase["jobs"] += r["job_take"]
        phase["context_words"] += r["ctx_take"]
        if r["lease_pending"] and not (previous and previous["lease_pending"]):
            assert current is None
            current = dict(phase=r["phase"], request_cycle=r["cycle"],
                           target=r["lease_target"], completed=r["completed_serial"],
                           context_open_at_request=context_open,
                           active_at_request=bool(r["active"]),
                           first_observed_boundary_cycle=None)
        # These fields cover complete existing stream ownership. raw_state==0
        # alone is insufficient: cull/packet/repair still need raw/list/context.
        if (current and r["lease_pending"] and
                current["first_observed_boundary_cycle"] is None and
                not context_open and r["stream_ready"] and
                not any(r[k] for k in ("active", "pending", "aborting", "view_active"))):
            current["first_observed_boundary_cycle"] = r["cycle"]
        if current and r["lease_granted"] and not (previous and previous["lease_granted"]):
            current["grant_cycle"] = r["cycle"]
            if current["first_observed_boundary_cycle"] is None:
                current["first_observed_boundary_cycle"] = r["cycle"]
            current["measured_wait_cycles"] = r["cycle"] - current["request_cycle"]
            current["cycles_before_boundary"] = (
                current["first_observed_boundary_cycle"] - current["request_cycle"])
            current["later_original_schedule_cycles"] = (
                r["cycle"] - current["first_observed_boundary_cycle"])
            leases.append(current)
            current = None
        # A partial or complete context remains owned until its JOB is taken,
        # including parser HEADER gaps between context chunks and JOB fields.
        if r["ctx_take"]:
            context_open = True
        if r["job_take"]:
            context_open = False
        previous = r
    assert current is None
    overlaps = [{k: int(v) for k, v in r.items()} for r in csv.DictReader(
        (base / "run/async-overlap.tsv").open(), delimiter="\t")]
    for p in (1, 2):
        records = [x for x in leases if x["phase"] == p]
        measured = sum(x["measured_wait_cycles"] for x in records)
        # Independent cycle-by-cycle hook's lease-pending bit.
        assert measured == sum(r["cycles"] for r in overlaps
                               if r["phase"] == p and r["mask"] & 1024)
        phases[p].update(
            leases=len(records), measured_wait_cycles=measured,
            cycles_before_first_observed_boundary=sum(x["cycles_before_boundary"] for x in records),
            later_original_schedule_cycles=sum(x["later_original_schedule_cycles"] for x in records),
            maximum_target_minus_completed=max(x["target"] - x["completed"] for x in records),
            requests_during_context=sum(x["context_open_at_request"] for x in records),
            full_context_data_words=28 * phases[p]["jobs"],
            extra_context_data_words=28 * phases[p]["jobs"] - phases[p]["context_words"],
        )
    return dict(phases=phases, leases=leases)


runs = {n: analyze(n) for n in
        ("candidate-v2-observer2", "candidate-appv2", "candidate-appv3")}
warm = runs["candidate-appv3"]["phases"][2]
assert warm["measured_wait_cycles"] == 599773
assert warm["cycles_before_first_observed_boundary"] == 17575
assert warm["later_original_schedule_cycles"] == 582198

requirements = [
    "Grant only at complete current-job ownership release, including packet output or repair END and accepted DMA/alias drain. raw_done/raw_state==0 alone is unsafe.",
    "Publish a full immutable 28-word context for every JOB. Older queued dirty-delta contexts cannot survive a newer CPU legacy LOAD overwriting the shared matrix/scalars.",
    "Lock from the first accepted CONTEXT word through JOB acceptance; active ownership then covers JOB completion. A parser HEADER state between chunks or before JOB is not a safe preemption boundary.",
    "Resolve simultaneous acquire/context/job edges explicitly: acquisition must prevent starting a new context bundle, and a bundle already started must finish its associated job before granting.",
    "Before any wait whose progress can depend on earlier async ingress, materialize all still-lazy canonical raw rows and release the legacy lease. This includes source-pool async_drain/recycle, GPU idle/fence/texture/DMA waits, and ingress reservation/full waits.",
    "A safe materialize-and-yield hook must not emit commands or recursively wait on the blocked worker. Releasing as soon as the first normal CPU reader finishes materializing all rows is preferable to holding an idle lease across arbitrary renderer work.",
    "Queued source pool allocations remain immutable until their true completed sequence, regardless of an earlier priority grant. Priority lease is not an async completion fence.",
    "Keep GPU output order unchanged: only future CPU geometry computation moves earlier. Ordinary CPU draw/state packets stay behind all older queued jobs.",
    "Preserve granted legacy lease/raw data across consumer reset, cancel pending acquire, advance repair epoch/token, and invalidate any partial context bundle. Full CPU/fabric reset still clears everything.",
    "Avoid async starvation: retained CPU ownership cannot span unbounded renderer work with a blocked queue; bound ownership by raw materialization and explicit progress waits.",
]
tests = [
    "Acquire before first context, during every context word, between context chunks and JOB, on job acceptance/completion, during repair, and during output-full backpressure.",
    "Legacy overwrites all matrix/scalar fields while older immutable jobs remain queued; each later actual START must equal its own captured full context and source bytes.",
    "Full ingress plus pending older job plus held legacy lease, source pool recycle/drain, GPU fence/idle/texture waits: each must materialize/yield and make progress without losing raw rows.",
    "Partial G_VTX survivors, fog alpha, first CPU canonical read, late clipping repair, and repeated lease release/reacquire preserve effective draws/FCSR/depth/framebuffer/state.",
    "Consumer reset before grant, during legacy DMA, during partial context, and during repair; stale ticket/token/END rejected while valid legacy dependencies survive.",
    "Fresh same-ELF full native control/candidate replay, unmodified GPU-drained boundaries, strict output/lifetime checks, then elapsed cycles. No subtractive timing estimate or fit/deployment before a measured win.",
]
report = dict(
    status="READ_ONLY_HYPOTHESIS_NOT_IMPLEMENTED",
    scope="Three existing two-interval native replays. No new runtime, source change, performance prediction, physical fit or deployment.",
    recommendation="The smallest credible structural experiment is a priority legacy raw-engine lease at a complete job/context boundary, with full per-job contexts and materialize-before-progress-wait ownership. It targets the observed mixed CPU/async handoff rather than adding a clipper, FPU, or larger queue.",
    method="Infer context-open from accepted ctx_take until job_take. For each existing lease-pending interval, find the first observed no-open-context, inactive, nonpending, nonaborting, alias-idle, stream-ready state. Compare its timestamp with the actual ordered-target grant. Validate total waits against independent cycle-by-cycle overlap counters.",
    interpretation="The later-original-schedule window is neither guaranteed saved CPU cycles nor a frame speedup upper bound. A changed grant changes later scheduling, source materialization, ring pressure and raster feed; original GPU fragment work still has to execute. Even the grant edge itself would take implementation-specific cycles.",
    evidence=runs,
    warm_appv3_context_tradeoff="76 jobs currently accept 756 context data words; full contexts require 2,128, adding 1,372 data words before header changes. This is a real software/ring cost, not free restoration.",
    current_result="App v3 removes all warm repairs but still takes 5,105,025 cycles versus same-ELF control 4,633,123. This supports testing the remaining handoff, not treating clipping hardware as the immediate remedy.",
    requirements=requirements,
    decisive_tests=tests,
    non_goals=["No claim that existing raw compute can be preempted mid-job.",
               "No new FPU/BRAM proposed, but logic/clock fit remains unproven.",
               "No existing negative-result source, receipt, or selection is superseded."],
    inputs_sha256={str(p.relative_to(A)): sha(p) for p in inputs},
)
OUT.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
text = """Optional next experiment: priority legacy raw-engine handoff

Status: read-only hypothesis. No implementation, timing prediction, new fit or deployment.

App v3 eliminates warm repairs yet remains slower: 5,105,025 cycles versus
4,633,123 for its unchanged-core, same-ELF control. The raw engine is shared
between autonomous jobs and legacy CPU work. Current acquire waits for every
earlier submitted job to publish its entire output before returning the raw bank.

Observed lease windows (same old schedule; these are NOT predicted savings):
                         warm leases  measured wait  first-boundary delay  later backlog
original app v1                  22        971,398                90,114        881,284
MMIO fast-path app v2             22        922,233                81,444        840,789
repair-hint app v3                28        599,773                17,575        582,198

The inferred boundary excludes every unfinished context bundle and every active
job, packet, repair, DMA or alias owner. App v3 has two requests arriving during
an open context and up to nine not-yet-completed jobs at acquire. Counter totals
exactly match the separate cycle-by-cycle lease-pending counters.

This makes priority acquisition a more direct, smaller structural hypothesis than
adding a clipping pipeline or enlarging a queue. It can let CPU fallback compute
begin before older immutable jobs have all reached the head. Raster work remains
ordered and still has to run: the 582,198-cycle window must not be subtracted from
frame time. The changed schedule can move waits elsewhere or starve raster feed.

Minimum safe contract:
""" + "\n".join(f"- {x}" for x in requirements) + """

The progress-wait requirement is broader than ingress-full handling. In the
current code, source-pool recycling calls gfx_gpu_async_drain, and SDK waits also
cover fences, GPU idle, texture/translucency and DMA. All can wait on a job blocked
by the retained lease after priority acquisition. The present ordered grant
implicitly prevents this cycle; priority would remove that guarantee.

Full context has a measurable cost: warm v3's 756 context data words become
2,128 for 76 jobs, an extra 1,372 data words plus any header difference. A full
CONTEXT followed by JOB still needs a bundle lock; full context alone does not
make preemption between its last word and JOB safe.

Required validation before judging the experiment:
""" + "\n".join(f"- {x}" for x in tests) + """

No frozen source or previous negative-result receipt changes. Exact trace/source
hashes, per-acquire records and reproducible analysis are in the sibling JSON and
priority_lease_review_v1.py. No claim about ALM/BRAM fit, achievable MHz or FPS.
"""
OUT.with_suffix(".txt").write_text(text)
print(json.dumps({"status": report["status"], "warm": warm,
                  "json_sha256": sha(OUT.with_suffix('.json')),
                  "txt_sha256": sha(OUT.with_suffix('.txt'))}, indent=2))
