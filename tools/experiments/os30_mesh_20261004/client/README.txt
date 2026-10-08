OS30 PRIVATE MESH CLIENT PROTOTYPE — 2026-10-04

Scope
-----
mesh_client.h implements the immutable mesh/index compiler, stable storage,
owned job records, completion-fence ring accounting, and the three DMA bank
segments used by the new processor. This is not a public GPU ABI. No command
opcode, hardware capability, MMIO register or production source is changed.

sm64_shadow.h is a real SM64 producer adapter staged in a private app by
stage.py. It observes every G_VTX after matrices/lights are current and every
immutable TRI1/TRI2 run. The original renderer executes normally. This app
does NOT offload rendering and must not be used to claim a speedup or deploy
the incomplete prepared-raster backend.

Warm immutable jobs look up a normalized vertex object and precompiled indices.
No vertex or face decoder runs on a cache hit. Index compilation stops at state
changes, 64 triangles, and a mixed partial vertex load; TRI2 stays indivisible.
Indices preserve vertex order and the first/provoking vertex, so a future
processor can evaluate per-face alpha without CPU splitting/repacking.

Cache pools never recycle addresses: on capacity exhaustion the caller falls
back. This conservatively preserves data referenced by pending jobs. A release
implementation should drain before clearing a scene cache or use explicitly
fenced generations; it must not silently reuse the old modulo caches. Mutable
vertices, fractional coordinates, unsupported light/texgen formats and mixed
vertex snapshots retain explicit ordered fallback. This first scene does not
exercise a fractional immutable vertex.

State and DMA
-------------
Bank words0..127 hold up to32 raw normalized vertices, four words each:
  x16|y16<<16; z16|zero_flag16; s16|t16<<16; RGB/normal8 plus alpha8.
Bank words128..191 hold the64-word immutable state snapshot.
Bank words192..255 hold packed indices i0[4:0],i1[9:5],i2[14:10].
mesh_job_segments generates those three reads with commit only on the last.
The DMA may split4-byte-aligned input addresses at its own32-byte boundaries.

Snapshot words0..15 are rowsx/y/z/w of the existing integer transform format
(Q8.24 coefficients, Q24.8 translations). Words16..23 are the vertex-load
light coefficients, diffuse/ambient, UV scale, geometry flags and fog settings.
Words24..39 record draw-time material/viewport/scissor/texture/color selectors.
Words40..55 retain the ORIGINAL float matrix for independent clipping studies;
word56 retains the aspect factor. These oracle fields are fixture-only and
can become concrete raster material packet fields during integration.

No full0x4A material packet is produced yet: the root integration must finalize
material resolution and output ordering. The shadow also converts matrix/light
state on each load for capture; a production producer should reuse matrix/light
epoch snapshots. Existing vertex/index decoding has already been removed from
warm capture jobs, but this is not yet the final CPU hot path.

MeshOwnedJob's8-word header is sequence, vertex address, index address, vertex
count, triangle count, original vertex pointer, original command pointer, flags.
The job owns its64 state words. MeshRing does not free state at fetch/ring
retirement: only a completed GPU fence advances completed. Completion must
include queued DMA, geometry, prepared packets and raster writeback. Cache
cleaning plus publication barriers are required before real hardware doorbells;
the shadow app never publishes a hardware doorbell.

Tests and private artifacts
---------------------------
check.py: AddressSanitizer+UndefinedBehaviorSanitizer client tests (leak checking
disabled because the sandbox runs under ptrace).100,000 warm cache jobs cause
zero decoder calls. Tests cover index/provoking order, TRI2 boundaries, mixed
partial loads, mutable/invalid formats, permanent cached addresses on capacity
exhaustion, fence rollover, backpressure and exact DMA segment bounds.

stage.py creates/patches only docs/measurements/os30-mesh-20261004/SM64.
Build the explicit app.elf target there; do not use default packaging/copy.
build_capture.py creates a private functional CPU harness under that app's
mesh-shadow/qsim. run_capture.py --start360 --frames8 captures Bob-omb inputs.
This functional producer harness is not the GPU/SDRAM timing model.

Binary capture format:18 little-endian uint32 words (MSH1 magic, job count,
16 counters), then each72-word job plus its4words/vertex and1word/triangle.
analyze.py validates dimensions/indices, verifies immutable addresses retain
their data across the window, and emits jobs.json and 256word/job banks.bin.
Its clip counts use fixed-point clip predicates; they are coverage, not proof
of exact clipping pixels. validate_source.py independently decodes the original
ELF vertex assets and TRI commands and checks every normalized vertex/index.

The360..367 window contains2,608 jobs,32,496 vertices and19,784 triangles.
536 jobs have fewer than8vertices and440 have only1–2triangles. Fixed-point
classification gives14,507 fully inside,1,670 clip-crossing and3,607 wholly
outside triangles. Warm7-frame delta has no vertex or index decoder calls,
2,282 cached jobs, no cache/queue overflow,189 mutable loads and7 unsupported
attribute loads. All captured vertices/indices match the source ELF exactly.
These artifacts include user-owned ROM-derived geometry and remain private.

All client changes and fixtures stay in this experiment and private app copy.
Parent integration adds transport and lazy CPU fallback there separately. No
live sibling app, prior snapshots, commit, seed sweep or deployment is changed.

Subsequent processor integration (still private)
----------------------------------------------
mesh_run_get_compact adds a separately keyed immutable compile format with two
15-bit triangle records per word. It packs only on a cache miss. Legacy shadow
fixtures retain one record per word; their format is not silently changed.
Tests compare mixed TRI1/TRI2 streams and100,000 warm compact hits, and check
odd triangle counts. MeshSegment counts useceil(triangles/2) when job flags
includeMESH_JOB_COMPACT_INDICES. Descriptor transport independently carries
{actual_triangle_count16,index_word_count16}; fetch words and logical counts
must not be confused.

