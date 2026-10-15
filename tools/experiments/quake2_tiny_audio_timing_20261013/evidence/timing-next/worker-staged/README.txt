Geometry worker: stage native admission and cull predicates

Frozen source: dc82691f019420352905292bb60d71943d989a42e4b2608feeefa218fe8f50f9
Direct parent: worker-local-copy/gpu_geometry_worker.v, 9096979f637f1e5947d8b9ed6bc280255dc73b3c68bc968e19edad80d722f20f.
Only this worker changes. Dock, audio, GPU rasterizer, CPU, clocks and constraints are outside this candidate. No Quartus builds were launched by this component owner.

Native admission lifetime
-------------------------
The native_job_valid cone combines the source-buffer bounds, descriptor validity, surface validity, positive near plane and stream bounds. Its last descriptor operand is loaded by COPY word 35. Native commands have 164 payload words; their final word 163 updates palette RAM only. Evaluate the predicate on that final word, and reuse the idle native_header_valid register through COMMIT and N_START. None of the predicate operands can change during those states: command ingestion/configuration writes occur only in their own state and mode, and the native reader cannot update native_stream before N_START. Clock-enable pauses retain the operands and cached predicate. Reset abandons the command and returns to HEADER, so a fresh descriptor is staged before its next N_START. The header register's next owner overwrites it in NP_HEADER before N_HEADER_CHECK. No register or cycle is added by this change.

Cull scheduling
---------------
Old sequence: F issues product1; G waits; H captures product1 and issues product2; I waits; J tests product1 versus product2.
New sequence: F issues product1; G issues product2 as product1 reaches the DSP register; H captures product1 as product2 reaches the DSP register; I registers equality and determinant sign; J makes the same branch decision from those two bits.
All states retain their cycle numbers. Exactly two predicate flip-flops are added; no latency, command/output sequence, queue capacity, arithmetic precision or feature changes. Every route to J passes through I, so the predicate flip-flops need no initialization before use.

The product register now freezes whenever enable is zero. Native and particle modes already did so. This is necessary for the overlapping cull issues: a disabled H must retain product1 until H captures it. Other legacy consumers have an enabled wait/capture stage after operand issue, so they observe the same product even though unused intermediate products can differ during a pause. The tests compare every architectural port, state, mode and published output on every clock; private pipeline registers are intentionally not required to remain identical.

Validation
----------
Run: python3 run_lockstep.py --output NEW_DIRECTORY
Only Verilator and a C++ compiler are required. The reference file is exactly the direct909 parent with its module name changed.

Final receipt: gates-v2/receipt.json.
Four native/particle parameter combinations pass 27,163,742 cycle comparisons. All four transcripts are byte-identical to the direct parent's original fixture results. An additional directed run passes 10,444,712 cycles with 17-clock disable intervals at 52 active mode/stage pairs and each of 11 native MAC columns. This includes all five compute modes: raw, clip, cached cull, native mesh and particles. The fixture compares the staged cull equality/sign at every CULL_J and cached admission against the parent's live native_job_valid at every N_START, including paused cycles. Existing malformed descriptor, boundary, queue wrap, backpressure, busy publication and reset/rebind tests are retained.

The first gates-v1 directed run reached its end without a lockstep mismatch, then failed a coverage assertion that incorrectly listed historical native states 47..50, which have no live case arms. gates-v2 targets active N_MAC_ADDR 45 and N_MAC_ROW 51 and every active MAC column instead. This was a fixture-only correction; the candidate RTL hash never changed.

Combined raster integration is independently tested by the mesh agent in timing-next/combined-routing/full-gpu-v1/receipt.json: eight recorded scenes in paced and unpaced modes, exact complete color/depth, cycle counters and fence traces. That proof combines this worker with the separate DSP routing/palette raster changes. It does not establish physical timing or device FPS. Physical fit and selection belong to the parent coordinator.
