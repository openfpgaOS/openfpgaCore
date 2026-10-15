Quake2 native MD2 core v5

STATUS: PENDING: pointer-backpressure silent-core fit and physical timing selection incomplete; no bitstream selected or deployed.

No bitstream is included. Source qualification and physical selection remain pending.
The archive preserves every prepared source byte and its original preparation receipt.
Verification derives the exact fit input by renaming build-receipt.json to
parent-preparation-receipt.json and, for --without-mixer, replacing the one
INCLUDE_HW_MIXER QSF macro with NATIVE_MESH_SILENT_AUDIO. All resulting file
hashes must equal the recorded fit input; no other transformation is accepted.

python3 package.py verify
python3 package.py stage /tmp/native-source-NEW
python3 package.py check-stage /tmp/native-source-NEW
python3 worker-tests/run_worker_tests.py /tmp/native-tests-NEW
python3 build_core.py /tmp/native-source-NEW /tmp/native-fit-NEW --without-mixer

All destinations must be new. Build requires pinned Quartus 25.1; strict
all-corner checks retain failures. No deployment or seed sweep is performed.
create_core_bundle.py preserves the reproducible packaging generator.
Worker tests are reused only after every tested source hash matches the recorded suite.
Silent audio checks and physical timing evidence are recorded separately.
Historical parent receipts and review notes do not qualify this revision.
Keep new build/test outputs outside this immutable source package.
