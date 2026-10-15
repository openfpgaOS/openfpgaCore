QGF1 asynchronous queued geometry experiment for os30

This is a source-only experiment package. It does not install an app, update
an SD card, run Quartus, or establish FPGA timing/hardware qualification.
The final paired results and qualification status belong to REPORT.txt and
results.json. The tested queued implementations preserve rendering but do
not produce a net frame-time improvement. The larger native FIFO is within
329 warm cycles of its baseline (+0.0075%), effectively a tie. No Quartus
build, timing sweep or deployment was performed for this prototype.
Package tooling deliberately requires those root-owned final
reports before creating a final manifest; no outcome is inferred here.

The CPU admits a supported opaque triangle run from immutable LOAD metadata,
then submits raw vertex indices and render context without synchronously
reading back vertices or waiting for a cull mask. A queued worker shares the
existing geometry FMA, culls exactly, converts kept vertices and emits the
ordinary ordered vertex/indexed commands ahead of the rasterizer. BIM2 owns
two immutable snapshots. Rare arithmetic repair uses only saved geometry;
the canonical CPU fallback remains available for unsupported admission.
abi.json defines QGF1 and bram-abi.json defines its BIM2 prerequisites.

core.patch targets the qualified physical project's frozen/src/... files
and build/ap_core.qsf, not the repository's current production tree. It is
derived from the selected staged physical copy, with four existing sources
changed and four new sources (361 total). The staged QSF is reproduced with
only its frozen-source path prefix relocated to ../frozen. Seed3, 90MHz and
placement effort3.0 are preserved. The selected stage and every source hash
are recorded in manifest.json; fit/timing outcomes are recorded separately.

sm64.patch contains application v2; sm64-v1.patch contains application v1.
Each independently targets the frozen P/work/sm64 baseline, never the other
patch. V2 reduces repair-status polling at display-list boundaries and raw
LOAD waits; ordered queue ownership, repair arithmetic and RTL are shared.
Neither patch is a game binary. ROMs, generated game assets, executables,
bitstreams and build databases are deliberately excluded.

counterfactual-2048.patch independently targets Q/native-v1, changing only
the output FIFO parameter9->11 (512->2048 words). It is a native capacity
experiment and is NOT part of core.patch, whose staged FIFO remains512.
counterfactual-2048.json binds the delta and native build. The larger FIFO
retains the original4096-word ingress; no memory-fit claim is made.
Archived observer-v2, replay_v3, strict native audits, and the separate
2048-word pressure/wrap/repair test receipt cover this comparison.

Source reproduction requires the original qualified project, original app
baseline, toolchain/SDK containers and your game data. P denotes
docs/measurements/os30-psram32-20261009; Q denotes P/queued-front-v1.
Both baselines and all postimages are bound by the manifest. To reproduce a
variant, copy its baseline into a NEW isolated directory and apply exactly
one corresponding patch there with patch -p1 --batch --forward --fuzz=0.
Never apply the core patch directly to a different production revision.
The verifier below performs this operation only in temporary directories
and checks exact reverse restoration as well.

recipes/ archives exact build/capture/native/test source; recipes.json
records the original commands and layout. These scripts reference original
evidence paths and dependencies. They are not a standalone simulator or a
request to rerun a frozen evidence directory. Use fresh output directories
when reproducing, keep the same app ELF on both sides of a native comparison,
and validate semantic draw/state, every color/depth buffer and CPU FCSR before
interpreting timing. Focused tests state their stubbed-producer scopes.

Check packaged bytes (after final package build):
  python3 tools/experiments/os30_queued_front_20261010/verify.py

Check exact patch round trips and all local source/evidence bindings:
  python3 tools/experiments/os30_queued_front_20261010/verify.py --patches --evidence

Prepare tooling and validate source inputs without final reports:
  python3 tools/experiments/os30_queued_front_20261010/package.py --preflight

Rebuild the final source package after Q/summary.json and Q/REPORT.txt exist:
  python3 tools/experiments/os30_queued_front_20261010/package.py

The final summary may select package_physical_stage; --physical-stage can
explicitly select the same independently staged source. Packaging never
launches its archived run_candidate.py. Any physical build, all-corner timing
qualification and subsequent deployment are separate work.
