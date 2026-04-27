# openfpgaOS Application Development

## Overview

Applications are ELF binaries compiled with a RISC-V cross-compiler. They run on the VexRiscv CPU at 100 MHz and communicate with the OS kernel through two `ecall`-based interfaces:

1. **Linux RISC-V syscalls** for everything POSIX (`read`, `write`, `brk`, `openat`, `clock_gettime`, ...). Apps statically link **upstream musl libc** unmodified, and musl emits these syscalls itself. The kernel implements the small subset musl actually uses.
2. **openfpgaOS SBI vendor extensions** (`OF_EID_*`, see `docs/syscall-abi.md`) for the platform-specific subsystems — video framebuffer, audio mixer, GPU, MIDI, etc. The SDK wraps these in inline `static` functions in `of_*.h`.

There is **no custom libc shim layer**. Apps build against unmodified musl headers and link `musl/lib/libc.a` like any standard riscv32 musl-static program.

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

- RISC-V GCC: `riscv64-elf-gcc` (the bare-metal toolchain — we don't need the Linux variant)
- Architecture: `rv32imafc` with `ilp32f` ABI
- musl static library: built once into `src/firmware/musl/lib/libc.a`
- Flags: `-nostartfiles -nostdlib` plus `-lc -lgcc` at link time

A ready-to-use template lives at `src/firmware/api/Makefile.fpga`:

```sh
make -f /path/to/openfpgaOS/src/firmware/api/Makefile.fpga \
     APP=mygame SRCS="main.c world.c"
```

Resulting `mygame.elf` is loaded and executed by the kernel ELF loader.

### Entry Point

musl provides `_start` (`src/firmware/musl/crt/crt1.c`) which initializes the C runtime, calls `__libc_start_main`, and then `main()`. When `main()` returns, musl emits `SYS_exit_group` (syscall 94) which the kernel handles in `linux_dispatch()`. No custom CRT is needed.

The kernel ELF loader sets up the stack pointer, copies the program segments to their target VAs, zeros BSS, applies relocations (for PIE/`ET_DYN` apps), and jumps to `e_entry`. From musl's perspective the environment looks exactly like Linux.

### Memory Layout

```
0x00002000 - 0x0000F7FF   BRAM app region (54 KB) — OF_FASTTEXT code
0x00007800                 of_capabilities struct (read-only)
0x00007A00                 of_services_table (read-only function pointers)
0x10400000 - 0x13BFFFFF   App code + data (48 MB SDRAM)
0x13C00000 - 0x13F7FFFF   Heap (grows up via brk)
0x13F80000                 Stack top (grows down)
```

### Heap

Managed by **musl's malloc** (`mallocng`), which calls `SYS_brk` (and optionally `SYS_mmap2`) under the hood. The kernel's `linux_dispatch()` honors `brk` against a heap region carved out of SDRAM. There is no separate kernel-side allocator exposed to apps.

### Standard C Library

Apps statically link the bundled **upstream musl 1.2.5**, built once for `rv32imafc/ilp32f` and stashed at `src/firmware/musl/lib/libc.a`. Standard headers come from `src/firmware/musl/include/`. There is **no SDK-shim libc** — `<stdio.h>`, `<stdlib.h>`, `<string.h>`, `<math.h>`, `<unistd.h>`, etc. are all upstream musl.

This is the same model every Alpine/Void Linux musl-static binary uses. The cost is ~50–100 KB per app for the formatter / allocator / math; the benefit is zero ongoing fork maintenance and full musl compatibility (every libc feature works, future musl updates flow through unchanged).

The Linux syscalls musl emits are handled by the kernel's `linux_dispatch()` in `src/firmware/os/kernel/syscall.c`. The implemented subset is:

| Syscall                | Used by                                  |
|------------------------|------------------------------------------|
| `brk` (214)            | musl mallocng heap growth                |
| `mmap2` (222)          | large allocations / aligned allocations  |
| `munmap` (215)         | matching unmap                           |
| `writev`/`write` (66/64) | stdio output                           |
| `readv`/`read` (65/63) | stdio input                              |
| `openat` (56) / `close` (57) | file I/O                           |
| `lseek` (62)           | seeking                                  |
| `statx` (291)          | rv32 musl uses statx, not fstat          |
| `getdents64` (61)      | opendir / readdir                        |
| `clock_gettime64` (403) | rv32 musl 64-bit clock                  |
| `clock_getres64` (406) | matching                                 |
| `clock_nanosleep` (115) | sleep / nanosleep                       |
| `exit_group`/`exit` (94/93) | exit                                |
| `set_tid_address` (96) | called once at startup                   |
| `rt_sigaction`/`rt_sigprocmask` | signal stubs (no-op)            |
| `ioctl` (29)           | musl stdout buffering detection          |
| `futex` (422)          | musl FILE locking (single-threaded)      |
| `riscv_flush_icache` (259) | JIT / loaders                        |

Adding a new POSIX call means appending one case to `linux_dispatch()`.

## API Reference

All functions are declared in `of.h` (which includes all subsystem headers) as `static inline` syscall wrappers.

### Video — `of_video.h`

320x240 framebuffer, triple-buffered. Default: 8-bit indexed with 256-color palette.

```c
void of_video_init(void);                          // Initialize video
uint8_t *of_video_surface(void);                   // Get draw buffer pointer
void of_video_flip(void);                          // Swap buffers at vsync
void of_video_wait_flip(void);                     // Wait for flip to complete
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

48 kHz stereo PCM over the hardware FIFO, plus a double-buffered
streaming path for music and voice.

```c
void of_audio_init(void);
int  of_audio_write(const int16_t *samples, int count);     // Interleaved L/R
int  of_audio_free(void);                                    // Free space in FIFO

int  of_audio_stream_open(int sample_rate);                  // Mono stream, resampled to 48 kHz
int  of_audio_stream_write(const int16_t *samples, int n);
int  of_audio_stream_ready(void);
void of_audio_stream_close(void);
```

### MIDI Playback — `of_midi.h` + `of_smp_bank.h`

Plays Standard MIDI Files (Format 0/1) through the CPU-side software mixer
using a pre-resolved `.ofsf` sample bank.  The repo ships an SC-55-derived
General MIDI bank at `assets/banks/sc55.ofsf` (~3 MB, drop it onto SD
and reference it with the file API).

```c
of_smp_bank_load("slot:10/sc55.ofsf");       // Load bank into CRAM1 (once, at init)
of_midi_init();                              // Init voice engine

of_midi_play(midi_data, midi_len, 1);        // Start playback (1 = loop)
of_midi_pump();                              // Call each frame
of_midi_stop();                              // Stop and silence
of_midi_pause();
of_midi_resume();
of_midi_set_volume(200);                     // Master volume 0-255

int playing = of_midi_playing();
int paused  = of_midi_paused();
int vol     = of_midi_get_volume();
```

**Error codes:** `OF_MIDI_OK` (0), `OF_MIDI_ERR_NOT_INIT` (-1),
`OF_MIDI_ERR_BAD_HDR` (-2), `OF_MIDI_ERR_FORMAT` (-3),
`OF_MIDI_ERR_NO_TRACKS` (-4), `OF_MIDI_ERR_PLAYING` (-5),
`OF_MIDI_ERR_NO_BANK` (-6, forgot to call `of_smp_bank_load`).

**Custom banks:** convert your own SF2 with `tools/sf2_to_ofsf`:

```bash
cd tools && make
./sf2_to_ofsf your_sound_font.sf2 your_bank.ofsf
```

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

### Save Files

10 slots, 256 KB each, staged in the CRAM0 save window and committed to SD card when a dirty save file is closed.

```c
FILE *f = fopen("MyGame_0.sav", "wb");
fwrite(data, sizeof(data), 1, f);
fclose(f);

FILE *f = fopen("MyGame_0.sav", "rb");
fread(data, sizeof(data), 1, f);
fclose(f);
```

Use POSIX file I/O with save filenames. Other APF data files are read-only.

### Terminal — `of_terminal.h`

40x30 text console with CP437 character set and box-drawing characters.
Use standard `printf()` (provided by statically-linked musl, which writes to the
terminal device); use ANSI escape sequences for cursor control, color,
and screen clear. The `ACS_*` constants in `of_terminal.h` give CP437
box-drawing characters for use with `printf("%c", ACS_VLINE)`.

```c
#include <stdio.h>
#include "of_terminal.h"

printf("\033[2J\033[H");                        /* clear, home */
printf("\033[%d;%dH", row + 1, col + 1);        /* set cursor */
printf("Score: %d\n", score);
printf("%c%c%c\n", ACS_ULCORNER, ACS_HLINE, ACS_URCORNER);
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

### Networking — `of_net.h`

Host/join sessions, broadcast and unicast messages between cores. See
`of_net.h` for the full API (`of_net_host_start`, `of_net_join`,
`of_net_send`, `of_net_recv`, `of_net_broadcast`, etc.).

### Analogizer — `of_analogizer.h`

```c
int of_analogizer_enabled(void);
uint32_t of_analogizer_state(void);
```

### Fast RAM Hot Path — `of_fastram.h`

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
        of_video_wait_flip();
    }
}
```
