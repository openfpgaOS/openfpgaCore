/*
 * SDL2 shim for openfpgaOS
 *
 * Minimal SDL2 implementation using of_* syscalls.
 * On PC builds, this header is never used — the real SDL2 is linked.
 * Covers the subset of SDL2 that game ports typically need:
 *   video (8-bit indexed surface), input, audio, timer.
 */

#ifndef _OF_SDL2_SHIM_H
#define _OF_SDL2_SHIM_H

#ifdef OF_PC
#include_next <SDL2/SDL.h>
#else

#include "of.h"

#include <stdint.h>
#include <string.h>

/* ======================================================================
 * Constants
 * ====================================================================== */

#define SDL_INIT_VIDEO          0x00000020
#define SDL_INIT_AUDIO          0x00000010
#define SDL_INIT_TIMER          0x00000001
#define SDL_INIT_EVENTS         0x00004000
#define SDL_INIT_GAMECONTROLLER 0x00002000
#define SDL_INIT_EVERYTHING     0x0000FFFF

#define SDL_WINDOW_SHOWN        0x00000004
#define SDL_WINDOW_RESIZABLE    0x00000020
#define SDL_WINDOWPOS_CENTERED  0x2FFF0000
#define SDL_WINDOWPOS_UNDEFINED 0x1FFF0000

#define SDL_RENDERER_ACCELERATED    0x00000002
#define SDL_RENDERER_PRESENTVSYNC   0x00000004

#define SDL_PIXELFORMAT_ARGB8888    0x16362004
#define SDL_PIXELFORMAT_RGB888      0x16161804
#define SDL_PIXELFORMAT_INDEX8      0x13000001
#define SDL_TEXTUREACCESS_STREAMING 1
#define SDL_TEXTUREACCESS_TARGET    2

#define SDL_QUIT            0x100
#define SDL_KEYDOWN         0x300
#define SDL_KEYUP           0x301
#define SDL_CONTROLLERBUTTONDOWN  0x650
#define SDL_CONTROLLERBUTTONUP    0x651
#define SDL_CONTROLLERDEVICEADDED 0x654

#define AUDIO_S16SYS    0x8010
#define AUDIO_S16       0x8010
#define AUDIO_F32SYS    0x8120

#ifndef SDL_bool
#define SDL_bool int
#define SDL_FALSE 0
#define SDL_TRUE 1
#endif

/* ======================================================================
 * Types
 * ====================================================================== */

typedef struct { int x, y, w, h; } SDL_Rect;

typedef struct { uint8_t r, g, b, a; } SDL_Color;

typedef struct {
    int ncolors;
    SDL_Color colors[256];
} SDL_Palette;

typedef struct {
    uint32_t format;
    SDL_Palette *palette;
    uint8_t BitsPerPixel;
    uint8_t BytesPerPixel;
} SDL_PixelFormat;

typedef struct {
    uint32_t flags;
    SDL_PixelFormat *format;
    int w, h;
    int pitch;
    void *pixels;
} SDL_Surface;

typedef struct { int unused; } SDL_Window;
typedef struct { int unused; } SDL_Renderer;
typedef struct { int unused; } SDL_Texture;

