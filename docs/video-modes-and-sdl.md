# Video Modes And SDL

## Current hardware contract

The Pocket target currently exposes one app framebuffer mode:

- 320x240, 8-bit indexed, stride 320
- triple buffered at `FB0_BASE`, `FB1_BASE`, `FB2_BASE`
- one framebuffer is `76,800` bytes
- `of_get_caps()->fb_width`, `fb_height`, and `fb_stride` report this mode

The scanout timing is 640 pixels wide but only 240 active lines. The scanout
module presents the 320x240 framebuffer by horizontally doubling each source
pixel. That means a true native 640x480 framebuffer is not just an SDK change:
the scanout module and timing path must learn a new framebuffer geometry.

## What SDL does

SDL separates the app's drawable size from the backend's physical display
path.

- `SDL_CreateWindow(..., w, h, ...)` records the requested logical window size.
- `SDL_GetWindowSurface(window)` returns a surface with that drawable size.
- `SDL_UpdateWindowSurface(window)` presents that surface through the backend.
- If the backend cannot expose the requested size directly, it copies or scales
  the logical surface to the real framebuffer.
- Real SDL also exposes display modes through `SDL_GetNumDisplayModes`,
  `SDL_GetDisplayMode`, and fullscreen display mode APIs.

The openfpgaOS SDL shim now follows the useful part of that model: when an app
requests a logical size such as 640x480, the shim allocates a 640x480 software
surface and scales it into the current OS framebuffer on
`SDL_UpdateWindowSurface()` / `SDL_Flip()`. When the requested size matches the
OS framebuffer, the surface points directly at the hardware draw buffer.

This gives ports like Diablo a correct SDL surface immediately, while keeping
the current 320x240 scanout hardware unchanged.

## Native framebuffer mode API

The OS-level API should be explicit about framebuffer geometry instead of
overloading `of_video_init()`.

Suggested SDK shape:

```c
typedef enum {
    OF_VIDEO_SCALE_NONE = 0,
    OF_VIDEO_SCALE_INTEGER,
    OF_VIDEO_SCALE_FIT,
    OF_VIDEO_SCALE_CENTER,
} of_video_scale_t;

typedef struct {
    uint16_t width;
    uint16_t height;
    uint16_t stride;
    uint8_t  color_mode;   /* OF_VIDEO_MODE_* */
    uint8_t  scale;        /* of_video_scale_t */
} of_video_mode_t;

int of_video_set_mode(const of_video_mode_t *mode);
int of_video_get_mode(of_video_mode_t *out);
int of_video_get_modes(of_video_mode_t *out, int max_modes);
```

Convenience wrappers can cover common modes:

```c
of_video_set_mode(&(of_video_mode_t){
    .width = 640,
    .height = 480,
    .stride = 640,
    .color_mode = OF_VIDEO_MODE_8BIT,
    .scale = OF_VIDEO_SCALE_FIT,
});
```

## Common modes worth exposing first

Start with modes that fit the current 1 MB spacing between framebuffer bases:

| Mode | 8-bit size | 16-bit size | Notes |
| --- | ---: | ---: | --- |
| 256x224 | 57,344 | 114,688 | common console |
| 256x240 | 61,440 | 122,880 | common console |
| 320x200 | 64,000 | 128,000 | DOS |
| 320x240 | 76,800 | 153,600 | current native app mode |
| 400x240 | 96,000 | 192,000 | wide low-res |
| 512x240 | 122,880 | 245,760 | wide low-res |
| 640x400 | 256,000 | 512,000 | DOS/VGA |
| 640x480 | 307,200 | 614,400 | VGA, Diablo-style ports |
| 800x600 | 480,000 | 960,000 | fits, little guard at 16-bit |

Do not expose a 16-bit mode above 800x600 without moving the framebuffer bases
farther apart.

## RTL work for native 640x480

Native 640x480 requires these hardware changes:

1. Add framebuffer mode registers in `axi_periph_slave.v`.
   - width
   - height
   - stride in bytes
   - color mode
   - scale policy or source-to-output step values

2. Feed those registers into `video_CRT_scanout_indexed_BRAM.v`.
   - Replace fixed 320-wide line address math.
   - Replace fixed 240-line fetch limits.
   - Use `stride` for `line_offset = y * stride`.

3. Increase the scanout line cache if 640-wide 16-bit modes are supported.
   - Current cache is four banks of 256 32-bit words.
   - 640x8-bit needs 160 words per line and fits.
   - 640x16-bit needs 320 words per line and does not fit.

4. Decide whether 640x480 means:
   - logical 640x480 scaled into the existing 640x240 output, or
   - true progressive 640x480 output timing.

The second option needs a separate video timing mode and likely a different
pixel clock. The current timing path is 240 active lines, so it cannot show 480
native progressive lines without RTL timing changes.

## OS and SDK work

After the RTL registers exist:

1. Change `FB_SIZE` usage in the OS video HAL from compile-time
   `FB_WIDTH * FB_HEIGHT` to the active mode's `frame_bytes`.
2. Flush and clear exactly `frame_bytes`.
3. Update `caps` or add `of_video_get_mode()` so apps can query the active
   runtime mode.
4. Update `of_video_surface()`, `of_video_buffer_addr()`, and GPU helpers to
   use the active stride.
5. Make the SDL shim call `of_video_set_mode()` before falling back to a
   software-scaled surface.

