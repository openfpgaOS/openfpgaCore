Quake2 GPU particle experiment, native5 baseline

Verify: python3 package.py verify
Stage: python3 package.py stage optimized /tmp/q2-gpu-draws-fresh
Control: python3 package.py stage control /tmp/q2-native5-control

Build with riscv64-elf-gcc15.2.0 from the staged directory:
  SOURCE_DATE_EPOCH=1791806400 make -C src/quake2 -j8 USE_SDK_CONTAINER=0 \
    OBJ_DIR=/tmp/q2-gpu-draws-fresh/obj /tmp/q2-gpu-draws-fresh/obj/app.elf
Expected ELF SHA256: 46d2206d655e663aab7c15b56ff0c88b7a1c547bb6b3516e921780d795c9b753

Host checks: python3 tools/check_quake2_renderer.py
             python3 tools/check_quake2_geometry.py
             python3 tools/check_quake2_textures.py .

This application requires the matching particle-capable FPGA for the new path.
Old hardware uses the legacy CPU path. The new fixed-point particle convention
has measured, localized image differences; it is not bit-identical to native5.
Measured performance and all qualification limits are in the parent report.
Simulator estimates are not Pocket FPS readings.
This source-only archive contains no PAK, ELF or bitstream. The pinned SDK
startup objects and static libraries are retained as required build inputs.