typedef enum {
    SDL_SCANCODE_UNKNOWN = 0,
    SDL_SCANCODE_A = 4, SDL_SCANCODE_B, SDL_SCANCODE_C, SDL_SCANCODE_D,
    SDL_SCANCODE_E, SDL_SCANCODE_F, SDL_SCANCODE_G, SDL_SCANCODE_H,
    SDL_SCANCODE_I, SDL_SCANCODE_J, SDL_SCANCODE_K, SDL_SCANCODE_L,
    SDL_SCANCODE_M, SDL_SCANCODE_N, SDL_SCANCODE_O, SDL_SCANCODE_P,
    SDL_SCANCODE_Q, SDL_SCANCODE_R, SDL_SCANCODE_S, SDL_SCANCODE_T,
    SDL_SCANCODE_U, SDL_SCANCODE_V, SDL_SCANCODE_W, SDL_SCANCODE_X,
    SDL_SCANCODE_Y, SDL_SCANCODE_Z,
    SDL_SCANCODE_1 = 30, SDL_SCANCODE_2, SDL_SCANCODE_3, SDL_SCANCODE_4,
    SDL_SCANCODE_5, SDL_SCANCODE_6, SDL_SCANCODE_7, SDL_SCANCODE_8,
    SDL_SCANCODE_9, SDL_SCANCODE_0,
    SDL_SCANCODE_RETURN = 40,
    SDL_SCANCODE_ESCAPE = 41,
    SDL_SCANCODE_BACKSPACE = 42,
    SDL_SCANCODE_TAB = 43,
    SDL_SCANCODE_SPACE = 44,
    SDL_SCANCODE_F1 = 58, SDL_SCANCODE_F2, SDL_SCANCODE_F3, SDL_SCANCODE_F4,
    SDL_SCANCODE_F5, SDL_SCANCODE_F6, SDL_SCANCODE_F7, SDL_SCANCODE_F8,
    SDL_SCANCODE_F9, SDL_SCANCODE_F10, SDL_SCANCODE_F11, SDL_SCANCODE_F12,
    SDL_SCANCODE_RIGHT = 79,
    SDL_SCANCODE_LEFT = 80,
    SDL_SCANCODE_DOWN = 81,
    SDL_SCANCODE_UP = 82,
    SDL_SCANCODE_LCTRL = 224,
    SDL_SCANCODE_LSHIFT = 225,
    SDL_SCANCODE_LALT = 226,
    SDL_SCANCODE_RCTRL = 228,
    SDL_SCANCODE_RSHIFT = 229,
    SDL_SCANCODE_RALT = 230,
} SDL_Scancode;

typedef struct {
    SDL_Scancode scancode;
    int sym;
    uint16_t mod;
} SDL_Keysym;

typedef struct {
    uint32_t type;
    struct { uint32_t type; uint8_t repeat; SDL_Keysym keysym; } key;
} SDL_Event;

typedef uint32_t SDL_AudioDeviceID;
typedef void (*SDL_AudioCallback)(void *userdata, uint8_t *stream, int len);

typedef struct {
    int freq;
    uint16_t format;
    uint8_t channels;
    uint8_t silence;
    uint16_t samples;
    uint32_t size;
    SDL_AudioCallback callback;
    void *userdata;
} SDL_AudioSpec;

typedef void *SDL_mutex;

/* ======================================================================
 * Internal state
 * ====================================================================== */

static SDL_Window       __sdl_win;
static SDL_Renderer     __sdl_ren;
static SDL_Texture      __sdl_tex;
static SDL_Palette      __sdl_palette;
static SDL_PixelFormat  __sdl_pixfmt;
static SDL_Surface      __sdl_surface;
static int              __sdl_surface_ready;

static of_input_state_t __sdl_prev_input;
static of_input_state_t __sdl_curr_input;
static int              __sdl_events_pending;
static uint32_t         __sdl_pressed;
static uint32_t         __sdl_released;
static int              __sdl_event_bit;

static SDL_AudioCallback __sdl_audio_cb;
static void             *__sdl_audio_userdata;

/* ======================================================================
 * Init / Quit
 * ====================================================================== */

static inline int SDL_Init(uint32_t flags) { (void)flags; return 0; }
static inline void SDL_Quit(void) {}
static inline const char *SDL_GetError(void) { return ""; }

/* ======================================================================
 * Video — Window / Surface / Palette
 * ====================================================================== */

static inline SDL_Window *SDL_CreateWindow(const char *title, int x, int y,
                                            int w, int h, uint32_t flags) {
    (void)title; (void)x; (void)y; (void)w; (void)h; (void)flags;
    of_video_init();
    return &__sdl_win;
}

static inline void SDL_DestroyWindow(SDL_Window *w) { (void)w; }

/* Get the 8-bit indexed framebuffer surface */
static inline SDL_Surface *SDL_GetWindowSurface(SDL_Window *w) {
    (void)w;
    if (!__sdl_surface_ready) {
        __sdl_palette.ncolors = 256;
        memset(__sdl_palette.colors, 0, sizeof(__sdl_palette.colors));

        __sdl_pixfmt.format = SDL_PIXELFORMAT_INDEX8;
        __sdl_pixfmt.palette = &__sdl_palette;
        __sdl_pixfmt.BitsPerPixel = 8;
        __sdl_pixfmt.BytesPerPixel = 1;

        __sdl_surface.format = &__sdl_pixfmt;
        __sdl_surface.w = OF_SCREEN_W;
        __sdl_surface.h = OF_SCREEN_H;
        __sdl_surface.pitch = OF_SCREEN_W;
        __sdl_surface.pixels = of_video_surface();
        __sdl_surface_ready = 1;
    }
    /* Update pixels pointer (may change after flip) */
    __sdl_surface.pixels = of_video_surface();
    return &__sdl_surface;
}

