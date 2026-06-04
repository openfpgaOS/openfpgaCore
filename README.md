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

The current data-slot contract is:

```text
slot 1   os.bin
slot 2   os.ini
slot 3   default app ELF
slot 4-6 app data files
slot 7   optional .ofsf sound bank
slot 8   shared nonvolatile config
slot 10-19 nonvolatile save slots
```

`os.ini` is optional. When present, the OS parses it before launching the app:

```ini
[os]
ELF=app.elf
ARGS=--help -a -p path
```

`ELF` names the app ELF to launch, resolved by APF filename. `slot:N` is also
accepted for explicit numeric slot launches. `ARGS` is tokenized and passed as
`argv[1...]`; `argv[0]` is the selected ELF name. Apps can read configuration
sections through `of_config_get()`, `of_config_get_int()`,
`of_config_get_bool()`, and `of_config_next()`.

The FPGA fabric provides the driver layer:

- indexed/RGB framebuffer scanout
- GPU scalar span, native 1/2/4-lane span-group with SDK-level 8-lane
  splitting, batch, clear, flip,
  translucency, and triangle commands
- 48 kHz, 32-voice hardware PCM mixer
- APF data-slot read/write and nonvolatile save handling
- Pocket controls, dock input, keyboard/mouse/controller events, Analogizer,
  SNAC physical pin pass-through/raw shifter paths, and a PSX decoded poller
- SDRAM, CRAM0, SRAM, and bridge arbitration

Diagnostic UART/trap output exists for fatal failures and service-host booting,
but normal production paths should not emit continuous UART traffic.

## Video Modes

The Pocket target boots in 320x240, 8-bit indexed framebuffer mode with three
SDRAM framebuffers. Apps can change the logical framebuffer through
`of_video_set_mode()` and query the active mode with `of_video_get_mode()`.

The packaged `video.json` scaler slots are:

```text
slot 0  320x240
slot 1  320x200
slot 2  320x224
slot 3  320x256
slot 4  320x288
slot 5  400x300
slot 6  256x240
```

The SDK accepts larger source framebuffers up to the current hardware limits
of 800x600 and 2048 bytes per row. The scanout path scales/crops the source
into the selected Pocket scaler slot where a matching physical mode exists.

## GPU Notes

The GPU is optimized for indexed-color software-renderer workloads: BUILD/Doom
style spans, colormap lookup, masked pixels, translucent spans, clears, flips,
and textured triangles. Command data is built by the CPU in a cached SDRAM
scratch buffer, flushed, then pulled into the GPU's 16 KB internal command ring
by doorbell DMA.

The framebuffer target is SDRAM. Apps set the render target with
`of_gpu_set_framebuffer()` and synchronize CPU framebuffer access with
`of_gpu_prepare_framebuffer_for_cpu()` when mixing GPU and direct CPU writes.

The translucency table is GPU-private SRAM. The SDK uploads it through
`GPU_TRANSLUC_ADDR` / `GPU_TRANSLUC_DATA`, and the GPU uses SRAM lookups during
translucent read-modify-write spans. Opaque and masked spans do not pay this
lookup cost.

## Maintenance Rules

- Keep source, core metadata, and required runtime assets tracked.
- Keep generated Quartus/Verilator/seed/simulation artifacts ignored.
- Keep target-specific behavior under `targets/pocket/`; keep reusable RTL and
  firmware interfaces in `common/`, `hal/`, `kernel/`, and `api/`.
- Validate firmware and RTL after changing shared interfaces.
- Update this README when build, deploy, or production workflow changes.

## Acknowledgments

- VexiiRiscv CPU: SpinalHDL / Charles Papon
- Analogizer adapter and SNAC pinout reference: RndMnkIII
- openFPGA framework: Analogue
- musl libc: Rich Felker
