# openfpgaOS Architecture

A bare-metal RISC-V game runtime inside a Cyclone V FPGA. One OS, one
app ABI, one set of platform-neutral RTL — multiple hardware targets
(Analogue Pocket, MiSTer) that differ only in I/O glue.

```
┌────────────────────────────── FPGA fabric ──────────────────────────────┐
│                                                                         │
│  VexiiRiscv (RV32IMAFC, 90-100 MHz, 32K I$ / 128K D$)                   │
│   │ i_axi      │ d_axi      │ p_axi          (3 AXI4 masters)           │
│   └────────────┴─┬──────────┴───────┐                                   │
│        cpu_system │ (cpu_target_port address decode + register slices)  │
│      ┌────────────┼───────────────┐ │                                   │
│      ▼            ▼               ▼ ▼                                   │
│   SDRAM        CRAM0*          axi_periph_slave (0x4xxxxxxx MMIO,       │
│   target       target           32 KB boot BRAM, DS regs, mixer/GPU     │
│      │            │              regs, palette, timers, IRQs)           │
│      ▼            ▼                       ▲          ▲                  │
│  axi_sdram_arbiter (M1)            target_dataslot_* │ cont1/2, rtc     │
│   M0 ▲  M2 ▲  M3 ▲                        │          │                  │
│      │     │     │                        ▼          │                  │
│  gpu_core  │  audio_mixer          bridge (per-target: APF cmd /        │
│      │     └──────────────────────  hps_bridge)                         │
│      ▼                                                                  │
│  axi_sdram_slave ──► io_sdram ──► 16-bit SDR SDRAM (64 MB map)          │
│                        ▲ burst port                                     │
│  video_CRT_scanout ────┘   24.576 MHz raster ──► per-target video out   │
│  audio_mixer 48 kHz stream ──► per-target audio out                     │
└─────────────────────────────────────────────────────────────────────────┘
   * Pocket only (PSRAM staging); MiSTer repoints the CRAM role into SDRAM
```

## 1. CPU and bus topology

- **VexiiRiscv** (generated netlist, `src/fpga/vendor/vexriscv/`):
  RV32IMAFC in-order, FPU, Zicbom (+Zicboz), 32 KB I$ / 128 KB 2-way D$
  (os20: 64 KB D$).  Single-issue on os25/os30; dual-issue on mister and
  os20. Three AXI masters: instruction fetch, data (cached +
  cbo), and uncached LSU I/O.
- **`cpu_system.v`** routes the three masters to three slave targets by
  PMA address decode — SDRAM (cached `0x10000000` + uncached alias
  `0x50000000`), CRAM0 (`0x30000000`, Pocket only), LOCAL
  (`0x40000000`+ MMIO + the 32 KB boot BRAM at `0x0`). Register slices
  decouple CPU placement from slave routing.
- **`axi_sdram_arbiter.v`** — fixed priority with CPU/audio fairness
  guards. Masters: M0 GPU (posted write queue + burst coalescing),
  M1 CPU, M2 bridge file DMA (write-only on Pocket; read+write on
  MiSTer for sector writeback), M3 audio mixer (latency-critical,
  promoted under the CPU guard).
- **`axi_sdram_slave.v` + `io_sdram.v`** — single-FSM word/burst
  controller for 16-bit SDR SDRAM (CAS 3, 100 MHz, 25-bit halfword
  addressing = 64 MB). `arlen` is capped at 15 (16-beat bursts). The
  scanout has its own dedicated burst read port with priority over the
  word interface.

## 2. Memory map (identical on all targets — the app portability anchor)

```
0x00000000  32 KB BRAM      bootloader + trap handler + hot OS code
                            (0x4000-0x7800 = app OF_FASTTEXT window)
0x10000000  SDRAM cached    FB0/FB1/FB2 (3 × 1 MB, triple buffer)
0x10300000                  terminal FB (uncached alias 0x50300000)
0x10320000                  OS .text/.rodata/.data (768 KB region)
0x10400000                  app load address + heap
   ...                      (heap ceiling = lowest reservation below)
0x13300000                  [MiSTer only] 8 MB staging arena
                            (os.bin staging, save mirrors, DMA scratch,
                             app async pool — the "CRAM role")
0x13B00000                  OS file read cache (4 MB)
0x13F00000                  OS runtime stack (512 KB)
0x13FC0000                  GPU palookup tables (fixed RTL address)
0x30000000  CRAM0 16 MB     [Pocket only] bridge staging PSRAM, uncached
0x40000000+ MMIO            see §3
0x50000000  SDRAM uncached  D-cache-bypass alias of 0x10000000
```

Apps never hardcode any of this: addresses arrive at runtime via the
capability table (§6).

## 3. MMIO regions (`axi_periph_slave.v` — the firmware ABI)

