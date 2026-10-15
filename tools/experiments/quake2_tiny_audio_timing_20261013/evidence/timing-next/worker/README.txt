Worker timing candidate: exact-cycle arithmetic factoring

This isolated candidate changes only gpu_geometry_worker.v, starting from
the e0f4ffbd ring-lookahead worker in the completed compact-audio sweep.
It does not change dock/video RTL, clocks, constraints, features, registers,
FSM states, or command scheduling. No frozen sweep/package/source-ready
tree was modified. Physical area and timing require a separate fitted build.

The two changes address measured paths. Seeds 2/14 reached the worker state
decision through determinant sum bits and the zero detector. Seed 20 reached
sx[11] through projected[28], particle rounding and saturation: eight logic
levels, 11.128 ns data delay and -1.367 ns slow 0 setup. Reports and their hashes
are recorded in timing-audit/leading-seed-cull-analysis.json and
timing-audit/seed20-review.json. Other CPU/GPU paths were close competitors;
these changes do not themselves promise a particular Fmax.

Determinant proof

Let A and B be the two 35-bit operands. The existing determinant is A-B
modulo 2^35. This difference is zero exactly when A equals B, for every
two-state operand pair, including values outside the usual winding range.
The equality now reads the operands directly; determinant[34] and both
culling flags are unchanged. The zero tree can run alongside the subtractor
used for sign, rather than following its sum outputs.

Particle screen proof for every two-state 64-bit input

Let f be the signed 32-bit integer floor from value[63:32], and let u be the
round-to-nearest-even increment. The original implementation computes
sat16(f+u) in signed 33-bit arithmetic. Because f is signed 32 and u is zero
or one, that 33-bit addition never overflows.

The identical increment is value[31] && (OR(value[30:0]) || value[32]):
the high fraction bit selects at least one-half; a nonzero low fraction
selects strictly greater than one-half; exact ties select the floor's odd
LSB. The new 16-bit increment is calculated before this decision and the
decision selects its result, removing a late carry input from that path.

The unrounded floor fits signed 16 exactly when value[63:47] is the sign
extension of value[47]. If f is below -32768, f+u is at most -32768, so the
negative saturated endpoint is already known. If f is above 32767, f+u is
above 32767, so the positive endpoint is already known. If f equals 32767,
either increment gives the same saturated positive endpoint. In the only
remaining case, -32768<=f<=32766, f+u fits signed 16, and the 16-bit increment
has exactly the required output bits. Thus range checking may precede
rounding without changing any 64-bit input's result. This is a mathematical
two-state argument; no SAT/formal tool proof is claimed.

Functional evidence

run_equivalence.py extracts both particle_screen functions and both CULL_J
expressions from the actual parent/candidate sources into a Verilator
arithmetic fixture. It compares them with each other and an independent
integer model: 5,146,368 screen vectors and 4,003,616 cull vectors. Coverage
includes every 16-bit low-floor value across high-word saturation classes,
both sides of fraction/tie boundaries, walking fraction bits, two million
random 64-bit inputs, modular 35-bit edges and randomized cull flags.

The full paired-worker fixture compares the candidate directly with its
e0f4ffbd parent for all four native/particle configurations. It checks
published words, state/mode, busy, ring pointers/lookahead, mesh ports and
divider behavior cycle by cycle. All 27,163,742 cycles pass, including
legacy raw/clip/cull, native meshes, particles, malformed input, pauses,
resets and command publication while busy. The shared input-memory model
also requires the two workers' lookahead addresses to match each cycle.

The existing fixture was copied locally. Only its staged-near-plane check
was adapted from the older native6 reference's active_nearclip wire to the
direct parent's already-preloaded divisor. The original reference file is
the complete e0f4ffbd source with only its module name changed. Module-scope
register declarations remain identical. No pipeline latency was added.

Reproduce locally with:
  python3 run_equivalence.py --output NEW_DIRECTORY

The runner records exact source/fixture pins and commands in the new output.
gates-v1/receipt.json records the completed run. Actual full-GPU frame
replay is a separate gate coordinated by the CPU review agent. All physical
builds are coordinated by the parent; this component performed no Quartus
jobs. A successful logical gate does not establish 100 MHz timing closure.
