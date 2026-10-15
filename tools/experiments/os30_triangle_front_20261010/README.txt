TFP1 exact triangle preflight with BIM2 direct vertex import

Experimental source-only os30 prototype. Native replay and focused tests
are available; physical fit, timing and hardware behavior are unqualified.
The tested versions are correct but slower. Keep the deployed core.
Best v2 increases warm frame time by 1.823%; latest v3 by 2.041%.
REPORT.txt, results.json and architecture-assessment.json record the final
paired native comparisons, evidence, limitations and architectural findings.

core.patch includes both the BIM2 prerequisites and TFP1 shared-FMA triangle
preflight. It targets the complete qualified physical project identified in
manifest.json: frozen/src/... plus build/ap_core.qsf. QSF source paths become
../frozen so a copied project remains independent. The two feature macros
and five new RTL source entries are os30-only. Clocks, seed3, placement3.0,
constraints, pins, firmware and the generated CPU are unchanged.

sm64.patch is the latest v3 application. sm64-v2.patch is the best tested
variant; each independently targets P/work/sm64, not the other patch.
Both include all BIM2 and TFP1 changes. No ROM, game assets, app binary,
bitstream, fitted database or generated simulation executable is packaged.

The common path supplies raw vertex indices to a bounded preflight that
reuses the existing geometry FMA and scratch RAM for exact CPU-compatible
culling. Successful runs can avoid canonical CPU readback, culling and
vertex-payload conversion. Unsupported batches take the original CPU path;
lazy materialization preserves partial-LOAD survivors and later consumers.
This is not an independent second CPU or a fixed-point winding shortcut.

abi.json specifies TFP1; bram-abi.json specifies BIM2 ownership and imports.
test-evidence.json records the current bound arithmetic/control/lifetime
tests, including the wrapper test's explicit stubbed-producer scope.
recipes/ contains exact archived build/replay/test source, and recipes.json
records original paths and commands. Those recipes require the original
experiment layout, qualified baselines, SDK/toolchains and user game data;
copying only this directory does not provide a standalone game simulation.

Verify packaged files:
  python3 tools/experiments/os30_triangle_front_20261010/verify.py

Verify exact forward/reverse patch round trips and all local evidence:
  python3 tools/experiments/os30_triangle_front_20261010/verify.py --patches --evidence

Rebuild the source package from its immutable local inputs:
  python3 tools/experiments/os30_triangle_front_20261010/package.py

The verifier patches only temporary directories, requires zero fuzz/offset,
checks every pre/postimage hash and verifies reverse restoration. These
commands do not run Quartus or deploy anything. Final reports can be added
then bound with package.py --refresh-manifest-files.