| Decode | Region | Contents |
|---|---|---|
| `0x00000000` | BRAM | 32 KB boot/trap/fasttext RAM (MIF-initialized) |
| `0x40000000` | SYSREG | status, cycle counter, FB swap/geometry, palette, **DS_\* data-slot regs**, controllers, input hub, timers, IRQ mask, HW_FEATURES, VRR, datatable query |
| `0x48000000` | MIXER | 32-voice PCM mixer (flat per-voice addressing) |
| `0x49000000` | HPS | MiSTer status block (boot.rom loaded, img mounted/size) — reads zero on Pocket |
| `0x4A000000` | GPU | command ring / doorbell / fence registers |
| `0x4C000000` | AUDIO | FIFO status (diagnostics) |
| `0x4D000000` | LINK | link-cable lite (Pocket) |
| `0x4E000000` | CRAM0 | CRAM ownership mux (Pocket) |
| `0x4F000000` | UART | 2 Mbaud DevKey service serial (Pocket) |

The **data-slot interface** (`DS_SLOT_ID/OFFSET/BRIDGE_ADDR/LENGTH/
COMMAND/STATUS`) is the universal storage handshake: firmware programs a
transfer, the periph raises level strobes (`target_dataslot_read/write`),
the per-target bridge acks/completes, and `DS_STATUS` reports
ACK/DONE/ERR/READY/WR_IDLE. Pocket forwards this to the APF host over
clk_74a; MiSTer's `hps_bridge` services it directly as a 512-byte sector
engine against the OSD-mounted disk image.

## 4. Video, audio, GPU

- **Scanout** (`video_CRT_scanout_indexed_BRAM.v`): fetches scanlines
  from SDRAM via burst DMA into a line cache, palettizes (256-entry,
  double-buffered palette with frame-start commit), and feeds a fixed
  780×525 raster at 24.576 MHz (~60.0 Hz). Modes: 8/4/2-bit indexed,
  RGB565/555/5551, up to 800×600 source, scaler slots 320×240…640×480.
  Pocket adds VRR (V_TOTAL 514-750) and a dedicated 15 kHz analog
  raster for the Analogizer; MiSTer pins 60 Hz and hands the raster to
  the framework `ascal` scaler (HDMI + analog).
- **Audio**: `audio_mixer.v` — 32 voices, SDRAM sample fetch via M3,
  Q16.16 rates with linear interpolation, per-voice HW volume ramps,
  voice-end IRQ bitmap, group/master volumes; emits one stereo pair per
  2083 CPU cycles (48 kHz). Pocket serializes to I2S
  (`audio_output.v`); MiSTer latches the pair onto parallel `AUDIO_L/R`.
- **GPU** (`gpu_core.v` + `gpu_tex_cache.v`): indexed-color span
  rasterizer for BUILD/Doom-style renderers — spans (1/2/4-lane
  groups), colormap lookup, masked/translucent pixels, clears, flips
  with vsync backpressure, perspective textured triangles. Commands are
  staged by the CPU in cached SDRAM and pulled by doorbell DMA into a
  16 KB internal ring. The translucency LUT lives in GPU-private memory
  (external async SRAM on Pocket, 32 KB BRAM on MiSTer). Architectural
  rule: **the GPU owns the framebuffer** — full GPU coverage means no
  CPU/GPU cache-coherency hazards.

## 5. Firmware

```
boot (BRAM)         kernel (SDRAM)              HAL            target layer
─────────────       ──────────────              ───            ─────────────
start.S             main.c    (init, banner,    hal.c          targets/<t>/
boot.c              boot app) caps_table.c      cache.c          target_platform.h
 wait for os.bin    syscall.c (Linux-compat +   disk.c           video/audio/timer/
 copy to 0x10320000  SBI vendor ecalls, file    mixer.c          terminal/input.c
 CRC verify+retry    registry, FD table, brk)   codec/lzw        file.c  save.c
 zero BSS/stack     loader.c  (ELF exec, auxv)  platform.c       analogizer.c
 jump os_main       irq.c, misaligned.c,                         disk_boot.c
                    config.c, bank_preload.c                     boot/boot.c
```

- **Boot**: the BRAM bootloader is target-specific only in *how it
  learns os.bin is ready* (APF ALLCOMPLETE + CRAM staging vs. MiSTer
  ioctl-done flag) — the copy/verify/jump skeleton is shared, including
  the `append_os_crc.py` CRC trailer with reload-on-mismatch.
- **Kernel**: single-process, no MMU/scheduler. `syscall.c` implements
  both Linux RISC-V syscalls (for musl) and openfpgaOS SBI vendor
  ecalls; it owns the **filename→slot registry** (`dir_probe_slots`
  walks slot ids 1-31 via the file HAL), the readahead cache (4 MB,
  32 KB blocks), and the nonvolatile-save FD path.
