Private exact projected-triangle producer and shared-DSP plane instruction
2026-10-04

Scope
-----
reference.py independently computes private 0x61 prepared edges and all seven
or ten attribute planes from projected x/y, raw s/t, reciprocal, independent
depth and RGB565 attributes. It includes stable Y ordering, optional existing
perspective normalization, both subpixel and integer-Y conventions, Q31/Q44
reciprocal selection, gradient saturation, half-pixel origin bias and signed
24-bit color-plane wrapping. Full 33-bit edge slopes survive fractional
presteps through an explicit correction to a signed32 multiply.

microcode.py emits actual executable ISA words implementing that contract.
The default control uses only ordinary microinstructions (472 words). The
--fused variant (392 words) invokes opcode40 PLANE, whose real RTL sequencer
is gpu_mesh_plane.v. That sequencer shares the engine's single32x32 DSP; it
does not instantiate another multiplier or floating-point processor.

This is a projected-triangle kernel. Transform, lighting, clip polygons,
whole-mesh scheduling, ownership, DMA and material-state packets belong to
the enclosing implementation. The test ABI below intentionally does not
claim to be the final phase-overlaid mesh-bank/scratch allocator.

Test ABI
--------
Bank0: flags bit0=subpixelY, bit1=normalize perspective, bit2=C+D combine.
Bank1: signed16 clipX0 | clipX1<<16. Bank2: clipY0 | clipY1<<16.
Bank3..23: three vertices, seven words each:
    xQ12.4 | y<<16, sQ16.16, tQ16.16, reciprocal, depth, RGB565, D_RGB565.
Y is Q12.4 with subpixel enabled, integer otherwise.
Bank40..42: sorted raw vertex pointers; bank64..93: expanded plane inputs.
Scratch initialization is performed by the generated program. This layout
consumes bank locations and therefore cannot directly coexist with a full
256-word transformed-vertex bank; integration requires the planned allocator.
Output is one 0x61 packet, or no output for a fully Y-clipped triangle.
There is no implicit culling: the job frontend must apply its winding rules.

PLANE interface
---------------
Instruction D names a scratch word holding third attribute bank address;
A/B select scratch words holding first/second attribute bank addresses.
Context scratch words: 100=dx1,101=dx2,102=dy1,103=dy2,106=reciprocal,
107=small-Q31,108=negative determinant,12=subpixelY,21=anchorX,22=anchorY.
Outputs114=du,115=dv,116=origin, all32bit. The program applies signed24
truncation for color planes afterwards. No other scratch word is modified.
The sequencer requires45cycles from start to done; the real engine instruction
takes48cycles including dispatch and return. A reset or cancel suppresses
scratch writes and terminates the active operation.

Retained correctness evidence
-----------------------------
results/reference/result.json:10,937 real-scene triangle setups from12retained
SM64 original-RTL replays, including10,341 prepared packets. All edge/plane
words match; original framebuffer/depth/fence streams and prepared vectors
remain byte-identical. 684 input triangles have C+D, of which682 emit packets.

results/edges/result.json: independent host setup versus corrected original
walker across10,000random/boundary triangles,7,490prepared cases,372,593spans,
78walker aborts. Explicit252signed33 prestep extremes also pass.

results/plane-rtl/result.json:98,611 exact RTL planes, comprising78,611 planes
from all captured setups and20,000full-range/saturation/sliver cases. The
original scene plane words are checked after their ABI-required truncation.
50cancellation phases pass.

results/kernel-rtl/result.json: actual392word program on real engine+PLANE
RTL,11,937executions,351,778exact output words. This includes all10,937real
setups plus1,000random full-width triangles. Random output backpressure must
hold valid/data stable.90cancellation phases and illegal-op handling pass.
Every measured cycle count matches the ISA timing model exactly; output
stall cycles are counted separately. Sources and fixtures are frozen there.

Measured cost and limits
------------------------
The first4cycle scalar ISA implementation was too slow: exact one-pass
normalization still averaged5,490cycles per entrance triangle. The2cycle
ISA plus fused PLANE reduces that to1,902.32cycles, before output stalls.
The entrance setup total is2,326,537cycles for1,223inputs (1,130packets).
At100MHz that alone is23.27ms. Other retained busy-world windows average
about1,800..1,957cycles per triangle. These are exact setup-engine costs,
not a coupled game frame time or a Pocket speedup. Transform/clip/fetch and
rasterization costs still need the complete pipeline, with real overlap.

Do not deploy this kernel as a replacement game/core. No production RTL,
sibling SDK, packaged bitstream or SD card is modified by these tools.

Further measured versions (retained separately)
---------------------------------------------
V2 adds exact constant-plane early-out (14unit/17instruction cycles versus
45/48general) and skips unpacking unused additive colors. Full RTL tests still
pass11,937programs,351,778words and exactcyclechecks. The394wordprogram needs
2,117,783entrancecycles (1,731.63per input), a9%setupcost reduction. See
results/plane-rtl-v2, results/kernel-rtl-v2 and results/profile-v2.json.

