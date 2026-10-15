Mixer loop-valid v2: remove the unused old operand selection

Source: 9e4028bc2d32fa17d74d821f40b16daaf1cc6eed4f3383e23f22e12e3944255c
Direct parent: ../mixer-loop-valid/audio_mixer.v, 5b044c9835e64159c2185eec3c69391cf7f16227997943f9cbaa21f86c689ddc.

This is exactly a four-line deletion from the shared position-ALU operand case: S_RD_LSTART_W no longer needs end-minus-start after the parent moves validity to the later captured-register comparison. All position_alu readers were inspected; each is inside another FSM state. The remaining S_RD_LSTART_W body only captures loop start and sets RAM address/next state. Thus the changed combinational result is never consumed, for any input value, including malformed descriptors, MMIO races or stalled memory. It does not alter a register, cycle, output or protocol.

Direct-parent paired fixture: lockstep/run.py audio_mixer.v NEW_OUTPUT.
Final receipt: lockstep-v1/receipt.json.
The fixture is the already-qualified paired mixer fixture, with the reference module replaced by the direct parent. It compares architectural ports, PCM, state, voice and cache request metadata every observed clock and the loop predicate at both consuming states. All 292,142,340 cycle comparisons pass. All inherited/reference/capacity transcripts, including counters and final coverage, are byte-identical to the parent's lockstep-v3. Coverage includes 177 inherited checks, 18,432 independent output samples across 96 scenarios, 12,808 starvation holds and 8,559,176 consumed loop predicates.

The parent also has exact cache-connected integration transcripts. This v2 gate directly proves the only delta does not change any observed mixer port or cycle; no new cache-connected run is claimed here. No Quartus builds, physical timing claims, clocks, constraints, Dock or video changes.
