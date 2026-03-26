# openfpgaOS

A game development platform for the [Analogue Pocket](https://www.analogue.co/pocket). Write C, compile, copy to SD, run — no 45-minute FPGA synthesis. A RISC-V soft CPU on the Cyclone V FPGA runs your code at 100 MHz with hardware-accelerated video, FM audio, and controller input.

## Features

- **One-header API** — `#include "of.h"` and start writing a game
- **Sub-second iteration** — compile C, copy ELF to SD, run. No FPGA synthesis required
- **VexRiscv RISC-V CPU** — rv32imafc at 100 MHz, AXI4 bus, 32 KB D-cache + 8 KB I-cache, hardware FPU
- **320×240 double-buffered video** — 6 color modes (8/4/2-bit indexed, RGB565, RGB555, RGBA5551), 256-entry palette
- **OPL3 FM synthesis** — 18-channel hardware YMF262 (both register banks) + 48 kHz stereo PCM mixer
- **MIDI playback** — `of_midi` library with built-in GM instrument bank, Format 0+1, non-blocking pump
- **Save system** — 10 × 256 KB save slots, CRAM1 PSRAM backed to SD via Chip32
- **96 MB+ memory** — 64 MB SDRAM, 16 MB CRAM0, 16 MB CRAM1, 256 KB SRAM
- **2-player input** — d-pad, ABXY, L/R shoulders, analog sticks, triggers
- **Analogizer support** — RGBS/YPbPr/composite video, SNAC controllers
- **musl libc** — standard C library via jump table (printf, malloc, fopen, math, etc.)
- **POSIX file I/O** — `fopen("game.dat")` for data files, `fopen("save_0")` for saves, auto-flush on close
- **Chip32 VM loader** — configurable save sizes, CRAM1 initialization

## Quick Start

For **game development**, use the [openfpgaOS SDK](https://github.com/ThinkElastic/openfpgaOS-SDK). You don't need this repo unless you're modifying the OS, FPGA design, or adding a new target.

### Building the OS

```bash
# Prerequisites: RISC-V toolchain + Intel Quartus Prime
make firmware          # Build OS kernel + apps
make chip32            # Build Chip32 loader
make fpga              # Compile FPGA bitstream (requires Quartus)
make full              # All of the above + package
```

### Deploying

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
│   │   ├── vendor/              ← third-party IP (VexRiscv, OPL3 YMF262)
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
| OPL3 synthesizer (YMF262) | LGPL | Greg Taylor |
| VexiiRiscv CPU | MIT | SpinalHDL / Charles Papon |
| Analogizer adapter | — | RndMnkIII |
| openFPGA framework | — | Analogue |
| musl libc | MIT | Rich Felker |
| jtframe utilities | GPL-3.0 | Jose Tejada |

## Acknowledgments

- [SpinalHDL/VexiiRiscv](https://github.com/SpinalHDL/VexiiRiscv) — RISC-V CPU core (Charles Papon)
- [Greg Taylor](https://github.com/gtaylormb/opl3_fpga) — OPL3 FPGA synthesizer
- [RndMnkIII](https://github.com/RndMnkIII) — Analogizer adapter
- [Analogue](https://www.analogue.co/developer) — Pocket openFPGA framework
- [musl libc](https://musl.libc.org/) — C library (Rich Felker)
- [jtframe](https://github.com/jotego/jtframe) — Clock and resync modules (Jose Tejada)
- dyreschlock — Platform image
