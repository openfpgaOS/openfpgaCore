# Building ofOS

## Prerequisites

### RISC-V Toolchain

A bare-metal RISC-V GCC cross-compiler with rv32imafc support.

```bash
# Arch Linux
sudo pacman -S riscv64-elf-gcc riscv64-elf-newlib

# Ubuntu/Debian
sudo apt install gcc-riscv64-unknown-elf

# macOS (Homebrew)
brew install riscv-gnu-toolchain
```

The build system auto-detects `riscv64-unknown-elf-gcc` or `riscv64-elf-gcc`.

### Intel Quartus Prime (FPGA only)

Required only for FPGA bitstream compilation. Lite edition is sufficient.

- Version 25.1 or later
- Cyclone V device support
- `quartus_sh`, `quartus_cdb`, `quartus_asm` must be in PATH

### Analogue Pocket

- Firmware 2.2 or later
- USB Blaster for JTAG programming (development)

## Build Targets

All commands run from the project root.

### OS Firmware

```bash
make firmware              # Build os.bin
```

This runs `make` in `src/firmware/os/`, producing:
- `firmware.mif` -- BRAM initialization (bootloader + trap handler)
- `os.bin` -- OS kernel binary (loaded from SD card at boot)

### Example Application

```bash
cd src/firmware/apps/example
make
```

Produces `app.elf` -- a static-PIE ELF binary.

### musl libc (optional)

```bash
cd src/firmware/musl
./build_musl.sh
```

Downloads musl 1.2.5, cross-compiles for rv32imafc, installs to `musl/lib/libc.a` and `musl/include/`. Once built, the OS Makefile automatically detects and links musl.

### FPGA Bitstream

```bash
make fpga                  # Full Quartus compilation (~15 min)
make firmware-update       # Update MIF only, skip resynthesis (~1 min)
```

`make fpga` rebuilds the firmware MIF first, clears Quartus cache, then runs a full compile. `make firmware-update` uses `quartus_cdb --update_mif` to patch the existing bitstream, which is much faster.

### Package Release

```bash
make                       # Package release/ directory
make full                  # FPGA + firmware + package
```

Creates the `release/` directory with the SD card structure:

```
release/
+-- Assets/ofos/common/os.bin
+-- Assets/ofos/ThinkElastic.ofOS/*.json
+-- Cores/ThinkElastic.ofOS/
|   +-- bitstream.rbf_r
|   +-- core.json, video.json, audio.json, ...
+-- Platforms/ofos.json
+-- Platforms/_images/ofos.bin
```

### JTAG Programming (Development)

```bash
make program               # Program FPGA via USB Blaster
```

Programs the bitstream directly to the FPGA for development testing. Does not persist across power cycles.

## Quick Firmware Iteration

For rapid firmware development (no FPGA recompile):

```bash
make fw                    # Build firmware, update MIF in bitstream, package
```

This is the fastest path: recompiles firmware, patches the MIF into the existing bitstream with `quartus_cdb --update_mif` + `quartus_asm`, then packages the release.

## Build System Details

### OS Makefile (`src/firmware/os/Makefile`)

| Variable | Default | Description |
|----------|---------|-------------|
| `CROSS` | auto-detected | Toolchain prefix |
| `ARCH` | `rv32imafc` | RISC-V ISA |
| `ABI` | `ilp32f` | ABI (hard float) |

The Makefile compiles in three stages:
1. **Boot code** (`boot/`) -- compiled with `-Os` for size, placed in BRAM
2. **HAL + kernel** (`hal/`, `kernel/`) -- compiled with `-O2 -flto` for speed, placed in SDRAM
3. **Link** -- produces `firmware.elf`, then extracts `firmware.mif` (BRAM) and `os.bin` (SDRAM)

### App Makefile (`src/firmware/apps/example/Makefile`)

Apps are compiled with `-fPIE` and linked with `-pie -static-pie`. The CRT startup (`crt/start.S`) provides `_start`. Apps link against `libgcc` only by default; add musl's `libc.a` for full C library support.

### Top-Level Makefile

| Target | Description |
|--------|-------------|
| `all` / `package` | Package release directory (requires existing bitstream) |
| `full` | FPGA + firmware + package |
| `fpga` | Full Quartus compilation |
| `firmware` | Build OS firmware only |
| `firmware-mif` | Build firmware and install MIF to FPGA directory |
| `firmware-update` / `fw` | Patch MIF into existing bitstream + package |
| `program` | JTAG programming |
| `clean` | Remove all build artifacts |
| `clean-fpga` | Remove FPGA build artifacts |

## Testing

After deploying:

```bash
# Program FPGA via JTAG
cd src/fpga && make program

# Capture and OCR the screen output
tools/capture_ocr.sh
```

## Troubleshooting

**`riscv64-elf-gcc: command not found`** -- Install the RISC-V toolchain for your platform.

**`quartus_sh: command not found`** -- Add Quartus to your PATH: `export PATH=$PATH:/path/to/quartus/bin`

**`Bitstream not found`** -- Run `make fpga` first to compile the FPGA design.

**`relocation truncated to fit: R_RISCV_JAL`** -- A function in BRAM is trying to call SDRAM code with a direct `jal`. Use `la t0, func; jalr ra, t0` for cross-region calls in assembly.

**musl build fails** -- Ensure you have `curl` installed for downloading the musl source. The build requires a working RISC-V cross-compiler.