/* Flip the framebuffer (present the surface) */
static inline int SDL_UpdateWindowSurface(SDL_Window *w) {
    (void)w;
    of_video_flip();
    return 0;
}

/* Set palette colors — syncs to hardware palette */
static inline int SDL_SetPaletteColors(SDL_Palette *palette,
                                        const SDL_Color *colors,
                                        int first, int ncolors) {
    for (int i = 0; i < ncolors && (first + i) < 256; i++) {
        int idx = first + i;
        palette->colors[idx] = colors[i];
        uint32_t rgb = ((uint32_t)colors[i].r << 16) |
                       ((uint32_t)colors[i].g << 8) |
                       (uint32_t)colors[i].b;
        of_video_palette((uint8_t)idx, rgb);
    }
    return 0;
}

/* Set surface palette (convenience) */
static inline int SDL_SetSurfacePalette(SDL_Surface *surface, SDL_Palette *palette) {
    surface->format->palette = palette;
    return 0;
}

/* Allocate a palette */
static inline SDL_Palette *SDL_AllocPalette(int ncolors) {
    (void)ncolors;
    return &__sdl_palette;
}

static inline void SDL_FreePalette(SDL_Palette *p) { (void)p; }

/* Fill rect on surface */
static inline int SDL_FillRect(SDL_Surface *dst, const SDL_Rect *rect, uint32_t color) {
    uint8_t c = (uint8_t)color;
    if (!rect) {
        memset(dst->pixels, c, dst->w * dst->h);
    } else {
        uint8_t *p = (uint8_t *)dst->pixels;
        int x0 = rect->x < 0 ? 0 : rect->x;
        int y0 = rect->y < 0 ? 0 : rect->y;
        int x1 = rect->x + rect->w; if (x1 > dst->w) x1 = dst->w;
        int y1 = rect->y + rect->h; if (y1 > dst->h) y1 = dst->h;
        for (int y = y0; y < y1; y++)
            for (int x = x0; x < x1; x++)
                p[y * dst->pitch + x] = c;
    }
    return 0;
}