gpu_mesh_edges.v adds a sequencer that shares both the main DSP and divider.
The standalone test uses the agreed34cycle divider handshake mock, while all
edge arithmetic and control are actualRTL. It passes10,000original-walker
random/boundarycases including7,490preparedsetups, max151cycles. Sharedengine
integration and actualdivider validation remain separate nextchecks. The
program with EDGE and PLANE occupies275words before further changes.

Final integrated arithmetic kernel (authoritative latest evidence)
---------------------------------------------------------------
results/kernel-rtl-v3-final and results/kernel-rtl-compact-driver both pass
11,937programs,351,778exact outputwords,90cancellation phases,illegal-op trap
and exact cycle-model checks with randomized output stalls. These runs use
the real engine shared divider/DSP, not the edge unit's latency mock.

The current complete projected-triangle program is234instructions. It uses
EDGE43, PLANE40 and NORM46 together with ordinary instructions, and the
engine's registered multiplier-to-RAM writeback (5cycles for MUL/MAC). The
actual entrance setup cost is1,425,832cycles for1,223inputs, or1,165.85per
input. Other retained busy-world windows average1,145..1,199cycles. At100MHz
entrance setup alone is14.26ms; this remains a setup-stage measurement, not
a coupled-game improvement or hardware FPS claim.

The compact allocator is now executable and continuously checked in RTL:
  Scratch0..31: preserved packed indices.
  Scratch32..55: preserved material/context.
  Scratch56..63: preserved caller counters/state.
  Scratch64..126: preserved final clipped polygon (9vertices x7words).
  Scratch127..201: triangle kernel registers;193is return-address register.
  Scratch232..255:24word projected input using the test ABI above.
  Bank0..223: preserved32transformed vertices x7words.
  Bank224..253:30expanded per-vertex attributes.
State input words232..234 are reused for sorted vertex pointers only after
flags and clip values are captured. The RTL monitor rejects EVERY write to
scratch0..126 or bank0..223 during kernel execution. Distinct RAM sentinels
and full output comparison also verify the lifetimes.

Both arithmetic sequencers take COMPACT_LAYOUT=1 for this ABI; the engine
forwards that elaboration parameter. register_map.py defines the matching
bytecode relocation. build(fused=True,fused_edges=True,compact=True,
fused_norm=True,return_link=240) returns a234word callable Program object;
logical link240 maps to physical193. The caller uses JAL193. Its labels and
fixups must be relocated when appending to a larger mesh program.

Generate the callable image with:
  python3 tools/experiments/os30_mesh_20261004/kernel/microcode.py \
    --fused --edges --norm --compact --callable

Two preceding kernel-rtl-v3/kernel-rtl-compact run directories retain an
initial cycle-model mismatch: correct outputs, two excessive predicted
cycles in the first case. The shared divider's real34cycle handshake,
rather than the mock's35cycle consumer observation, corrects that model.
Those failed attempts are not used as passing evidence; both final runs
above check every cycle and every output word successfully.

Projection instruction
----------------------
gpu_mesh_project.v implements PROJ47 with the engine's existing multiplier
and reciprocal divider. Its input scratch pointer addresses seven words:
clipx/y/z/w,SQ16.16,TQ16.16,RGBA8. Viewport scratch49=cxinteger,50=cyQ4,
51=halfwidthinteger,52=halfheightQ4. The destination receives sevenwords:
xyQ4,S,T,zi,independentdepth,(alpha<<16)|RGB565,0. UVbias and additive color
remain explicit material-driver operations. All source/context reads finish
before output writes, and no scratch words outside those seven are touched.

Depth=floor(2^38/max(w,256)), zi=depth>>6. Ratios are signed32 truncations of
(clipXY*zi)>>16. ScreenX=(cx<<4)+(halfwidth*ratioX>>12), ScreenY=cyQ4-
(halfheightQ4*ratioY>>16), both saturated to signed16. The existing independent
depth and near-clamp conventions are retained. This unit does not implement
new clipping semantics or establish equivalence of float and fixed clipping.

results/project-rtl/result.json passes42,496vertices (32,496from captured
Bob-omb mesh states plus10,000full-range/denominator-boundary cases),75abort
phases and continuous output ownership checks. This isolated unit uses the
agreed divider-latency mock and needs70cycles.
results/project-engine/result.json repeats all42,496cases through actual
PROJ47 instructions, the real shared39beatdivider and DSP arbitration:
297,472exact outputwords,90engineabort phases,and every cycle matches. The
instruction takes72cycles; the11instruction fixture including pointer setup,
7OUTs and HALT takes97cycles. Full mesh transform/clipping/program integration
and coupled raster timing are measured by the sibling client/engine campaign.

