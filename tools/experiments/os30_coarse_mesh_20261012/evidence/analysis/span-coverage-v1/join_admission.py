#!/usr/bin/env python3
"""Join independently derived traversal and routing; does not change either."""
import collections
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ledger_path = HERE / "ledger.json"
route_path = HERE.parent / "admission-v1/result.json"
ledger = json.loads(ledger_path.read_text())
routing = json.loads(route_path.read_text())
route = {x["call"]: x for x in routing["details"]}
summary = {}
for phase in ("1", "2"):
    rs = {x["call"]: x for x in ledger[phase]["calls"]}
    counts = collections.Counter()
    tails = []
    for g in ledger[phase]["all_chains"]:
        members = [rs[x] for x in g["calls"]]
        for r in members:
            assert route[r["call"]]["command"] == r["address"]
            if route[r["call"]]["route"] != "topology_cache_probe_exhausted":
                assert route[r["call"]]["triangles"] == r["triangles"]
        terminal_covers = members[-1]["count"] == max(x["count"] for x in members)
        if terminal_covers:
            counts["all_terminal_max_chains"] += 1
            counts["all_terminal_max_loads"] += len(members)
            if len(members) > 1:
                counts["multi_terminal_max_chains"] += 1
                counts["multi_terminal_max_loads"] += len(members)
        first = next((i for i, x in enumerate(members) if x["async"]), None)
        if first is None:
            continue
        tail = members[first:]
        counts["tails_starting_with_observed_accepted_job"] += 1
        counts["tail_loads"] += len(tail)
        counts["tail_triangles"] += sum(x["triangles"] for x in tail)
        counts.update("tail_route_" + route[x["call"]]["route"] for x in tail)
        covers = tail[-1]["count"] == max(x["count"] for x in tail)
        if covers:
            counts["tail_terminal_max_groups"] += 1
            counts["tail_terminal_max_loads"] += len(tail)
        tails.append({"calls": [x["call"] for x in tail],
                      "source_begin": tail[0]["address"], "source_end": tail[-1]["end"],
                      "vertex_counts": [x["count"] for x in tail],
                      "routes": [route[x["call"]]["route"] for x in tail],
                      "triangles": sum(x["triangles"] for x in tail),
                      "terminal_LOAD_overwrites_all_prior_slots": covers})
    summary[phase] = {"counts": dict(counts), "tails": tails}

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

out = {"status": "PASS_INDEPENDENT_ROUTE_JOIN", "phases": summary,
       "limits": [
           "Tail membership is structural, not newly measured async admission or a speed prediction.",
           "A previously accepted job establishes material eligibility at that point; this report does not fabricate cold-material state for preceding jobs.",
           "A terminal smaller LOAD leaves older higher slots live. Preserving only its current-count alias view is insufficient.",
           "Existing-admission group caches must honor repair-hint changes, e.g. invalidate on a global hint epoch; source generation alone does not preserve current routing.",
           "A common group context must be restored after any priority legacy grant, or the ownership policy must explicitly hold the group. Context correctness cannot be inferred from upload order.",
       ], "inputs_sha256": {str(p): sha(p) for p in (Path(__file__), ledger_path, route_path)}}
(HERE / "admission-join.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps({p: x["counts"] for p, x in summary.items()}, indent=2))
