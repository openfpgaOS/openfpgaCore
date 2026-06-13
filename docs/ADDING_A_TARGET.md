# Adding a Hardware Target

How to port openfpgaOS to a new platform. The repo is structured so a
target is two directories plus an SDK packaging arm — the kernel, HAL
interfaces, common RTL, and every app binary stay untouched. The MiSTer
port (`targets/mister/`) is the reference for everything below; `pocket`
is the original and `sim` shows the minimal firmware-only shape.

Everything is auto-discovered: a directory under `src/fpga/targets/<name>/`
with a `Makefile` makes `make <goal> TARGET=<name>` and `make use-<name>`
work from the root with **zero root-Makefile edits**. The root names no
target — it forwards every goal (including `package`) to your target
Makefile, loops every target's `sdk-runtime.sh` for `make sdk`, and reads
each target's `about.txt` for `make help`. Add the directory, implement
the contract, done.

---

## 1. What a target actually is

Both existing hardware targets are the same machine — VexiiRiscv @
100 MHz, the common AXI fabric, GPU, scanout, mixer — wearing different
I/O glue. A port supplies:

| Concern | Pocket | MiSTer | Your target |
|---|---|---|---|
| Host/bridge I/O | APF bridge + CRAM staging | `hps_bridge.v` (ioctl + sd sector engine) | something serving the `DS_*` ABI |
| Video out | APF scaler (LCD) | framework ascal → HDMI | consume the 24.576 MHz raster |
| Audio out | I2S (`audio_output.v`) | parallel `AUDIO_L/R` | consume the mixer's 48 kHz stream |
| Input | APF cont regs | joysticks remapped in RTL | produce APF-layout cont regs |
| Main RAM | 16-bit SDR SDRAM | 16-bit SDR SDRAM | `io_sdram` word/burst interface |
| GPU LUT scratch | external async SRAM | 32 KB BRAM (`sram_bram.v`) | either, behind the sram word interface |
| os.bin delivery | APF data slot → CRAM | boot.rom ioctl → SDRAM staging | any path that lands os.bin in RAM + a "ready" flag |
| File/save store | APF data slots (SD) | FAT32 disk image (FatFs) | any blocking byte/sector transport |

## 2. FPGA: `src/fpga/targets/<name>/`

