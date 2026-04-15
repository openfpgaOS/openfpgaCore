# openfpgaOS

A game development platform for the [Analogue Pocket](https://www.analogue.co/pocket). Write C, compile, copy to SD, run — no 45-minute FPGA synthesis. A RISC-V soft CPU on the Cyclone V FPGA runs your code at 100 MHz with hardware-accelerated video, sample-based audio, and controller input.

## Features

- **One-header API** — `#include "of.h"` and start writing a game
- **Sub-second iteration** — compile C, copy ELF to SD, run. No FPGA synthesis required
- **VexRiscv RISC-V CPU** — rv32imafc at 100 MHz, AXI4 bus, 32 KB D-cache + 8 KB I-cache, hardware FPU
- **320×240 double-buffered video** — 6 color modes (8/4/2-bit indexed, RGB565, RGB555, RGBA5551), 256-entry palette
- **32-voice PCM mixer** — 16-bit signed samples, per-voice pitch/pan/volume/SVF filter, 48 kHz stereo I2S output
- **Sample-based MIDI** — `of_midi` library renders Standard MIDI Files (Format 0/1) through `of_smp_voice`; ships with a Roland SC-55-derived `.ofsf` General MIDI bank at `assets/banks/sc55.ofsf`
- **Save system** — 10 × 256 KB save slots, CRAM1 PSRAM backed to SD via Chip32
- **96 MB+ memory** — 64 MB SDRAM, 16 MB CRAM0, 16 MB CRAM1, 256 KB SRAM
- **2-player input** — d-pad, ABXY, L/R shoulders, analog sticks, triggers
- **Analogizer support** — RGBS/YPbPr/composite video, SNAC controllers
- **musl libc** — standard C library via jump table (printf, malloc, fopen, math, etc.)
- **POSIX file I/O** — `fopen("game.dat")` for data files, `fopen("save_0")` for saves, auto-flush on close
- **Chip32 VM loader** — configurable save sizes, CRAM1 initialization

## Quick Start

For **game development**, use the [openfpgaOS SDK](https://github.com/ThinkElastic/openfpgaOS-SDK). You don't need this repo unless you're modifying the OS, FPGA design, or adding a new target.

### Building

```bash
# Prerequisites: RISC-V toolchain (riscv64-elf-*), Intel Quartus Prime, Java + sbt
cd src/fpga/targets/pocket

make              # full clean build: cpu → firmware → compile → test → deploy
make flash        # quick: rebuild firmware, patch into bitstream, deploy
make build        # incremental Quartus compile only
make cpu          # regenerate VexiiRiscv from SpinalHDL
make firmware     # rebuild bootloader + os.bin
make test         # run Verilator test suite (836 tests)
make check        # fast RTL syntax check
make program      # JTAG flash via USB Blaster (dev)
make clean        # wipe Quartus build artifacts
```

### Deploying

Copy `build/Cores/ThinkElastic.openfpgaOS/` to SD card, or:
```bash
./deploy.sh                    # auto-detect SD card
./deploy.sh ../openfpgaOS-SDK  # push to SDK repo instead
```

## Project Structure

```
openfpgaOS/
├── src/
│   ├── fpga/
│   │   ├── common/              ← portable RTL (CPU, bus, video, audio engines)
│   │   ├── vendor/              ← third-party IP (VexRiscv)
│   │   └── targets/pocket/     ← Analogue Pocket (APF, core_top, PLLs)
│   ├── firmware/
│   │   └── os/
│   │       ├── hal/             ← portable HAL + headers
│   │       ├── targets/pocket/  ← Pocket HAL implementations
│   │       └── kernel/          ← portable kernel (syscalls, ELF loader)
│   └── chip32/pocket/           ← Chip32 VM loader (APF-specific)
├── dist/
│   ├── core/                    ← JSON configs (audio, video, input, etc.)
│   └── platforms/               ← platform definition
├── tools/                       ← reverse_bits, capture_ocr
├── docs/                        ← developer documentation
├── deploy.sh                    ← deploy to SD card or SDK
└── Makefile                     ← build system (TARGET=pocket)
```

### Multi-target support

The codebase is split between portable code (`common/`, `hal/`, `kernel/`) and target-specific code (`targets/pocket/`). Adding a new FPGA target:

1. `src/fpga/targets/<name>/` — core_top.v, PLLs, pin assignments
2. `src/firmware/os/targets/<name>/` — regs.h, HAL implementations
3. `src/chip32/<name>/` — target-specific loader
4. `make TARGET=<name>`

## Design Approach

openfpgaOS is a single-process bare-metal runtime with a Linux-compatible syscall ABI. There's no MMU, no scheduler, no kernel modules. The FPGA fabric acts as the driver layer — video scanout, audio mixing, and memory control are hardware state machines, not software.

Apps are ELF binaries that call standard C functions (`fopen`, `malloc`, `printf`) through a jump table backed by musl libc. OS services (video, audio, input) use Linux syscall numbers handled by a minimal dispatcher. This means existing C codebases port with few changes — they don't know they're not on Linux.

| | openfpgaOS | Linux | Zephyr/FreeRTOS | Newlib bare-metal | CP/M |
|---|---|---|---|---|---|
| Processes | 1 | Many | Threads | 1 | 1 |
| MMU | No | Yes | Optional | No | No |
| Syscall ABI | Linux subset | Linux | Custom | Custom stubs | BDOS |
| Drivers | FPGA fabric | Kernel modules | HAL | BSP | BIOS |
| libc | musl (jump table) | musl/glibc | Newlib (partial) | Newlib | None |
| Kernel size | ~120 KB | Megabytes | 10-100 KB | N/A | ~8 KB |

The closest historical analog is CP/M or MS-DOS: single-process, hardware-specific BIOS, apps call the OS through a fixed interface. The Linux syscall ABI is what makes porting practical.

Trade-offs: no memory protection (apps are trusted), no concurrency (event callbacks instead of threads), no dynamic linking (jump table serves the same purpose with less overhead). These are deliberate choices for a platform where the CPU shares an FPGA with custom hardware and every ALM counts.

See [architecture.md](architecture.md) for the roadmap (multi-target support, capability descriptors, async I/O).

## Documentation

| Document | Description |
|----------|-------------|
| [Developer Guide](docs/developer-guide.md) | Tutorial + API reference |
| [Architecture](docs/architecture.md) | System architecture, memory map, boot flow |
| [HAL API](docs/hal-api.md) | Hardware abstraction layer reference |
| [Syscalls](docs/syscalls.md) | Syscall number table |
| [FPGA Design](docs/fpga-design.md) | Verilog module hierarchy, register map |
| [Building](docs/building.md) | Build instructions |

## License

| Component | License | Author |
|-----------|---------|--------|
| openfpgaOS (FPGA, firmware, OS) | MIT | ThinkElastic |
| VexiiRiscv CPU | MIT | SpinalHDL / Charles Papon |
| Analogizer adapter | — | RndMnkIII |
| openFPGA framework | — | Analogue |
| musl libc | MIT | Rich Felker |
| jtframe utilities | GPL-3.0 | Jose Tejada |

## Acknowledgments

- [SpinalHDL/VexiiRiscv](https://github.com/SpinalHDL/VexiiRiscv) — RISC-V CPU core (Charles Papon)
- [RndMnkIII](https://github.com/RndMnkIII) — Analogizer adapter
- [Analogue](https://www.analogue.co/developer) — Pocket openFPGA framework
- [musl libc](https://musl.libc.org/) — C library (Rich Felker)
- [jtframe](https://github.com/jotego/jtframe) — Clock and resync modules (Jose Tejada)
- dyreschlock — Platform image
