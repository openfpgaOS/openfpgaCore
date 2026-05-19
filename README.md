# openfpgaOS

openfpgaOS is a bare-metal game runtime and FPGA core for the Analogue Pocket.
It runs a 32-bit RISC-V CPU in the Cyclone V fabric and exposes hardware
services for video, audio, input, storage, and app loading.

For game development, use the SDK repository. This repository is for changing
the OS, firmware, FPGA RTL, Pocket packaging, and shared SDK headers/runtime.

## Current Target

The production target is `pocket`.

Core pieces:

- `src/fpga/common/`: reusable RTL for CPU integration, AXI, GPU, video,
  audio, SDRAM, CRAM0, input, and shared peripherals.
- `src/fpga/targets/pocket/`: Analogue Pocket top level, APF bridge, PLLs,
  constraints, Analogizer output, and target build flow.
- `src/firmware/os/`: bootloader, OS kernel, HAL, syscall layer, file/save
  services, input, terminal, and Pocket runtime.
- `src/firmware/api/`: SDK-facing headers, linker script, and shared runtime
  sources.
- `dist/`: Pocket core JSON, platform metadata, and default Analogizer config.
- `assets/`: runtime assets such as the sample bank.

Generated Quartus, Verilator, seed sweep, SignalTap, and simulation outputs are
not source artifacts and should not be committed.

## Requirements

- RISC-V embedded toolchain providing `riscv64-elf-*`
- Intel Quartus Prime Lite, currently expected at
  `/home/alberto/altera_lite/25.1std/quartus`
- Java plus sbt for VexiiRiscv generation
- Verilator for RTL tests
- Standard Unix tools: `make`, `gcc`, `rsync`, `find`, `grep`

Initialize submodules after a fresh checkout:

```bash
git submodule update --init --recursive
```

## Build

From the repository root:

```bash
make                 # show help
make full            # target full build: CPU, bootloader, OS, FPGA, summary
make build           # clean Quartus compile for the Pocket bitstream
make firmware        # rebuild bootloader + OS and patch an existing bitstream
make os              # rebuild os.bin and install it into build/
make check           # Quartus analysis/synthesis only
make test            # Verilator RTL test suite
make timing          # timing summary from the last full compile
make package         # create build/ SD-card package
make clean           # remove generated build artifacts
```

The target Makefile can also be used directly:

```bash
make -C src/fpga/targets/pocket check
make -C src/fpga/targets/pocket build
make -C src/fpga/targets/pocket firmware
```

## Verification

Useful narrow checks before committing core changes:

```bash
make -C src/firmware/os
make -C src/fpga/test gpu-acceptance gpu-acceptance-single
make -C src/fpga/targets/pocket bootloader os
make -C src/fpga/targets/pocket check
git diff --check
```

For a release candidate, run a full Quartus build and a seed sweep when timing
margin matters:

```bash
make -C src/fpga/targets/pocket build
make sweep SEEDS=1-30
```

## Deploy

`make package` writes the SD-card layout under `build/`.

The expected core directory is:

```text
build/Cores/ThinkElastic.openfpgaOS/
```

To deploy to an SDK checkout:

```bash
make sdk DEST=/path/to/openfpgaOS-SDK
```

This syncs SDK headers/sources, musl files, runtime binaries, the default
Analogizer config, and the sample bank when present.

## Runtime Model

The OS is single-process and bare-metal. Apps are ELF binaries loaded by the
boot/runtime path and call services through a stable SDK ABI backed by musl.
There is no MMU, scheduler, or dynamic linking; apps are trusted and run close
to the hardware.

The FPGA fabric provides the driver layer:

- indexed/RGB framebuffer scanout
- GPU scalar span, native 1/2/4-lane span-group with SDK-level 8-lane
  splitting, batch, clear, flip,
  translucency, and triangle commands
- 48 kHz, 32-voice hardware PCM mixer
- APF data-slot read/write and nonvolatile save handling
- Pocket controls, dock input, keyboard/mouse/controller events, Analogizer,
  and SNAC GPIO/shifter paths
- SDRAM, CRAM0, SRAM, and bridge arbitration

