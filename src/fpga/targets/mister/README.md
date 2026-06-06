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
make sdk DEST=path/to/sdk      # sync headers + runtime/mister artifacts
```

This repo **builds** the core; the **SDK pushes to hardware** (same split
as the Pocket). After `make sdk`, deploy from the SDK:

```sh
# openfpgaSDK
src/sdk/platforms/mister/copy.sh core 192.168.x.x      # core-only bring-up
src/sdk/platforms/mister/copy.sh <app> 192.168.x.x     # app image + core
```

Artifacts on the MiSTer:
- `openfpgaOS.rbf` → `/media/fat/_Console/`
- `boot.rom` (= os.bin, auto-loaded at core start) and `openfpgaOS.vhd`
  (built by `mkimage.sh`, mounted once from the OSD)
  → `/media/fat/games/openfpgaOS/`

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
- No USB keyboard plumbing yet (`ps2_key` unconnected).
- OPENFILE/GETFILE data-slot ops report err=7 (no MiSTer backing; the
  FAT name registry replaces them).

`sys/` is the vendored MiSTer framework — see `sys/VENDOR.md`; do not
edit in place.
