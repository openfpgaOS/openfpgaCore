Quake2 native MD2 mesh application experiment — 2026-10-12

Experimental and not deployment-qualified. This app needs the matching native
mesh core (GPU_STATUS bit12, opcode0x60 with164 payload words). Existing cores
retain the CPU alias path. The baseline is the deployed world-v1 experiment,
not the earlier application-only scalar release. No ROMs, PAKs, ELFs or fitted
bitstreams are included; SDK-provided startup/library inputs remain pinned.

Native-v3 adds only a sound-startup gate to native-v2: when GPU_STATUS bit12 is
set and OF_HW_MIXER_HW is clear, SDL_BackendInit returns false before audio or
mixer service initialization. The engine stays in SS_NOT with no alternative
software mixing backend. Hardware-mixer-equipped and earlier cores retain
their existing audio startup. The matching silent native core also reports
its audio FIFO full and suppresses PCM writes while preserving I2S clocks and
zero output: this prevents the unchanged OS's unconditional software-mixer
pump from generating silent samples. Both halves are required for this
mixer-less experiment; app-side silence alone does not stop the OS pump.

Verify: python3 package.py verify
Stage native: python3 package.py stage native /tmp/q2-native-fresh
Stage control: python3 package.py stage control /tmp/q2-world-control-fresh
Check source: python3 package.py check-stage native /tmp/q2-native-fresh

To reproduce the pinned executable use native riscv64-elf-gcc15.2.0 and make:
  cd /tmp/q2-native-fresh
  SOURCE_DATE_EPOCH=1791806400 make -C src/quake2 -j8 USE_SDK_CONTAINER=0 \
    OBJ_DIR=/tmp/q2-native-fresh/obj /tmp/q2-native-fresh/obj/app.elf
The epoch fixes the build-date string to Oct12,2026. Build inputs, compiler,
expected ELF hashes and independent staged rebuild result are in manifest.json
and evidence/. No source from sibling checkouts is read by staging or build.

Host checks (sanitizers, no assets):
  python3 tools/check_quake2_renderer.py
  python3 tools/check_quake2_geometry.py

The native path snapshots per-draw interpolation/projection/material and a
normal RGB565 palette into the ordered GPU stream. Immutable MD2 animation
frames and ordered <=32vertex/64triangle meshlets grow downward from the fast
memory end while textures grow upward. Map registration fences prior draws,
reclaims only the native arena and advances its generation; all texture
addresses/content remain valid. Alternating skin dimensions fall back instead
of reallocating animations within the same epoch. Whole-draw bounds and
precision checks keep clipped, translucent, shell, weapon/depthhack and
required-shadow draws on the original CPU path. Allocation/upload waits are
first-use events; accepted resident jobs have no per-model GPU completion wait.

Current CPU observer results establish reduced software work, not Pocket FPS
or raster correctness. Matching full actual RTL color/depth output and fitted
timing/resource review remain independent qualification requirements. See the
parent experiment report for latest results and limitations.
