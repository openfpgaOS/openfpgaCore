# ofOS FPGA Design

## Overview

The FPGA design targets the Intel Cyclone V 5CEBA4F23C8 on the Analogue Pocket. It implements a complete SoC: RISC-V CPU, memory controllers, video scanout, audio output, and peripheral I/O.

## Module Hierarchy

```
core_top.v                                    Top-level (APF integration)
+-- cpu_system.v                              CPU + AXI4 bus fabric
|   +-- VexiiRiscv_Full.v                     RISC-V CPU core (generated)
|   +-- axi_periph_slave.v                    BRAM + system registers + palette + controllers
|   +-- axi_sdram_slave.v                     SDRAM AXI4 slave (burst support)
|   +-- axi_sdram_arbiter.v                   CPU/video SDRAM arbitration
|   +-- axi_cram0_slave.v                     CRAM0 AXI4 slave
|   +-- axi_cram1_slave.v                     CRAM1 AXI4 slave (saves/bridge)
|
+-- video_CRT_scanout_indexed_BRAM.v          Multi-mode video scanout (6 color modes)
+-- text_terminal.v                           40x30 character overlay
+-- audio_output.v                            I2S output stage (dcfifo + serializer)
+-- audio_mixer.v                             48-voice PCM mixer (16-bit, SVF filter)
+-- link_mmio.v                               Link cable serial transceiver
+-- io_sdram.v                                SDRAM controller
+-- cram0_controller.v / cram0_phy.sv         CRAM0 PSRAM controller
+-- cram1_controller.v / cram1_phy.sv         CRAM1 PSRAM controller
+-- bram.v                                    32 KB block RAM
+-- openFPGA_Pocket_Analogizer.v              Analogizer adapter (RndMnkIII)
    +-- scandoubler.v                         Video scandoubler
    +-- rgb2YPbPr.v                           Color space conversion
    +-- serlatch_gc.v                         NES/SNES/DB15 serial controllers
    +-- pcengine_gc.v                         PCEngine controllers
    +-- dualshock_controller.v                PlayStation controllers
    +-- psPAD_top.v                           PSX controller interface
```

## System Registers (0x40000000)

Implemented in `axi_periph_slave.v`.

| Offset | Name | R/W | Description |
|--------|------|-----|-------------|
| 0x00 | `SYS_STATUS` | R | [0] SDRAM ready, [1] allcomplete (bridge ready) |
| 0x04 | `CYCLE_LO` | R | Cycle counter low 32 bits |
| 0x08 | `CYCLE_HI` | R | Cycle counter high 32 bits |
| 0x0C | `DISPLAY_MODE` | RW | 0 = terminal overlay, 1 = framebuffer |
| 0x10 | `FB_DISPLAY` | RW | Display framebuffer base address |
| 0x14 | `FB_DRAW` | RW | Draw framebuffer base address |
| 0x18 | `FB_SWAP` | RW | Write 1 = request swap at vsync; read = pending |
| 0x20 | `DS_SLOT_ID` | W | Data slot ID for DMA |
| 0x24 | `DS_SLOT_OFFSET` | W | Byte offset within data slot |
| 0x28 | `DS_BRIDGE_ADDR` | W | Bridge-relative SDRAM address |
| 0x2C | `DS_LENGTH` | W | DMA transfer length |
| 0x30 | `DS_PARAM_ADDR` | W | Parameter buffer address (for open_file) |
| 0x34 | `DS_RESP_ADDR` | W | Response buffer address |
| 0x38 | `DS_COMMAND` | W | 1=read, 2=write, 3=open_file |
| 0x3C | `DS_STATUS` | R | [0] ACK, [1] done, [4:2] error code, [5] ready (~target_ack) |
| 0x40 | `PAL_INDEX` | W | Palette write index (auto-increment) |
| 0x44 | `PAL_DATA` | W | Palette entry (0x00RRGGBB, triggers write) |
| 0x50 | `CONT1_KEY` | R | Controller 1 buttons |
| 0x54 | `CONT1_JOY` | R | Controller 1 joystick |
| 0x58 | `CONT1_TRIG` | R | Controller 1 triggers |
| 0x5C | `CONT2_KEY` | R | Controller 2 buttons |
| 0x60 | `CONT2_JOY` | R | Controller 2 joystick |
| 0x64 | `CONT2_TRIG` | R | Controller 2 triggers |
| 0x68 | `GAME_ID` | R | Interact menu settings (Analogizer config) |
| 0x70 | `COLOR_MODE` | RW | Video color mode (0-5, see Video Scanout) |

