AJ02 priority mesh: CPU waiting removed, no net speedup

Keep the existing deployed core. Final F24 is 1.197% slower in
the warm interval and 5.579% slower across both intervals than its
identical-ELF qualified control. No physical fit, seed sweep or deployment ran.

The identical-F23 hardware comparison proves the intended CPU unblock: warm
raw-lease waiting fell from 204,231 to 8,372 cycles
(95.90%), reducing that complete warm interval by
2.865% against the previous priority experiment. This is not a speedup
against the qualified control. REPORT.txt and reviews/conclusion.json contain
the exact measured table, actual overlap proof, decision and limitations.

Selected sources: native-v2 and F24 PRI2 app. The existing survivor M10K stores
converted packet data so raw/FMA can be reused while old output drains. Physical
area/timing remain unqualified. Five strict native receipts, two fresh fixtures,
F23 source history, all181 selected native source hashes, six exact source patch
round trips and the failed pre-binding observer attempt are recorded explicitly.
Game assets, binaries and Quartus databases are not included.

Verify locally:
  python3 verify.py --patches --evidence
  python3 run_tests.py --output /tmp/aj02-portable-tests

The portable actual-RTL wrapper/combined suites passed 71,729 checks from
this bundle. Full native replay also needs separately held game inputs/tooling;
these are hash-bound, not distributed. Workspace recipes preserve original paths
for provenance. core.patch targets the qualified physical frozen tree;
recipes/prepare_physical.py records source/QSF integration but never compiles.
Historical priority-only RTL and F23 app patches are retained for review.

These are two bounded native intervals in one scene, not sustained Pocket FPS,
all-level coverage, or a Quake2 result. No script automatically fits or deploys.
