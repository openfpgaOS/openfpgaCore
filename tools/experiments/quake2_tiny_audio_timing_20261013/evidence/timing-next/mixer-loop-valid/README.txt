Audio mixer: compute loop validity after capturing LOOP_START

Candidate: 5b044c9835e64159c2185eec3c69391cf7f16227997943f9cbaa21f86c689ddc
Parent: c6d2517af662f356b44617230c27aa880c449928282e738c374a2db86663f73a
This isolated change modifies only audio_mixer.v. No new registers, states, cycles, features or memory requests. No Quartus jobs, Dock/video changes or clock/constraint changes.

The old S_RD_LSTART_W assignment computes loop validity directly from CPU-table RAM through its bypass mux, shared position ALU and zero test. The new assignment runs in the existing S_FETCH_TAP0_CALC state, using captured cur_loop_end and cur_loop_start. The old 23-bit subtraction of zero-extended 22-bit values has no signed overflow: nonnegative and nonzero means exactly unsigned(end > start).

Ownership proof: S_RD_LOOP_END captures end; S_RD_LSTART_W captures start. CALC is reached before either consumer, S_FETCH_TAP1_NXT_CALC or S_ADVANCE. Neither captured endpoint can change along the intervening states. A stream that starves skips CALC and also skips both consumers, so its retained validity is unobserved. Voice end and reset likewise discard the current computation. S_ADV_FRAC later reuses cur_loop_start for rate scratch, after validity has been computed; the validity register continues to hold the original loop predicate. Live MMIO writes affect the RAM but do not alter those captured registers.

Validation:
- tests-v1: all 177 inherited checks, 18,432 independent sample references across 96 scenarios, and the entire capacity matrix. PCM, status, position, IRQ and cycle/capacity transcripts are byte-identical to the parent.
- lockstep-v3: candidate and parent receive identical stimuli and memory responses in one simulation. Assertions check every architectural output, state, voice, cache metadata and consumed loop-valid value every clock. All 292,142,340 observed cycles pass, with 8,559,176 loop-valid consumer visits. Fixtures include stalls, FIFO pressure, starvation/resume, malformed/edge loops, mutable updates, rearm races and reset. The independent reference model's arithmetic is unchanged.
- cache-integration-v1: the complete cache-connected inherited/reference/capacity transcripts are byte-identical to the final-ring parent integration proof.

Run the normal suite using the existing ../../tests/run.py helper with audio_mixer.v and a new output directory. Run the paired suite using lockstep/run.py with the same arguments. Existing integration-tests/mixer-cache-harness/run.py accepts the candidate directly. All runners copy/pin their exact input sources before compiling.

lockstep-v1 passed assertions but did not call Verilator final(), so its optional final cycle-count print was absent. lockstep-v2 added final() after a helper had already deleted its DUT, causing a test-runner cleanup fault after the reference run. lockstep-v3 calls final() before deletion, records counts and passes. Candidate RTL remained unchanged throughout.

These are simulation and source-level proofs. They do not establish fitted timing, Pocket audio playback or measured game FPS. Root owns physical integration and selection.
