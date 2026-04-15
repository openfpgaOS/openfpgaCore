# ofOS HAL API Reference

This document covers the kernel-side HAL API defined in `src/firmware/os/hal/`. For the app-facing API (syscall wrappers), see [App Development](app-development.md).

## Initialization

```c
#include "hal/hal.h"

void hal_init(void);
```

Initializes all subsystems in order: timer, terminal, dataslot, input, audio, framebuffer, save, analogizer, link. Call once at OS boot.

---

## Framebuffer (`hal/fb.h`)

320x240 8-bit indexed color, triple-buffered with hardware palette and vsync-synchronized swap.

### Functions

```c
void fb_init(void);
```
Initialize framebuffer subsystem. Sets display mode to framebuffer, clears both buffers.

```c
uint8_t *fb_get_draw_buffer(void);
```
Returns pointer to the current draw buffer. Write pixels here.

```c
const uint8_t *fb_get_display_buffer(void);
```
Returns pointer to the buffer currently being displayed (read-only).

```c
void fb_swap(void);
```
Flush D-cache and request vsync-synchronized buffer swap. Non-blocking.

```c
void fb_wait_swap(void);
```
Block until pending swap completes.

```c
void fb_swap_wait(void);
```
Convenience: swap then wait.

```c
void fb_set_palette(uint8_t index, uint8_t r, uint8_t g, uint8_t b);
```
Set a single palette entry (0-255) to an RGB888 color.

```c
void fb_set_palette_bulk(const uint32_t *palette, int count);
```
Load multiple palette entries. Each entry is `0x00RRGGBB`.

```c
void fb_set_display_mode(int mode);
```
Switch between `DISPLAY_MODE_TERMINAL` (0) and `DISPLAY_MODE_FRAMEBUFFER` (1).

```c
void fb_clear(uint8_t color);
```
Fill draw buffer with a palette index.

```c
void fb_flush_cache(void);
```
Flush D-cache to ensure draw buffer is visible to video scanout.

```c
static inline void fb_pixel(uint8_t *buf, int x, int y, uint8_t color);
```
Draw a single pixel with bounds checking.

### Constants

| Name | Value | Description |
|------|-------|-------------|
| `FB_WIDTH` | 320 | Framebuffer width in pixels |
| `FB_HEIGHT` | 240 | Framebuffer height in pixels |
| `FB_STRIDE` | 320 | Bytes per row (1 byte/pixel) |
| `FB_SIZE` | 76800 | Total framebuffer size in bytes |
| `FB0_BASE` | `0x10000000` | Framebuffer 0 base address |
| `FB1_BASE` | `0x10100000` | Framebuffer 1 base address |

---

## Audio (`hal/audio.h`)

### PCM Audio

```c
int audio_write_samples(const int16_t *samples, int count);
```
Write stereo sample pairs to the audio FIFO. Each pair is `{left, right}` as signed 16-bit. Returns number of samples written.

```c
int audio_fifo_space(void);
```
Returns number of sample pairs that can be written without blocking.

```c
int audio_fifo_full(void);
```
Returns 1 if FIFO is full.

```c
int audio_fifo_level(void);
```
Returns current FIFO fill level.

---

## Input (`hal/input.h`)

### Types

```c
typedef struct {
    uint32_t buttons;           // Current button state (bitmask)
    uint32_t buttons_pressed;   // Buttons pressed this frame (edge)
    uint32_t buttons_released;  // Buttons released this frame (edge)
    int16_t  joy_lx, joy_ly;    // Left analog stick
    int16_t  joy_rx, joy_ry;    // Right analog stick
    uint16_t trigger_l, trigger_r;  // Analog triggers
} input_state_t;
```

### Functions

```c
void input_init(void);
void input_poll(void);
```
Poll controller registers and compute edge detection.

```c
const input_state_t *input_get(int player);
```
Get input state for player 0 or 1.

```c
static inline int input_pressed(int player, uint32_t mask);
static inline int input_released(int player, uint32_t mask);
static inline int input_held(int player, uint32_t mask);
```
Query button state for a player.

### Button Masks

| Mask | Bit | Button |
|------|-----|--------|
| `BTN_DPAD_UP` | 0 | D-pad up |
| `BTN_DPAD_DOWN` | 1 | D-pad down |
| `BTN_DPAD_LEFT` | 2 | D-pad left |
| `BTN_DPAD_RIGHT` | 3 | D-pad right |
| `BTN_A` | 4 | A |
| `BTN_B` | 5 | B |
| `BTN_X` | 6 | X |
| `BTN_Y` | 7 | Y |
| `BTN_L1` | 8 | L1 |
| `BTN_R1` | 9 | R1 |
| `BTN_L2` | 10 | L2 |
| `BTN_R2` | 11 | R2 |
| `BTN_L3` | 12 | L3 |
| `BTN_R3` | 13 | R3 |
| `BTN_SELECT` | 14 | Select |
| `BTN_START` | 15 | Start |

---

## Save (`hal/save.h`)

Nonvolatile CRAM1 PSRAM-backed save system. The APF bridge persists the save region to SD card. The Chip32 loader creates seed save files and configures the datatable at boot.

Apps should prefer standard C `fopen("save_N")`/`fwrite`/`fclose` — the OS maps these to save slots automatically and flushes with the actual written size on close.

```c
int save_read(int slot, void *buf, uint32_t offset, uint32_t len);
```
Read from save slot (0-9). Uses uncached CRAM1 alias for bridge coherency. Returns bytes read.

```c
int save_write(int slot, const void *buf, uint32_t offset, uint32_t len);
```
Write to save slot. Uses uncached CRAM1 alias. Returns bytes written.

