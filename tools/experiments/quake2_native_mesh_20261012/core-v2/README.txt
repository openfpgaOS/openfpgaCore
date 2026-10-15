Quake2 native MD2 core v2

STATUS: Physical fit pending; mixer retained. Functional prototype, not physically qualified or selected for deployment.

Exact prepared source matches the named fit input receipt. No bitstream is included.
The independent review note describes historical v1 findings and is not a selection
or physical timing claim for this revision. Current qualification is stated above.

python3 package.py verify
python3 package.py stage /tmp/native-source-NEW
python3 package.py check-stage /tmp/native-source-NEW
python3 worker-tests/run_worker_tests.py /tmp/native-tests-NEW
python3 build_core.py /tmp/native-source-NEW /tmp/native-fit-NEW

All destinations must be new. Build requires the pinned Quartus 25.1 installation;
strict all-corner checks retain timing failures. No deployment or seed sweep.
Source includes historical parent preparation receipts/timing text, not current
qualification evidence. QSF selects common/os30/gpu_core.v. Physical evidence
belongs alongside this immutable package. Keep all test/build outputs outside it.