## Audio (0x4C000000)

Implemented in `audio_output.v`.

| Offset | Name | R/W | Description |
|--------|------|-----|-------------|
| 0x00 | `AUDIO_SAMPLE` | W | Stereo sample (left:right, 16-bit each) |
| 0x00 | `AUDIO_STATUS` | R | [11:0] FIFO level, [12] full |

The audio output stage is just a dual-clock FIFO bridging `clk_cpu`
(mixer output) to `clk_audio` (12.288 MHz), followed by an I2S
serializer.  All voice mixing, pitch/pan/volume/SVF filtering, and
sample fetch happen inside `audio_mixer.v` on `clk_cpu` — the output
stage sees a pre-mixed stereo stream at 48 kHz.

## Link Cable (0x4D000000)

Implemented in `link_mmio.v`. GB/GBC-compatible serial at 256 kHz.

## Video Scanout

`video_CRT_scanout_indexed_BRAM.v` reads the framebuffer from SDRAM via burst reads into a line buffer, then decodes pixels based on the active color mode. Features:

- Six color modes, switchable at runtime via the `COLOR_MODE` register:
  - **Mode 0:** 8-bit indexed (256 colors, 1 byte/pixel, 76,800 B framebuffer)
  - **Mode 1:** 4-bit indexed (16 colors, 2 pixels/byte, 38,400 B)
  - **Mode 2:** 2-bit indexed (4 colors, 4 pixels/byte, 19,200 B)
  - **Mode 3:** RGB565 direct color (16-bit, 2 bytes/pixel, 153,600 B)
  - **Mode 4:** RGB555 direct color (15-bit, 2 bytes/pixel, 153,600 B)
  - **Mode 5:** RGBA5551 (15-bit + 1-bit alpha, 2 bytes/pixel, 153,600 B)
- 256-entry palette RAM for indexed modes
- Double-buffered with vsync swap
- SDRAM burst reads with per-mode burst lengths
- 320x240 resolution at 2x horizontal (640 display pixels)
- 3-stage pipeline: BRAM read, pixel decode, palette lookup / direct output

## Text Terminal

`text_terminal.v` renders a 40x30 character grid from VRAM at 0x20000000. When `DISPLAY_MODE = 0`, the terminal is overlaid on the video output. A built-in 8x8 font ROM provides character rendering.

## Analogizer

The Analogizer subsystem (`src/fpga/analogizer/`) is contributed by RndMnkIII. It provides:

- **Video output:** RGBS, RGsB, YPbPr, S-Video (NTSC/PAL), composite with scandoubler modes
- **SNAC controllers:** NES, SNES, PCEngine (2/6-button, multitap), PlayStation (digital, DualShock), DB15
- **Scandoubler:** Multiple modes including HQ2X-style filtering

Configuration is set through the Pocket's interact menu (`interact.json`) and read by the CPU via the `GAME_ID` register.

## Clock Domains

| Clock | Frequency | Source | Usage |
|-------|-----------|--------|-------|
| `clk_100` | 100 MHz | PLL | CPU, AXI bus, peripherals |
| `clk_133` | 133 MHz | PLL | SDRAM controller |
| `clk_74` | 74.25 MHz | APF | Video output timing |
| `clk_12` | 12.288 MHz | PLL | Audio I2S |

## FPGA Resource Usage

The design fits within the Cyclone V 5CEBA4F23C8:
- ~18K ALMs available
- Uses M10K block RAM for BRAM, caches, FIFO, palette, font ROM
- DSP blocks for multiply operations in the CPU