Diagnostic UART/trap output exists for fatal failures and service-host booting,
but normal production paths should not emit continuous UART traffic.

## GPU Notes

The GPU is optimized for indexed-color software-renderer workloads: BUILD/Doom
style spans, colormap lookup, masked pixels, translucent spans, clears, flips,
and textured triangles. Command data is built by the CPU in a cached SDRAM
scratch buffer, flushed, then pulled into the GPU's 16 KB internal command ring
by doorbell DMA. The old CPU MMIO command-data path is retired.

The active framebuffer target is SDRAM. The tested intermediate BRAM
framebuffer path did not improve measured rendering performance, so it has been
removed from the active RTL. The remaining SDK symbols for BRAM framebuffer
policy are compatibility shims only; new code should render directly to SDRAM.

The BUILD-style translucency table is GPU-private SRAM, not M10K BRAM. The SDK
still uploads it through `GPU_TRANSLUC_ADDR` / `GPU_TRANSLUC_DATA`, and the GPU
uses SRAM lookups during translucent read-modify-write spans. Opaque and masked
spans do not pay this lookup cost.

## Hardware Comparison

The audio path is a hardware sample mixer, not an FM chip. It is closest in
spirit to the Gravis Ultrasound or the wavetable side of an AWE32/AWE64: many
independent PCM voices are mixed in hardware from sample memory, with per-voice
rate, volume, pan, loop, and group/master control. Compared with common PC
sound cards:

| Device class | Relationship to openfpgaOS |
|--------------|----------------------------|
| AdLib / OPL2 / OPL3 | Different model. Those are FM synthesizers; openfpgaOS uses PCM samples and a sample-bank MIDI path. |
| Sound Blaster PCM DMA | More capable for game music/effects. Classic SB playback is mostly one streamed PCM channel; openfpgaOS mixes many hardware voices. |
| Gravis Ultrasound | Similar conceptually: hardware-mixed sample voices from memory. openfpgaOS is smaller and game-runtime focused, with 32 voices at 48 kHz stereo. |
| AWE32 / AWE64 wavetable | Similar high-level role for MIDI playback, but openfpgaOS exposes a simpler fixed runtime mixer instead of emulating EMU8000 behavior. |

The GPU is also a purpose-built raster accelerator, not a VGA clone and not a
general OpenGL-style 3D core. It keeps the simple framebuffer/palette model
that old PC games expect, then adds commands for the expensive draw paths:
spans, four-column vertical spans, batch submission, clears, flips,
translucency, texture lookup, colormap lookup, and triangle rasterization.

| Device class | Relationship to openfpgaOS |
|--------------|----------------------------|
| VGA / Mode 13h | Same broad framebuffer/palette heritage, but drawing is accelerated by FPGA commands instead of being entirely CPU-written. |
| SVGA linear framebuffer | Similar app-visible idea for pixels, with a custom low-resolution game focus rather than a PC display adapter feature set. |
| 2D blitter | Overlaps on clears, copies-by-command, and write coalescing, but the hot path is span/raster work rather than GUI rectangles. |
| Early 3D cards | Shares textured triangle and translucency ideas, but it is not a full fixed-function PC 3D pipeline: no driver stack, no OpenGL, no programmable shaders, and no general Z-buffer path. |
| BUILD/Doom-era software renderer | The closest workload match. The GPU accelerates the span and palette/colormap-heavy raster work those engines spend time on. |

## Maintenance Rules

- Keep source, core metadata, and required runtime assets tracked.
- Keep generated Quartus/Verilator/seed/simulation artifacts ignored.
- Keep target-specific behavior under `targets/pocket/`; keep reusable RTL and
  firmware interfaces in `common/`, `hal/`, `kernel/`, and `api/`.
- Validate firmware and RTL after changing shared interfaces.
- Update this README when build, deploy, or production workflow changes.

## Acknowledgments

- VexiiRiscv CPU: SpinalHDL / Charles Papon
- Analogizer adapter: RndMnkIII
- openFPGA framework: Analogue
- musl libc: Rich Felker
- jtframe utilities: Jose Tejada
