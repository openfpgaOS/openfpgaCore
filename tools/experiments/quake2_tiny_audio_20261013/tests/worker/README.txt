Geometry ring lookahead candidate

Worker SHA256 e0f4ffbd20f2a20ab416543a0e9d830d7ec6b4d8d55df094479428b67f933fbb.
Parent source: divisor-preload ec474a5e04a8053fc0eea8c1034cc18f4580aeabb21816ce7acc2f700b24fd15.
The previously qualified divisor preload remains unchanged.

The NORMAL and EXTRA fits of divisor-preload produced identical limiting paths: worker write_ptr[2] -> used_words subtraction -> free_words subtraction -> zero equality -> take_input -> conditional address increment -> command-ring M10K address. Slow0 slack -1.942 ns (11.722 ns data); slow85 -1.697 ns. Both fits were18,074ALM/1,842LAB/305M10K/42DSP. Divisor context selection was no longer the immediate worst path. These fits do not qualify this newer candidate.

This source removes the second subtraction from both places that only ask whether free_words is nonzero. QUEUE_LAST is explicitly [ADDR_BITS:0], matching free_words. For N=ADDR_BITS+1, C=QUEUE_LAST and U={0,used_words}, subtraction yields (C-U) modulo2^N. That result is zero exactly when C=U as N-bit vectors. Thus replacing free_words!=0 with U!=C is exact even if used_words is outside the normal occupancy range, and even if free_words underflows. No assumption about valid software, remaining count or pointer ordering is used. Other consumers that require actual free_words or a minimum count retain the original arithmetic value.

The old input_addr is (input_rdptr+take_input) modulo2^ADDR_BITS, with take_input exactly one bit. Selecting either input_rdptr or its independently computed +1 is identical for both take_input values and all pointer values, including wrap. The new spelling aims to place the increment before the late selection. The fitter may choose a different equivalent structure; only a new timing report can show physical improvement. No keep constraint is currently present.

No register declarations, FSM states, command formats, capacities, feature flags or cycles change. No SDC/PLL changes. Live source and earlier immutable artifacts were not edited.

Evidence
* lockstep-gates/receipt.json: four native/particle feature configurations, old/native6 and candidate architectural ports, addresses, output words, busy/state/mode and divider results compared every cycle; 27,163,742 total cycles. Includes native bursts/backpressure, concurrent CPU publication, queue wrap, enable holds, malformed descriptors, resets and signed near-plane extremes. Each transcript is byte-identical to the parent candidate's corresponding lockstep transcript.
* width-proof/receipt.json: actual Verilog sizing/expression test,35 ADDR_BITS/QUEUE_BITS combinations including1 and32-bit addresses;1,851,968 input vectors. Exhausts every pointer/consume combination through6 bits, then checks every capacity bit, wrap, adjacent full points, invalid occupancy and one million random full32-bit vectors. Runnable RTL, C++ fixture and executable hash retained. Supported storage cases require1<=QUEUE_BITS<=ADDR_BITS; arithmetic comparison itself does not rely on occupancy bounds.
* full-gpu-v1/receipt.json: when present, worker-only replacement in eight frozen native6 actualGPU frames, each paced/unpaced, comparing complete color/depth bytes, every counter/cycle and fence trace. This isolates worker correctness and does not measure restored-audio contention or Pocket FPS.