mesh_transform.py contains an integer seven-word vertex reference (clipx/y/z/w,
S/T Q16.16texels, RGBA), matching generic ISA transform/light/UV/fog code, and
the full six-plane Sutherland-Hodgman clipper. The fixed clipper mirrors CPU
plane order, strictinside>0, color-increment truncationtowardzero, and64-bit
interpolation including signed33-bit attribute differences. Its result is
deterministic, but not presumed float-byte-exact. The source-float reconstruction
is a sensitivity check, not a trace of the compiled CPU instructions; it shares
fixed inputlighting and is not a complete framebuffer oracle.

All32,496 entrance-window vertices and19,784triangles were reconstructed.
Fixed versus float reconstruction had zero clip-code/topology differences,
maximum ideal screen-coordinate delta0.00114pixel, and one clippedRGBA vertex
difference. Thus even the small observed discrepancy must be checked in full
color/depth rendering before deployment. Maximum scene polygon size was5.

The clip ISAprogram uses196instructions. check_clip.py passes all19,784scene
triangles,2,000random cases, coordinate extrema and33-bitUV differences. It
returns the final polygon in scratch64..126 and preservesbank/owned state.
The supported clip-coordinate format isabs<=2^29 Q24.8; larger inputs return
an explicit error before a prepared packet, never a silently wrapped polygon.
Inside and trivially outside triangles bypass this routine. The whole driver
uses GATHER's coordinate validation, then calls the same clipper with only its
duplicate initial range scan omitted. Convex interpolation preserves the
validated coordinate range. Standalone clipping still validates its inputs.

mesh_program.py is the complete private indexed-mesh driver, assembled with
COPY49,VERTEX48,GATHER50,PROJA51,ATTR52, the clipper, and the compact triangle
setup kernel. It occupies509instructions including the projected fallback
entry at504. It keeps both mesh banks
independent: transformed vertices0..223 in the activebank, triangle attributes
224..253, and the other bank available for next-job DMA. Scratch0..31 owns
packed indices;32..48 owns the17wordmaterial;49..52viewport;53flags;54Dcolor;
55Ccolor;56..63loop/UV/control;64..126finalclippolygon. Kernel working registers
and projected inputs occupy127..255 after clipping. The input descriptor's
scratch62/63vertex/triangle counts are consumed before those words change role.

This driver's explicit64wordstate is documented in mesh_program.py. It uses
actual17word0x4A state, permits constant or shadedC/D, keeps triangle/painter
order, and can update flat/provoking alpha by repeatingmaterial only on change.
It supports indexedtinybatches, full clipping/fanemission and projected winding
culling. Early homogeneousculling is deliberately not approximated with a
potentially overflowing64-bit tripleproduct; crossingfaces clip before cull.

check_mesh_program.py compares the complete instruction stream to independent
transform/clip/edge/plane references. It pairs real shadow geometry with
EXPLICIT SYNTHETIC material records and variedC/D/cull/alpha flags. These tests
are processor semantics, not real SM64 material capture or a reconstructed
framebuffer. Diagnostic shadow selectors are never valid substitutes for a
concrete material packet.

Real material and complete RTL validation
-----------------------------------------
stage_material.py adds a pure snapshot of the existing renderer's state builder
without changing the SDK. Its capture ELF is frozen as mesh-shadow/material-
snapshot.elf and each new run retains its own app.elf. material-*.bin binds one
status+64word material record to each same-index mesh-*.bin job; textures-*.bin
records the actual RGB565 backing bytes at their recorded addresses. Formats:
MMT1,count,(status,state[64])*count; TMX1,count,(address,bytecount,bytes)*count.
See material_cases.py for a checked parser and source-job/frame identifiers.

material-textures-360-8 contains2520supportedjobs/31392vertices/19112triangles;
88jobs require the original backend. Supported jobs include280constantC,
72opaque shade-alpha, and24constant-alpha blend jobs. Selection explicitly
falls back for HILITE/signedC, decal depth bias, two-pass texture alpha, LOD,
dynamic shade-alpha translucency, and incompatible fog/alpha semantics.
The projected fallback entry accepts those jobs' already-resolved colors and
depth through24words, so the application retains their original shading.

check_mesh_rtl.py runs the complete pruned RTL engine with real supported
material and shader state, randomized output backpressure, every final bank
word, and exact unstalled cycles. --projected additionally tests the whole
program's fallback entry against original GPU edge/plane fixtures. PROJA's
independent unit corpus contains84992old/new cases and90cancel phases; it
preserves provoking alpha while applying material RGB565 selectors and UV
offsets. ATTR shares the existing multiplier; it does not add a DSP.

Final509word program RTL evidence: whole-mesh-final-rtl-v1/result.json passes
13457programs and617605exactoutputwords, including2520real material jobs and
10937original GPU projected-input cases. Every final bank word and unstalled
cycle matches. Real supported jobs cost26612150cycles over8frames (33.27ms per
frame at100MHz, before DMA/raster stalls); this is not an FPS prediction. The
64material/cull variant ISA campaign adds1536jobs and246288exactoutputwords.

Material snapshots add diagnostic work to the original renderer; their CPU
instruction counts cannot be called the new app's speed. Warm producer work
is bounded by immutable cache lookups, matrix/light state, material resolution,
and descriptor publication. Vertex expansion and face decoding happen only
on immutable cache misses. Root's lazy vertex/fallback and transport integration
must be used to measure the removed per-frame CPU work. Hardware DMA/raster
stalls, cache-size tradeoffs, fixed-versus-float image differences, and final
coupled-frame gains require the physical replay and complete fit.
