Quake2 native mesh follow-up application — 2026-10-12

Source-only follow-up to the deployed native-v3 app (control ELF44f668ef...).
The optimized variant targets the remaining CPU work without reducing the
320x240 resolution, particles, animation, lighting or draw order. It combines:
* exact-result sinf/cosf/tanf memoization by every float argument bit;
* direct particle billboard preparation with the existing ordered clipper;
* shared BSP position transforms, with independent live UV/colour attributes;
* exact sparse vertex-lightmap calculation/packing and page-history validation.
The world position cache also clears its tags on epoch wrap.

No additional GPU opcode or ABI is required. Both variants retain the native
0x60 MD2 jobs and GPU_STATUS bit12 capability fallback from the control. Audio
policy is unchanged: native capability with no hardware mixer means silence,
with no application software-mixer fallback. The matching silent core must
also stop the OS mixer pump. First-use immutable model uploads can still stall;
resident accepted models retain their asynchronous ordered submission path.

Verify: python3 package.py verify
Stage optimized: python3 package.py stage optimized /tmp/q2-followup-fresh
Stage control: python3 package.py stage control /tmp/q2-native-v3-control
Check source: python3 package.py check-stage optimized /tmp/q2-followup-fresh

Rebuild the pinned ELF with native riscv64-elf-gcc15.2.0:
  cd /tmp/q2-followup-fresh
  SOURCE_DATE_EPOCH=1791806400 make -C src/quake2 -j8 USE_SDK_CONTAINER=0 \
    OBJ_DIR=/tmp/q2-followup-fresh/obj /tmp/q2-followup-fresh/obj/app.elf
Expected optimized SHA256:
13c8f43af0ca9ea84b84cf1cd9e824bd6f0c1d39eb2b65dc01337408102862a0

Asset-free host checks:
  python3 tools/check_quake2_renderer.py
  python3 tools/check_quake2_geometry.py
  python3 tools/check_quake2_textures.py .

Evidence contains exact command/order/frame assignment, complete CPU-framebuffer
and sound-service comparisons, compiler/build pins and the independent clean
staged rebuild. The 600-frame workloads comprise a scripted demo and a short
live movement trace; they are not exhaustive gameplay coverage. The CPU model
uses scalar/cache estimates, not measured Pocket FPS or cycle-accurate Vexii.
Its CPU framebuffer capture does not rasterize GPU triangles; actual GPU RTL
raster/timing/physical-fit validation is a separate parent experiment result.
No ROMs, PAKs, ELFs or fitted bitstreams are packaged. Only previously supplied
SDK startup objects/static libraries remain as necessary pinned build inputs.
Parent report records final hardware qualification and deployment status.
