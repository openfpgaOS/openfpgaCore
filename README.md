# openfpgaOS

openfpgaOS is a bare-metal game runtime and FPGA core for retro-style
handhelds and consoles. It runs a 32-bit RISC-V CPU (VexiiRiscv) in a
Cyclone V fabric and exposes hardware services for video, audio, input,
storage, and app loading.

Two hardware targets share the same OS, the same app ABI, and the same
app binaries:

- **pocket** — Analogue Pocket (openFPGA/APF, Cyclone V E `5CEBA4F23C8`)
- **mister** — MiSTer: DE10-Nano and SuperStation One (Cyclone V SoC
  `5CSEBA6U23I7`, SDR SDRAM module required)

A third target, **sim**, builds the firmware for the Verilator
full-system testbench.

For game development, use the SDK repository —
[openfpgaOS-SDK](https://github.com/ThinkElastic/openfpgaOS-SDK)
(app build system, `of_*` API, SDL2 compatibility layer, PC test shim,
per-platform packaging/deploy under `src/sdk/platforms/`). This
repository is for changing the OS, firmware, FPGA RTL, target packaging,
and the canonical SDK headers/runtime, which `make sdk DEST=` mirrors
into an SDK checkout. The same SDK app `.elf` runs unchanged on both
hardware targets — everything target-specific is delivered at runtime
through the capability/services tables.

System internals are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); porting to new hardware in
[docs/ADDING_A_TARGET.md](docs/ADDING_A_TARGET.md).

## Layout

- `src/fpga/common/`: reusable RTL for CPU integration, AXI, GPU, video,
  audio, SDRAM, CRAM0, input, and shared peripherals. Platform-neutral —
  both targets instantiate these unchanged.
- `src/fpga/targets/pocket/`: Analogue Pocket top level, APF bridge, PLLs,
  constraints, Analogizer output, and target build flow (Quartus 25.1std).
- `src/fpga/targets/mister/`: MiSTer core top (`emu.sv`), `hps_bridge.v`
  (boot.rom ioctl DMA + disk-image sector engine), vendored MiSTer
  framework under `sys/` (do not edit — see `sys/VENDOR.md`), and target
  build flow (Quartus 17.0.x — a hard MiSTer framework requirement).
  See `src/fpga/targets/mister/README.md` for the port architecture.
- `src/firmware/os/`: bootloader, OS kernel, HAL, syscall layer, file/save
  services, input, terminal. Per-target code lives in
  `targets/{pocket,mister,sim}/`; the kernel and HAL are shared.
- `src/firmware/api/`: SDK-facing headers, linker script, and shared
  runtime sources (canonical copies — `make sdk` mirrors them to the SDK).
- `dist/`: Pocket core JSON, platform metadata, and default Analogizer
  config (MiSTer needs no metadata — discovery is by rbf/folder name).
- `assets/`: runtime assets such as the sample bank.

Generated Quartus, Verilator, seed sweep, SignalTap, and simulation
outputs are not source artifacts and should not be committed.

## Targets

Every build goal takes `TARGET=<name>`. Resolution order:

1. command line — `make build TARGET=mister`
2. environment — `export TARGET=mister`
3. per-checkout default — `make use-mister` writes a gitignored `.target`
   file; `make use-default` removes it
4. built-in default — `pocket`

Each target builds into its own isolated tree
(`src/firmware/os/bld/<target>/`), so `pocket`, `mister`, and `sim`
coexist and switching between them recompiles nothing — no shared
objects, no manual clean.

Porting to a new platform: see
[docs/ADDING_A_TARGET.md](docs/ADDING_A_TARGET.md) — what a target must
provide (RTL glue, firmware HAL files, memory contract, SDK packaging),
with the MiSTer port as the worked reference.

Slicing the FPGA into feature profiles (the os25 / os30 variants and
the `INCLUDE_*` addon system): see
[docs/VARIANTS_AND_ADDONS.md](docs/VARIANTS_AND_ADDONS.md).

The SDK app build system runs the RISC-V toolchain in a Docker
container — no host install needed; downstream cores can reuse the
same flow.  See [docs/SDK_PORTING.md](docs/SDK_PORTING.md).

## Requirements

The pocket build flow runs every long-lived toolchain (sbt / SpinalHDL,
riscv64 GCC + libc, Quartus Prime) inside containers, so a fresh
clone on Linux or macOS (including Apple Silicon) needs only:

- a **container runtime** — either:
  - **Docker** (Docker Desktop, OrbStack, Colima, or native dockerd), or
  - **Apple `container`** (<https://github.com/apple/container>) on
    Apple-silicon macOS — Quartus's x86_64 binaries run via Rosetta.
- **git**, **make**, **bash**

The runtime is auto-detected (Docker is preferred when both are present);
force one with `OCI=docker` or `OCI=container`.  See [`tools/oci.sh`](tools/oci.sh)
for the detection + flag-translation details.

The first invocation of `make full` builds the three container images
(vexii / firmware / quartus) on demand. They're tagged
`openfpgaos-{vexii,firmware,quartus-full}` and cached locally — subsequent
builds reuse them.

> **Apple `container` note:** each container is its own lightweight VM
> capped at ~1 GiB by default, so the Quartus/sbt wrappers pass `--memory`
> (16 GiB for Quartus, 8 GiB for sbt; override with `CONTAINER_MEM`).
> Quartus 25.1 installs and runs fine under Rosetta; the *mister* Quartus
> 17.0 installer does **not** translate (`rosetta error: bss_size overflow`),
> so bake the mister image from a pre-extracted tree (the `prebuilt` path in
> `build-quartus17-image.sh`) rather than the raw 2017 installer.

The mister target still uses a host Quartus install (`17.0.x`, expected
at `/home/alberto/intelFPGA_lite/17.0/quartus`) because Quartus 17 is a
hard MiSTer framework requirement and isn't worth containerizing for the
single-host Linux build flow it expects.

Quartus is not redistributable, so each contributor supplies their own
copy.  Download the Lite (free, covers Cyclone V) or Standard tarball:

- Lite: <https://www.altera.com/downloads/fpga-development-tools/quartus-prime-lite-edition-design-software-version-25-1-linux>
- Standard: <https://www.altera.com/downloads/fpga-development-tools/quartus-prime-standard-edition-design-software-version-25-1-linux>

Drop the resulting `Quartus-*-linux.tar` (~9 GB) anywhere under `tools/`
(top level works); `make full` auto-bakes it into the image on first run
(~10 min, one-time).  See `tools/build-quartus-image.sh` for details.

Submodules auto-init on first build — no manual `git submodule update`
needed.  (For Verilator-based RTL tests, install Verilator 5.x and run
`make test` directly; tests are not part of the default build.)

## Build

From the repository root:

```bash
make                 # show help (lists targets and the active default)
make full            # target full build: CPU, bootloader, OS, FPGA, summary
make build           # clean Quartus compile (ap_core.rbf / mister.rbf)
make firmware        # rebuild bootloader + OS and patch an existing bitstream (~10 s)
make os              # rebuild os.bin
make check           # Quartus analysis/synthesis only
make test            # Verilator RTL test suite
make timing          # timing summary from the last full compile
make report          # resource summary; FULL=true adds top ALM entities
                     #   and the 25 worst setup paths (TOP=/PATHS= to tune)
make sweep SEEDS=1-30  # fitter seed sweep; ranks Fmax, reports WNS/TNS,
                     #   rebuilds with the best seed (both targets)
make package         # SD-card layout (pocket) → build/; MiSTer builds a
                     #   core-release zip + Downloader DB in releases/mister/
                     #   (make release TARGET=mister then drafts the GH release)
make clean           # remove generated build artifacts
```

Add `TARGET=mister` to any of the above, or set a sticky default with
`make use-mister`. The target Makefiles can also be used directly:

```bash
make -C src/fpga/targets/pocket check
make -C src/fpga/targets/mister build
```

## Verification

Useful narrow checks before committing core changes:

```bash
make -C src/firmware/os                       # firmware build (active target)
make -C src/firmware/os check-api             # SDK portability gate
                                              #   (builds pocket+sim+mister)
make -C src/fpga/test gpu-acceptance gpu-acceptance-single
make -C src/fpga/test hps-bridge              # MiSTer bridge datapath suite
make -C src/firmware/os/targets/mister/test/pc run   # host-native FAT-stack
                                              #   suite (file/save HAL on a
                                              #   real .vhd, ASAN/UBSAN)
make -C src/fpga/targets/pocket check
git diff --check
```

For a release candidate, run a full Quartus build and a seed sweep when
timing margin matters:

```bash
make build  [TARGET=mister]
make sweep SEEDS=1-30  [TARGET=mister]
```

When changing shared RTL (`src/fpga/common/`), validate **both** targets:
the Verilator suites plus `make check` per target.

## Deploy

The structure is the same for both targets: **this repo builds the core;
the SDK pushes to hardware.**

`make deploy` refreshes the core artifacts under `build/` from whatever
is on disk (it never touches a device):

- pocket: `build/Cores/ThinkElastic.openfpgaOS/` (APF SD-card tree)
- mister: `build/mister/` (`openfpgaOS.rbf` + `boot.rom`)

To sync an SDK checkout:

```bash
make sdk DEST=/path/to/openfpgaOS-SDK
```

This mirrors the SDK headers/sources, musl headers + `libc.a`/crt
objects, the Pocket core JSON, the sample bank, and the `os.bin` runtime
binary of both targets (`runtime/mister/os.bin`). The os.bin copy is
read from the target's own build tree (`src/firmware/os/bld/<target>/os.bin`),
so a mister-built kernel can never land in the pocket runtime slot or vice versa. The MiSTer core bitstream
is no longer vendored per game: it ships once from the openfpgaOS core
release (`make package`/`make release TARGET=mister`), so `make sdk` no
longer copies `openfpgaOS.rbf` into `runtime/mister/`.

Device deployment then happens from the SDK:

- pocket: `platforms/pocket/copy.sh` → SD card
- mister: the primary flow is a per-game engine update —
  `platforms/mister/copy.sh game <Game> <GameElf> <elf> [ip]` atomically
  scp's the built engine ELF to the loose file
  `games/OpenfpgaOS/<Game>/<GameElf>` (e.g. `doom.elf`), leaving the
  read-only `boot.vhd` (the wads) and the writable saves volume untouched
  (no Main-stop, no loop-mount). `make copy TARGET=mister` (aliased by
  `make copy-app`) drives this. The core is installed once from the
  openfpgaOS core release (see `make package`/`make release TARGET=mister`
  above), not vendored per game, so `copy.sh core` now pushes only
  `boot.rom`. Full flow: `src/sdk/platforms/mister/PACKAGING.md`.

## Runtime Model

The OS is single-process and bare-metal. Apps are ELF binaries loaded by
the boot/runtime path and call services through a stable SDK ABI backed
by musl. There is no MMU, scheduler, or dynamic linking; apps are trusted
and run close to the hardware.

The data-slot contract is identical on both targets from the app's point
of view; only the backing differs (APF data slots on Pocket, fixed paths
inside a FAT32 disk image on MiSTer):

```text
slot       pocket (APF)            mister (.vhd path)
1          os.bin                  boot.rom staging (RAM-backed)
2          os.ini                  /os.ini
3          default app ELF         /app.elf
4-6        app data files          enumerated from / and /assets/
7          optional .ofsf bank     /bank.ofsf
8          shared nonvol config    /config/shared.cfg
9          per-game settings       /config/duke3d.cfg
10-19      nonvolatile saves       /saves/slot_N.sav
20+        (datatable scan)        enumerated from / and /assets/
```

Apps open data files by filename on both targets — the registry is
populated from APF GETFILE on Pocket and from a directory scan on MiSTer.
Saves on MiSTer are preallocated contiguous 256 KB files written through
(`f_write` + `f_sync` per write): a power cut can corrupt at most one
save's payload, never FAT metadata. Do not re-create the save files with
ordinary tools — `mkimage.c` preallocates them correctly.

`os.ini` is optional. When present, the OS parses it before launching the
app:

```ini
[os]
ELF=app.elf
ARGS=--help -a -p path
```

`ELF` names the app ELF to launch, resolved by filename. `slot:N` is also
accepted for explicit numeric slot launches. `ARGS` is tokenized and
passed as `argv[1...]`; `argv[0]` is the selected ELF name. Apps can read
configuration sections through `of_config_get()`, `of_config_get_int()`,
`of_config_get_bool()`, and `of_config_next()`.

The FPGA fabric provides the driver layer:

- indexed/RGB framebuffer scanout
- GPU scalar span, native 1/2/4-lane span-group with SDK-level 8-lane
  splitting, batch, clear, flip, translucency, and triangle commands
- 48 kHz, 32-voice hardware PCM mixer
- data-slot read/write and nonvolatile save handling (APF bridge on
  Pocket; `hps_bridge` sector engine on MiSTer, serving the same
  data-slot register ABI)
- controls: Pocket pad/dock/keyboard/mouse + Analogizer/SNAC paths on
  Pocket; hps_io joysticks remapped in RTL to the same OF_BTN layout on
  MiSTer
- SDRAM, CRAM0 (Pocket), SRAM/BRAM LUT, and bridge arbitration

Per-target HAL notes: `of_analogizer_*` is a HAL contract every target
implements (the kernel answers app-facing detection ecalls through it —
MiSTer's implementation reports "not present"); the SNAC driver is
Pocket-only and is not built for MiSTer.

Diagnostic UART/trap output exists for fatal failures and service-host
booting (PHDP is Pocket-only), but normal production paths should not
emit continuous UART traffic.

## Video Modes

Both targets boot in 320x240, 8-bit indexed framebuffer mode with three
SDRAM framebuffers. Apps can change the logical framebuffer through
`of_video_set_mode()` and query the active mode with `of_video_get_mode()`.

The scaler slots (Pocket `video.json` / MiSTer raster active region):

```text
slot 0  320x240
slot 1  320x200
slot 2  320x224
slot 3  320x256
slot 4  320x288
slot 5  400x300
slot 6  256x240
```

The SDK accepts larger source framebuffers up to the current hardware
limits of 800x600 and 2048 bytes per row. The scanout path scales/crops
the source into the selected slot where a matching physical mode exists.

On the Pocket the raster feeds the APF scaler (VRR 42–60 Hz supported);
on MiSTer it feeds the framework `ascal` scaler to HDMI (and the
framework's analog outputs), pinned to a fixed 60.0 Hz in v1 — the
firmware VRR register is accepted but ignored there.

## MiSTer Target Notes

Port status: first full Quartus 17.0.2 compile is clean and timing-closed
(setup +0.68 ns at the slow corner, 51 % ALMs), the bridge datapath has a
27-assertion Verilator suite and the FAT stack a 99-assertion host-native
suite. The MiSTer core boots the OS and runs the per-game Doom bundle on hardware.

Key architecture points (details in `src/fpga/targets/mister/README.md`):

- One SDR SDRAM module backs everything; the hot 64 MB map is identical
  to the Pocket, with an 8 MB staging arena at `0x13300000` replacing the
  CRAM roles. v1 uses 64 MB of the module (128 MB addressing is a planned
  follow-up).
- `hps_bridge.v` replaces the whole APF bridge stack and serves the
  existing `DS_*` data-slot register handshake: slot 0 is the raw disk
  device (512-byte sectors), boot.rom is ioctl-DMA'd into staging at core
  start.
- Firmware mounts the OSD-mounted disk image with vendored FatFs
  (R0.15a); the SDK's `mkimage.c` builds the image with the same FatFs
  (`f_mkfs` + contiguous `f_expand`) — no mtools/loopback needed.
- Two core PLLs (100 MHz integer pair for CPU/SDRAM, fractional
  24.576 MHz for video — no legal shared VCO exists for both).
- The GPU's translucency LUT lives in a 32 KB BRAM (`sram_bram.v`);
  the Pocket's external async SRAM has no MiSTer equivalent.
- v1 limits: fixed 60 Hz, no 15 kHz native analog raster, no USB
  keyboard, OPENFILE/GETFILE data-slot ops report "unsupported".

## GPU Notes

The GPU is optimized for indexed-color software-renderer workloads:
BUILD/Doom style spans, colormap lookup, masked pixels, translucent
spans, clears, flips, and textured triangles. Command data is built by
the CPU in a cached SDRAM scratch buffer, flushed, then pulled into the
GPU's 16 KB internal command ring by doorbell DMA.

The framebuffer target is SDRAM. Apps set the render target with
`of_gpu_set_framebuffer()` and synchronize CPU framebuffer access with
`of_gpu_prepare_framebuffer_for_cpu()` when mixing GPU and direct CPU
writes.

The translucency table is GPU-private (external SRAM on Pocket, BRAM on
MiSTer). The SDK uploads it through `GPU_TRANSLUC_ADDR` /
`GPU_TRANSLUC_DATA`, and the GPU uses table lookups during translucent
read-modify-write spans. Opaque and masked spans do not pay this lookup
cost.

## Maintenance Rules

- Keep source, core metadata, and required runtime assets tracked.
- Keep generated Quartus/Verilator/seed/simulation artifacts ignored
  (including `.target` and the per-target firmware build trees `src/firmware/os/bld/`).
- Keep target-specific behavior under `targets/<target>/`; keep reusable
  RTL and firmware interfaces in `common/`, `hal/`, `kernel/`, and `api/`.
- Vendored trees are pristine: `targets/mister/sys/` (MiSTer framework)
  and `targets/mister/fatfs/` (FatFs) are re-synced from upstream, never
  edited in place.
- Shared-RTL changes must keep the other target building and its
  Verilator suites green — MiSTer-only hooks in shared files are tied
  off on Pocket (see `axi_periph_slave.v` REGION_HPS and the arbiter M2
  read channel for the pattern).
- Validate firmware and RTL after changing shared interfaces.
- Update this README when build, deploy, or production workflow changes.

## Acknowledgments

- VexiiRiscv CPU: SpinalHDL / Charles Papon
- musl libc: Rich Felker and contributors
- Analogizer adapter and SNAC pinout reference: RndMnkIII
- openFPGA framework: Analogue
- MiSTer framework (`targets/mister/sys/`): MiSTer-devel / Sorgelig
- FatFs: ChaN
