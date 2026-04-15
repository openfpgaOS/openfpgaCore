# openfpgaOS Developer Guide

A complete reference for building applications on the openfpgaOS platform for Analogue Pocket.

## Table of Contents

1. [Platform Overview](#platform-overview)
2. [Getting Started](#getting-started)
3. [Your First App in 15 Minutes](#your-first-app-in-15-minutes)
4. [API Reference](#api-reference)
   - [Video](#video)
   - [Input](#input)
   - [Audio -- PCM](#audio----pcm)
   - [MIDI Playback](#midi-playback)
   - [Timer](#timer)
   - [Save Files](#save-files)
   - [File I/O (Data Slots)](#file-io-data-slots)
   - [Link Cable](#link-cable)
   - [Terminal (Debug)](#terminal-debug)
   - [Analogizer](#analogizer)
   - [System](#system)
5. [Memory Map](#memory-map)
6. [Register Map](#register-map)
7. [Data Slots and Instances](#data-slots-and-instances)
8. [Build System](#build-system)
9. [Architecture](#architecture)
10. [Timing and Performance](#timing-and-performance)
11. [Cookbook](#cookbook)

---

## Platform Overview

| Feature | Specification |
|---------|--------------|
| **CPU** | VexRiscv RV32IMAFC @ 100 MHz |
| **Display** | 320x240, 8-bit indexed color, 256-entry palette |
| **Framebuffer** | Double-buffered, vsync-swapped |
| **Audio** | 48 kHz stereo FIFO + 48-voice hardware PCM mixer (16-bit, SVF filter) |
| **MIDI** | Sample-based synthesis via `.ofsf` banks (SC-55 bank included) |
| **Input** | 2 controllers: d-pad, ABXY, L1/R1, L2/R2, L3/R3, Select, Start, dual sticks, analog triggers |
| **SDRAM** | 64 MB (cached) |
| **CRAM0** | 16 MB cellular RAM (cached or uncached) |
| **CRAM1** | 16 MB cellular RAM (cached or uncached) |
| **SRAM** | 256 KB static RAM (uncached) |
| **BRAM** | 32 KB on-chip (OS kernel) |
| **Save** | Nonvolatile, CRAM1 PSRAM-backed, 10 × 256 KB save slots, persisted to SD via Chip32 |
| **Link** | Bidirectional 32-bit link cable port |
| **Dock** | Supported (HDMI output, USB controllers) |
| **Analogizer** | SNAC controllers + analog video output |

### Why This Platform

- **Sub-second iteration**: Write C, compile, copy ELF to SD, run. No 45-minute FPGA synthesis.
- **Real hardware**: Not an emulator. FPGA-implemented video scanout, PCM mixer, controller interface.
- **One header API**: `#include "of.h"` and start writing a game. No init ceremony, no framework.
- **Portable**: Same binary runs on Pocket, Dock, and with Analogizer.

---

## Getting Started

### Prerequisites

- **RISC-V GCC toolchain**: `riscv64-elf-gcc` or `riscv64-unknown-elf-gcc`
  - Arch Linux: `pacman -S riscv64-elf-gcc riscv64-elf-newlib`
  - macOS: `brew install riscv64-elf-gcc`
  - Ubuntu: `apt install gcc-riscv64-unknown-elf`
- **Analogue Pocket** with openfpgaOS core installed
- **microSD card** for deploying builds

### Project Structure

```
your-game/
  main.c          Your game code
  app.ld          Linker script (copy from an example app)
  Makefile         Build rules (copy from an example app)
  assets/          Game data files (images, music, etc.)
```

### The Minimal Makefile

```makefile
CROSS ?= $(shell which riscv64-unknown-elf-gcc >/dev/null 2>&1 \
           && echo riscv64-unknown-elf- || echo riscv64-elf-)
CC = $(CROSS)gcc
LD = $(CROSS)gcc
SIZE = $(CROSS)size

TARGET = app
ARCH = rv32imafc
ABI = ilp32f

CRT_DIR = ../crt

CFLAGS = -march=$(ARCH) -mabi=$(ABI) -O2 -Wall -Wextra
CFLAGS += -ffreestanding -nostdlib -nostartfiles
CFLAGS += -ffunction-sections -fdata-sections -fno-builtin
CFLAGS += -I$(CRT_DIR)

LDFLAGS = -march=$(ARCH) -mabi=$(ABI) -nostdlib -nostartfiles
LDFLAGS += -static -T app.ld -Wl,--gc-sections

LIBGCC = $(shell $(CC) -march=$(ARCH) -mabi=$(ABI) -print-libgcc-file-name)
AS = $(CROSS)gcc
ASFLAGS = -march=$(ARCH)_zicsr -mabi=$(ABI)

CRT_START = $(CRT_DIR)/start.o
SRCS = main.c
OBJS = $(CRT_START) $(SRCS:.c=.o)

all: $(TARGET).elf
	$(SIZE) $<

$(TARGET).elf: $(OBJS) app.ld
	$(LD) $(LDFLAGS) -o $@ $(OBJS) $(LIBGCC)

$(CRT_DIR)/%.o: $(CRT_DIR)/%.S
	$(AS) $(ASFLAGS) -c -o $@ $<

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET).elf

.PHONY: all clean
```

### The Linker Script (`app.ld`)

```ld
ENTRY(_start)

SECTIONS {
    . = 0x10400000;

    .text : {
        *(.text.start)
        *(.text*)
        *(.rodata*)
        *(.srodata*)
        . = ALIGN(4);
    }

    .data : {
        *(.data*)
        *(.sdata*)
        . = ALIGN(4);
    }

    .bss : {
        __bss_start = .;
        *(.bss*)
        *(.sbss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    }
}
```

The load address `0x10400000` places the app at SDRAM + 4 MB, after the two framebuffers and DMA buffer.

---

## Your First App in 15 Minutes

### Step 1: Hello World (Terminal)

```c
#include "of.h"
#include <stdio.h>

int main(void) {
    printf("\033[2J\033[H");  /* clear screen, home cursor */
    printf("Hello from openfpgaOS!\n");
    printf("Press START to continue...\n");

    while (1) {
        of_input_poll();
        if (of_btn_pressed(OF_BTN_START))
            break;
    }

    return 0;
}
```

This runs in terminal mode (40x30 text console). No video init needed.

### Step 2: Drawing Pixels

```c
#include "of.h"

int main(void) {
    of_video_init();

    // Set up some colors
    of_video_palette(0, 0x000000);  // black
    of_video_palette(1, 0xFF0000);  // red
    of_video_palette(2, 0x00FF00);  // green
    of_video_palette(3, 0x0000FF);  // blue
    of_video_palette(4, 0xFFFFFF);  // white

    int x = 160, y = 120;

    while (1) {
        of_input_poll();

        // Move with d-pad
        if (of_btn(OF_BTN_UP))    y--;
        if (of_btn(OF_BTN_DOWN))  y++;
        if (of_btn(OF_BTN_LEFT))  x--;
        if (of_btn(OF_BTN_RIGHT)) x++;

        // Clamp to screen
        if (x < 0) x = 0;
        if (x > 319) x = 319;
        if (y < 0) y = 0;
        if (y > 239) y = 239;

        of_video_clear(0);

        // Draw a crosshair
        uint8_t *fb = of_video_surface();
        for (int i = 0; i < OF_SCREEN_W; i++)
            fb[y * OF_SCREEN_W + i] = 1;
        for (int i = 0; i < OF_SCREEN_H; i++)
            fb[i * OF_SCREEN_W + x] = 2;

        // Center pixel
        fb[y * OF_SCREEN_W + x] = 4;

        of_video_flip();
    }
}
```

### Step 3: Adding Sound

```c
#include "of.h"

static int16_t beep_buf[1024];  // 21 ms of mono 48 kHz

static void fill_beep(void) {
    // 440 Hz square wave
    for (int i = 0; i < 1024; i++)
        beep_buf[i] = ((i / 54) & 1) ? 12000 : -12000;
}

static void play_beep(void) {
    of_mixer_play(beep_buf, 1024, 48000, /*loop=*/0, /*volume=*/200);
}

int main(void) {
    of_video_init();
    of_mixer_init();
    fill_beep();

    of_video_palette(0, 0x000000);
    of_video_palette(1, 0x00FF00);

    while (1) {
        of_input_poll();

        if (of_btn_pressed(OF_BTN_A))
            play_beep();

        of_video_clear(0);
        if (of_btn(OF_BTN_A)) {
            uint8_t *fb = of_video_surface();
            for (int y = 100; y < 140; y++)
                for (int x = 140; x < 180; x++)
                    fb[y * OF_SCREEN_W + x] = 1;
        }
        of_video_flip();
    }
}
```

### Step 4: Build and Deploy

```bash
# Build
make

# Copy to SD card (adjust path to your SD mount)
cp app.elf /media/pocket/Assets/openfpgaos/common/your_app.elf

# Create an instance JSON (see Data Slots section)
```

---

## API Reference

Every function listed below is a `static inline` in `of.h`. No library to link. Include the header and go.

### Video

The display is a 320x240 framebuffer with 8-bit indexed color. Each pixel is a byte (palette index 0--255). Two framebuffers are maintained: one is displayed while you draw to the other. Call `of_video_flip()` to swap them.

#### `void of_video_init(void)`
Switch from terminal mode to framebuffer mode. Clears both buffers to color 0. Call this once at startup.

#### `uint8_t *of_video_surface(void)`
Returns a pointer to the current draw buffer (76,800 bytes = 320 x 240). Write palette indices directly:
```c
uint8_t *fb = of_video_surface();
fb[y * OF_SCREEN_W + x] = color_index;
```

#### `void of_video_flip(void)`
Queue a buffer swap. The swap happens at the next vertical blank. Returns immediately -- the CPU can start drawing the next frame while the display shows the previous one.

#### `void of_video_wait_flip(void)`
Block until the last `of_video_flip()` completes. Use this to pace your game loop to vsync:
```c
of_video_flip();
of_video_wait_flip();  // Waits for vsync, then returns
```

#### `void of_video_clear(uint8_t color)`
Fill the entire draw buffer with the given palette index. Fast (OS-level memset).

#### `void of_video_palette(uint8_t index, uint32_t rgb)`
Set a single palette entry. Color format is `0x00RRGGBB`:
```c
of_video_palette(0, 0x000000);   // Index 0 = black
of_video_palette(1, 0xFF0000);   // Index 1 = red
of_video_palette(15, 0x1A1C2C);  // Index 15 = dark blue
```

#### `void of_video_palette_bulk(const uint32_t *pal, int count)`
Load multiple palette entries starting from index 0. `pal` is an array of `0x00RRGGBB` values:
```c
uint32_t my_palette[256] = { 0x000000, 0xFF0000, 0x00FF00, ... };
of_video_palette_bulk(my_palette, 256);
```

#### `void of_video_pixel(int x, int y, uint8_t color)`
Draw a single pixel with bounds checking. Convenience wrapper -- for bulk drawing, use `of_video_surface()` directly for better performance.

#### `void of_video_flush(void)`
Flush the CPU D-cache for the draw buffer. Rarely needed -- `of_video_flip()` handles this internally. Only use if you need the FPGA scanout engine to see writes before a flip (e.g., tearing-free status bar).

#### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `OF_SCREEN_W` | 320 | Framebuffer width in pixels |
| `OF_SCREEN_H` | 240 | Framebuffer height in pixels |

---

### Input

Two controllers are supported (Pocket buttons + docked USB/Bluetooth controllers). Call `of_input_poll()` once per frame, then check buttons.

#### `void of_input_poll(void)`
Read the current state of all controllers. This updates the internal state used by `of_btn()` and friends. **Call exactly once per frame**, before any button checks.

#### `int of_btn(uint32_t mask)`
Returns non-zero if the button is currently **held down** (player 0):
```c
if (of_btn(OF_BTN_A)) fire_weapon();
if (of_btn(OF_BTN_LEFT | BTN_RIGHT)) {}  // Either direction
```

#### `int of_btn_pressed(uint32_t mask)`
Returns non-zero if the button was **just pressed this frame** (not held from last frame). Use for one-shot actions:
```c
if (of_btn_pressed(OF_BTN_START)) toggle_pause();
if (of_btn_pressed(OF_BTN_A))     jump();  // Won't re-trigger if held
```

#### `int of_btn_released(uint32_t mask)`
Returns non-zero if the button was **just released this frame**.

#### `int of_btn_p2(uint32_t mask)`
Same as `of_btn()` but for player 1's controller.

#### `int of_btn_pressed_p2(uint32_t mask)` / `int of_btn_released_p2(uint32_t mask)`
Pressed/released checks for player 1.

#### `uint32_t of_input_state(int player, of_input_state_t *state)`
Get the full input state including analog sticks and triggers:
```c
of_input_state_t state;
of_input_state(0, &state);

int16_t stick_x = state.joy_lx;  // Left stick X: -32768 to +32767
int16_t stick_y = state.joy_ly;  // Left stick Y: -32768 to +32767
uint16_t lt = state.trigger_l;   // Left trigger: 0 to 65535
```

Returns the button bitmask (same as `state.buttons`).

#### `of_input_state_t` struct

```c
typedef struct {
    uint32_t buttons;           // Current button state (bitmask)
    uint32_t buttons_pressed;   // Buttons pressed this frame
    uint32_t buttons_released;  // Buttons released this frame
    int16_t  joy_lx, joy_ly;    // Left analog stick
    int16_t  joy_rx, joy_ry;    // Right analog stick
    uint16_t trigger_l;         // Left trigger
    uint16_t trigger_r;         // Right trigger
} of_input_state_t;
```

#### Button Masks

| Mask | Bit | Pocket Button |
|------|-----|--------------|
| `OF_BTN_UP` | 0 | D-pad Up |
| `OF_BTN_DOWN` | 1 | D-pad Down |
| `OF_BTN_LEFT` | 2 | D-pad Left |
| `OF_BTN_RIGHT` | 3 | D-pad Right |
| `OF_BTN_A` | 4 | A (right face) |
| `OF_BTN_B` | 5 | B (bottom face) |
| `OF_BTN_X` | 6 | X (top face) |
| `OF_BTN_Y` | 7 | Y (left face) |
| `OF_BTN_L1` | 8 | Left shoulder |
| `OF_BTN_R1` | 9 | Right shoulder |
| `OF_BTN_L2` | 10 | Left trigger (digital) |
| `OF_BTN_R2` | 11 | Right trigger (digital) |
| `OF_BTN_L3` | 12 | Left stick click |
| `OF_BTN_R3` | 13 | Right stick click |
| `OF_BTN_SELECT` | 14 | Select / - |
| `OF_BTN_START` | 15 | Start / + |

---

### Audio -- PCM

48 kHz stereo audio. Write interleaved `int16_t` sample pairs (left, right) to a 4096-entry FIFO.

#### `void of_audio_init(void)`
Initialize the audio subsystem. Call once at startup if you intend to use PCM audio.

#### `int of_audio_write(const int16_t *samples, int count)`
Write stereo sample pairs to the FIFO. `samples` is interleaved L/R pairs, `count` is the number of **pairs** (not individual samples). Returns the number of pairs actually written (may be less than `count` if FIFO is full).

```c
int16_t buf[2] = { left_sample, right_sample };
of_audio_write(buf, 1);  // Write 1 stereo pair
```

#### `int of_audio_free(void)`
Returns the number of free entries in the audio FIFO. Use to avoid blocking:
```c
while (of_audio_free() > 0) {
    int16_t samples[2];
    generate_sample(&samples[0], &samples[1]);
    of_audio_write(samples, 1);
}
```

#### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `OF_AUDIO_RATE` | 48000 | Sample rate in Hz |
| `OF_AUDIO_FIFO` | 4096 | FIFO depth (stereo pairs) |

---

### MIDI Playback

The `of_midi` library renders Standard MIDI Files (Format 0/1)
through the 48-voice PCM mixer using a pre-resolved `.ofsf` sample
bank.  The repo ships an SC-55-derived General MIDI bank at
`assets/banks/sc55.ofsf` — drop it on the SD card and load it via
`of_smp_bank_load()`.

```c
#include "of.h"
#include <stdio.h>

static uint8_t midi_data[256*1024] __attribute__((aligned(512)));

int main(void) {
    // Load the bank once (it lives in CRAM1 for mixer DMA).
    of_smp_bank_load("slot:10/sc55.ofsf");

    of_file_slot_register(3, "music.mid");
    FILE *f = fopen("music.mid", "rb");
    uint32_t n = fread(midi_data, 1, sizeof(midi_data), f);
    fclose(f);

    of_midi_init();
    of_midi_play(midi_data, n, 1);  // loop

    while (1) {
        of_midi_pump();
        of_delay_ms(1);
    }
}
```

**Full API:**

| Function | Description |
|----------|-------------|
| `of_smp_bank_load(path)` | Load an `.ofsf` bank into CRAM1 (call once). |
| `of_midi_init()` | Init voice engine + reset channel state |
| `of_midi_play(data, len, loop)` | Start playback. Returns `OF_MIDI_OK` or error. |
| `of_midi_stop()` | Stop and silence all voices |
| `of_midi_pause()` / `of_midi_resume()` | Freeze/unfreeze event processing |
| `of_midi_pump()` | Advance envelopes + process events (call each frame) |
| `of_midi_playing()` / `of_midi_paused()` | Query state |
| `of_midi_set_volume(0-255)` | Master volume |
| `of_midi_get_volume()` | Get current master volume |

**Features:** Format 0 + Format 1, velocity scaling, channel volume
(CC7), expression (CC11), pan (CC10), sustain pedal (CC64), mod wheel
(CC1), filter cutoff (CC74) and resonance (CC71), pitch bend, tempo
changes, looping.  Up to 48 simultaneous voices with DAHDSR envelopes
and dual LFOs.

**Custom banks:** use `tools/sf2_to_ofsf` to convert any SF2
SoundFont:

```bash
cd tools && make
./sf2_to_ofsf your_font.sf2 your_bank.ofsf
```

See `src/firmware/api/of_smp_bank.h` for the `.ofsf` layout.

---

### Timer

#### `uint32_t of_time_us(void)`
Microseconds since boot. Wraps at ~4295 seconds (~71 minutes). Good for frame timing and short intervals.

#### `uint32_t of_time_ms(void)`
Milliseconds since boot. Wraps at ~49 days.

#### `void of_delay_us(uint32_t us)`
Busy-wait for the specified number of microseconds. Blocks the CPU.

#### `void of_delay_ms(uint32_t ms)`
Busy-wait for the specified number of milliseconds. Blocks the CPU.

#### Frame Timing Example

```c
uint32_t last = of_time_us();

while (1) {
    uint32_t now = of_time_us();
    uint32_t dt = now - last;  // Works even across wrap
    last = now;

    float dt_sec = dt / 1000000.0f;
    update_game(dt_sec);
    render();
    of_video_flip();
}
```

---

### Save Files

Persistent storage backed by CRAM1 PSRAM, persisted to SD card by the APF bridge. 10 save slots of 256 KB each. The Chip32 loader creates seed save files on first boot.

**Preferred: use standard C file I/O.** The OS maps `fopen("save_N")` to save slot N automatically:

```c
/* Write a save */
FILE *f = fopen("save_0", "wb");
fwrite(&game_state, sizeof(game_state), 1, f);
fclose(f);  /* auto-flushes to SD with actual written size */

/* Read a save */
FILE *f = fopen("save_0", "rb");
fread(&game_state, sizeof(game_state), 1, f);
fclose(f);
```

The low-level `of_save_*` API is still available for advanced use:

#### `int of_save_read(int slot, void *buf, uint32_t offset, uint32_t len)`
Read `len` bytes from save slot `slot` (0-9) at byte `offset` into `buf`. Returns bytes read, or negative on error.

#### `int of_save_write(int slot, const void *buf, uint32_t offset, uint32_t len)`
Write `len` bytes to save slot `slot` at byte `offset` from `buf`. Returns bytes written, or negative on error.

#### `void of_save_flush(int slot)`
Force the save data to be persisted to SD card. Normally happens automatically on game swap and shutdown, but call this after critical writes (e.g., checkpoint save). Flushes the full 256 KB slot.

#### `int of_save_flush_size(int slot, uint32_t size)`
Flush only `size` bytes of the save slot to SD card. Use this when your save data is smaller than 256 KB to reduce write time.

#### `void of_save_erase(int slot)`
Fill the entire save slot with `0xFF`. Useful for "New Game" or factory reset.

#### Example: Simple High Score

```c
/* Using POSIX file I/O (preferred) */
typedef struct {
    uint32_t magic;
    uint32_t high_score;
} save_data_t;

#define SAVE_MAGIC 0xDEADBEEF

void load_high_score(uint32_t *score) {
    save_data_t data;
    FILE *f = fopen("save_0", "rb");
    if (f) {
        fread(&data, sizeof(data), 1, f);
        fclose(f);
        *score = (data.magic == SAVE_MAGIC) ? data.high_score : 0;
    } else {
        *score = 0;
    }
}

void save_high_score(uint32_t score) {
    save_data_t data = { .magic = SAVE_MAGIC, .high_score = score };
    FILE *f = fopen("save_0", "wb");
    if (f) {
        fwrite(&data, sizeof(data), 1, f);
        fclose(f);  /* auto-flushes with actual size written */
    }
}
```

---

### File I/O

**Preferred: use standard C file I/O.** Register your data files at startup, then use `fopen` with the filename:

```c
/* Register data files at startup (maps filename → data slot ID) */
of_file_slot_register(3, "game.dat");

/* Open by name */
FILE *f = fopen("game.dat", "rb");
if (!f) {
    printf("File not found!\n");
    return;
}

/* Get file size */
fseek(f, 0, SEEK_END);
long size = ftell(f);
fseek(f, 0, SEEK_SET);

/* Read contents */
void *buf = malloc(size);
fread(buf, 1, size, f);
fclose(f);

/* Or access a slot directly without registration */
FILE *f = fopen("slot:3", "rb");
```

The low-level API is still available for direct slot access:

#### `int of_file_read(uint32_t slot_id, uint32_t offset, void *dest, uint32_t length)`
Read `length` bytes from data slot `slot_id` at byte `offset` into `dest`. The destination buffer **must be in SDRAM** (address >= `0x10000000`) and should be 512-byte aligned for best DMA performance.

Returns 0 on success, negative on error.

#### `long of_file_size(uint32_t slot_id)`
Get the size of a data slot in bytes. Returns negative on error.

#### Data Slot IDs

Slot IDs are defined in `data.json`. The standard openfpgaOS slots are:

| Slot ID | Purpose | Extensions |
|---------|---------|------------|
| 1 | OS kernel binary | `.bin` |
| 2 | Application ELF | `.elf` |
| 3-6 | Application data files | any |
| 10-19 | Save slots (nonvolatile, 256 KB each) | `.sav` |

Slots 1-6 use deferred loading -- they're loaded on demand by the OS after boot. Save slots (10-19) are backed by CRAM1 PSRAM and persisted to SD card by the bridge.

---

### Link Cable

Bidirectional communication via the Analogue Pocket's link port. Useful for multiplayer or connecting external devices.

#### `int of_link_send(uint32_t data)`
Send a 32-bit word. Returns 0 on success, -1 if the link is busy.

#### `int of_link_recv(uint32_t *data)`
Receive a 32-bit word. Returns 0 on success, -1 if no data is available.

#### `uint32_t of_link_status(void)`
Get link cable status flags.

#### Example

```c
// Send
if (of_link_send(my_game_state) < 0) {
    // Link busy, try again next frame
}

// Receive
uint32_t remote_state;
if (of_link_recv(&remote_state) == 0) {
    // Got data from the other Pocket
    apply_remote_state(remote_state);
}
```

---

### Terminal (Debug)

A 40x30 text console overlaid on the display. The terminal is the default display mode before `of_video_init()` is called. Useful for debug output and simple text-mode applications.

#### Debug Printing

Apps statically link upstream musl, so `printf()` is available the
moment you `#include <stdio.h>`. Output goes through `SYS_writev` to
the kernel, which writes to the terminal device. Use ANSI escape
sequences for cursor control, color, and screen clear.

```c
#include <stdio.h>
printf("\033[2J\033[H");                       /* clear + home */
printf("\033[%d;%dH", row + 1, col + 1);       /* cursor to (col,row) */
printf("Score: %d  Health: %d\n", score, health);
```

---

### Analogizer

Support for the Analogizer adapter, which provides analog video output (RGBS, YPbPr, S-Video, composite) and SNAC controller ports.

#### `int of_analogizer_enabled(void)`
Returns non-zero if an Analogizer is connected and enabled by the user.

#### `int of_analogizer_state(of_analogizer_state_t *state)`
Fill the state struct with current Analogizer configuration. Returns whether Analogizer is enabled.

```c
of_analogizer_state_t anlg;
if (of_analogizer_state(&anlg)) {
    // anlg.video_mode  -- RGBS, YPbPr, S-Video, etc.
    // anlg.snac_type   -- controller type (NES, SNES, PSX, etc.)
}
```

#### `of_analogizer_state_t` struct

```c
typedef struct {
    uint8_t  enabled;          // 1 if Analogizer is active
    uint8_t  video_mode;       // Output format (RGBS=0, YPbPr=2, etc.)
    uint8_t  snac_type;        // SNAC controller type ID
    uint8_t  snac_assignment;  // Which player SNAC is assigned to
    int8_t   h_offset;         // Horizontal video offset
    int8_t   v_offset;         // Vertical video offset
} of_analogizer_state_t;
```

---

### System

#### `void of_exit(void)`
Exit the application and return to the OS. The OS will halt the CPU. On the Pocket, the user can select a new game from the menu.

---

## Memory Map

### CPU Address Space

| Start | End | Size | Region | Cache | Description |
|-------|-----|------|--------|-------|-------------|
| `0x00000000` | `0x00007FFF` | 32 KB | BRAM | -- | On-chip RAM (OS kernel) |
| `0x10000000` | `0x13FFFFFF` | 64 MB | SDRAM | D-cache | Main memory (apps, data, framebuffers) |
| `0x10000000` | `0x100BFFFF` | 768 KB | FB0 | D-cache | Framebuffer 0 (320x240) |
| `0x10100000` | `0x101BFFFF` | 768 KB | FB1 | D-cache | Framebuffer 1 (320x240) |
| `0x10200000` | `0x102BFFFF` | 768 KB | FB2 | D-cache | Framebuffer 2 (320x240) |
| `0x10280000` | `0x102FFFFF` | 512 KB | DMA | D-cache | DMA bounce buffer |
| `0x10400000` | -- | -- | App | D-cache | Application load address |
| `0x20000000` | `0x200004AF` | 1200 B | Term VRAM | Uncached | Terminal character buffer (40x30) |
| `0x30000000` | `0x30FFFFFF` | 16 MB | CRAM0 | D-cache | Cellular RAM bank 0 (cached) |
| `0x31000000` | `0x31FFFFFF` | 16 MB | CRAM1 | D-cache | Cellular RAM bank 1 (cached) |
| `0x38000000` | `0x38FFFFFF` | 16 MB | CRAM0 | Uncached | CRAM0 (D-cache bypass) |
| `0x39000000` | `0x39FFFFFF` | 16 MB | CRAM1 | Uncached | CRAM1 (D-cache bypass) |
| `0x3A000000` | `0x3A03FFFF` | 256 KB | SRAM | Uncached | Static RAM |
| `0x40000000` | `0x4000006F` | 112 B | SysRegs | Uncached | System registers |
| `0x4C000000` | `0x4C000003` | 4 B | Audio | Uncached | Audio FIFO |
| `0x4D000000` | -- | -- | Link | Uncached | Link cable registers |
| `0x50000000` | `0x53FFFFFF` | 64 MB | SDRAM | Uncached | SDRAM (D-cache bypass alias) |

### Cached vs Uncached Access

CRAM0 and CRAM1 can be accessed through two address windows:
- **Cached** (`0x30xxxxxx` / `0x31xxxxxx`): Goes through the CPU D-cache. Best for sequential reads and code that re-accesses data.
- **Uncached** (`0x38xxxxxx` / `0x39xxxxxx`): Bypasses the D-cache. Best for DMA buffers, data shared with FPGA logic, or write-only data.

SRAM (`0x3A000000`) is always uncached.

SDRAM similarly has a cached window (`0x10xxxxxx`) and uncached alias (`0x50xxxxxx`).

---

## Register Map

### System Registers (`0x40000000`)

| Offset | Name | R/W | Description |
|--------|------|-----|-------------|
| `0x00` | `SYS_STATUS` | R | Bit 0: SDRAM ready, Bit 1: All data slots loaded |
| `0x04` | `SYS_CYCLE_LO` | R | Cycle counter (low 32 bits) |
| `0x08` | `SYS_CYCLE_HI` | R | Cycle counter (high 32 bits) |
| `0x0C` | `SYS_DISPLAY_MODE` | W | 0 = terminal, 1 = framebuffer |
| `0x10` | `FB_DISPLAY_ADDR` | R/W | Display framebuffer base (SDRAM offset) |
| `0x14` | `FB_DISPLAY_IDX` | R | Current display buffer index (0-2) |
| `0x18` | `FB_SWAP_CTRL` | R/W | Write: `(idx<<1)\|1` queues buffer idx for vsync. Read: `{disp_idx[2:1], pending[0]}` |
| `0x20` | `DS_SLOT_ID` | W | Data slot ID for DMA operation |
| `0x24` | `DS_SLOT_OFFSET` | W | Byte offset within data slot |
| `0x28` | `DS_BRIDGE_ADDR` | W | Bridge address for DMA target |
| `0x2C` | `DS_LENGTH` | W | DMA transfer length (bytes) |
| `0x30` | `DS_PARAM_ADDR` | W | Parameter address for commands |
| `0x34` | `DS_RESP_ADDR` | W | Response address for commands |
| `0x38` | `DS_COMMAND` | W | 1=read, 2=write, 3=openfile |
| `0x3C` | `DS_STATUS` | R | Bit 0: ACK, Bit 1: done, Bits 4:2: error |
| `0x40` | `PAL_INDEX` | W | Palette index for next write |
| `0x44` | `PAL_WRITE` | W | Palette color (24-bit RGB) |
| `0x50` | `CONT1_KEY` | R | Controller 1 button state |
| `0x54` | `CONT1_JOY` | R | Controller 1 analog sticks (packed) |
| `0x58` | `CONT1_TRIG` | R | Controller 1 analog triggers (packed) |
| `0x5C` | `CONT2_KEY` | R | Controller 2 button state |
| `0x60` | `CONT2_JOY` | R | Controller 2 analog sticks |
| `0x64` | `CONT2_TRIG` | R | Controller 2 analog triggers |
| `0x68` | `SYS_GAME_ID` | R | Current game/instance ID |

### Audio FIFO (`0x4C000000`)

| Offset | Name | R/W | Description |
|--------|------|-----|-------------|
| `0x00` | `AUDIO_SAMPLE` | W | Write: 32-bit stereo sample (L[31:16], R[15:0]) |
| `0x00` | `AUDIO_STATUS` | R | Read: Bits 11:0 = FIFO level, Bit 12 = full |

---

## Data Slots and Instances

### How Games are Loaded

The Analogue Pocket's APF framework uses **data slots** and **instances** to load game content:

1. **`data.json`** defines the available data slots (what types of files the core can load).
2. **Instance JSON files** specify which files to load into which slots for a particular game.
3. At boot, the APF loads files from the SD card into the appropriate memory regions.
4. The OS waits for all slots to be loaded, then boots the application ELF.

### Creating an Instance

To create a new game/app instance, create a JSON file in `Assets/openfpgaos/common/` on the SD card:

```json
{
    "instance": {
        "magic": "APF_VER_1",
        "variant_select": {
            "id": 666,
            "select": false
        },
        "data_slots": [
            {
                "id": 1,
                "filename": "os.bin"
            },
            {
                "id": 2,
                "filename": "mygame.elf"
            },
            {
                "id": 3,
                "filename": "gamedata.dat"
            },
            {
                "id": 10,
                "filename": "MyGame.sav"
            }
        ]
    }
}
```

Save this as e.g. `My Game.json` in the `Assets/openfpgaos/common/` directory. The Pocket menu will show "My Game" as a selectable game.

### File Placement on SD Card

```
SD Card/
  Assets/
    openfpgaos/
      common/
        os.bin              OS kernel (always slot 1)
        mygame.elf          Your compiled app (slot 2)
        gamedata.dat        Your game data (slot 3)
        My Game.json        Instance definition
  Cores/
    ThinkElastic.openfpgaOS/
      ...                   Core files (bitstream, etc.)
  Saves/
    openfpgaos/
      common/
        MyGame.sav          Save file (auto-created)
```

---

## Build System

### Building from the Repository

```bash
# Build everything (OS + all apps)
make firmware

# Build just the OS
make -C src/firmware/os

# Build a specific app
make -C src/firmware/apps/fbdemo

# Deploy to SD card
make deploy SD=/path/to/sd/mount
```

### Compiler Flags

The toolchain targets RV32IMAFC (integer, multiply, atomic, single-precision float, compressed instructions):

| Flag | Purpose |
|------|---------|
| `-march=rv32imafc` | Target ISA |
| `-mabi=ilp32f` | ABI: 32-bit int/long/pointer, hardware float in F registers |
| `-ffreestanding` | No hosted environment assumptions |
| `-nostdlib` | Don't link standard C library |
| `-nostartfiles` | Don't link default startup files |
| `-ffunction-sections` | Each function in its own section (for `--gc-sections`) |
| `-fdata-sections` | Each data item in its own section |
| `-fno-builtin` | Don't use built-in function implementations |

### Using Hardware Float

The CPU has a single-precision FPU. Use `float` freely -- it compiles to hardware instructions:

```c
float angle = 0.0f;
float x = 160.0f + 50.0f * sinf(angle);  // Hardware FP
angle += 0.02f;
```

**Important**: Use `float`, not `double`. Double-precision operations are emulated in software via libgcc and are ~100x slower.

### No Standard Library

There is no `libc` linked by default. You get:
- `libgcc` for compiler support routines (soft-divide, float conversions, etc.)
- Hardware float instructions via `-march=rv32imafc`
- `of.h` for all platform I/O

If you need `memcpy`, `memset`, etc., implement them yourself or use the `__builtin_*` variants:

```c
__builtin_memcpy(dst, src, n);
__builtin_memset(buf, 0, n);
```

---

## Architecture

### Boot Sequence

1. FPGA configures from bitstream on SD card
2. VexRiscv begins executing from BRAM at `0x00000000`
3. Boot code waits for SDRAM to initialize
4. OS kernel loads from boot ROM (firmware.mif, baked into bitstream)
5. APF loads data slots (os.bin, app.elf, game data)
6. OS waits for `dataslot_allcomplete` signal
7. OS copies `os.bin` from SDRAM to BRAM (runtime kernel)
8. OS loads `app.elf` into SDRAM at `0x10400000`
9. OS jumps to app `_start` entry point

### OS/App Boundary

Applications run in the same address space as the OS (no MMU). The OS provides services via `ecall` (RISC-V environment call):

```
App code (SDRAM 0x10400000+)
    |
    |  ecall instruction
    v
OS trap handler (BRAM 0x00000000+)
    |
    |  Direct register/memory access
    v
Hardware (FPGA peripherals)
```

All `of_*` functions in `of.h` are thin inline wrappers around `ecall`. The syscall overhead is ~50 cycles.

### Syscall Numbers

openfpgaOS HAL syscalls start at `0x1000` and are grouped by subsystem:

| Range | Subsystem |
|-------|-----------|
| `0x1000--0x1008` | Video |
| `0x1010--0x1014` | Audio |
| `0x1020--0x1021` | Input |
| `0x1030--0x1033` | Save |
| `0x1040--0x1041` | Analogizer |
| `0x1050--0x1053` | Terminal |
| `0x1060--0x1062` | Link |
| `0x1070--0x1073` | Timer |
| `0x1080--0x1081` | File |

Standard Linux-compatible syscalls (brk, read, write, openat, etc.) are also supported for musl libc compatibility.

---

## Timing and Performance

### CPU Budget

At 100 MHz with a 60 Hz display, you have **~1.67 million cycles per frame**.

| Operation | Approximate Cycles |
|-----------|-------------------|
| Syscall (ecall round-trip) | ~50 |
| Palette set (1 entry) | ~60 |
| Framebuffer clear (76,800 bytes) | ~20,000 |
| Full framebuffer copy | ~80,000 |
| Pixel write (direct) | 1 |
| `float` multiply | 4 |
| `float` divide | 12 |
| SDRAM read (cached, hit) | 1 |
| SDRAM read (cache miss) | ~20 |
| CRAM read (cached, hit) | 1 |
| CRAM read (cache miss) | ~60 |
| SRAM read (uncached) | ~10 |

### Frame Pacing

The display runs at 60 Hz. `of_video_flip()` queues a swap at the next vsync. Two common patterns:

**Pattern 1: Vsync-locked (recommended)**
```c
while (1) {
    update();
    render();
    of_video_flip();
    of_video_wait_flip();  // Blocks until vsync
}
```
Guaranteed 60 FPS if your frame fits in the budget. Drops to 30 FPS if it doesn't.

**Pattern 2: Free-running**
```c
while (1) {
    uint32_t t0 = of_time_us();
    update();
    render();
    of_video_flip();
    // Don't sync -- start next frame immediately
}
```
Can produce tearing but allows variable frame timing.

### DMA Buffer Alignment

When loading data via `of_file_read()`, align your buffers to 512 bytes for optimal DMA performance:

```c
static uint8_t data[4096] __attribute__((aligned(512)));
```

---

## Cookbook

### Loading and Displaying a PNG Image

```c
#include "of.h"
#include "png.h"  // Included PNG decoder

#define IMAGE_SLOT 3
#define MAX_PNG    (128 * 1024)

static uint8_t png_buf[MAX_PNG] __attribute__((aligned(512)));
static uint8_t pixels[OF_SCREEN_W * OF_SCREEN_H];
static uint32_t palette[256];

void show_image(void) {
    long size = of_file_size(IMAGE_SLOT);
    if (size <= 0 || size > MAX_PNG) return;

    of_file_read(IMAGE_SLOT, 0, png_buf, (uint32_t)size);

    int w, h;
    png_decode(png_buf, (uint32_t)size, palette, pixels, &w, &h);

    of_video_init();
    of_video_palette_bulk(palette, 256);

    uint8_t *fb = of_video_surface();
    of_video_clear(0);

    int ox = (OF_SCREEN_W - w) / 2;
    int oy = (OF_SCREEN_H - h) / 2;
    for (int y = 0; y < h && y < OF_SCREEN_H; y++)
        for (int x = 0; x < w && x < OF_SCREEN_W; x++)
            fb[(oy + y) * OF_SCREEN_W + ox + x] = pixels[y * w + x];

    of_video_flip();
    of_video_wait_flip();
}
```

### Smooth 60 FPS Game Loop

```c
int main(void) {
    of_video_init();
    setup_palette();

    int player_x = 160, player_y = 120;
    int speed = 2;

    while (1) {
        of_input_poll();

        // Movement
        if (of_btn(OF_BTN_UP))    player_y -= speed;
        if (of_btn(OF_BTN_DOWN))  player_y += speed;
        if (of_btn(OF_BTN_LEFT))  player_x -= speed;
        if (of_btn(OF_BTN_RIGHT)) player_x += speed;

        // Clamp
        if (player_x < 0) player_x = 0;
        if (player_x > 311) player_x = 311;
        if (player_y < 0) player_y = 0;
        if (player_y > 231) player_y = 231;

        // Render
        of_video_clear(0);
        uint8_t *fb = of_video_surface();
        for (int dy = 0; dy < 8; dy++)
            for (int dx = 0; dx < 8; dx++)
                fb[(player_y + dy) * OF_SCREEN_W + player_x + dx] = 1;

        of_video_flip();
        of_video_wait_flip();
    }
}
```

### Sample-Based MIDI Playback

See the SDK `mididemo` app for a complete player implementation:
- Load an `.ofsf` bank via `of_smp_bank_load()`
- Parse Standard MIDI Files with `of_midi_play()`
- Up to 48 voices with DAHDSR envelopes and dual LFOs
- Real-time terminal display of channel activity

Convert your own SoundFont (SF2) to `.ofsf` with `tools/sf2_to_ofsf`;
the repo ships an SC-55-derived General MIDI bank at
`assets/banks/sc55.ofsf`.

### Two-Player Input

```c
while (1) {
    of_input_poll();

    // Player 1
    if (of_btn(OF_BTN_A))       p1_fire();
    if (of_btn(OF_BTN_UP))      p1_y--;

    // Player 2
    if (of_btn_p2(BTN_A))    p2_fire();
    if (of_btn_p2(BTN_UP))   p2_y--;

    // Or get full state for analog
    of_input_state_t p2;
    of_input_state(1, &p2);
    p2_aim_x = p2.joy_rx;
}
```

### Using CRAM for Large Assets

CRAM0 and CRAM1 provide 32 MB of additional storage. Use cached access for read-heavy data and uncached for write-once data:

```c
// Write tile data to CRAM0 (uncached -- write once, no cache pollution)
volatile uint8_t *cram = (volatile uint8_t *)0x38000000;
for (int i = 0; i < tile_data_size; i++)
    cram[i] = tile_data[i];

// Read tile data from CRAM0 (cached -- fast repeated access)
uint8_t *tiles = (uint8_t *)0x30000000;
uint8_t pixel = tiles[tile_offset];  // First access: cache miss ~60 cycles
uint8_t next  = tiles[tile_offset + 1];  // Cache hit: 1 cycle
```

### Persistent Save with Integrity Check

```c
#define SAVE_SLOT 0

typedef struct {
    uint32_t magic;       // 0xCAFEBABE
    uint32_t version;     // Save format version
    uint32_t checksum;    // Simple checksum
    // ... your game state ...
    uint32_t score;
    uint8_t  level;
    uint8_t  lives;
} game_save_t;

static uint32_t calc_checksum(const game_save_t *s) {
    uint32_t sum = 0;
    const uint8_t *p = (const uint8_t *)s;
    for (int i = 12; i < (int)sizeof(*s); i++)  // Skip magic/version/checksum
        sum += p[i];
    return sum;
}

int load_game(game_save_t *save) {
    FILE *f = fopen("save_0", "rb");
    if (!f) return -1;
    fread(save, sizeof(*save), 1, f);
    fclose(f);
    if (save->magic != 0xCAFEBABE) return -1;
    if (save->checksum != calc_checksum(save)) return -2;
    return 0;
}

void save_game(const game_save_t *save) {
    game_save_t tmp = *save;
    tmp.magic = 0xCAFEBABE;
    tmp.version = 1;
    tmp.checksum = calc_checksum(&tmp);
    FILE *f = fopen("save_0", "wb");
    if (f) {
        fwrite(&tmp, sizeof(tmp), 1, f);
        fclose(f);  /* auto-flushes with actual size */
    }
}
```