- **HAL contracts** (`hal/*.h`): each target implements `file.h`
  (slot reads, name/size queries, async reads, write-back),
  `save.h` (`of_nvslot_*`), the `of_disk_driver_t` read backends,
  plus video/audio/input/timer/terminal. `of_analogizer_*` is part of
  the contract everywhere (apps probe it; "not present" is a valid
  answer) — the SNAC driver, by contrast, is a Pocket-only peripheral.
- **Per-target storage** is the biggest divergence:
  - *Pocket*: APF data slots over bridge DMA; saves live in a CRAM0
    window persisted by the launcher at exit; datatable queries via CDC.
  - *MiSTer*: FatFs (vendored, R0.15a) over the sector engine; slots map
    to fixed paths in the disk image; saves are preallocated contiguous
    256 KB files written through (`f_write`+`f_sync`) so a power cut
    can never corrupt FAT metadata; the APF datatable view is
    synthesized in software.

## 6. App ABI and portability

Apps are static ELF binaries: upstream **musl 1.2.5** (rv32imafc,
Linux syscall ABI) + the SDK's `of_*` API. At `elf_exec` the loader
pushes auxv entries including `AT_PAGESZ` and two vendor tags:

- `AT_OF_CAPS` → `of_capabilities` (platform id, hw_features bitmask,
  fb/heap/gpu/sample addresses, cpu_freq_hz, mixer shape)
- `AT_OF_SVC` → the services function-pointer table (fast-path calls
  that skip the ecall trap)

This is why **one app binary runs on Pocket, MiSTer, and sim**: nothing
target-specific is compiled in. Apps gate optional hardware on
`hw_features` (e.g. `OF_HW_ANALOGIZER` — set on Pocket, clear on
MiSTer). The portability gate is `make -C src/firmware/os check-api`,
which greps the public API for leaks and builds all three targets.

Hot-path support: apps may place code/data in the BRAM window
(`OF_FASTTEXT`, `0x4000-0x7800`) — the loader copies it at startup.

## 7. Target I/O glue (what a port actually writes)

| | pocket | mister |
|---|---|---|
| Top level | `apf_top.v` → `core_top.v` | `sys_top` (framework) → `emu.sv` |
| Bridge | `core_bridge_cmd.v` + `bridge_to_sdram.v` + CRAM ctrl/CDC/PHY | `hps_bridge.v` (boot ioctl DMA + sd sector engine) |
| Clocks | 2 PLLs from clk_74a | 2 PLLs from 50 MHz (100 MHz pair integer; 24.576 fractional — no shared VCO exists) |
| Video out | APF LCD scaler (+ Analogizer DAC) | ascal → HDMI (+ framework analog) |
| Audio out | I2S @ 12.288 MHz | parallel AUDIO_L/R |
| Input | APF cont regs / hub / SNAC | hps_io joysticks → APF layout in RTL |
| GPU LUT | `sram_controller.v` (ext. chip) | `sram_bram.v` (M10K) |
| Toolchain | Quartus 25.1std | Quartus 17.0.x (framework req.) |
| Device | 5CEBA4F23C8 (18.5K ALM, 87-89 % full) | 5CSEBA6U23I7 (41.9K ALM, 67 % full) |

Shared files carry tied-off hooks rather than forks: the arbiter's M2
read channel and the periph's `REGION_HPS` exist on Pocket but read
zero / fold away. See `docs/ADDING_A_TARGET.md` for the porting recipe.

## 8. Test architecture

- **Verilator component suites** (`src/fpga/test/`): sdram, sram, gpu
  (+acceptance: ~1655 byte-exact checks across 7 suites), gpu-persp,
  axi-periph, bridge-cmd, audio, audio-mixer, arbiter, **hps-bridge**
  (MiSTer bridge datapath: boot DMA, sector R/W, error paths,
  handshake), scanout, and `tb_system` (full CPU + SDRAM + BRAM boot of
  the real firmware, sim target).
- **Host-native firmware tests**: `targets/mister/test/pc/` compiles the
  real `file.c`/`save.c`/FatFs against a pread/pwrite block device and a
  real disk image (ASAN/UBSAN) — storage-HAL coverage with no hardware.
- **Quartus gates**: `make check` (A&S) per target; `make report
  [FULL=true]` for utilization/critical paths; `make sweep` for seed
  exploration ranked by the 100 MHz domain's Fmax with WNS/TNS.

## 9. Related documents

- [ADDING_A_TARGET.md](ADDING_A_TARGET.md) — porting recipe
- [`src/fpga/targets/mister/README.md`](../src/fpga/targets/mister/README.md)
  — MiSTer port details and bring-up plan
- SDK repository — app development, SDL2 shim, packaging/deployment
  (`platforms/{pocket,mister}/`)
