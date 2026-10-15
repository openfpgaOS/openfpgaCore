Quake2 exact application optimization, native4 baseline

Verify: python3 package.py verify
Stage: python3 package.py stage optimized /tmp/q2-batching-fresh
Control: python3 package.py stage control /tmp/q2-native4-control

Build with riscv64-elf-gcc15.2.0 from the staged directory:
  SOURCE_DATE_EPOCH=1791806400 make -C src/quake2 -j8 USE_SDK_CONTAINER=0 \
    OBJ_DIR=/tmp/q2-batching-fresh/obj /tmp/q2-batching-fresh/obj/app.elf
Expected ELF SHA256: c08bed78555f08252c9694c1f019db968e2ce79809ff41b909cfdd61b30e743b

Host checks: python3 tools/check_quake2_renderer.py
             python3 tools/check_quake2_geometry.py
             python3 tools/check_quake2_textures.py .

The matching FPGA image remains native4 MAC+CB4 (542029e588e2...).
Measured performance, exact output gates and limitations are recorded in the
parent experiment report. Simulator estimates are not Pocket FPS readings.
This source-only archive contains no PAK, ELF or bitstream. The pinned SDK
startup objects and static libraries are retained as required build inputs.
