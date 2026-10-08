Controlled SM64 batch-culling validation

prepare.py copies the retained P3 qsim and changes only the hardware capability
constant plus explicit GEO0/1 selection. It evaluates the real selected-dual
core_top parameter expressions and peripheral HW_FEATURES expression through
Verilator. The resulting feature word is0x2b136753; geometry capability remains
a separate status bit. Existing route, input, audio and timing code is retained.

add_observer.py copies that model and reads existing sm64_render_stats after
each completed frame. It makes no guest writes and adds no guest instructions.
The baseline control proves existing frame cycles, command vectors, scene and
diagnostic records unchanged. Both paired apps use the same observed executable.

capture.py LABEL --geometry0|1 --audio --elf PATH
(the syntax is --geometry 0 or --geometry 1) captures721 deterministic frames
with the same bank.ofsf and app profile, copying the ELF and hashing results.
Use separate versioned labels; never overwrite a capture.

build_replay.py freezes selected GPU8e471, texture0d2d, render03e4 and common
RTL from the qualified100MHz os30 fit. It retains the real Pocket SDRAM
controller/arbiter/refresh model and selected BRAM texture queues. Geometry
worker/SM64 geometry are disabled. Textures use the retained SDRAM test arena.
replay.py LABEL --modes 1,2 replays captured frames with appended depth dumps.
Mode1 injects scanout/audio; mode2 adds CPU traffic. GEO1 is only a compatibility
control and explicitly uses the retained worker executable via --exe.

analyze_v1.py through analyze_v5.py audit completed versioned captures/replays.
Gameplay metrics exclude boot: frames360–720 inclusive. Actual guest instruction
counts, estimated CPU cycles, software work counts and GPU workload cycles are
separate. qsim is not the selected dual CPU RTL and cannot establish device FPS.

add_bounds_eligible_observer.py creates a separate v3-only model with a host
callback at the actual post-eligibility instruction PC. It reads the current
source/count arguments and bounds-cache headers after all level/mode/range/
perspective gates. analyze_bounds_v3.py requires identical original guest
instructions, modeled cycles, scenes and vectors. This authoritative observer
uses the actual 24-byte Vtx layout. The earlier entry-only observer and its
16-byte provisional range estimate are retained as superseded evidence.

prepare_function_model.py makes a separate standalone-app qsim that skips
absent game level-entry hooks. CPU/FPU and OS execution stay unchanged.
run_function_harness.py executes the exact SDK RV32 test ELF with a bounded
instruction budget and requires the actual CPU comparison PASS marker.
The original game-capture models are never modified for this harness.

Artifacts: docs/measurements/sm64-batch-cull-20261005/validation/
No sibling/SD writes, production edits, FPGA fits or seed sweeps occur here.
