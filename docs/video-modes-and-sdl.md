# Video Modes And SDL

## Hardware contract

The Pocket target boots in the legacy app framebuffer mode:

- 320x240, 8-bit indexed, stride 320
- triple buffered at `FB0_BASE`, `FB1_BASE`, `FB2_BASE`
- one framebuffer is `76,800` bytes
- `of_get_caps()->fb_width`, `fb_height`, and `fb_stride` report this boot mode

The default scanout timing is 320 pixels wide by 240 active lines. The scanout
module now reads the active framebuffer width, height, stride, and color mode
from sysregs and scales that source framebuffer into the selected Pocket scaler
slot. This supports dynamic source framebuffers such as 320x288, 640x480, and
800x600 by switching to the matching physical scaler slot when one exists.

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
requests a logical size such as 640x480, the shim first asks the OS to make
that the native framebuffer mode. If the mode is unsupported, it allocates a
software surface and scales it into the current OS framebuffer on
`SDL_UpdateWindowSurface()` / `SDL_Flip()`. When the requested size matches the
OS framebuffer, the surface points directly at the hardware draw buffer.

## Native framebuffer mode API

The OS-level API should be explicit about framebuffer geometry instead of
overloading `of_video_init()`.

SDK shape:

```c
typedef struct {
    uint16_t width;
    uint16_t height;
    uint16_t stride;
    uint8_t  color_mode;   /* OF_VIDEO_MODE_* */
    uint8_t  reserved;
} of_video_mode_t;

int of_video_set_mode(const of_video_mode_t *mode);
void of_video_get_mode(of_video_mode_t *out);
int of_video_get_mode_count(void);
int of_video_get_mode_info(int index, of_video_mode_t *out);
```

Convenience wrappers can cover common modes:

```c
of_video_set_mode(&(of_video_mode_t){
    .width = 640,
    .height = 480,
    .stride = 0,                 /* auto */
    .color_mode = OF_VIDEO_MODE_8BIT,
});
```

## Common modes worth exposing first

Start with modes that fit the current 1 MB spacing between framebuffer bases:

| Mode | 8-bit size | 16-bit size | Notes |
| --- | ---: | ---: | --- |
| 256x224 | 57,344 | 114,688 | common console |
| 256x240 | 61,440 | 122,880 | common console |
| 320x200 | 64,000 | 128,000 | DOS |
| 320x288 | 92,160 | 184,320 | PAL-height low-res |
| 320x240 | 76,800 | 153,600 | current native app mode |
| 400x240 | 96,000 | 192,000 | wide low-res |
| 512x240 | 122,880 | 245,760 | wide low-res |
| 640x400 | 256,000 | 512,000 | DOS/VGA |
| 640x480 | 307,200 | 614,400 | VGA, Diablo-style ports |
| 800x600 | 480,000 | 960,000 | fits, little guard at 16-bit |

Do not expose a 16-bit mode above 800x600 without moving the framebuffer bases
farther apart.

## Implementation notes

- `FB_MODE_SIZE` at `0x400000E4` packs width in bits `[15:0]` and height in
  bits `[31:16]`.
- `FB_MODE_STRIDE` at `0x400000E8` is the source row stride in bytes.
- Hardware clamps to 800x600 and a 2048-byte stride.
- The scanout line cache is four 512-word banks, enough for 800-wide 16-bit
  source lines.
- `of_video_get_mode()` is the runtime query. The capability table stays a
  boot-time descriptor for compatibility.
