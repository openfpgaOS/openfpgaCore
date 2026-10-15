#!/usr/bin/env python3
"""Source-bound F24 dynamic VTX/TRI adjacency; no replay or app mutation.

Counts describe the measured scene and structural admission, not saved cycles.
Output lives beside this script; every original input remains untouched.
"""
import bisect
import collections
import csv
import hashlib
import json
import struct
from pathlib import Path
from elftools.elf.elffile import ELFFile

HERE = Path(__file__).resolve().parent
P = HERE.parents[2]
B = P / "priority-mesh-v1"
V = B / "validation"
R = V / "candidate-v2/run"
C = V / "control-v2/run"
FIX = V / "fixture-v2"
ELF = Path(json.loads((R.parent / "build.json").read_text())["elf"])
APP = B / "work-app-v2/src/sm64/sm64/src/pc/gfx/gfx_pc.c"

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def rows(p):
    with p.open() as f:
        return [{k: int(v, 0) for k, v in r.items()}
                for r in csv.DictReader(f, delimiter="\t")]

def mmio(p):
    return [(int(c), k, int(a) // 4, int(v))
            for c, k, a, v in (x.split() for x in (p / "mmio.tsv").read_text().splitlines())]

with ELF.open("rb") as f:
    e = ELFFile(f)
    segments = [(s["p_vaddr"], s.data()) for s in e.iter_segments()
                if s["p_type"] == "PT_LOAD"]
    sy = e.get_section_by_name(".symtab")
    low = sy.get_symbol_by_name("_start")[0]["st_value"]
    high = sy.get_symbol_by_name("__EH_FRAME_BEGIN__")[0]["st_value"]

def immutable(a, n):
    if not low <= a <= high - n:
        return None
    for base, data in segments:
        if base <= a and a + n <= base + len(data):
            return data[a - base:a - base + n]
    return None

def pair(a):
    data = immutable(a, 8)
    return struct.unpack("<II", data) if data is not None else None

def indices(a, b):
    return [[(w >> s & 255) // 2 for s in (16, 8, 0)]
            for w in ((a, b) if a >> 24 == 6 else (a,))]

def dead_suffix(a, count):
    """Exactly the frozen conservative app proof, with diagnostic reason."""
    alive = set(range(count))
    for steps in range(256):
        p = pair(a)
        if p is None:
            return False, "outside_immutable", steps
        w, v = p
        op = w >> 24
        if op == 1:
            n, end = w >> 12 & 255, w >> 1 & 127
            if not (0 < n <= 32 and n <= end <= 32):
                return False, "VTX_shape", steps
            alive.difference_update(range(end - n, end))
            if not alive:
                return True, "overwritten", steps + 1
        elif op in (5, 6):
            if any(x >= 32 or x in alive for tri in indices(w, v) for x in tri):
                return False, "live_TRI_read", steps
        else:
            return False, f"state_or_list_0x{op:02x}", steps
        a += 8
    return False, "scan_limit", 256

calls, baseline_calls = rows(R / "vtx-calls.tsv"), rows(C / "vtx-calls.tsv")
identity = ("phase", "a0", "a1", "a2", "s1", "ra")
assert [[r[k] for k in identity] for r in calls] == [[r[k] for k in identity] for r in baseline_calls]
boundaries = [int(x.split()[1]) for x in (R / "boundaries.tsv").read_text().splitlines()]
data = [(cycle, value) for cycle, kind, reg, value in mmio(R) if kind == "GPU" and reg == 1]
assert struct.pack(f"<{len(data)}I", *(v for _, v in data)) == (R / "commands.bin").read_bytes()
jobs = {}
i = 0
context = None
while i < len(data):
    cycle, header = data[i]
    count, op = header & 8191, header >> 24
    d = [v for _, v in data[i + 1:i + count + 1]]
    assert len(d) == count
    if op == 0x63:
        assert header == 0x6300001d and len(d) == 29 and d[0] == 0
        context = d[1:]
    elif op == 0x64:
        assert header == 0x6400000b and len(d) == 11 and context is not None
        phase = bisect.bisect_right(boundaries, cycle)
        seq, source, vtx, cmdsrc, config, flags, u, v, cpusrc, cpucmd, alpha = d
        matches = [j for j, c in enumerate(calls) if c["phase"] == phase and
                   (c["a0"], c["a1"], c["a2"], c["s1"] + 8) ==
                   (vtx >> 12 & 255, 0, cpusrc, cpucmd)]
        assert len(matches) == 1
        assert pair(cpucmd - 8)[0] == vtx
        jobs[matches[0]] = {"serial": seq, "context": context,
                            "material_cull_alpha": [config >> 8, flags, u, v, alpha],
                            "fields": d}
        context = None
    i += count + 1

# Reference raw contexts consumed by exact CPU call, not reordered candidate START.
initial_path = FIX / "capture/run-reference/snapshot/geometry-registers.bin"
initial = struct.unpack("<80I", initial_path.read_bytes())
raw_state = [initial[64 + i] for i in range(16)] + list(initial[5:13])
mapping = {**{64 + i: i for i in range(16)}, **{5 + i: 16 + i for i in range(8)}}
stamps = [c["cycle"] for c in baseline_calls]
raw_contexts = {}
for cycle, kind, reg, value in mmio(C):
    if kind != "GEO":
        continue
    if reg in mapping:
        raw_state[mapping[reg]] = value
    elif reg == 2 and value & 1:
        call = bisect.bisect_right(stamps, cycle) - 1
        assert call >= 0 and call not in raw_contexts
        raw_contexts[call] = raw_state.copy()

def consumed(ctx):
    return ctx[:17] + (ctx[17:22] if ctx[23] else []) + ctx[22:24]

records = []
for i, c in enumerate(calls):
    a = c["s1"]
    p = pair(a)
    q, nc, nt, own, reason = a + 8, 0, 0, True, ""
    if p is None:
        reason = "mutable_command"
    elif p[0] >> 24 != 1:
        reason = "not_VTX"
    elif c["a1"] or not 0 < c["a0"] <= 32:
        reason = "shape"
    elif immutable(c["a2"], 24 * c["a0"]) is None:
        reason = "mutable_source"
    else:
        # These measured immutable words are direct ELF pointers, no segment map.
        assert p[1] == c["a2"]
        while True:
            v = pair(q)
            if v is None or v[0] >> 24 not in (5, 6):
                break
            nc += 1
            for tri in indices(*v):
                nt += 1
                own &= all(x < c["a0"] for x in tri)
            q += 8
        if not nc:
            reason = "no_TRI"
        elif not own:
            reason = "mixed_slots"
        elif nt > 64 or nc > 64:
            reason = "per_task_size"
    dead, dead_reason, steps = dead_suffix(q, c["a0"]) if not reason else (False, "not_candidate", 0)
    rec = {"call": i, "phase": c["phase"], "address": a, "source": c["a2"],
           "end": q, "count": c["a0"], "commands": nc, "triangles": nt,
           "reason": reason, "dead_after": dead, "dead_reason": dead_reason,
           "dead_scan_commands": steps, "next_opcode": pair(q)[0] >> 24 if pair(q) else None,
           "async": i in jobs, "reference_raw": i in raw_contexts}
    if i in jobs:
        rec["job"] = jobs[i]
        assert dead and not reason
        assert consumed(jobs[i]["context"][:24]) == consumed(raw_contexts[i])
    records.append(rec)

def chains(phase, mode):
    result, current = [], []
    for rec in records:
        if rec["phase"] != phase:
            continue
        include = not rec["reason"] and (mode != "accepted" or rec["async"])
        if not include:
            if current:
                result.append(current)
                current = []
            continue
        if current and (current[-1]["end"] != rec["address"] or current[-1]["call"] + 1 != rec["call"]):
            result.append(current)
            current = []
        current.append(rec)
    if current:
        result.append(current)
    return result

ledger, summary = {}, {}
for phase in (1, 2):
    rs = [r for r in records if r["phase"] == phase]
    groups, accepted = chains(phase, "all"), chains(phase, "accepted")
    for g in groups:
        contexts = [consumed(raw_contexts[x["call"]]) for x in g if x["reference_raw"]]
        assert not contexts or all(c == contexts[0] for c in contexts)
    for g in accepted:
        key = lambda r: (r["job"]["context"], r["job"]["material_cull_alpha"])
        assert all(key(x) == key(g[0]) for x in g), "adjacent accepted contexts differ"
    def describe(g):
        return {"calls": [x["call"] for x in g], "loads": len(g),
                "triangles": sum(x["triangles"] for x in g),
                "command_words": 2 * sum(1 + x["commands"] for x in g),
                "source_begin": g[0]["address"], "source_end": g[-1]["end"],
                "last_boundary_opcode": g[-1]["next_opcode"],
                "accepted_loads": sum(x["async"] for x in g),
                "dead_proven_loads": sum(x["dead_after"] for x in g),
                "dead_proven_prefix": next((i for i, x in enumerate(g) if not x["dead_after"]), len(g))}
    ledger[str(phase)] = {"calls": rs, "all_chains": [describe(g) for g in groups],
                          "accepted_chains": [describe(g) for g in accepted]}
    summary[str(phase)] = {
        "CPU_VTX_calls": len(rs), "reference_raw_LOADs": sum(r["reference_raw"] for r in rs),
        "structural_reasons": dict(collections.Counter(r["reason"] or "candidate" for r in rs)),
        "candidate_triangles": sum(r["triangles"] for r in rs if not r["reason"]),
        "dead_suffix_candidates": sum(r["dead_after"] for r in rs),
        "dead_suffix_reasons": dict(collections.Counter(r["dead_reason"] for r in rs if not r["reason"])),
        "candidate_chain_count": len(groups),
        "candidate_chain_length_histogram": dict(sorted(collections.Counter(map(len, groups)).items())),
        "max_candidate_chain_loads": max(map(len, groups)),
        "max_candidate_chain_triangles": max(sum(x["triangles"] for x in g) for g in groups),
        "accepted_jobs": sum(r["async"] for r in rs),
        "accepted_triangles": sum(r["triangles"] for r in rs if r["async"]),
        "accepted_chain_count": len(accepted),
        "accepted_chain_length_histogram": dict(sorted(collections.Counter(map(len, accepted)).items())),
        "accepted_identical_context_material_chains": len(accepted),
        "submission_count_reduction_existing_admission": sum(r["async"] for r in rs) - len(accepted),
        "max_accepted_chain_loads": max(map(len, accepted)),
        "max_accepted_chain_triangles": max(sum(x["triangles"] for x in g) for g in accepted),
        "dead_proven_chain_prefix_loads": sum(describe(g)["dead_proven_prefix"] for g in groups),
        "dead_proven_nonempty_chain_prefixes": sum(describe(g)["dead_proven_prefix"] > 0 for g in groups),
    }

inputs = [Path(__file__), ELF, APP, R / "vtx-calls.tsv", C / "vtx-calls.tsv",
          R / "mmio.tsv", C / "mmio.tsv", R / "boundaries.tsv", R / "commands.bin",
          R.parent / "build.json", C.parent / "build.json", initial_path,
          R.parent / "check-independent-v2.json"]
out = {"status": "PASS_EXACT_F24_COMMAND_ADJACENCY", "phases": summary,
       "limits": [
           "Dynamic call order is exact native F24; command and vertex bytes are immutable ELF inputs.",
           "All structural chains stop on every state/list command; no cross-call or mutable graph is assumed.",
           "Reference consumed raw context is identical within each chain; accepted chains additionally match all28 context words and material/cull/alpha.",
           "Structural candidates are not material or numeric eligibility claims. Repair hints and warm material scope must be joined separately.",
           "Context/source preparation remains cold work; fewer CPU submissions is not a measured cycle or FPS gain.",
           "Direct ELF vertex pointers were verified for this trace only; generic segmented pointers need explicit resolution.",
           "A terminal legacy LOAD or proven suffix must restore/discard every deferred canonical slot before a CPU reader.",
       ], "inputs_sha256": {str(p): sha(p) for p in inputs}}
(HERE / "ledger.json").write_text(json.dumps(ledger, indent=2) + "\n")
out["ledger_sha256"] = sha(HERE / "ledger.json")
(HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(summary, indent=2))