Start from `mister/emu.sv` — it is the cleaner of the two tops (the
Pocket's `core_top.v` carries APF/Analogizer history). The non-negotiable
contracts when instantiating the common RTL:

- **`cpu_system`** — three AXI masters out (sdram/cram0/local). Tie off
  `m_cram0_*` if you have no second memory (MiSTer pattern: tied so a
  stray 0x30000000 access stalls loudly).
- **`axi_periph_slave`** — this is the firmware ABI; never fork it.
  Drive its inputs (cont1/2 keys in OF_BTN bit order, rtc, vsync,
  dataslot ack/done/err, `hps_status` block or zeros) and consume its
  outputs (dataslot level-strobes, mixer MMIO, GPU regs, palette).
  Author `HW_FEATURES` honestly — apps gate on it.
  - The **data-slot handshake** is the universal bridge contract:
    `target_dataslot_read/write` are LEVEL signals held until DONE is
    captured; your bridge must do ack LOW→HIGH while active, done
    LOW→HIGH while ack high, release both after the strobe drops
    (READY = ~ack). `hps_bridge.v` + `tb_hps_bridge` show and verify the
    exact sequencing.
- **`axi_sdram_arbiter`** — M0 GPU, M1 CPU, M2 your bridge (read+write),
  M3 mixer. Tie off unused channels explicitly.
- **`axi_sdram_slave` + `io_sdram`** — 24-bit word addresses (32-bit
  words), `arlen ≤ 15`, plus the scanout's dedicated burst port. Copy the
  pulse-adapter block verbatim from either target.
- **Scanout/raster** — `video_CRT_scanout_indexed_BRAM` + the 780×525
  24.576 MHz raster (both tops carry the same ~80-line raster block).
  Keep V_TOTAL fixed unless your display chain tolerates VRR.
- **Clocks** — 100 MHz CPU/RAM pair (chip clock phase-shifted; both
  targets ship 6750 ps) + 24.576 MHz video. These cannot share a PLL VCO
  (24.576/100 = 768/3125) — plan two PLLs and the matching SDC clock
  groups. hps-style host logic should run on the CPU clock to avoid CDC
  (the Pocket's clk_74a CDC exists only because APF forces it).
- **Project files** — `<proj>.qsf/.qpf`, an `.sdc` with the intra-core
  clock groups + RAM I/O constraints, `MIF_FILE firmware.mif` (the boot
  BRAM init inside `axi_periph_slave`).
- **`Makefile`** — mirror `mister/Makefile`. The root delegates these
  goals, so provide them all: `full cpu bootloader os firmware compile
  build check test timing report sweep deploy package clean`. Set
  `PROJECT` and pass `PROJECT=`/`CLOCK_RE=` (the 100 MHz clock's BRE in
  the Fmax table) to `tools/sweep.sh`; `tools/report.sh` only needs
  `PROJECT`. `deploy` refreshes `build/<name>/` from on-disk artifacts;
  `package` assembles the **release** layout under `build/<name>/` (Pocket
  = APF `Cores/Assets/Platforms` tree; MiSTer = `rbf + boot.rom`). A goal
  that doesn't apply must still exist — MiSTer's `program` just prints
  guidance and exits 1, so the root's delegation never hits make's "no
  rule" error.
- **`about.txt`** — one line shown in `make help`'s target list, e.g.
  `MiSTer / DE10-Nano — Cyclone V SE A6 (Quartus 17.0.x)`.
- **`sdk-runtime.sh <sdk_dir>`** — copies THIS target's runtime artifacts
  into an SDK checkout under `runtime/<target>/` (bitstream, kernel,
  loader, …). Run from anywhere (resolve paths via `$0`); the root `make
  sdk` loops every target's script. Gate the kernel copy on the firmware
  build stamp (`src/firmware/os/.last_target`) so you never publish
  another target's `os.bin`. Each target gets its own subdir — Pocket
  writes `runtime/pocket/{bitstream.rbf_r, os.bin, loader.bin,
  ap_core.sof}`, MiSTer writes `runtime/mister/{openfpgaOS.rbf, os.bin}`.
  Only genuinely target-agnostic files (the SC-55 `bank.ofsf`) live at
  `runtime/` root, written by the root sdk rule.

A different FPGA **vendor** (Xilinx/Vivado, Lattice/oss-cad, Gowin, …) is
just a target whose Makefile invokes that vendor's tools inside these
goals — the root and the shared RTL under `src/fpga/common/` are
vendor-neutral, and `make help` reads your toolchain string straight from
`about.txt`.

Validate before any hardware exists:

```bash
# elaboration lint of the full top against the real common RTL
verilator --lint-only ... --top-module <your-top>     # see the MiSTer
                                                      # session pattern
make -C src/fpga/test all                             # shared suites
# + a bridge testbench: copy tb_hps_bridge.v/_main.cpp and adapt the
#   host model — the dataslot handshake asserts are target-independent
```

## 3. Firmware: `src/firmware/os/targets/<name>/`

The Makefile builds this exact manifest per target (plus conditional
extras): `target_platform.h`, `video.c`, `audio.c`, `input.c`, `save.c`,
`file.c`, `analogizer.c`, `terminal.c`, `timer.c`, `disk_boot.c`,
`boot/{start.S, boot.c}`. Dispositions, from the MiSTer port:

| File | Typical effort |
|---|---|
| `target_platform.h` | author — the memory contract (see below) |
| `video.c` `audio.c` `timer.c` `terminal.c` | one-line `#include "../pocket/<f>.c"` shim (same MMIO contract; both sim and mister do this so pocket fixes propagate) |
| `input.c` | light rewrite if your RTL presents APF-layout cont regs |
| `analogizer.c` | **mandatory stub** — `of_analogizer_*` is a HAL contract; the kernel answers app-facing detection ecalls through it. "Not present" is a valid implementation; absence is not. |
| `snac.c` | **don't create it** — Pocket peripheral driver, built only for `pocket`/`sim` (see the `ifneq (filter ...)` in `os/Makefile`) |
| `file.c` `save.c` | rewrite over your transport, satisfying `hal/file.h` + `hal/save.h` + the `of_disk_bridge` driver in `hal/disk.h` |
| `disk_boot.c` | stub `of_disk_boot` with `probe()→0` unless you have a host debug channel (PHDP is Pocket-only) |
| `boot/boot.c` | moderate rewrite: wait for your os.bin-ready flag, copy staging → `0x10320000`, keep the CRC trailer verify |
| `boot/start.S`, `boot/append_os_crc.py` | copy verbatim |

Extra sources (e.g. MiSTer's `blockdev.c` + vendored FatFs) are added
under an `ifeq ($(TARGET),<name>)` block in `os/Makefile`.

**Memory contract rules** (`target_platform.h`):

- The hot 64 MB map must stay **byte-identical** across targets — FBs at
  `0x10000000`, OS at `0x10320000`, apps at `0x10400000`, file cache /
  stack / GPU LUTs at the top. This is what makes one app `.elf` run
  everywhere; `os.ld` and the loader assume it.
- The CRAM0 macros are the "staging" role, not a chip: point them at
  whatever RAM your bridge can DMA into (MiSTer carves an arena at
  `0x13300000` inside SDRAM; `CRAM0_BRIDGE` is that region's address in
  your DMA engine's address space, keeping `cpu_to_bridge()` working).
- Keep `SAVE_SLOT_SIZE`/`SAVE_MAX_SLOTS` (apps' save-name parsing
  depends on the 10×256 KB shape) and the `#error` guard chain — extend
  it for your regions.
- Cache discipline: if your staging RAM is cached (Pocket's CRAM was
  not), your file/save HAL owns the `of_cache_*` maintenance around DMA,
  and DMA scratch should only ever be touched through the `0x5xxxxxxx`
  uncached alias.

**Gates** (all must pass before hardware):

```bash
make -C src/firmware/os TARGET=<name>      # builds; check BRAM budget in
                                           # the mif line (8192 words max)
make -C src/firmware/os check-api          # add your target to
                                           # tools/check_api_portability.sh
```

Strongly consider a host-native HAL test harness — the MiSTer port's
`targets/mister/test/pc/` (99 assertions against a real disk image,
ASAN/UBSAN) found integration issues for free and runs in CI with no
hardware or Verilator. The stub-header pattern there (`-include` a fake
`regs.h` with an MMIO table) ports directly.

## 4. Boot flow checklist

1. RTL holds the os.bin "ready" status readable by the BRAM bootloader
   (MiSTer: `HPS_STATUS`/`HPS_BOOT_LEN` in the REGION_HPS block at
   `0x49000000` — reuse that block if it fits your platform).
2. `boot.c` spins on it, copies staging → runtime VMA via the uncached
   alias, CRC-verifies (`append_os_crc.py` trailer), retries the copy on
   mismatch, zeroes BSS/stack, jumps.
3. The boot console works before any filesystem: palette init + terminal
   FB clear means "Waiting for boot.rom"-style text is your first sign
   of life on hardware.

## 5. SDK: `openfpgaSDK/src/sdk/platforms/<name>/`

The SDK is target-generic the same way the OS repo is: every verb
(`build`/`copy`/`package`/`release`) dispatches to your platform
directory, so adding a target = add `src/sdk/platforms/<name>/` plus a
`runtime/<name>/`. The shared SDK Makefile + `scripts/` name no target.
Full contract in `openfpgaSDK` — provide these four scripts/files:

- `platform.conf` — sourced by `scripts/release.sh`:
  `PLATFORM_PRODUCT`, `PLATFORM_TAG_SUFFIX`, `PLATFORM_BUNDLE_KIND`
  (`apf`|`image`), `PLATFORM_COREJSON` (`bundle`|`dist`).
- `image.sh <app> <elf> <sdk_root> <dist_dir|""> [assets...]` — assemble
  ONE app's deliverable into `build/<name>/<app>/`. Custom cores pass a
  `dist_dir`; SDK demo apps pass `""` (a platform with a shared demo core
  like Pocket should no-op then — those apps bundle via `make build
  CORE=sdk`).
- `copy.sh <app> <elf> [host]` — deploy to the device. `MISTER_IP` (or
  your own host var) arrives via the environment, so the shared `copy`
  rule needs no per-target branch; support a `core` mode for core-only
  bring-up. (MiSTer: `mkimage.{c,sh}` builds the FAT32 image with FatFs
  itself — steal it if you also use a disk image; the save files'
  preallocation-contiguity is part of the power-cut-safety story.)
- `package.sh <build_dir> <label> <releases_dir>` — zip one built bundle
  into `releases/<name>/`; exit 0 to skip a dir that isn't your kind.

`make sdk DEST=<sdk>` from the OS repo populates `runtime/<name>/`
automatically (it runs your target's `sdk-runtime.sh`, §2) — **no SDK or
OS root-Makefile edits.** `make help` lists `TARGET=<discovered
platforms>` on its own.

## 6. Final checklist

- [ ] `make full TARGET=<name>` produces a bitstream; `make report
      FULL=true` shows sane utilization and the worst paths you expect
- [ ] `make sweep TARGET=<name>` works (CLOCK_RE matches your 100 MHz
      domain in the Fmax table)
- [ ] All Verilator suites green; your bridge testbench added to
      `test/Makefile`'s `all`
- [ ] `check-api` includes the target; pocket + sim still build
- [ ] Pocket `make check` still passes if you touched shared RTL —
      MiSTer-only hooks in shared files are tied off on other targets
      (see `axi_periph_slave.v` REGION_HPS / arbiter M2 read channel)
- [ ] `make package TARGET=<name>` works (the target Makefile's own
      `package` goal — no root edit) and `about.txt` + `sdk-runtime.sh`
      are present
- [ ] SDK platform dir (`platforms/<name>/`: platform.conf, image.sh,
      copy.sh, package.sh) + `runtime/<name>/` populated by `make sdk`
- [ ] Target README (`targets/<name>/README.md`) + root README updated
- [ ] Bring-up phases planned smallest-first: terminal → input → storage
      → saves → full app suite

## Vendored code policy

Anything imported from upstream (the MiSTer `sys/` framework, FatFs)
stays pristine in its own directory with a `VENDOR.md` recording the
source and commit — re-sync, never edit in place.