static inline uint32_t SDL_MapRGB(const SDL_PixelFormat *fmt, uint8_t r, uint8_t g, uint8_t b) {
    (void)fmt;
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

/* ======================================================================
 * Video — Renderer / Texture (for games that use this path)
 * ====================================================================== */

static inline SDL_Renderer *SDL_CreateRenderer(SDL_Window *w, int index,
                                                uint32_t flags) {
    (void)w; (void)index; (void)flags;
    return &__sdl_ren;
}

static inline void SDL_DestroyRenderer(SDL_Renderer *r) { (void)r; }

static inline int SDL_RenderSetLogicalSize(SDL_Renderer *r, int w, int h) {
    (void)r; (void)w; (void)h; return 0;
}

static inline SDL_Texture *SDL_CreateTexture(SDL_Renderer *r, uint32_t format,
                                              int access, int w, int h) {
    (void)r; (void)format; (void)access; (void)w; (void)h;
    return &__sdl_tex;
}

static inline void SDL_DestroyTexture(SDL_Texture *t) { (void)t; }

static inline int SDL_UpdateTexture(SDL_Texture *t, const SDL_Rect *rect,
                                     const void *pixels, int pitch) {
    (void)t; (void)rect; (void)pixels; (void)pitch;
    return 0;
}

static inline int SDL_RenderClear(SDL_Renderer *r) {
    (void)r; of_video_clear(0); return 0;
}

static inline int SDL_RenderCopy(SDL_Renderer *r, SDL_Texture *t,
                                  const SDL_Rect *src, const SDL_Rect *dst) {
    (void)r; (void)t; (void)src; (void)dst; return 0;
}

static inline void SDL_RenderPresent(SDL_Renderer *r) {
    (void)r; of_video_flip();
}

/* ======================================================================
 * Events / Input
 * ====================================================================== */

static inline SDL_Scancode __sdl_btn_to_scancode(int bit) {
    switch (1 << bit) {
    case OF_BTN_UP:     return SDL_SCANCODE_UP;
    case OF_BTN_DOWN:   return SDL_SCANCODE_DOWN;
    case OF_BTN_LEFT:   return SDL_SCANCODE_LEFT;
    case OF_BTN_RIGHT:  return SDL_SCANCODE_RIGHT;
    case OF_BTN_A:      return SDL_SCANCODE_Z;
    case OF_BTN_B:      return SDL_SCANCODE_X;
    case OF_BTN_X:      return SDL_SCANCODE_A;
    case OF_BTN_Y:      return SDL_SCANCODE_S;
    case OF_BTN_L1:     return SDL_SCANCODE_Q;
    case OF_BTN_R1:     return SDL_SCANCODE_W;
    case OF_BTN_SELECT: return SDL_SCANCODE_RSHIFT;
    case OF_BTN_START:  return SDL_SCANCODE_RETURN;
    default:            return SDL_SCANCODE_UNKNOWN;
    }
}

static inline int SDL_PollEvent(SDL_Event *event) {
    if (!__sdl_events_pending) {
        of_input_poll();
        of_input_state(0, &__sdl_curr_input);
        __sdl_pressed = __sdl_curr_input.buttons & ~__sdl_prev_input.buttons;
        __sdl_released = ~__sdl_curr_input.buttons & __sdl_prev_input.buttons;
        __sdl_prev_input = __sdl_curr_input;
        __sdl_events_pending = 1;
        __sdl_event_bit = 0;
    }

    while (__sdl_event_bit < 16) {
        uint32_t mask = 1u << __sdl_event_bit;
        __sdl_event_bit++;
        if (__sdl_pressed & mask) {
            event->type = SDL_KEYDOWN;
            event->key.type = SDL_KEYDOWN;
            event->key.repeat = 0;
            event->key.keysym.scancode = __sdl_btn_to_scancode(__sdl_event_bit - 1);
            event->key.keysym.sym = 0;
            event->key.keysym.mod = 0;
            return 1;
        }
        if (__sdl_released & mask) {
            event->type = SDL_KEYUP;
            event->key.type = SDL_KEYUP;
            event->key.repeat = 0;
            event->key.keysym.scancode = __sdl_btn_to_scancode(__sdl_event_bit - 1);
            event->key.keysym.sym = 0;
            event->key.keysym.mod = 0;
            return 1;
        }
    }

    __sdl_events_pending = 0;
    return 0;
}

static inline const uint8_t *SDL_GetKeyboardState(int *numkeys) {
    static uint8_t dummy[256];
    if (numkeys) *numkeys = 256;
    return dummy;
}

/* ======================================================================
 * Timer
 * ====================================================================== */

static inline uint32_t SDL_GetTicks(void) { return of_time_ms(); }
static inline void SDL_Delay(uint32_t ms) { of_delay_ms(ms); }

/* ======================================================================
 * Audio
 * ====================================================================== */

static inline SDL_AudioDeviceID SDL_OpenAudioDevice(const char *device, int iscapture,
                                                      const SDL_AudioSpec *desired,
                                                      SDL_AudioSpec *obtained,
                                                      int allowed_changes) {
    (void)device; (void)iscapture; (void)allowed_changes;
    of_audio_init();
    if (desired->callback) {
        __sdl_audio_cb = desired->callback;
        __sdl_audio_userdata = desired->userdata;
    }
    if (obtained) *obtained = *desired;
    return 1;
}

static inline void SDL_CloseAudioDevice(SDL_AudioDeviceID dev) {
    (void)dev; __sdl_audio_cb = 0;
}

static inline void SDL_PauseAudioDevice(SDL_AudioDeviceID dev, int pause) {
    (void)dev; (void)pause;
}

/* Pump audio callback — call from game loop */
static inline void SDL_AudioPump(void) {
    if (__sdl_audio_cb) {
        int free_pairs = of_audio_free();
        if (free_pairs > 256) free_pairs = 256;
        if (free_pairs > 0) {
            int16_t buf[512];
            __sdl_audio_cb(__sdl_audio_userdata, (uint8_t *)buf, free_pairs * 4);
            of_audio_write(buf, free_pairs);
        }
    }
}

static inline int SDL_QueueAudio(SDL_AudioDeviceID dev, const void *data, uint32_t len) {
    (void)dev;
    of_audio_write((const int16_t *)data, (int)(len / 4));
    return 0;
}

/* Load a WAV file into memory.
 * src/freesrc are ignored — file is loaded via fopen/fread.
 * Returns spec and sets *audio_buf / *audio_len.
 * Caller must SDL_FreeWAV() the buffer when done. */
static inline SDL_AudioSpec *SDL_LoadWAV_RW(void *src, int freesrc,
                                             SDL_AudioSpec *spec,
                                             uint8_t **audio_buf,
                                             uint32_t *audio_len) {
    (void)src; (void)freesrc;
    *audio_buf = 0;
    *audio_len = 0;
    return 0;
}

static inline SDL_AudioSpec *SDL_LoadWAV(const char *file,
                                          SDL_AudioSpec *spec,
                                          uint8_t **audio_buf,
                                          uint32_t *audio_len) {
    *audio_buf = 0;
    *audio_len = 0;

    /* Load entire file into memory */
    FILE *f = fopen(file, "rb");
    if (!f) return 0;

    /* Get file size */
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    if (size <= 0 || size > 4 * 1024 * 1024) { fclose(f); return 0; }
    fseek(f, 0, SEEK_SET);

    uint8_t *data = (uint8_t *)malloc((size_t)size);
    if (!data) { fclose(f); return 0; }

    size_t got = fread(data, 1, (size_t)size, f);
    fclose(f);
    if ((long)got != size) { free(data); return 0; }

    /* Parse WAV header */
    of_codec_result_t result;
    if (of_codec_parse_wav(data, (uint32_t)size, &result) < 0) {
        free(data);
        return 0;
    }

    /* Fill SDL_AudioSpec */
    spec->freq = (int)result.sample_rate;
    spec->format = (result.bits_per_sample == 16) ? AUDIO_S16SYS : 0x0008;
    spec->channels = result.channels;
    spec->silence = 0;
    spec->samples = 4096;
    spec->size = result.pcm_len;
    spec->callback = 0;
    spec->userdata = 0;

    /* Copy PCM data to a new buffer (original file buffer will be freed) */
    uint8_t *pcm_copy = (uint8_t *)malloc(result.pcm_len);
    if (!pcm_copy) { free(data); return 0; }
    memcpy(pcm_copy, result.pcm, result.pcm_len);
    free(data);

    *audio_buf = pcm_copy;
    *audio_len = result.pcm_len;
    return spec;
}

static inline void SDL_FreeWAV(uint8_t *audio_buf) {
    free(audio_buf);
}

/* Mix audio: add src into dst with volume (0-128).
 * Assumes AUDIO_S16SYS format. Clamps to int16 range. */
static inline void SDL_MixAudioFormat(uint8_t *dst, const uint8_t *src,
                                       uint16_t format, uint32_t len,
                                       int volume) {
    (void)format;  /* assumes AUDIO_S16SYS */
    const int16_t *s = (const int16_t *)src;
    int16_t *d = (int16_t *)dst;
    uint32_t samples = len / 2;

    for (uint32_t i = 0; i < samples; i++) {
        int32_t mixed = (int32_t)d[i] + (((int32_t)s[i] * volume) >> 7);
        if (mixed > 32767) mixed = 32767;
        if (mixed < -32768) mixed = -32768;
        d[i] = (int16_t)mixed;
    }
}

#define SDL_MIX_MAXVOLUME 128

/* ======================================================================
 * Misc stubs
 * ====================================================================== */

static inline SDL_mutex *SDL_CreateMutex(void) { return (SDL_mutex *)1; }
static inline void SDL_DestroyMutex(SDL_mutex *m) { (void)m; }
static inline int SDL_LockMutex(SDL_mutex *m) { (void)m; return 0; }
static inline int SDL_UnlockMutex(SDL_mutex *m) { (void)m; return 0; }
static inline void *SDL_GameControllerOpen(int idx) { (void)idx; return 0; }

#endif /* OF_PC */
#endif /* _OF_SDL2_SHIM_H */