Gather instruction
------------------
gpu_mesh_gather.v implements GATHER50 with no additional RAM or DSP. A names
the register containing a triangle ordinal0..63; B contains destination64;
D directly names the OR-mask output word (normally62), with AND atD+1.
The instruction reads one packed16-bit index record from scratch[ordinal/2],
gathers three7-word transformed vertices from bank[7*index], and writes the
21-word polygon plus masks. OR bit31 marks clip coordinates outside the
inclusive signed range[-2^29,+2^29]. Its signed33 minus-w comparison also
handles INT_MIN without negation overflow. No other scratch/bank is written.

results/gather-rtl-final passes29,784cases (all19,784real triangles plus
10,000random/duplicate/range-boundary inputs),65abort phases and ownership
checks. results/gather-engine and gather-engine-lean repeat all29,784cases
through the actual shared engine, checking685,032output words and every cycle.
The latter uses ENABLE_DOT=0,EXTENDED_ISA=0, matching the pruned fit. Unit
latency is59cycles; actual instruction latency is62cycles. The full engine
run also checks90generic abort phases; standalone gather covers its own
65abort phases. Frozen sources accompany all results.

Earlier aggregate triangle timing figures above are retained-version results.
The parent subsequently added registered scratch writeback to ordinary ALU,
LI/LUI/JAL instructions. Use current full-program profiles when reporting the
current design's total cost; these older exact arithmetic checks remain valid.

Lazy CPU fallback in the private SM64 producer
-------------------------------------------
stage_lazy.py applies only to a supplied/private gfx directory. It does not
edit gfx_gpu.c, production app sources, SDK sources or the SD card. It extracts
the original gfx_sp_vertex float loop into sm64_lazy_cpu.h without changing
expression order; ordinary calls use current RSP state, while deferred calls
use their saved vertex-load state and immutable original Vtx source.

Only a mesh-capable core and a successful immutable mesh_shadow_vertices
capture permit deferral. sm64_lazy_state.h snapshots MP matrix, lights and
coefficients, lookat, geometry flags, UV scale, fog parameters, global fog
enable, aspect and lighting memo epoch. The backing RSP storage is3388bytes
in app BSS, but the matrix stack and loaded-vertex array are never copied.
RSP commands do not materialize lazy vertices. A partial G_VTX resolves the
old preserved slots before replacing the snapshot and discards only slots
being overwritten. Unsupported triangles materialize precisely their used
slots before entering the normal renderer. Legacy geometry ownership is
invalidated for deferred slots, preventing stale accelerator metadata reads.

Parent runtime's gfx_mesh_try_run(cmd) must precede gfx_mesh_lazy_fallback(cmd)
in the interpreter. Successful hardware submissions then skip all CPU vertex
math; fallback executes the unchanged math using load-time state. No active
hardware capability means the ordinary eager path remains in use.

check_lazy.py results/lazy-cpu-final compares every LoadedVertex byte in
32,608loads /529,820vertices, including2,608real-scene loads and30,000random
contexts. Both O3/fast-math and ASan/UBSan pass, including changed fog-enable,
lighting memo collisions/epochs, matrix/UV/state changes, partial materialize
requests and overwrite preservation. These are CPU fallback correctness
tests; they do not equate the fixed-point mesh shader with float arithmetic.

check_lazy_packets.py results/lazy-packets compares actual old-capability
application command streams at frames360/367:2,109/2,091commands respectively.
Every matrix/vertex/material field and usable texture byte matches. One
documented source padding exception is retained explicitly: actors/star's
display list loads32x64 from a32x32 texture. The unused second half includes
relocated code pointers. Its first2048bytes match, and all referenced star
vertex UVs remain below32. The functional CPU harness reports no GPU pixel
work and is not evidence of hardware frame speed or mesh raster correctness.

replay_lazy_packets.py subsequently feeds both old-capability app captures to
the original corrected GPU RTL. Explicit framebuffer and depth-buffer dumps,
plus completion fences, match byte-for-byte at frames360/367. See
results/lazy-packets/replay-depth-result.json. The earlier replay-result.json
has framebuffer output only; its original vectors did not request depth.

PLANE area sharing, unchanged execution cost
------------------------------------------
gpu_mesh_plane.v shares one64-bit add/sub chain between numerator subtraction
and reciprocal scaling, and shares two32-bit add/sub chains between anchor
subtraction and pixel-center bias. This preserves state/cycle schedules and
all intermediate widths. results/plane-rtl-shared-adders passes98,611exact
plane cases and50abort phases with unchanged14/45cycle unit latencies. Area
benefit must be judged from the parent full-core fit, not RTL operator counts.

The subsequent final version also removes two33-bit temporary delta registers:
DELTA0 directly stores saturated differences into a1/a2; DELTA1 still performs
the constant-plane test. sat33 uses the exact two-sign-bit overflow predicate.
results/plane-rtl-shared-final passes the same98,611cases and50abort phases
with identical totalcycles. gpu_mesh_edges.v similarly selects x/y endpoints
before one shared pair of signed17 subtractors and uses one common prestep
subtract lane. results/edges-rtl-shared-subtractors passes10,000original-walker
fixtures,7,490prepared cases and unchanged1,162,043totalcycles/max151cycles.
Neither change modifies architectural outputs, latency, reset or cancellation.
