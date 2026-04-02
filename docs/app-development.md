# openfpgaOS Application Development

## Overview

Applications are ELF binaries compiled with a RISC-V cross-compiler. They run on the VexRiscv CPU at 100 MHz and communicate with the OS kernel via `ecall` syscalls. The SDK wraps all syscalls in inline C functions.

For game development, use the [openfpgaOS SDK](https://github.com/ThinkElastic/openfpgaOS-SDK). This document covers the internals for OS contributors.

## Application Structure

### Minimal App

```c
#include "of.h"
#include <stdio.h>

int main(void) {
    of_video_init();

    uint8_t *fb = of_video_surface();
    for (int y = 0; y < 240; y++)
        for (int x = 0; x < 320; x++)
            fb[y * 320 + x] = x ^ y;

    of_video_flip();
    printf("Hello world!\n");

    while (1) {
        of_input_poll();
        if (of_btn_pressed(OF_BTN_START))
            of_exit();
        of_delay_ms(16);
    }
}
```

### Build Requirements

- RISC-V GCC: `riscv64-elf-gcc` or `riscv64-unknown-elf-gcc`
- Architecture: `rv32imafc` with `ilp32f` ABI
- CRT: `crt/start.S` (entry point) and `crt/app.ld` (linker script)
- Flags: `-ffreestanding -nostdlib -nostartfiles`

### Entry Point

The CRT startup (`crt/start.S`) provides `_start` which calls `main()`. When `main()` returns, the CRT issues `SYS_exit` (syscall 93) to halt. The OS sets up the stack, arguments, and BSS before jumping to `_start`.

### Memory Layout

```
0x00002000 - 0x0000FDFF   BRAM app region (55 KB) — OF_FASTTEXT code
0x10400000 - 0x13BFFFFF   App code + data (48 MB SDRAM)
0x13C00000 - 0x13F7FFFF   Heap (grows up via brk)
0x13F80000                 Stack top (grows down)
```

### Heap

Managed via the `brk` syscall. Starts after BSS, grows upward. Upper bound is the save region. `malloc()` / `free()` are provided by the OS kernel (dlmalloc) and accessible via the libc jump table.

### Standard C Library

The OS kernel exports 88 libc functions via a jump table at `0x103FF000` (including POSIX I/O: open, close, read, write, lseek). SDK apps include thin wrappers in `libc/stdio.h`, `libc/stdlib.h`, etc. that forward to this table. No musl or newlib is needed in the app build.

For game ports that need full POSIX compatibility, apps can link against musl libc instead. musl's syscalls go through the same `ecall` mechanism.

## API Reference

All functions are declared in `of.h` (which includes all subsystem headers) as `static inline` syscall wrappers.

### Video — `of_video.h`

320x240 framebuffer, triple-buffered. Default: 8-bit indexed with 256-color palette.

```c
void of_video_init(void);                          // Initialize video
uint8_t *of_video_surface(void);                   // Get draw buffer pointer
void of_video_flip(void);                          // Swap buffers at vsync
void of_video_sync(void);                          // Wait for flip to complete
void of_video_clear(uint8_t color);                // Fill with palette index
void of_video_pixel(int x, int y, uint8_t color);  // Set pixel (bounds-checked)
void of_video_flush(void);                         // Flush D-cache
```

**Palette (0x00RRGGBB format):**

```c
void of_video_palette(uint8_t index, uint32_t rgb);
void of_video_palette_bulk(const uint32_t *pal, int count);
void of_video_palette_vga6(const uint8_t *vga_pal, int count);  // 6-bit VGA (0-63)
```

**Color modes** — switch at runtime:

| Mode | Constant | Bits/pixel | Framebuffer |
|------|----------|-----------|-------------|
| 8-bit indexed (256 colors) | `OF_VIDEO_MODE_8BIT` | 8 | 76,800 B |
| 4-bit indexed (16 colors) | `OF_VIDEO_MODE_4BIT` | 4 | 38,400 B |
| 2-bit indexed (4 colors) | `OF_VIDEO_MODE_2BIT` | 2 | 19,200 B |
| RGB565 direct | `OF_VIDEO_MODE_RGB565` | 16 | 153,600 B |
| RGB555 direct | `OF_VIDEO_MODE_RGB555` | 16 | 153,600 B |
| RGBA5551 (alpha) | `OF_VIDEO_MODE_RGBA5551` | 16 | 153,600 B |

```c
void of_video_set_color_mode(int mode);
uint16_t *of_video_surface16(void);   // For 16-bit modes
```

**Blitting helpers:**

```c
void of_blit(int dx, int dy, int w, int h, const uint8_t *src, int stride);
void of_blit_pal(int dx, int dy, int w, int h, const uint8_t *src, int stride, uint8_t offset);
void of_fill_rect(int x, int y, int w, int h, uint8_t color);
void of_video_blit_letterbox(const uint8_t *src, int src_w, int src_h);
```

### Audio — `of_audio.h`

48 kHz stereo PCM + hardware OPL3 (YMF262) FM synthesis with 18 channels (both register banks).

```c
void of_audio_init(void);
int  of_audio_enqueue(const int16_t *samples, int count);  // Stereo pairs
int  of_audio_ring_free(void);                              // Free samples in ring
void of_audio_opl_write(uint16_t reg, uint8_t val);        // OPL3 register write
void of_audio_opl_reset(void);                              // Reset OPL3
```

Registers `0x00`-`0xFF` target bank 0 (channels 0-8), `0x100`-`0x1FF` target bank 1 (channels 9-17). Enable OPL3 mode by writing `of_audio_opl_write(0x105, 0x01)`.

### MIDI Playback — `of_midi.h`

Plays Standard MIDI Files (Format 0 and 1) through all 18 OPL3 channels. Non-blocking design driven by `of_time_us()`. Includes a built-in General MIDI instrument bank (128 melodic + 47 percussion).

```c
of_midi_init();                              // Reset OPL3 + enable OPL3 mode
of_midi_play(midi_data, midi_len, 1);        // Start playback (1 = loop)
of_midi_pump();                              // Call each frame
of_midi_stop();                              // Stop and silence
of_midi_pause();                             // Freeze playback
of_midi_resume();                            // Resume from pause
of_midi_set_volume(200);                     // Master volume 0-255
of_midi_load_bank(custom_bank);              // Custom GM bank (NULL = built-in)

int playing = of_midi_playing();
int paused  = of_midi_paused();
int vol     = of_midi_get_volume();
```

**Error codes:** `OF_MIDI_OK` (0), `OF_MIDI_ERR_NOT_INIT` (-1), `OF_MIDI_ERR_BAD_HDR` (-2), `OF_MIDI_ERR_FORMAT` (-3), `OF_MIDI_ERR_NO_TRACKS` (-4), `OF_MIDI_ERR_PLAYING` (-5).

**Custom instrument bank:** 175 instruments x 11 bytes = 1,925 bytes. Format per instrument: `[FB/CNT, mod_AVEK, mod_TL, mod_ARDR, mod_SLRR, mod_WS, car_AVEK, car_TL, car_ARDR, car_SLRR, car_WS]`. Programs 0-127 are melodic (GM order), 128-174 are percussion (GM drum map notes 35-81).

### Audio Mixer — `of_mixer.h`

Multi-voice software mixer with resampling. Input: unsigned 8-bit PCM.

```c
void of_mixer_init(int max_voices, int output_rate);
int  of_mixer_play(const uint8_t *pcm, uint32_t len, uint32_t rate, int pri, int vol);
void of_mixer_stop(int voice);
void of_mixer_stop_all(void);
void of_mixer_set_volume(int voice, int volume);   // 0-255
void of_mixer_pump(void);                           // Call each frame
int  of_mixer_voice_active(int voice);              // 1=playing
```

### Input — `of_input.h`

Two controllers: d-pad, ABXY, L/R shoulders, L2/R2 triggers, L3/R3 sticks, SELECT, START.

```c
void of_input_poll(void);                    // Read hardware (once per frame)

int of_btn(uint32_t mask);                   // Held (player 1)
int of_btn_pressed(uint32_t mask);           // Edge: just pressed
int of_btn_released(uint32_t mask);          // Edge: just released

int of_btn_p2(uint32_t mask);               // Player 2 equivalents
int of_btn_pressed_p2(uint32_t mask);
int of_btn_released_p2(uint32_t mask);

uint32_t of_input_state(int player, of_input_state_t *state);
void of_input_set_deadzone(int16_t deadzone);
```

Button masks: `OF_BTN_UP`, `DOWN`, `LEFT`, `RIGHT`, `A`, `B`, `X`, `Y`, `L1`, `R1`, `L2`, `R2`, `L3`, `R3`, `SELECT`, `START`.

### Timer — `of_timer.h`

100 MHz hardware cycle counter. Wraps at ~71 min (us) / ~49 days (ms).

```c
uint32_t of_time_us(void);
uint32_t of_time_ms(void);
void of_delay_us(uint32_t us);   // Busy-wait
void of_delay_ms(uint32_t ms);
```

### File I/O — `of_file.h`

Standard C file I/O via registered data slots:

```c
of_file_slot_register(3, "game.dat");          // Map filename to slot

FILE *f = fopen("game.dat", "rb");             // Opens slot 3
fread(buf, 1, size, f);
fclose(f);

FILE *f = fopen("slot:3", "rb");               // Direct slot access
```

Low-level (bypasses stdio):

```c
int  of_file_read(uint32_t slot, uint32_t offset, void *dest, uint32_t len);
long of_file_size(uint32_t slot);
void of_set_idle_hook(void (*hook)(void));      // Background work during DMA
```

### Save System — `of_save.h`

10 slots, 256 KB each, CRAM1 PSRAM backed to SD.

```c
FILE *f = fopen("save_0", "wb");
fwrite(data, sizeof(data), 1, f);
fclose(f);                                      // Auto-flushes actual size

FILE *f = fopen("save_0", "rb");
fread(data, sizeof(data), 1, f);
fclose(f);
```

Low-level:

```c
int  of_save_read(int slot, void *buf, uint32_t offset, uint32_t len);
int  of_save_write(int slot, const void *buf, uint32_t offset, uint32_t len);
void of_save_flush(int slot);                   // Flush full 256 KB
int  of_save_flush_size(int slot, uint32_t sz); // Flush only what was written
void of_save_erase(int slot);
```

### Terminal — `of_terminal.h`

40x30 text console with CP437 character set and box-drawing characters.

```c
void of_print(const char *s);
void of_print_char(char c);
void of_print_clear(void);
void of_print_at(int col, int row);
printf("Score: %d\n", score);                  // Also works
```

### Tile Engine — `of_tile.h`

Hardware tile layer (64x32 tilemap, 8x8 tiles, 4bpp) and 64 sprites (8x8, 4bpp).

```c
void of_tile_enable(int enable);
void of_tile_scroll(int x, int y);
void of_tile_set(int col, int row, int tile);
void of_tile_load_map(const void *data, int count);
void of_tile_load_chr(const void *data, int size);

void of_sprite_enable(int enable);
void of_sprite_set(int id, int tile, int pal, int flip_h, int flip_v);
void of_sprite_move(int id, int x, int y);
void of_sprite_load_chr(const void *data, int size);
void of_sprite_hide(int id);
void of_sprite_hide_all(void);
```

### Link Cable — `of_link.h`

GB/GBC-compatible serial link at 256 kHz.

```c
int of_link_send(uint32_t data);
int of_link_recv(uint32_t *data);
uint32_t of_link_status(void);
```

### Analogizer — `of_analogizer.h`

```c
int of_analogizer_enabled(void);
uint32_t of_analogizer_state(void);
```

### BRAM Hot Path — `of_bram.h`

Place inner loops in on-chip BRAM for zero-latency execution (~55 KB available):

```c
OF_FASTTEXT void inner_loop(void) { /* runs from BRAM */ }
OF_FASTDATA int table[256];
OF_FASTRODATA const int constants[16];
```

The OS ELF loader copies BRAM-targeted segments from the ELF to BRAM at load time.

### Additional APIs

- **MIDI** (`of_midi.h`): `of_midi_init()`, `of_midi_play()`, `of_midi_pump()`, `of_midi_stop()`, `of_midi_set_volume()`
- **Cache** (`of_cache.h`): `of_cache_flush_video()`, `of_cache_invalidate_icache()`
- **Codec** (`of_codec.h`): `of_codec_parse_voc()`, `of_codec_parse_wav()`
- **LZW** (`of_lzw.h`): `of_lzw_compress()`, `of_lzw_uncompress()`
- **Interact** (`of_interact.h`): `of_interact_get(index)` — read Pocket menu options
- **Version** (`of_version.h`): `of_get_version()` — runtime API version

## Example: Input + Drawing

```c
#include "of.h"

int main(void) {
    of_video_init();

    uint32_t pal[256];
    for (int i = 0; i < 256; i++)
        pal[i] = (i << 16) | (i << 8) | i;
    of_video_palette_bulk(pal, 256);

    int x = 152, y = 112;

    while (1) {
        of_input_poll();

        if (of_btn(OF_BTN_UP))    y -= 2;
        if (of_btn(OF_BTN_DOWN))  y += 2;
        if (of_btn(OF_BTN_LEFT))  x -= 2;
        if (of_btn(OF_BTN_RIGHT)) x += 2;

        of_video_clear(0);
        of_fill_rect(x, y, 16, 16, 255);
        of_video_flip();
        of_video_sync();
    }
}
```