```c
void save_flush(int slot);
```
Flush full slot (256 KB) to SD card via bridge.

```c
int save_flush_size(int slot, uint32_t size);
```
Flush only `size` bytes of the slot. Used by `fclose()` to write the actual data size.

```c
void save_erase(int slot);
```
Fill slot with 0xFF.

```c
uint32_t save_slot_size(int slot);
```
Returns 0x40000 (256 KB per slot).

### Constants

| Name | Value |
|------|-------|
| `SAVE_MAX_SLOTS` | 10 |
| `SAVE_SLOT_SIZE` | 0x40000 (256 KB) |
| `SAVE_REGION_ADDR` | 0x39000000 (CRAM1 uncached) |

---

## Data Slots (`hal/dataslot.h`)

Low-level APF bridge file I/O via DMA.

```c
int dataslot_read(uint32_t slot_id, uint32_t slot_offset,
                  void *dest, uint32_t length);
```
DMA read from a data slot to SDRAM. Handles fence and timeout.

```c
int dataslot_write(uint32_t slot_id, uint32_t slot_offset,
                   const void *src, uint32_t length);
```
DMA write from SDRAM to a data slot.

```c
int dataslot_read_chunked(uint32_t slot_id, uint32_t slot_offset,
                          void *dest, uint32_t total);
```
Read large files in DMA_CHUNK_SIZE (512KB) chunks.

```c
int dataslot_open_file(uint32_t slot_id, const char *filename,
                       uint32_t flags, uint32_t size);
```
Open a named file within a data slot. Returns file size on success.

### Error Codes

| Code | Meaning |
|------|---------|
| `DATASLOT_ERR_TIMEOUT` (-100) | DMA operation timed out |
| `DATASLOT_ERR_PARAM` (-101) | Invalid parameter (address out of range) |

---

## Analogizer (`hal/analogizer.h`)

Reads Analogizer adapter configuration from bridge-synced registers. The Analogizer is configured by the user through the Pocket's interact menu; the OS reads the current state.

```c
int analogizer_is_enabled(void);
```
Returns 1 if Analogizer adapter is detected.

```c
int analogizer_get_video_mode(void);
```
Returns video output mode (RGBS, YPbPr, composite, scandoubler, etc.).

```c
int analogizer_get_snac_type(void);
```
Returns SNAC controller type (NES, SNES, PSX, PCEngine, DB15, etc.).

```c
const analogizer_state_t *analogizer_get_state(void);
```
Returns full state struct.

### SNAC Controller Types

| Define | Value | Controller |
|--------|-------|------------|
| `SNAC_NONE` | 0x00 | No controller |
| `SNAC_DB15` | 0x01 | DB15 (Neo Geo style) |
| `SNAC_NES` | 0x02 | NES |
| `SNAC_SNES` | 0x03 | SNES |
| `SNAC_PCE_2BTN` | 0x04 | PC Engine 2-button |
| `SNAC_PCE_6BTN` | 0x05 | PC Engine 6-button |
| `SNAC_PSX` | 0x10 | PlayStation digital |
| `SNAC_PSX_ANALOG` | 0x12 | PlayStation DualShock |

### Video Output Modes

| Define | Value | Mode |
|--------|-------|------|
| `ANLG_VIDEO_RGBS` | 0x0 | RGB + sync |
| `ANLG_VIDEO_YPBPR` | 0x2 | Component YPbPr |
| `ANLG_VIDEO_YC_NTSC` | 0x3 | S-Video NTSC |
| `ANLG_VIDEO_YC_PAL` | 0x4 | S-Video PAL |
| `ANLG_VIDEO_SC_0PCT` | 0x5 | Scandoubler (no scanlines) |
| `ANLG_VIDEO_SC_50PCT` | 0x6 | Scandoubler (50% scanlines) |
| `ANLG_VIDEO_SC_HQ2X` | 0x7 | Scandoubler (HQ2X) |

Add `ANLG_VIDEO_POCKET_OFF` (0x8) to any mode to disable the Pocket screen.

---

## Terminal (`hal/terminal.h`)

40x30 character text terminal rendered by FPGA hardware. Characters are written to VRAM at 0x20000000.

```c
void term_init(void);
void term_clear(void);
void term_putchar(char c);       // Handles \n, \r, \b
void term_puts(const char *s);
void term_printf(const char *fmt, ...);  // %d %u %x %X %s %c %p
void term_set_pos(int col, int row);
void term_get_pos(int *col, int *row);
void term_scroll(void);
```

---

## Timer (`hal/timer.h`)

Hardware cycle counter at 100 MHz.

```c
uint32_t timer_get_us(void);     // Microseconds since boot
uint32_t timer_get_ms(void);     // Milliseconds since boot
uint64_t timer_get_cycles(void); // Raw cycle count
uint32_t timer_get_seconds(uint32_t *ns_out);  // Seconds + nanosecond remainder
void timer_delay_us(uint32_t us);  // Busy-wait
void timer_delay_ms(uint32_t ms);  // Busy-wait
```

---

## Cache (`hal/cache.h`)

```c
void cache_flush_dcache(void);      // Evict all D-cache dirty lines
void cache_invalidate_icache(void); // fence.i
void cache_flush_all(void);         // Both
```

D-cache flush works by conflict eviction: reading 32KB from the top of SDRAM, which evicts all dirty lines from the 32KB direct-mapped cache.

---

## Link Cable (`hal/link.h`)

GB/GBC-compatible link cable at 256 kHz, full-duplex serial.

```c
int link_send(uint32_t data);
int link_recv(uint32_t *data);
uint32_t link_status(void);
void link_flush(void);
```
