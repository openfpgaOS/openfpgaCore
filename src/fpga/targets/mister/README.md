# openfpgaOS — MiSTer target (DE10-Nano / SuperStation One)

The MiSTer port of openfpgaOS. Shared platform-neutral RTL from
`src/fpga/common/` (CPU system, GPU, scanout, mixer, SDRAM path) with
MiSTer-specific glue replacing the Pocket's APF stack:

| Pocket | MiSTer |
|---|---|
| APF bridge + CRAM staging | `hps_bridge.v` — boot.rom ioctl DMA + disk-image sector engine on arbiter M2, serving the unchanged `DS_*` data-slot register ABI |
| Pocket LCD + Analogizer | `emu.sv` raster → framework ascal scaler → HDMI/analog (fixed 60 Hz) |
| I2S `audio_output` | parallel `AUDIO_L/R` (sys_top samples; `AUDIO_S=1`) |
| External async SRAM (GPU LUT) | `sram_bram.v` — 32 KB M10K |
| APF `cont1/2` inputs | hps_io joysticks, remapped to the OF_BTN layout in RTL |

Memory: the SDRAM module backs everything. The hot 64 MB map is
byte-identical to the Pocket; the CRAM staging roles live in an 8 MB
arena at `0x13300000` (see `src/firmware/os/targets/mister/target_platform.h`).

## Build

Requires **Quartus Prime 17.0.x** (the MiSTer framework requirement;
`QUARTUS` in the Makefile points at `~/intelFPGA_lite/17.0/quartus/bin`).
The Pocket target keeps its own 25.1std toolchain — they don't conflict.

```sh
make full TARGET=mister        # from the repo root: cpu → firmware → compile
make firmware TARGET=mister    # fast bootloader iteration (MIF patch, ~10 s)
make deploy TARGET=mister      # refresh build/mister/ (rbf + boot.rom)
make sdk DEST=path/to/sdk      # sync headers + os.bin into runtime/mister (core NOT vendored)
make package TARGET=mister     # releases/mister/openfpgaos-core-v<ver>.zip (_Computer/OpenfpgaOS.rbf
                               #   + games/OpenfpgaOS/boot.rom + INSTALL.txt) + Downloader custom DB
                               #   openfpgaos.json.zip (+ .downloader.ini) via the SDK's mkdb.py
make release TARGET=mister     # draft a GitHub release (dist/mister/release.sh)
```

This repo **builds and releases** the core; the **SDK pushes games to
hardware** (same split as the Pocket). Install the core once from the
release (`make package` / `make release`), then push a game's engine ELF
from the SDK. See `src/sdk/platforms/mister/PACKAGING.md` for the full flow.

```sh
# openfpgaSDK — per-game engine update (primary; wrapped by make copy /
# make copy-app): scp the built ELF atomically to the loose F-loaded engine
# games/OpenfpgaOS/<Game>/<GameElf> (e.g. doom.elf); boot.vhd (wads) and the
# saves volume are untouched — no Main-stop / loop-mount.
src/sdk/platforms/mister/copy.sh game <Game> <GameElf> <elf> [192.168.x.x]

# Legacy single-image model:
src/sdk/platforms/mister/copy.sh core 192.168.x.x      # push boot.rom only
src/sdk/platforms/mister/copy.sh <app> <elf>           # build + push full ~64 MB openfpgaOS.vhd
src/sdk/platforms/mister/copy.sh update <elf>          # loop-mount on-card vhd, swap in-vhd ELF
```

Artifacts on the MiSTer (primary per-game / update-safe model):
- `OpenfpgaOS.rbf` — the game-agnostic core → `/media/fat/_Computer/`
- `boot.rom` (= os.bin, auto-loaded at core start) → `/media/fat/games/OpenfpgaOS/`
- per instance: a loose F-loaded engine ELF at
  `games/OpenfpgaOS/<Game>/<GameElf>` (e.g. `doom.elf`), a read-only
  `boot.vhd` (the wads, slot S0), a writable `<Game>.vhd` (saves, slot S1)
  under `/media/fat/saves/OpenfpgaOS/`, and one 4-line `.mgl` per instance.

## Bring-up phases

1. **Terminal over HDMI** — core + boot.rom only, no disk. The OS boots,
   draws its console; app load fails to the retry screen. Proves boot
   handshake, SDRAM, scanout→ascal.
2. **Input** — OSD navigation + joystick (CONF_STR `J1` order is the
   input ABI; see `hps_bridge.v` `key_remap`).
3. **Disk image I/O** — mount the `.vhd`; os.ini loads, then `testdemo`
   (182 assertions) and `freadalign`.
4. **Saves** — `savea`/`saveb` + power-cycle integrity (saves are
   write-through `f_sync` into preallocated contiguous FAT files).
5. **Full parity** — audio, GPU apps, `celeste`; SuperStation One uses
   the identical artifacts.

If SDRAM reads are marginal on hardware, tune the `clk_ram_chip` phase
in `pll_sys.v` (`phase_shift1`, shipped at the Pocket's 6750 ps).

## Tests

- `make -C src/fpga/test hps-bridge` — 27-assertion Verilator suite for
  the bridge datapath (boot DMA, sector R/W, error codes, handshake).
- `make -C src/firmware/os/targets/mister/test/pc run` — 99-assertion
  host-native FAT-stack suite against a real `.vhd` (ASAN/UBSAN).
- All common-RTL Verilator suites cover the shared datapath unchanged.

## Known v1 limits

- VRR ignored (raster pinned to 60.0 Hz — ascal wants a stable rate).
- 64 MB of the SDRAM module used (128 MB needs io_sdram column widening
  + VexiiRiscv PMA regen + AXI width audit — planned follow-up).
- No 15 kHz native analog raster yet (framework analog out works).
- USB keyboard works: `hps_keyboard.v` turns hps_io's `ps2_key` events into a
  HID boot report on input-hub slot 2 (`cont3_*`), the same layout the Pocket
  dock keyboard uses, so firmware shares one decoder and no new sysreg was
  needed.  No key auto-repeat — a held key reports as held (correct HID boot
  behaviour, and what the Pocket dock does); software repeat would belong in
  the SDL shim, not the RTL.
- OPENFILE/GETFILE data-slot ops report err=7 (no MiSTer backing; the
  FAT name registry replaces them).

`sys/` is the vendored MiSTer framework — see `sys/VENDOR.md`; do not
edit in place.
