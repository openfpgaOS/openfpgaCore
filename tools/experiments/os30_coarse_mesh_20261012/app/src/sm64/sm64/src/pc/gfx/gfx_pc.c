#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <assert.h>
#if defined(OF_DBG_SHADE) || defined(OF_DBG_FACE)
#include <stdio.h>
#endif

#ifndef _LANGUAGE_C
#define _LANGUAGE_C
#endif
#include <PR/gbi.h>
#ifndef TARGET_OPENFPGA
#define SM64_PERF_SCOPE(bucket) ((void)0)
#define SM64_PERF_COUNT(route,count) ((void)0)
#define SM64_PERF_TOP_SCOPE(bucket) ((void)0)
#endif
#include "gfx_pc.h"
#include "gfx_cc.h"
#include "gfx_window_manager_api.h"
#include "gfx_rendering_api.h"
#include "gfx_screen_config.h"

#include "pc/configfile.h"

#ifdef TARGET_OPENFPGA
#include "gfx_gpu.h"   /* GPU vertex-cache fast path (0x56/0x54) entry points */
/* The shipping single-issue os30 uses CPU geometry. Make the accelerator
 * predicate constant in this translation unit so its unused deferred state
 * and dispatch checks can disappear. Define this to 1 in BOTH graphics
 * translation units to restore the optional accelerator renderer. */
#ifndef GFX_GPU_GEOMETRY_WORKER_APP
#define GFX_GPU_GEOMETRY_WORKER_APP 0
#endif
#if !GFX_GPU_GEOMETRY_WORKER_APP
#define gfx_gpu_geometry_enabled() 0
#endif
#include "pocket/perf_pocket.h"
#include "game/area.h"
#include "level_table.h"
#endif

/* PROFILE=1 enables the detailed counters and UART report. Keep per-triangle
 * timer reads out of the normal build: they contend with GPU bus traffic. */
#if defined(TARGET_OPENFPGA) && SM64_PROFILE && !defined(TARGET_POCKET)
#define TARGET_POCKET
#endif

#ifdef TARGET_POCKET
extern int printf(const char *fmt, ...);   /* musl printf -> stdout (terminal/UART) */
void gpu_ring_prof_get(unsigned *spins, unsigned *min_free);  /* gfx_gpu.c: GPU ring backpressure */
void gpu_present_prof_get(unsigned *flip, unsigned *acq);      /* gfx_gpu.c: flip submit vs buffer-acquire wait */
void gpu_vc_prof_get(unsigned *words, unsigned *ctris, unsigned *ltris,
                     unsigned *loads);                          /* gfx_gpu.c: vertex-cache wire counters */
#ifdef OF_PC
/* Host desktop build (OF_PC): the 0x40000004 PROF MMIO counter is unmapped (it
 * would segfault), so source the per-frame profiling brackets from the portable
 * microsecond wall-clock instead. The PERF/OTHER lines are cosmetic. */
#include "of_timer.h"
#define PROF_TMR() ((uint32_t)of_time_us())
#define PROF_HZ()  1000000u                 /* of_time_us: 1 tick = 1 us */
#else
#include "of_caps.h"
#define PROF_TMR() (*(volatile uint32_t *)0x40000004)
/* The 0x40000004 counter runs at the CPU clock, so take the rate from the caps
 * descriptor rather than hardcoding it -- this build reports 90 MHz. */
#define PROF_HZ()  (of_get_caps() && of_get_caps()->cpu_freq_hz \
                        ? of_get_caps()->cpu_freq_hz : 90000000u)
#endif
static uint32_t prof_vtx, prof_mtx, prof_emit, prof_tex;  /* per-frame sub-dl cycle buckets */
static uint32_t prof_tex_import;                 /* of prof_tex: import_texture (conversion) only */
static uint32_t prof_prev_t0;                    /* prior frame's _t0, for true frame-to-frame fps */
unsigned g_prof_audio;                           /* pc_main.c: audio pump ticks since the last rendered frame */
static uint32_t g_flush_n, g_flush_sub, g_flush_tris;  /* flush calls / actual submits / tris submitted */
static uint32_t g_fln_comb, g_fln_tex, g_fln_zst, g_fln_vp;  /* flushes forced, by state-change source */
#endif

typedef float rspv_t;
#define RSPV_ONE     1.0f
#define RSPV_ZERO    0.0f
#define RSPV_HALF    0.5f
#define RSPV(f)      (f)
#define RSPV_FROM_INT(i) ((float)(i))
#define RSPV_TO_INT(v)   ((int)(v))
#define RSPV_TO_FLOAT(v) (v)
#define RSPV_MUL(a,b) ((a)*(b))
#define RSPV_RCP(a)    (1.0f/(a))
#define RSPV_DIV(a,b)  ((a)/(b))

#define SUPPORT_CHECK(x) assert(x)

// SCALE_M_N: upscale/downscale M-bit integer to N-bit
/* SCALE_5_8 was ((VAL_) * 0xFF) / 0x1F.  255/31 is not an integer, so (unlike
 * SCALE_4_8/SCALE_3_8 below) the divide could not fold to a multiply, and the
 * container toolchain (GCC 13.2) did NOT strength-reduce it either — it hoisted
 * 31 into a register and emitted a real `div`.  On this VexiiRiscv rv32imafc
 * core an integer divide is ~30+ cycles and non-pipelined, and the three RGBA16
 * channel scalings below sit in the per-texel loop of import_texture_rgba16 /
 * _ci8 / _ci4 (SM64's dominant texture formats) -> ~100 cycles/texel of pure
 * divide, ~1 ms per 32x32 texture import.
 *
 * (VAL_ * 1053) >> 7 is BIT-IDENTICAL to (VAL_ * 255) / 31 for every 5-bit
 * input 0..31 (brute-force verified); max product 31*1053 = 32643, no overflow. */
#define SCALE_5_8(VAL_) (((VAL_) * 1053) >> 7)
#define SCALE_8_5(VAL_) ((((VAL_) + 4) * 0x1F) / 0xFF)
#define SCALE_4_8(VAL_) ((VAL_) * 0x11)
#define SCALE_8_4(VAL_) ((VAL_) / 0x11)
#define SCALE_3_8(VAL_) ((VAL_) * 0x24)
#define SCALE_8_3(VAL_) ((VAL_) / 0x24)

#define SCREEN_WIDTH 320
#define SCREEN_HEIGHT 240
#define HALF_SCREEN_WIDTH (SCREEN_WIDTH / 2)
#define HALF_SCREEN_HEIGHT (SCREEN_HEIGHT / 2)

#define MAX_BUFFERED 256
#define MAX_LIGHTS 2
#define MAX_VERTICES 64

#ifdef TARGET_OPENFPGA
#if GFX_VC_MAX_BUFFERED != MAX_BUFFERED
#error "gfx_vc_true[] (gfx_gpu.h) must cover exactly MAX_BUFFERED buffered tris"
#endif
#endif

// clip triangles for the software rasterizer in advance
#define GFX_MANUAL_CLIPPING 1

#ifdef ENABLE_SOFTRAST
// leave colors as 0-255 floats
#define GFX_DONT_SCALE_COLORS 1
// don't put in fog color
#define GFX_NO_FOG_COLOR 1
// premultiply by W
#define GFX_W_PREMULT 1
#endif

#if defined(GFX_DONT_SCALE_COLORS)
#define GFX_COLOR_ONE 255.f
#define GFX_COLOR_CONVERT(x) ((float)(x))
#else
#define GFX_COLOR_ONE 1.f
#define GFX_COLOR_CONVERT(x) (x / 255.f)
#endif

/* GFX_W_PREMULT premultiplies the interpolated attributes (u, v, fog, colour
 * inputs) by 1/w so a plain affine software rasterizer can interpolate them
 * perspective-correctly and divide by the interpolated 1/w per pixel.
 *
 * The os30 GPU path does NOT want that.  It derives its own perspective planes
 * from raw texels + 1/w in hardware, so gpu_draw_triangles() immediately
 * recovered w = 1.0f/f[3] and multiplied every one of those attributes straight
 * back out again -- a pure round trip costing an fdiv.s plus ~12 fmul.s PER
 * VERTEX (and losing precision on the way).  fdiv.s is non-pipelined on this
 * VexiiRiscv core, so those divides are not free.
 *
 * So on TARGET_OPENFPGA don't premultiply the attributes at all: emit them raw
 * and let gpu_draw_triangles consume them directly.  x/y/z/1-over-w (emitted
 * explicitly below, not via this macro) still carry 1/w -- the GPU genuinely
 * needs NDC and 1/w.  ENABLE_SOFTRAST's rasterizer is not built into this core
 * (PC_SRCS has no gfx_soft.c), so nothing is left that wants premultiplied
 * attributes; the softras spelling is kept for a non-OPENFPGA build.
 *
 * NOTE: the two sides MUST agree.  If GFX_OUT_PROP ever premultiplies again,
 * gpu_draw_triangles has to restore its `w` de-premultiply (and cc_val_rgb its
 * `w` argument) or every colour and texcoord is scaled by 1/w. */
#if defined(GFX_W_PREMULT) && !defined(TARGET_OPENFPGA)
    #define GFX_OUT_PROP(x) RSPV_MUL((x), w_inv)
#else
#define GFX_OUT_PROP(x) (x)
#endif

enum {
    CLIP_NONE   = 0,
    CLIP_NEAR   = 1,
    CLIP_FAR    = 2,
    CLIP_TOP    = 4,
    CLIP_BOTTOM = 8,
    CLIP_RIGHT  = 16,
    CLIP_LEFT   = 32,
    CLIP_ALL    = 63,
};

struct RGBA {
    uint8_t r, g, b, a;
};

struct XYWidthHeight {
    uint16_t x, y, width, height;
};

struct LoadedVertex {
    rspv_t x, y, z, w;
    /* 1/w, computed ONCE here rather than re-divided per triangle.  The N64's
     * RSP transformed each vertex once and then referenced it by index from many
     * triangles; this port has no GPU vertex cache (XFORM_RGB is clear on this
     * bitstream), so triangles carry full vertices -- but the RECIPROCAL at least
     * need not be recomputed.  It was being taken 3x in the backface test and
     * another 3x in gfx_push_triangle, i.e. 6 non-pipelined fdiv.s per triangle
     * for what is one divide per vertex.  Bit-identical: same w, same 1.0f/w. */
    rspv_t w_inv;
    rspv_t u, v;
    struct RGBA color;
    uint8_t clip_rej;
};

struct TextureHashmapNode {
    struct TextureHashmapNode *next;

    const uint8_t *texture_addr;
    const uint8_t *palette_addr;
    uint32_t size_bytes, line_size_bytes;
    uint8_t fmt, siz;

    uint32_t texture_id;
    uint8_t cms, cmt;
    bool linear_filter;
};
static struct {
    struct TextureHashmapNode *hashmap[1024];
    struct TextureHashmapNode pool[512];
    uint32_t pool_pos;
} gfx_texture_cache;

/* Per-tile last-IMPORTED texture signature, for the textures_changed dedup in
 * gfx_update_textures: SM64 re-issues SETTILE/LOADBLOCK per object and re-flags
 * the SAME texture, so the renderer was flushing the batch + re-importing on every
 * re-bind (avgbatch=3).  We skip that when the bound texture is unchanged.
 * Invalidated on cache wrap (below) so a recycled GTex slot is never stale-skipped. */
static const uint8_t *s_tex_sig_addr[2];
static uint8_t  s_tex_sig_fmt[2], s_tex_sig_siz[2];
static uint32_t s_tex_sig_line[2];
static uint32_t s_tex_sig_size[2];
static const uint8_t *s_tex_sig_palette[2];

struct ColorCombiner {
    uint32_t cc_id;
    struct ShaderProgram *prg;
    uint8_t shader_input_mapping[2][4];
    uint8_t num_inputs;
    bool used_textures[2];
};

static struct ColorCombiner color_combiner_pool[64];
static uint8_t color_combiner_pool_size;

static struct RSP {
    rspv_t modelview_matrix_stack[11][4][4];
    uint8_t modelview_matrix_stack_size;

    rspv_t MP_matrix[4][4];
    rspv_t P_matrix[4][4];
    bool matrix_changed;

    Light_t current_lights[MAX_LIGHTS + 1];
    rspv_t current_lights_coeffs[MAX_LIGHTS][3];
    rspv_t current_lookat_coeffs[2][3]; // lookat_x, lookat_y
    uint8_t current_num_lights; // includes ambient light
    bool lights_changed;
    bool lookat_changed;

    uint32_t geometry_mode;
    int16_t fog_mul, fog_offset;

    struct {
        // U0.16
        uint16_t s, t;
    } texture_scaling_factor;

    struct LoadedVertex loaded_vertices[MAX_VERTICES + 4];
} rsp;
#ifdef TARGET_OPENFPGA
static uint32_t vertex_state_epoch=1,lighting_epoch=1;
struct LightingMemo {uint32_t epoch,normal;struct RGBA color;};
static struct LightingMemo lighting_memo[256];

/* The memo key contains only normal XYZ. Explicit byte packing avoids the
 * target compiler's small-memcpy stack round trip; retain native-byte-order
 * behavior on other targets. Lighting and alpha evaluation are unchanged. */
static inline __attribute__((always_inline)) uint32_t gfx_lighting_normal_key(
        const uint8_t *cn) {
#if defined(__BYTE_ORDER__) && defined(__ORDER_LITTLE_ENDIAN__) && \
    __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    return (uint32_t)cn[0] | ((uint32_t)cn[1] << 8) | ((uint32_t)cn[2] << 16);
#else
    uint32_t normal;
    memcpy(&normal, cn, sizeof(normal));
    return normal & 0x00ffffffu;
#endif
}
#endif

static struct RDP {
    const uint8_t *palette;
    struct {
        const uint8_t *addr;
        uint8_t siz;
        uint8_t tile_number;
    } texture_to_load;
    struct {
        const uint8_t *addr;
        uint32_t size_bytes;
    } loaded_texture[2];
    struct {
        uint8_t fmt;
        uint8_t siz;
        uint8_t cms, cmt;
        uint16_t uls, ult, lrs, lrt; // U10.2
        uint32_t line_size_bytes;
    } texture_tile;
    bool textures_changed[2];

    uint32_t other_mode_l, other_mode_h;
    uint32_t combine_mode;

    struct RGBA env_color, prim_color, fog_color, fill_color;
    struct XYWidthHeight viewport, scissor;
    bool viewport_or_scissor_changed;
    void *z_buf_address;
    void *color_image_address;
} rdp;

static struct RenderingState {
    bool depth_test;
    bool depth_mask;
    bool decal_mode;
    bool alpha_blend;
    bool alpha_cvg_sel;
    struct XYWidthHeight viewport, scissor;
    struct ShaderProgram *shader_program;
    struct TextureHashmapNode *textures[2];
} rendering_state;

struct GfxDimensions gfx_current_dimensions;
/* Installed with aspect_ratio at frame start, before any display list. */
static float gfx_vertex_aspect_scale;
static float ratio_x = 1.f;
static float ratio_y = 1.f;
static float inv_ratio_x = 1.f;
static float inv_ratio_y = 1.f;

static bool dropped_frame;

#ifdef OF_DBG_SHADE
/* Bounded SHADE-color probe (PC build): print the per-vertex SHADE RGB for the
 * G_CC_SHADE main-face combiner (1 color input, no texture) for the first few
 * frames only, capped to a few hundred lines. Incremented in gfx_run(). */
static int of_dbg_frame = -1;     /* -1 until first gfx_run() */
static int of_dbg_lines = 0;
#endif

#ifdef TARGET_POCKET
// Per-frame triangle diagnostic counters
static struct {
    int total, clip_reject, cull_reject, clipped, direct;
    int frame;
} tri_diag;
static int vtx_dump_count;  // counts gfx_sp_vertex calls, dump first few
#endif

#ifdef TARGET_OPENFPGA
/* Both representations have the same batch lifetime. The fixed records keep
 * byte colours and original clip positions; they still emit the exact 0x4E
 * fallback packet, never an indexed-triangle substitute. */
static union {
    float legacy[MAX_BUFFERED * (26 * 3)];
    struct gfx_vc_vtx direct[MAX_BUFFERED * 3];
} buf_vbo_storage;
#define buf_vbo (buf_vbo_storage.legacy)
/* The representation selector is consumed synchronously by draw_triangles.
 * Keep dispatch in that backend so every inlined gfx_flush remains small. */
bool gfx_gpu_direct_batch;
#define buf_vbo_direct gfx_gpu_direct_batch
#else
static float buf_vbo[MAX_BUFFERED * (26 * 3)];
#endif
static size_t buf_vbo_len;
static size_t buf_vbo_num_tris;

#ifdef TARGET_OPENFPGA
/* GPU vertex-cache slot map: 1:1 rsp.loaded_vertices[i] <-> GPU slot i for
 * i < 32 (F3DEX2 caps v0+n at 32, so the live G_VTX index range fits the
 * 32-slot cache exactly; the rect scratch verts at 64-67 and clipper stack
 * verts have no slot and fall back to 0x4E).  Bit i set = slot i STALE: the
 * loaded vertex has not been uploaded (0x56) since it was last (re)written.
 * Invalidated by G_VTX rewrites, frame start, and any texture-affecting
 * state change (vc_tex_sig below).  Shade staleness across combiner changes
 * remains accepted (0x4A rides at 0x54 draw time). */
static uint32_t vc_slot_stale = 0xFFFFFFFFu;

/* Texture-affecting signature of the last cached-path batch.  Slot s/t are
 * TILE-RELATIVE, baked at UPLOAD time against the then-current tile origin
 * and filter bias — and SETTILESIZE can move uls/ult WITHOUT any flush or
 * re-import, so a slot uploaded under tile A and drawn under tile B samples
 * garbage (the hardware A/B "glitchy textures").  When any field changes,
 * every slot is wiped: re-uploads cost 8 words x <=32 slots per texture
 * change, far below the cached path's savings.
 * TODO(v2): a per-slot texture EPOCH (stamp each slot with the signature
 * generation it was uploaded under) would confine re-uploads to the slots a
 * new tile actually draws, instead of this global wipe. */
static struct {
    const uint8_t *addr;          /* imported texture (re-import/animation) */
    uint32_t cc_id;               /* combiner id: slot rgb565 is a per-material
                                   * FUNCTION of the vertex (white for texel-
                                   * only/blend-texel-alpha cycles, shade/csel
                                   * otherwise).  Same texture re-drawn under a
                                   * different cc with no G_VTX between reuses
                                   * slots whose baked shade obeys the OLD
                                   * rules — white-baked slots in a modulate
                                   * batch render unlit, shade-baked slots in a
                                   * texel-only batch render DARK (the round-4
                                   * "dark zones").  The 0x4A can't fix it: the
                                   * shade lives IN the slot. */
    uint16_t uls, ult;            /* tile origin (bakes into slot s/t) */
    uint16_t scale_s, scale_t;    /* G_TEXTURE scale (bakes into v->u/v) */
    uint8_t  textured;            /* textured vs white-texel batch */
    uint8_t  bias;                /* filter half-texel bias */
    uint8_t  cms, cmt;            /* clamp/mirror wrap mode */
} vc_tex_sig;
#endif

static struct GfxWindowManagerAPI *gfx_wapi;
static struct GfxRenderingAPI *gfx_rapi;

static void gfx_flush(void) {
#ifdef TARGET_POCKET
    g_flush_n++;
#endif
    if (buf_vbo_len > 0) {
        int num = buf_vbo_num_tris;
#ifdef TARGET_POCKET
        g_flush_sub++;
        g_flush_tris += (uint32_t)num;
        uint32_t _pe = PROF_TMR();
#endif
        gfx_rapi->draw_triangles(buf_vbo, buf_vbo_len, buf_vbo_num_tris);
#ifdef TARGET_POCKET
        prof_emit += PROF_TMR() - _pe;
#endif
        buf_vbo_len = 0;
        buf_vbo_num_tris = 0;
#ifdef TARGET_OPENFPGA
        buf_vbo_direct = false;
#endif
    }
}

static struct ShaderProgram *gfx_lookup_or_create_shader_program(uint32_t shader_id) {
    struct ShaderProgram *prg = gfx_rapi->lookup_shader(shader_id);
    if (prg == NULL) {
        gfx_rapi->unload_shader(rendering_state.shader_program);
        prg = gfx_rapi->create_and_load_new_shader(shader_id);
        rendering_state.shader_program = prg;
    }
    return prg;
}

static void gfx_generate_cc(struct ColorCombiner *comb, uint32_t cc_id) {
    uint8_t c[2][4];
    uint32_t shader_id = (cc_id >> 24) << 24;
    uint8_t shader_input_mapping[2][4] = {{0}};
    for (int i = 0; i < 4; i++) {
        c[0][i] = (cc_id >> (i * 3)) & 7;
        c[1][i] = (cc_id >> (12 + i * 3)) & 7;
    }
    for (int i = 0; i < 2; i++) {
        if (c[i][0] == c[i][1] || c[i][2] == CC_0) {
            c[i][0] = c[i][1] = c[i][2] = 0;
        }
        uint8_t input_number[8] = {0};
        int next_input_number = SHADER_INPUT_1;
        for (int j = 0; j < 4; j++) {
            int val = 0;
            switch (c[i][j]) {
                case CC_0:
                    break;
                case CC_TEXEL0:
                    val = SHADER_TEXEL0;
                    break;
                case CC_TEXEL1:
                    val = SHADER_TEXEL1;
                    break;
                case CC_TEXEL0A:
                    val = SHADER_TEXEL0A;
                    break;
                case CC_PRIM:
                case CC_SHADE:
                case CC_ENV:
                case CC_LOD:
                    if (input_number[c[i][j]] == 0) {
                        shader_input_mapping[i][next_input_number - 1] = c[i][j];
                        input_number[c[i][j]] = next_input_number++;
                    }
                    val = input_number[c[i][j]];
                    break;
            }
            shader_id |= val << (i * 12 + j * 3);
        }
    }
    comb->cc_id = cc_id;
    comb->prg = gfx_lookup_or_create_shader_program(shader_id);
    gfx_rapi->shader_get_info(comb->prg, &comb->num_inputs, comb->used_textures);
    memcpy(comb->shader_input_mapping, shader_input_mapping, sizeof(shader_input_mapping));
}

static struct ColorCombiner *gfx_lookup_or_create_color_combiner(uint32_t cc_id) {
    static struct ColorCombiner *prev_combiner;
    if (prev_combiner != NULL && prev_combiner->cc_id == cc_id) {
        return prev_combiner;
    }

    for (size_t i = 0; i < color_combiner_pool_size; i++) {
        if (color_combiner_pool[i].cc_id == cc_id) {
            return prev_combiner = &color_combiner_pool[i];
        }
    }
    gfx_flush();
    if (color_combiner_pool_size == sizeof(color_combiner_pool) / sizeof(color_combiner_pool[0]))
        color_combiner_pool_size = 0;
    struct ColorCombiner *comb = &color_combiner_pool[color_combiner_pool_size++];
    gfx_generate_cc(comb, cc_id);
    return prev_combiner = comb;
}

static bool gfx_texture_cache_lookup(int tile, struct TextureHashmapNode **n, const uint8_t *orig_addr, uint32_t fmt, uint32_t siz) {
    const uint8_t *palette = fmt == G_IM_FMT_CI ? rdp.palette : NULL;
    size_t hash = (uintptr_t)orig_addr;
    hash = (hash >> 5) & 0x3ff;
    struct TextureHashmapNode **node = &gfx_texture_cache.hashmap[hash];
    while (*node != NULL && *node - gfx_texture_cache.pool < (int)gfx_texture_cache.pool_pos) {
        if ((*node)->texture_addr == orig_addr && (*node)->fmt == fmt && (*node)->siz == siz
            && (*node)->palette_addr == palette
            && (*node)->size_bytes == rdp.loaded_texture[tile].size_bytes
            && (*node)->line_size_bytes == rdp.texture_tile.line_size_bytes) {
            gfx_rapi->select_texture(tile, (*node)->texture_id);
            *n = *node;
            return true;
        }
        node = &(*node)->next;
    }
    if (gfx_texture_cache.pool_pos == sizeof(gfx_texture_cache.pool) / sizeof(struct TextureHashmapNode)) {
        // Pool is full. We just invalidate everything and start over.
        gfx_texture_cache.pool_pos = 0;
        /* Old bucket heads can point at slots reused by a different bucket.
         * Clear them before those slots become live again. */
        memset(gfx_texture_cache.hashmap, 0, sizeof(gfx_texture_cache.hashmap));
        node = &gfx_texture_cache.hashmap[hash];
        s_tex_sig_addr[0] = s_tex_sig_addr[1] = NULL;  /* recycled GTex -> force re-import */
        //puts("Clearing texture cache");
    }
    *node = &gfx_texture_cache.pool[gfx_texture_cache.pool_pos++];
    if ((*node)->texture_addr == NULL) {
        (*node)->texture_id = gfx_rapi->new_texture();
    }
    gfx_rapi->select_texture(tile, (*node)->texture_id);
    gfx_rapi->set_sampler_parameters(tile, false, 0, 0);
    (*node)->cms = 0;
    (*node)->cmt = 0;
    (*node)->linear_filter = false;
    (*node)->next = NULL;
    (*node)->texture_addr = orig_addr;
    (*node)->palette_addr = palette;
    (*node)->size_bytes = rdp.loaded_texture[tile].size_bytes;
    (*node)->line_size_bytes = rdp.texture_tile.line_size_bytes;
    (*node)->fmt = fmt;
    (*node)->siz = siz;
    *n = *node;
    return false;
}

static void import_texture_rgba16(int tile) {
    uint32_t width = rdp.texture_tile.line_size_bytes / 2;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    /* Fast path: hand the ROM texels to the backend as-is.  RGBA16 is SM64's
     * dominant texture format and the expansion below is pure waste for a
     * 16-bit-native backend — see upload_texture_rgba16 in gfx_rendering_api.h.
     * Falls through to the generic path when the backend declines (or has no
     * such entry point), so behaviour is unchanged everywhere else. */
    if (gfx_rapi->upload_texture_rgba16 &&
        gfx_rapi->upload_texture_rgba16(rdp.loaded_texture[tile].addr,
                                        (int)width, (int)height))
        return;

    uint8_t rgba32_buf[8192];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes / 2; i++) {
        uint16_t col16 = (rdp.loaded_texture[tile].addr[2 * i] << 8) | rdp.loaded_texture[tile].addr[2 * i + 1];
        uint8_t a = col16 & 1;
        uint8_t r = col16 >> 11;
        uint8_t g = (col16 >> 6) & 0x1f;
        uint8_t b = (col16 >> 1) & 0x1f;
        rgba32_buf[4*i + 0] = SCALE_5_8(r);
        rgba32_buf[4*i + 1] = SCALE_5_8(g);
        rgba32_buf[4*i + 2] = SCALE_5_8(b);
        rgba32_buf[4*i + 3] = a ? 255 : 0;
    }

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}

static void import_texture_rgba32(int tile) {
    uint32_t width = rdp.texture_tile.line_size_bytes / 2;
    uint32_t height = (rdp.loaded_texture[tile].size_bytes / 2) / rdp.texture_tile.line_size_bytes;
    gfx_rapi->upload_texture(rdp.loaded_texture[tile].addr, width, height);
}

static void import_texture_ia4(int tile) {
    uint8_t rgba32_buf[32768];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes * 2; i++) {
        uint8_t byte = rdp.loaded_texture[tile].addr[i / 2];
        uint8_t part = (byte >> (4 - (i % 2) * 4)) & 0xf;
        uint8_t intensity = part >> 1;
        uint8_t alpha = part & 1;
        uint8_t r = intensity;
        uint8_t g = intensity;
        uint8_t b = intensity;
        rgba32_buf[4*i + 0] = SCALE_3_8(r);
        rgba32_buf[4*i + 1] = SCALE_3_8(g);
        rgba32_buf[4*i + 2] = SCALE_3_8(b);
        rgba32_buf[4*i + 3] = alpha ? 255 : 0;
    }

    uint32_t width = rdp.texture_tile.line_size_bytes * 2;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}

static void import_texture_ia8(int tile) {
    uint8_t rgba32_buf[16384];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes; i++) {
        uint8_t intensity = rdp.loaded_texture[tile].addr[i] >> 4;
        uint8_t alpha = rdp.loaded_texture[tile].addr[i] & 0xf;
        uint8_t r = intensity;
        uint8_t g = intensity;
        uint8_t b = intensity;
        rgba32_buf[4*i + 0] = SCALE_4_8(r);
        rgba32_buf[4*i + 1] = SCALE_4_8(g);
        rgba32_buf[4*i + 2] = SCALE_4_8(b);
        rgba32_buf[4*i + 3] = SCALE_4_8(alpha);
    }

    uint32_t width = rdp.texture_tile.line_size_bytes;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}

static void import_texture_ia16(int tile) {
    uint8_t rgba32_buf[8192];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes / 2; i++) {
        uint8_t intensity = rdp.loaded_texture[tile].addr[2 * i];
        uint8_t alpha = rdp.loaded_texture[tile].addr[2 * i + 1];
        uint8_t r = intensity;
        uint8_t g = intensity;
        uint8_t b = intensity;
        rgba32_buf[4*i + 0] = r;
        rgba32_buf[4*i + 1] = g;
        rgba32_buf[4*i + 2] = b;
        rgba32_buf[4*i + 3] = alpha;
    }

    uint32_t width = rdp.texture_tile.line_size_bytes / 2;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}

static void import_texture_i4(int tile) {
    uint8_t rgba32_buf[32768];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes * 2; i++) {
        uint8_t byte = rdp.loaded_texture[tile].addr[i / 2];
        uint8_t part = (byte >> (4 - (i % 2) * 4)) & 0xf;
        uint8_t intensity = part;
        uint8_t r = intensity;
        uint8_t g = intensity;
        uint8_t b = intensity;
        rgba32_buf[4*i + 0] = SCALE_4_8(r);
        rgba32_buf[4*i + 1] = SCALE_4_8(g);
        rgba32_buf[4*i + 2] = SCALE_4_8(b);
        rgba32_buf[4*i + 3] = 255;
    }

    uint32_t width = rdp.texture_tile.line_size_bytes * 2;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}

static void import_texture_i8(int tile) {
    uint8_t rgba32_buf[16384];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes; i++) {
        uint8_t intensity = rdp.loaded_texture[tile].addr[i];
        uint8_t r = intensity;
        uint8_t g = intensity;
        uint8_t b = intensity;
        rgba32_buf[4*i + 0] = r;
        rgba32_buf[4*i + 1] = g;
        rgba32_buf[4*i + 2] = b;
        rgba32_buf[4*i + 3] = 255;
    }

    uint32_t width = rdp.texture_tile.line_size_bytes;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}


static void import_texture_ci4(int tile) {
    uint8_t rgba32_buf[32768];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes * 2; i++) {
        uint8_t byte = rdp.loaded_texture[tile].addr[i / 2];
        uint8_t idx = (byte >> (4 - (i % 2) * 4)) & 0xf;
        uint16_t col16 = (rdp.palette[idx * 2] << 8) | rdp.palette[idx * 2 + 1]; // Big endian load
        uint8_t a = col16 & 1;
        uint8_t r = col16 >> 11;
        uint8_t g = (col16 >> 6) & 0x1f;
        uint8_t b = (col16 >> 1) & 0x1f;
        rgba32_buf[4*i + 0] = SCALE_5_8(r);
        rgba32_buf[4*i + 1] = SCALE_5_8(g);
        rgba32_buf[4*i + 2] = SCALE_5_8(b);
        rgba32_buf[4*i + 3] = a ? 255 : 0;
    }

    uint32_t width = rdp.texture_tile.line_size_bytes * 2;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}

static void import_texture_ci8(int tile) {
    uint8_t rgba32_buf[16384];

    for (uint32_t i = 0; i < rdp.loaded_texture[tile].size_bytes; i++) {
        uint8_t idx = rdp.loaded_texture[tile].addr[i];
        uint16_t col16 = (rdp.palette[idx * 2] << 8) | rdp.palette[idx * 2 + 1]; // Big endian load
        uint8_t a = col16 & 1;
        uint8_t r = col16 >> 11;
        uint8_t g = (col16 >> 6) & 0x1f;
        uint8_t b = (col16 >> 1) & 0x1f;
        rgba32_buf[4*i + 0] = SCALE_5_8(r);
        rgba32_buf[4*i + 1] = SCALE_5_8(g);
        rgba32_buf[4*i + 2] = SCALE_5_8(b);
        rgba32_buf[4*i + 3] = a ? 255 : 0;
    }

    uint32_t width = rdp.texture_tile.line_size_bytes;
    uint32_t height = rdp.loaded_texture[tile].size_bytes / rdp.texture_tile.line_size_bytes;

    gfx_rapi->upload_texture(rgba32_buf, width, height);
}

static void import_texture(int tile) {
    uint8_t fmt = rdp.texture_tile.fmt;
    uint8_t siz = rdp.texture_tile.siz;

    if (gfx_texture_cache_lookup(tile, &rendering_state.textures[tile], rdp.loaded_texture[tile].addr, fmt, siz)) {
        return;
    }

    if (fmt == G_IM_FMT_RGBA) {
        if (siz == G_IM_SIZ_16b) {
            import_texture_rgba16(tile);
        } else if (siz == G_IM_SIZ_32b) {
            import_texture_rgba32(tile);
        } else {
            abort();
        }
    } else if (fmt == G_IM_FMT_IA) {
        if (siz == G_IM_SIZ_4b) {
            import_texture_ia4(tile);
        } else if (siz == G_IM_SIZ_8b) {
            import_texture_ia8(tile);
        } else if (siz == G_IM_SIZ_16b) {
            import_texture_ia16(tile);
        } else {
            abort();
        }
    } else if (fmt == G_IM_FMT_CI) {
        if (siz == G_IM_SIZ_4b) {
            import_texture_ci4(tile);
        } else if (siz == G_IM_SIZ_8b) {
            import_texture_ci8(tile);
        } else {
            abort();
        }
    } else if (fmt == G_IM_FMT_I) {
        if (siz == G_IM_SIZ_4b) {
            import_texture_i4(tile);
        } else if (siz == G_IM_SIZ_8b) {
            import_texture_i8(tile);
        } else {
            abort();
        }
    } else {
        abort();
    }
}

/* Renamed from rsqrtf to of_rsqrtf: recent glibc <math.h> declares its own
 * non-static rsqrtf, which collides with this file-local fast-inverse-sqrt. */
static inline float of_rsqrtf(const float x) {
    const float x2 = x * 0.5f;
    float y = x;
    int32_t i = *(int32_t *)&y;
    i = 0x5f3759df - (i >> 1);
    y = *(float *)&i;
    y = y * (1.5f - (x2 * y * y));
    return y;
}

static inline void gfx_normalize_vector(float v[3]) {
    const float s = of_rsqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    v[0] *= s;
    v[1] *= s;
    v[2] *= s;
}

static inline void gfx_transposed_matrix_mul(float *restrict res, const float *restrict a, const float (*restrict b)[4]) {
    res[0] = a[0] * b[0][0] + a[1] * b[0][1] + a[2] * b[0][2];
    res[1] = a[0] * b[1][0] + a[1] * b[1][1] + a[2] * b[1][2];
    res[2] = a[0] * b[2][0] + a[1] * b[2][1] + a[2] * b[2][2];
}

static inline void calculate_normal_dir(const Light_t *light, rspv_t coeffs[3]) {
    float light_dir[3] = {
        light->dir[0] / 127.0f,
        light->dir[1] / 127.0f,
        light->dir[2] / 127.0f
    };
    float fcoeffs[3];
    float (*mv)[4] = (float (*)[4])rsp.modelview_matrix_stack[rsp.modelview_matrix_stack_size - 1];
    gfx_transposed_matrix_mul(fcoeffs, light_dir, mv);
    gfx_normalize_vector(fcoeffs);
    coeffs[0] = RSPV(fcoeffs[0]);
    coeffs[1] = RSPV(fcoeffs[1]);
    coeffs[2] = RSPV(fcoeffs[2]);
}

static inline void gfx_matrix_mul_inplace(const rspv_t (*restrict a)[4], rspv_t (*restrict res)[4]) {
    rspv_t tmp[4][4];
    for (int i = 0; i < 4; i++) {
        for (int j = 0; j < 4; j++) {
            tmp[i][j] = a[i][0] * res[0][j] +
                        a[i][1] * res[1][j] +
                        a[i][2] * res[2][j] +
                        a[i][3] * res[3][j];
        }
    }
    memcpy(res, tmp, sizeof(tmp));
}

static inline void gfx_matrix_mul(rspv_t (*restrict res)[4], const rspv_t (*restrict a)[4], const rspv_t (*restrict b)[4]) {
    for (int i = 0; i < 4; i++) {
        for (int j = 0; j < 4; j++) {
            res[i][j] = a[i][0] * b[0][j] +
                        a[i][1] * b[1][j] +
                        a[i][2] * b[2][j] +
                        a[i][3] * b[3][j];
        }
    }
}

static void gfx_sp_matrix(uint8_t parameters, const int32_t *addr) {
    SM64_PERF_SCOPE(SM64_PERF_VTX_MATRIX);
#ifdef TARGET_OPENFPGA
    ++vertex_state_epoch;
#endif
#ifdef TARGET_POCKET
    uint32_t _pm = PROF_TMR();
#endif
    rspv_t matrix[4][4];
#ifndef GBI_FLOATS
    // Original GBI where fixed point matrices are used
    register int idx;
    for (int i = 0; i < 4; i++) {
        for (int j = 0; j < 4; j += 2) {
            idx = (i << 1) + (j >> 1);
            const int32_t int_part = addr[idx];
            const uint32_t frac_part = addr[8 + idx];
            matrix[i][j] = (int32_t)((int_part & 0xffff0000) | (frac_part >> 16)) / 65536.f;
            matrix[i][j + 1] = (int32_t)((int_part << 16) | (frac_part & 0xffff)) / 65536.f;
        }
    }
#else
    // For a modified GBI where fixed point values are replaced with floats
    memcpy(matrix, addr, sizeof(matrix));
#endif

    if (parameters & G_MTX_PROJECTION) {
        if (parameters & G_MTX_LOAD) {
            memcpy(rsp.P_matrix, matrix, sizeof(matrix));
        } else {
            gfx_matrix_mul_inplace(matrix, rsp.P_matrix);
        }
    } else { // G_MTX_MODELVIEW
        if ((parameters & G_MTX_PUSH) && rsp.modelview_matrix_stack_size < 11) {
            ++rsp.modelview_matrix_stack_size;
            memcpy(rsp.modelview_matrix_stack[rsp.modelview_matrix_stack_size - 1], rsp.modelview_matrix_stack[rsp.modelview_matrix_stack_size - 2], sizeof(matrix));
        }
        if (parameters & G_MTX_LOAD) {
            memcpy(rsp.modelview_matrix_stack[rsp.modelview_matrix_stack_size - 1], matrix, sizeof(matrix));
        } else {
            gfx_matrix_mul_inplace(matrix, rsp.modelview_matrix_stack[rsp.modelview_matrix_stack_size - 1]);
        }
        rsp.lights_changed = 1;
        rsp.lookat_changed = true;
    }
    /* Several matrix commands can precede the next vertex load. Only its
     * final modelview/projection pair needs a product. */
    rsp.matrix_changed = true;
#ifdef TARGET_POCKET
    prof_mtx += PROF_TMR() - _pm;
#endif
}

static void gfx_sp_pop_matrix(uint32_t count) {
    SM64_PERF_SCOPE(SM64_PERF_VTX_MATRIX);
#ifdef TARGET_OPENFPGA
    ++vertex_state_epoch;
#endif
#ifdef TARGET_POCKET
    uint32_t _pm = PROF_TMR();
#endif
    if (count && rsp.modelview_matrix_stack_size > 1) {
        uint32_t available = rsp.modelview_matrix_stack_size - 1;
        if (count > available) count = available;
        rsp.modelview_matrix_stack_size -= count;
        rsp.matrix_changed = true;
        rsp.lights_changed = true;
        rsp.lookat_changed = true;
    }
#ifdef TARGET_POCKET
    prof_mtx += PROF_TMR() - _pm;
#endif
}

static inline float gfx_adjust_x_for_aspect_ratio(float x) {
    return x * (4.0f / 3.0f) / (float)gfx_current_dimensions.aspect_ratio;
}

#ifdef TARGET_OPENFPGA
static void gfx_materialize_vertex_attributes(unsigned slot);
#include "gfx_vertex_cull.h"
#include "gfx_geometry.h"
#endif

#ifdef TARGET_OPENFPGA
/* The deferred path needs only UVs and lighting. Keep its register footprint
 * independent of the sixteen matrix scalars and clipping/fog temporaries. */
static void gfx_load_geometry_attributes(size_t count,size_t first,const GeometryInput *vertices,uint32_t needed) {
    SM64_PERF_SCOPE(SM64_PERF_VTX_ATTR);
    const uint16_t scale_s=rsp.texture_scaling_factor.s,scale_t=rsp.texture_scaling_factor.t;
    const unsigned light_count=rsp.current_num_lights-1;
    const bool lighting=(rsp.geometry_mode&G_LIGHTING)!=0;
    const float c0=rsp.current_lights_coeffs[0][0],c1=rsp.current_lights_coeffs[0][1],c2=rsp.current_lights_coeffs[0][2];
    const int ar=rsp.current_lights[light_count].col[0],ag=rsp.current_lights[light_count].col[1],ab=rsp.current_lights[light_count].col[2];
    const float lr=rsp.current_lights[0].col[0],lg=rsp.current_lights[0].col[1],lb=rsp.current_lights[0].col[2];
    for(size_t i=0;i<count;i++) {
        if(!(needed&(1u<<i)))continue;
        const struct GeometryInputV *v=&vertices[i].v;const struct GeometryInputN *vn=&vertices[i].n;
        struct LoadedVertex *d=&rsp.loaded_vertices[first+i];
        d->u=(short)(v->tc[0]*scale_s>>16);d->v=(short)(v->tc[1]*scale_t>>16);
        d->clip_rej=0;d->color.a=v->cn[3];
        if(!lighting) {d->color.r=v->cn[0];d->color.g=v->cn[1];d->color.b=v->cn[2];continue;}
        const uint32_t normal=gfx_lighting_normal_key(v->cn);
        struct LightingMemo *memo=&lighting_memo[(normal^(normal>>11))&255u];
        if(memo->epoch==lighting_epoch && memo->normal==normal) {
            d->color.r=memo->color.r;d->color.g=memo->color.g;d->color.b=memo->color.b;continue;
        }
        int r=ar,g=ag,b=ab;
        if(light_count==1) {
            float intensity=(vn->n[0]*c0+vn->n[1]*c1+vn->n[2]*c2)/127.0f;
            if(intensity>0) {r+=intensity*lr;g+=intensity*lg;b+=intensity*lb;}
        } else for(unsigned l=0;l<light_count;l++) {
            float intensity=(vn->n[0]*rsp.current_lights_coeffs[l][0]
                +vn->n[1]*rsp.current_lights_coeffs[l][1]+vn->n[2]*rsp.current_lights_coeffs[l][2])/127.0f;
            if(intensity>0) {r+=intensity*rsp.current_lights[l].col[0];g+=intensity*rsp.current_lights[l].col[1];b+=intensity*rsp.current_lights[l].col[2];}
        }
        d->color.r=r<0?0:r>255?255:r;d->color.g=g<0?0:g>255?255:g;d->color.b=b<0?0:b>255?255:b;
        memo->epoch=lighting_epoch;memo->normal=normal;
        memo->color.r=d->color.r;memo->color.g=d->color.g;memo->color.b=d->color.b;
    }
}
/* Attribute evaluation may wait until a triangle survives culling. RSP
 * changes flush it first; G_VTX materializes only slots that survive its
 * overwrite. Thus no full RSP snapshot or mutable source pointer is needed. */
static uint32_t vertex_attributes_pending;
static const GeometryInput *vertex_attributes_source;
static unsigned vertex_attributes_first,vertex_attributes_count;

static void gfx_flush_vertex_attributes(uint32_t mask) {
    uint32_t needed=vertex_attributes_pending&mask;
    if(!needed)return;
    gfx_load_geometry_attributes(vertex_attributes_count,vertex_attributes_first,
                                 vertex_attributes_source,needed>>vertex_attributes_first);
    vertex_attributes_pending&=~needed;
}

static void gfx_materialize_vertex_attributes(unsigned slot) {
    if(slot<32)gfx_flush_vertex_attributes(1u<<slot);
}

#endif


#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
/* TFP1 keeps the current raw LOAD resident until a CPU reader needs it.
 * The helper is defined beside the alias ABI below; fog must copy first so
 * a later materialization cannot overwrite its computed alpha. */
static void load_alias_materialize(uint32_t mask);
static void load_alias_materialize_all(void);
#define GFX_CPU_DEFERRED_FOG 1
/* This backend ignores the fog channel and substitutes opaque shade alpha
 * while use_fog is active. Preserve the original alpha lazily for any later
 * non-fog reuse. Positions/reciprocals are already owned by loaded_vertices;
 * only one pending batch and its two fog coefficients need to survive. */
static uint32_t cpu_fog_pending;
static int16_t cpu_fog_mul, cpu_fog_offset;

static __attribute__((noinline)) void gfx_cpu_materialize_fog(uint32_t needed) {
    SM64_PERF_SCOPE(SM64_PERF_VTX_CPU);
    load_alias_materialize_all();
    cpu_fog_pending &= ~needed;
    for (unsigned slot = 0; needed; ++slot, needed >>= 1) {
        if (!(needed & 1u)) continue;
        struct LoadedVertex *d = &rsp.loaded_vertices[slot];
        float wf = d->w;
        float zf = d->z;
        float winv = (wf == 0.f) ? 1.f / 0.001f : d->w_inv;
        if (winv < 0.0f) winv = 32767.0f;
        float fog_z = zf * winv * cpu_fog_mul + cpu_fog_offset;
        int fog_i = (int)fog_z;
        d->color.a = (fog_i < 0) ? 0 : (fog_i > 255) ? 255 : fog_i;
    }
}

static inline __attribute__((always_inline)) void gfx_cpu_flush_fog(uint32_t mask) {
    uint32_t needed = cpu_fog_pending & mask;
    if (needed) gfx_cpu_materialize_fog(needed);
}
#else
#define GFX_CPU_DEFERRED_FOG 0
#endif

/* One owned prepared batch may cross exactly one adjacent G_VTX.
 * It contains values, not pointers into the overwritten CPU vertex array.
 * No material, viewport, texture or draw command can intervene. The next
 * LOAD submits this batch after START, hiding submission behind transform;
 * every no-START path submits it before the CPU fallback loop. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define GFX_LOAD_SUBMIT_OVERLAP 1
static struct gfx_vc_prepared load_submit_payload[32];
static uint32_t load_submit_jobs[64];
static unsigned load_submit_count;
static uint8_t load_submit_alpha;
/* By-value ownership descriptor: BIM2 snapshots survive the next raw START,
 * while this run's material, prepared fallback slots and jobs stay immutable. */
static struct gfx_bram_import load_submit_import;
/* queued batches, submitted while LOAD runs, fallback submissions. */
uint32_t load_submit_stats[3];
static __attribute__((noinline)) void gfx_cpu_submit_pending(void) {
    unsigned count = load_submit_count;
    if (!count) return;
    load_submit_count = 0;
    if (load_submit_import.slots) {
        gfx_gpu_vtx_cache_import_batch(load_submit_payload, load_submit_jobs,
                                      count, load_submit_alpha, &load_submit_import);
        load_submit_import.slots = 0;
    } else {
        gfx_gpu_vtx_cache_batch(load_submit_payload, load_submit_jobs,
                               count, load_submit_alpha);
    }
}
#else
#define GFX_LOAD_SUBMIT_OVERLAP 0
#endif

/* Private LOD5. The fixed view occupies the existing reserved SDRAM guard.
 * It is read-only, cacheable, and never supplied to GPU/audio/DMA consumers.
 * A successful LOAD replaces view slots0..count-1; CPU destination remains
 * the normal36B LoadedVertex array, preserving fog and fallback stores. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define LOAD_ALIAS_ENABLED 1
struct LoadAliasRegisters {
    const volatile uint32_t id, status;
    volatile uint32_t control, source, shape, aspect, coefficient[3];
    volatile uint32_t ambient, diffuse, scales, lighting;
    uint32_t reserved[51];
    volatile uint32_t matrix[16];
};
_Static_assert(__builtin_offsetof(struct LoadAliasRegisters,matrix)==0x100,"LOD5 matrix ABI");
_Static_assert(sizeof(Vtx)==24,"LOD5 raw source stride");
_Static_assert(sizeof(struct LoadedVertex)==36,"LOD5 canonical vertex size");
_Static_assert(__builtin_offsetof(struct LoadedVertex,u)==20,"LOD5 UV offset");
_Static_assert(__builtin_offsetof(struct LoadedVertex,color)==28,"LOD5 color offset");
_Static_assert(__builtin_offsetof(struct LoadedVertex,clip_rej)==32,"LOD5 clip offset");
#define LOAD_ALIAS_REGS ((struct LoadAliasRegisters *)0x4b000000u)
#define LOAD_ALIAS_ID 0x4c4f4405u
#define LOAD_ALIAS_VIEW ((const uint32_t *)0x13f80000u)
/* MTF2 topology is compiled only for immutable ELF display-list bytes. The
 * cached end pointer replaces steady per-triangle CPU decoding; physical DMA
 * offsets are separately rebound to the retained source-pool generation. */
struct MeshTask {
    const Gfx *source;
    Gfx *next;
    uint16_t commands, triangles;
    uint16_t dead_after, prefer_legacy;
};
static struct MeshTask mesh_tasks[512];
/* A contextual proof must never change source-only MeshTask.dead_after.
 * kind:0 empty,1 caller-independent success,2 caller-bound success,3 failure. */
struct AsyncDeadProof { Gfx *return_command; unsigned kind; };
static struct AsyncDeadProof async_dead_proofs[512];
static Gfx *async_return_command;
/* Keep the hot topology layout unchanged. These optional physical offsets
 * belong to one exact immutable task, source shape and live pool generation. */
struct AsyncTaskSources {
    const void *vertices;
    uint32_t generation,vertex_offset,command_offset,counts;
};
static struct AsyncTaskSources async_task_sources[512];
struct AsyncGroup {
    Gfx *next,*return_command;
    unsigned caller_sensitive;
    uint32_t generation,hint_epoch,hint_cursor,descriptor_offset,loaded_mask;
    uint16_t members,triangles;
    uint32_t cull;
};
static struct AsyncGroup async_groups[512];
static uint32_t async_hint_epoch=1;
/* Exact source addresses marked by the monotonic topology repair hints.
 * A cached group only needs to recompile when a new hint falls inside its
 * contiguous immutable span. Pool recycles still invalidate every offset. */
static const Gfx *async_hint_sources[512];
static unsigned async_hint_count;
/* submissions, member jobs, cached hits, builds, generation/hint retries,
 * build failures, singleton routes, skipped internal CPU LOAD handlers. */
uint32_t gfx_async_group_stats[8];
/* cached hits, successful rebinds, source failures, actual pool recycles. */
uint32_t gfx_async_source_stats[4];
void gfx_async_forget_sources(void) {
    memset(async_task_sources,0,sizeof(async_task_sources));
    memset(async_groups,0,sizeof(async_groups));
    ++gfx_async_source_stats[3];
}
static int gfx_async_sources(const struct MeshTask *task,const void *vertices,
        unsigned count,uint32_t *vertex_offset,uint32_t *command_offset) {
    struct AsyncTaskSources *entry=&async_task_sources[task-mesh_tasks];
    const uint32_t generation=gfx_gpu_cram_source_generation();
    const uint32_t counts=count|((uint32_t)task->commands<<16);
    if(entry->vertices==vertices&&entry->counts==counts&&entry->generation==generation) {
        *vertex_offset=entry->vertex_offset;*command_offset=entry->command_offset;
        ++gfx_async_source_stats[0];return 1;
    }
    if(!gfx_gpu_mesh_sources(vertices,count,task->source+1,task->commands,
                             vertex_offset,command_offset)) {
        ++gfx_async_source_stats[2];return 0;
    }
    /* Allocation can recycle between resolving vertices and commands. The
     * existing source API retries both, and this generation is read afterward. */
    entry->vertices=vertices;entry->counts=counts;
    entry->generation=gfx_gpu_cram_source_generation();
    entry->vertex_offset=*vertex_offset;entry->command_offset=*command_offset;
    ++gfx_async_source_stats[1];return 1;
}
/* callbacks, exact cache-key matches, first marks, future route rejections.
 * This is only a performance hint: later geometry is never inferred from it. */
uint32_t gfx_async_hint_stats[4];
void gfx_async_note_repair(const void *commands) {
    ++gfx_async_hint_stats[0];
    const uintptr_t p=(uintptr_t)commands,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if((p&7u)||p<(uintptr_t)&_start+sizeof(Gfx)||p>end-sizeof(Gfx))return;
    const Gfx *source=(const Gfx *)(p-sizeof(Gfx));
    const unsigned h=(((uintptr_t)source>>3)^((uintptr_t)source>>13))&511u;
    for(unsigned probe=0;probe<16;++probe) {
        struct MeshTask *entry=&mesh_tasks[(h+probe)&511u];
        if(entry->source==source) {
            if(entry->next&&entry->commands) {
                ++gfx_async_hint_stats[1];
                if(!entry->prefer_legacy) {
                    entry->prefer_legacy=1;++gfx_async_hint_stats[2];
                    if(!++async_hint_epoch||async_hint_count==512) {
                        memset(async_groups,0,sizeof(async_groups));
                        async_hint_epoch=1;async_hint_count=0;
                    }
                    async_hint_sources[async_hint_count++]=source;
                }
            }
            return;
        }
        if(!entry->source)return;
    }
}
static const struct MeshTask *mesh_armed,*async_armed;
static Gfx *async_next;
static unsigned async_submitted;
static int gfx_async_try_load(unsigned count,unsigned first,const Vtx *vertices,
                              float aspect,int eager_fog);
static unsigned mesh_raw_completed;
static uint32_t mesh_serial;
#define MESH_REGS ((volatile uint32_t *)0x4b000000u)
#define MESH_OWNER 1u
#define MESH_PREFLIGHT_READY 2u
#define MESH_GEOMETRY_FALLBACK 4u
#define MESH_PROTOCOL_ERROR 8u
#define MESH_RAW_SUCCESS 16u
#define MESH_VALIDATED 32u
#define MESH_PACKET_ERROR 64u
#define MESH_RAW_DONE (1u<<15)
static void gfx_mesh_discard(void) {
    if(MESH_REGS[0x21]&MESH_OWNER)MESH_REGS[0x26]=2;
    gfx_gpu_mesh_wait_source();
}
static Gfx *gfx_mesh_finish(Gfx *cmd);
/* Cold-only liveness proof. Every command is immutable ELF data. Refuse all
 * state/list/end commands (including canonical fog barriers); only VTX
 * overwrites and TRI reads of unrelated slots may intervene. */
static unsigned gfx_async_dead_suffix(const Gfx *scan,unsigned count) {
    uint32_t pending=count==32?0xffffffffu:((1u<<count)-1u);
    const uintptr_t end=(uintptr_t)__EH_FRAME_BEGIN__;
    for(unsigned steps=0;steps<256;++steps,++scan) {
        const uintptr_t p=(uintptr_t)scan;
        if((p&7u)||p<(uintptr_t)&_start||p>end-8u)return 0;
        const uint32_t a=scan->words.w0,b=scan->words.w1;
        const unsigned op=a>>24;
        if(op==(uint8_t)G_VTX) {
            const unsigned n=(a>>12)&255u,limit=(a>>1)&127u;
            if(!n||n>32||limit<n||limit>32)return 0;
            const unsigned first=limit-n;
            const uint32_t mask=n==32?0xffffffffu:((1u<<n)-1u)<<first;
            pending&=~mask;
            if(!pending)return 1;
        } else if(op==(uint8_t)G_TRI1||op==(uint8_t)G_TRI2) {
            const unsigned n=op==(uint8_t)G_TRI2?2u:1u;
            for(unsigned t=0;t<n;++t) {
                const uint32_t w=t?b:a;
                for(unsigned shift=0;shift<=16;shift+=8) {
                    const unsigned slot=((w>>shift)&255u)/2u;
                    if(slot>=32||(pending&(1u<<slot)))return 0;
                }
            }
        } else return 0;
    }
    return 0;
}
/* Look past canonical-neutral state only while proving rows dead. The CPU
 * dispatcher still executes each opcode, and GROUP still stops at state/list
 * boundaries. Other-mode commands remain barriers because they can flush fog. */
static __attribute__((cold,noinline)) unsigned gfx_async_dead_caller(
        const Gfx *scan,unsigned count,const Gfx *return_command) {
    uint32_t pending=count==32?0xffffffffu:((1u<<count)-1u);
    const uintptr_t end=(uintptr_t)__EH_FRAME_BEGIN__;
    const Gfx *returns[8];unsigned depth=0,external_return=0;
    for(unsigned steps=0;steps<256;++steps) {
        const uintptr_t p=(uintptr_t)scan;
        if((p&7u)||p<(uintptr_t)&_start||p>end-8u)return 0;
        const uint32_t a=scan->words.w0,b=scan->words.w1;
        const unsigned op=a>>24;
        if(op==(uint8_t)G_VTX) {
            const unsigned n=(a>>12)&255u,limit=(a>>1)&127u;
            if(!n||n>32||limit<n||limit>32)return 0;
            pending&=~(n==32?0xffffffffu:((1u<<n)-1u)<<(limit-n));
            if(!pending)return external_return?2u:1u;
        } else if(op==(uint8_t)G_TRI1||op==(uint8_t)G_TRI2) {
            const unsigned n=op==(uint8_t)G_TRI2?2u:1u;
            for(unsigned t=0;t<n;++t) {
                const uint32_t word=t?b:a;
                for(unsigned shift=0;shift<=16;shift+=8) {
                    const unsigned slot=((word>>shift)&255u)/2u;
                    if(slot>=32||(pending&(1u<<slot)))return 0;
                }
            }
        } else if(op==(uint8_t)G_DL) {
            if(!(a&(1u<<16))) {
                if(depth==8)return 0;
                returns[depth++]=scan+1;
            }
            scan=(const Gfx *)(uintptr_t)b;continue;
        } else if(op==(uint8_t)G_ENDDL) {
            if(depth)scan=returns[--depth];
            else {
                if(external_return||!return_command)return 0;
                scan=return_command;external_return=1;
            }
            continue;
        } else {
            switch(op) {
            case 0xd7: case 0xfd: case 0xd9: case 0xfb:
            case 0xf5: case 0xf2: case 0xf3: case 0xf4:
            case 0xf0: case 0xfa: case 0xfc: case 0xf8:
            case 0xe6: case 0xe7: case 0xe8: case 0xe9:
                break;
            default:return 0;
            }
        }
        ++scan;
    }
    return 0;
}
static inline unsigned gfx_async_task_proof(const struct MeshTask *task) {
    if(task->dead_after)return 1;
    struct AsyncDeadProof *proof=&async_dead_proofs[task-mesh_tasks];
    if(proof->kind==1)return 1;
    if(proof->kind&&proof->return_command==async_return_command)
        return proof->kind==2?2u:0u;
    const unsigned result=gfx_async_dead_caller(task->next,
        (task->source->words.w0>>12)&255u,async_return_command);
    proof->return_command=async_return_command;
    proof->kind=result?result:3u;
    return result;
}
static const struct MeshTask *gfx_async_route_task(const struct MeshTask *entry,int asynchronous,int account) {
    if(!entry->next)return NULL;
    /* Static routing proof/hint needs no dynamic material/context setup. Keep
     * the original diagnostic accounting while skipping the larger helper. */
    if(asynchronous&&(entry->prefer_legacy||!gfx_async_task_proof(entry))) {
        if(account) {
            ++gfx_async_stats[0];
            if(entry->prefer_legacy)++gfx_async_hint_stats[3];
            else ++gfx_async_stats[1];
        }
        return NULL;
    }
    return entry;
}
static const struct MeshTask *gfx_mesh_lookup_impl(Gfx *cmd,int asynchronous,int account) {
    if(account)gfx_mesh_stats[0]++;
    const uintptr_t p=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if((p&7)||p<(uintptr_t)&_start||p>=end||end-p<16)return NULL;
    const unsigned h=((p>>3)^(p>>13))&511u;
    struct MeshTask *entry=NULL;
    for(unsigned probe=0;probe<16;++probe) {
        struct MeshTask *e=&mesh_tasks[(h+probe)&511u];
        if(e->source==cmd) {
            if(account)gfx_mesh_stats[1]++;
            return gfx_async_route_task(e,asynchronous,account);
        }
        if(!e->source){entry=e;break;}
    }
    if(!entry)return NULL;
    entry->source=cmd;
    memset(&async_dead_proofs[entry-mesh_tasks],0,sizeof(struct AsyncDeadProof));
    async_task_sources[entry-mesh_tasks].vertices=NULL;
    memset(&async_groups[entry-mesh_tasks],0,sizeof(struct AsyncGroup));
    entry->prefer_legacy=0;
    const unsigned count=(cmd->words.w0>>12)&255u;
    const unsigned limit=(cmd->words.w0>>1)&127u;
    if(!count||count>32||limit!=count)return NULL; /* initial dest0 scope */
    unsigned commands=0,triangles=0;
    Gfx *scan=cmd+1;
    while((uintptr_t)scan<=end-8u) {
        unsigned op=scan->words.w0>>24;
        if(op!=(uint8_t)G_TRI1&&op!=(uint8_t)G_TRI2)break;
        const unsigned n=op==(uint8_t)G_TRI2?2u:1u;
        if(commands==64||triangles+n>64)return NULL;
        for(unsigned t=0;t<n;++t) {
            const uint32_t word=t?scan->words.w1:scan->words.w0;
            if(((word>>16)&255u)/2u>=count||((word>>8)&255u)/2u>=count||
               (word&255u)/2u>=count)return NULL;
        }
        ++commands;triangles+=n;++scan;
    }
    if(!commands||(uintptr_t)scan>end-8u)return NULL;
    entry->commands=commands;entry->triangles=triangles;entry->next=scan;
    entry->dead_after=gfx_async_dead_suffix(scan,count);
    if(account)gfx_mesh_stats[2]++;
    return gfx_async_route_task(entry,asynchronous,account);
}
static const struct MeshTask *gfx_mesh_lookup(Gfx *cmd) {
    const int asynchronous=gfx_gpu_async_supported();
    if(!asynchronous&&!gfx_gpu_mesh_supported())return NULL;
    return gfx_mesh_lookup_impl(cmd,asynchronous,1);
}

/* A group contains only individually proven state-free tasks. Context and
 * material eligibility are checked once by its first LOAD; no state, list or
 * return opcode is consumed here. Each member has a bounded dead-slot proof
 * and an immutable direct ELF vertex pointer. Caller-dependent proofs bind
 * the cached group to this exact immediate continuation. */
static __attribute__((cold,noinline)) int gfx_async_group_refresh(
        const struct MeshTask *first,struct AsyncGroup *group) {
    const uintptr_t begin=(uintptr_t)first->source,end=(uintptr_t)group->next;
    for(unsigned i=group->hint_cursor;i<async_hint_count;++i) {
        const uintptr_t source=(uintptr_t)async_hint_sources[i];
        if(source>=begin&&source<end) {group->members=0;return 0;}
    }
    group->hint_epoch=async_hint_epoch;group->hint_cursor=async_hint_count;
    return 1;
}
static __attribute__((cold,noinline)) const struct AsyncGroup *gfx_async_group_build(
        const struct MeshTask *first,unsigned cull) {
    struct AsyncGroup *group=&async_groups[first-mesh_tasks];
    uint32_t generation;
    ++gfx_async_group_stats[3];
    for(unsigned attempt=0;attempt<3;++attempt) {
        uint32_t descriptors[32][6];
        const uint32_t epoch=async_hint_epoch;
        generation=gfx_gpu_cram_source_generation();
        const struct MeshTask *task=first;
        Gfx *next=first->next;
        unsigned members=0,triangles=0,caller_sensitive=0;uint32_t mask=0;
        while(task&&members<32&&!task->prefer_legacy) {
            const unsigned proof=gfx_async_task_proof(task);
            if(!task->dead_after&&proof!=1)caller_sensitive=1;
            if(!proof)break;
            const unsigned count=(task->source->words.w0>>12)&255u;
            const uintptr_t source=(uintptr_t)task->source->words.w1;
            const uintptr_t end=(uintptr_t)__EH_FRAME_BEGIN__;
            if(!count||count>32||(source&3u)||source<(uintptr_t)&_start||
               source>=end||count>(end-source)/sizeof(Vtx))break;
            uint32_t vertex_offset,command_offset;
            if(!gfx_async_sources(task,(const void *)source,count,
                                  &vertex_offset,&command_offset))break;
            if(generation!=gfx_gpu_cram_source_generation()||epoch!=async_hint_epoch)break;
            uint32_t *d=descriptors[members];
            d[0]=vertex_offset;d[1]=task->source->words.w0;d[2]=command_offset;
            d[3]=task->commands|((cull&3u)<<8);
            d[4]=(uint32_t)source;d[5]=(uint32_t)(uintptr_t)(task->source+1);
            ++members;triangles+=task->triangles;
            mask|=count==32?0xffffffffu:((1u<<count)-1u);
            next=task->next;
            if(members==32||(next->words.w0>>24)!=(uint8_t)G_VTX)break;
            task=gfx_mesh_lookup_impl(next,0,0);
        }
        if(generation!=gfx_gpu_cram_source_generation()||epoch!=async_hint_epoch) {
            ++gfx_async_group_stats[4];continue;
        }
        if(!members)break;
        uint32_t offset=0;
        if(members>1&&!gfx_gpu_async_group_upload(&descriptors[0][0],members,generation,&offset)) {
            if(generation!=gfx_gpu_cram_source_generation()||epoch!=async_hint_epoch) {
                ++gfx_async_group_stats[4];continue;
            }
            break;
        }
        if(epoch!=async_hint_epoch) {++gfx_async_group_stats[4];continue;}
        group->next=next;group->generation=generation;group->hint_epoch=epoch;
        group->caller_sensitive=caller_sensitive;group->return_command=async_return_command;
        group->hint_cursor=async_hint_count;
        group->descriptor_offset=offset;group->loaded_mask=mask;
        group->members=members;group->triangles=triangles;group->cull=cull;
        if(members<2)++gfx_async_group_stats[6];
        return members>1?group:NULL;
    }
    group->members=0;++gfx_async_group_stats[5];return NULL;
}
static inline const struct AsyncGroup *gfx_async_group_sources(
        const struct MeshTask *first,unsigned cull) {
    if(!gfx_gpu_async_group_supported())return NULL;
    struct AsyncGroup *group=&async_groups[first-mesh_tasks];
    const uint32_t generation=gfx_gpu_cram_source_generation();
    if(group->members&&group->generation==generation&&group->cull==cull&&
       (!group->caller_sensitive||group->return_command==async_return_command)&&
       (group->hint_epoch==async_hint_epoch||gfx_async_group_refresh(first,group))) {
        ++gfx_async_group_stats[2];
        return group->members>1?group:NULL;
    }
    return gfx_async_group_build(first,cull);
}
/* Only current-generation rows may enter TFP1/BIM2. Lazy canonical slots
 * refer to that same raw bank; every surviving slot is copied before reuse. */
static uint32_t load_bram_slots, load_bram_generation;
static unsigned load_bram_first;
static uint32_t load_queued_safe, load_queued_hash[4];
static uint32_t load_lazy_slots;
static unsigned load_lazy_first;
/* Probe state is reset to unknown in both sides of the native same-ELF test. */
unsigned sm64_triangle_front_capability;
/* attempted batches/triangles, accepted batches/triangles, kept/clip/cull,
 * hardware fallback, bank fallback, lazy slots, materialized slots/calls,
 * polling iterations, protocol/timeout errors, scope skips, absent probes. */
uint32_t gfx_front_stats[16];
#define TRI_FRONT_REGS ((volatile uint32_t *)0x4b000000u)
static int gfx_triangle_front_supported(void) {
    if (!sm64_triangle_front_capability) {
        sm64_triangle_front_capability = TRI_FRONT_REGS[0x11] == 0x54465031u ? 1u : 2u;
        if (sm64_triangle_front_capability == 2u) gfx_front_stats[15]++;
    }
    return sm64_triangle_front_capability == 1u;
}
/* calls, launched, successful, vertices, ineligible, absent/disabled,
 * unsupported, read errors, source-pool fallback, timeout-drained, other error, writes. */
uint32_t load_driver_stats[16];
static unsigned load_alias_present,load_alias_dirty=3,load_alias_controls_valid;
static uint32_t load_alias_scales,load_alias_lighting;
static inline uint32_t load_alias_bits(float f) {
    uint32_t u;memcpy(&u,&f,4);return u;
}
static __attribute__((noinline)) void load_alias_copy(uint32_t *restrict d,
                                                     const uint32_t *restrict s,
                                                     unsigned count) {
    for(unsigned i=0;i<count;i++,s+=8,d+=9) {
        d[0]=s[0];d[1]=s[1];d[2]=s[2];d[3]=s[3];d[4]=s[4];
        uint32_t uv=s[5];float u=(float)(int16_t)uv,v=(float)(int16_t)(uv>>16);
        memcpy(d+5,&u,4);memcpy(d+6,&v,4);d[7]=s[6];d[8]=s[7];
    }
}
/* Copy contiguous needed runs, preserving the raw-to-canonical conversion
 * and leaving untouched slots (including already materialized fog alpha)
 * alone. No current matrix, texture, light or fog state enters this copy. */
static unsigned load_alias_transaction;
static __attribute__((noinline)) void load_alias_materialize(uint32_t mask) {
    uint32_t needed = load_lazy_slots & mask;
    if (!needed) return;
    gfx_front_stats[11]++;
    while (needed) {
        unsigned first = (unsigned)__builtin_ctz(needed), count = 1;
        while (first + count < 32 && (needed & (1u << (first + count)))) ++count;
        uint32_t copied = count == 32 ? 0xffffffffu : ((1u << count) - 1u) << first;
        load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],
                        LOAD_ALIAS_VIEW + (first - load_lazy_first) * 8u, count);
        load_lazy_slots &= ~copied;
        needed &= ~copied;
        gfx_front_stats[10] += count;
    }
    if(!load_lazy_slots)gfx_gpu_async_raw_release_if_pending();
}
/* Outside the G_VTX survivor barrier, pending slots are one contiguous
 * successful LOAD block. The first ordinary CPU reader copies it once;
 * subsequent triangle culling has no per-slot materialization overhead. */
static __attribute__((noinline)) void load_alias_materialize_all(void) {
    const uint32_t needed = load_lazy_slots;
    if (!needed) return;
    const unsigned first = (unsigned)__builtin_ctz(needed);
    const unsigned count = 32u - (unsigned)__builtin_clz(needed) - first;
    load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],
                    LOAD_ALIAS_VIEW + (first - load_lazy_first) * 8u, count);
    load_lazy_slots = 0;
    gfx_front_stats[10] += count;
    gfx_front_stats[11]++;
    gfx_gpu_async_raw_release_if_pending();
}
void gfx_async_materialize_raw(void) {
    /* Progress waits are forbidden between installing legacy context and
     * registering its completed result. Fail closed if a future edit violates
     * that boundary; otherwise a drained but unregistered result could be lost. */
    if(load_alias_transaction)__builtin_trap();
    while(LOAD_ALIAS_REGS->status&1u)gfx_gpu_async_service();
    load_alias_materialize_all();
}
static inline void load_alias_materialize_triangle(unsigned a, unsigned b, unsigned c) {
    uint32_t mask = (a < 32 ? 1u << a : 0u) | (b < 32 ? 1u << b : 0u)
                  | (c < 32 ? 1u << c : 0u);
    if (load_lazy_slots & mask) load_alias_materialize_all();
}
static inline void load_alias_discard(unsigned count) {
    __asm__ volatile("fence rw,rw" ::: "memory");
    for(uintptr_t p=(uintptr_t)LOAD_ALIAS_VIEW,
                  end=p+count*32u;p<end;p+=64u)
        __asm__ volatile(".insn i 0x0f,2,x0,%0,0" :: "r"(p) : "memory");
    __asm__ volatile("fence rw,rw" ::: "memory");
}
static __attribute__((noinline)) int load_alias_vertex_legacy(unsigned count,unsigned first,
                                                      const Vtx *vertices,float aspect,
                                                      int eager_fog) {
    load_driver_stats[0]++;
    const int priority=gfx_gpu_async_supported();
    const uintptr_t p=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    const unsigned lit=(rsp.geometry_mode&G_LIGHTING)!=0;
    unsigned frm;__asm__ volatile("csrr %0,frm" : "=r"(frm));
    if(!count||first>=32||count>32-first||(p&3)||p<(uintptr_t)&_start||p>=end||
       count>(end-p)/sizeof(Vtx)||eager_fog||frm||
       (rsp.geometry_mode&G_TEXTURE_GEN)||(lit&&rsp.current_num_lights!=2)) {
        load_driver_stats[4]++;return 0;
    }
    /* Packet generation reads the same raw rows. The previous marker was
     * force-published, so this wait cannot depend on unpublished CPU staging. */
    gfx_gpu_mesh_wait_source();
    if(!load_alias_present) {
        load_alias_present=LOAD_ALIAS_REGS->id==LOAD_ALIAS_ID?1:2;
        if(load_alias_present==1) {
            /* A previous app may have left a job running. Read-only input
             * DMA must drain before any new context is installed. */
            if(!priority)while(LOAD_ALIAS_REGS->status&1u)gfx_gpu_queued_front_service();
            load_alias_dirty=3;load_alias_controls_valid=0;
        }
    }
    if(load_alias_present!=1) {load_driver_stats[5]++;return 0;}
    if(!priority&&(LOAD_ALIAS_REGS->status&1u)) {
        while(LOAD_ALIAS_REGS->status&1u)gfx_gpu_queued_front_service();
        load_alias_present=2;load_driver_stats[10]++;return 0;
    }
    uint32_t source_offset;
    uint32_t command_offset=0;
    if(mesh_armed ? !gfx_gpu_mesh_sources(vertices,count,mesh_armed->source+1,
                         mesh_armed->commands,&source_offset,&command_offset)
                  : !gfx_gpu_cram_vertex_source(vertices,count,&source_offset)) {
        load_driver_stats[8]++;return 0;
    }
    if(priority) {
#if GFX_LOAD_SUBMIT_OVERLAP
        /* Submission can wait on older jobs. It must precede the lease and
         * START, not run before this new result has acquired a lazy-slot owner. */
        if(load_submit_count){load_submit_stats[1]++;gfx_cpu_submit_pending();}
#endif
        if(gfx_gpu_async_raw_acquire()) {
            load_alias_dirty=3;load_alias_controls_valid=0;
        }
        load_alias_transaction=1;
    }
    unsigned writes=3;
    if(load_alias_dirty&1) {
        for(unsigned i=0;i<16;i++)
            LOAD_ALIAS_REGS->matrix[i]=load_alias_bits(rsp.MP_matrix[i/4][i%4]);
        LOAD_ALIAS_REGS->aspect=load_alias_bits(aspect);load_alias_dirty&=~1u;writes+=17;
    }
    if(lit&&(load_alias_dirty&2)) {
        for(unsigned i=0;i<3;i++)
            LOAD_ALIAS_REGS->coefficient[i]=load_alias_bits(rsp.current_lights_coeffs[0][i]);
        const uint8_t *a=rsp.current_lights[1].col,*d=rsp.current_lights[0].col;
        LOAD_ALIAS_REGS->ambient=a[0]|((uint32_t)a[1]<<8)|((uint32_t)a[2]<<16);
        LOAD_ALIAS_REGS->diffuse=d[0]|((uint32_t)d[1]<<8)|((uint32_t)d[2]<<16);
        load_alias_dirty&=~2u;writes+=5;
    }
    uint32_t scales=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    if(!load_alias_controls_valid||scales!=load_alias_scales) {
        LOAD_ALIAS_REGS->scales=scales;load_alias_scales=scales;writes++;
    }
    if(!load_alias_controls_valid||lit!=load_alias_lighting) {
        LOAD_ALIAS_REGS->lighting=lit;load_alias_lighting=lit;writes++;
    }
    load_alias_controls_valid=1;
    LOAD_ALIAS_REGS->source=source_offset;LOAD_ALIAS_REGS->shape=count;
    /* BIM2 copies raw rows into an immutable snapshot. Only that short copy
     * reserves the raw bank; queued GPU imports do not delay the next START. */
    if(!priority) {
        gfx_gpu_queued_front_wait_source();
        gfx_gpu_bram_import_wait();
    }
    load_bram_slots=0;
    load_queued_safe=0;
    if(mesh_armed) {
        if(!++mesh_serial)++mesh_serial;
        MESH_REGS[0x22]=mesh_serial;
        MESH_REGS[0x23]=mesh_armed->source->words.w0;
        MESH_REGS[0x24]=command_offset;
        const unsigned cull=((rsp.geometry_mode&G_CULL_FRONT)?1u:0u)
                            |((rsp.geometry_mode&G_CULL_BACK)?2u:0u);
        MESH_REGS[0x25]=mesh_armed->commands|(cull<<8);
        writes+=4;
    }
    __asm__ volatile("fence rw,rw" ::: "memory");
    if(mesh_armed){MESH_REGS[0x26]=1;++gfx_mesh_stats[3];}
    else LOAD_ALIAS_REGS->control=1;
    load_driver_stats[1]++;load_driver_stats[11]+=writes;
#if GFX_LOAD_SUBMIT_OVERLAP
    if (load_submit_count) {
        load_submit_stats[1]++;
        gfx_cpu_submit_pending();
    }
#endif
    if(mesh_armed) {
        /* Classification is synchronous in this first integrated prototype.
         * RSP/RDP cannot advance before geometry and packet validation agree. */
        unsigned ms;
        do {ms=MESH_REGS[0x21];}
        while((ms&MESH_OWNER)&&!(ms&(MESH_PREFLIGHT_READY|MESH_GEOMETRY_FALLBACK)));
        if(ms&MESH_PROTOCOL_ERROR)++gfx_mesh_stats[14];
        if(!(ms&MESH_OWNER)||!(ms&(MESH_PREFLIGHT_READY|MESH_GEOMETRY_FALLBACK))||!(ms&MESH_RAW_SUCCESS)) {
            gfx_mesh_discard();
            ++gfx_mesh_stats[14];++load_driver_stats[10];return 0;
        }
        load_alias_discard(count);
        load_lazy_first=first;
        load_lazy_slots=count==32?0xffffffffu:((1u<<count)-1u)<<first;
        gfx_front_stats[9]+=count;
        ++gfx_mesh_stats[4];++load_driver_stats[2];load_driver_stats[3]+=count;
        if(ms&(MESH_GEOMETRY_FALLBACK|MESH_PROTOCOL_ERROR)) {
            ++gfx_mesh_stats[9];gfx_mesh_discard();
        } else {
            mesh_raw_completed=1;
        }
        /* On geometry fallback raw data are still canonical input. The
         * following original TRI handlers materialize them without redoing
         * transform/light/reciprocal work. ACK retains the alias contents. */
        return 1;
    }
    unsigned remaining=4096,status;
    /* Completion polling is frequent; repair needs bounded progress, not a
     * second MMIO read on every iteration. Ring/fence waits still service it
     * immediately, and timeout draining below remains unconditional. */
    do {
        status=LOAD_ALIAS_REGS->status;
        if ((status & 1u) && !(remaining & 15u)) gfx_gpu_queued_front_service();
    } while ((status & 1u) && --remaining);
    const unsigned timed_out=(status&1u)!=0;
    if(timed_out) {
        /* No local reset may abandon an accepted AXI read. Drain first,
         * disable offload, then use the unchanged CPU fallback. */
        do {gfx_gpu_queued_front_service();status=LOAD_ALIAS_REGS->status;}while(status&1u);
        load_alias_present=2;load_driver_stats[9]++;
    }
    if(status&8u)load_driver_stats[6]++;
    if(status&0x60u)load_driver_stats[7]++;
    if(!timed_out&&(status&6u)==6u&&!(status&0xe8u)) {
        /* Discard every successful generation, including lazy ones. A later
         * sparse CPU fallback must never hit a previous raw alias cache line. */
        load_alias_discard(count);
        const uint32_t loaded = (count == 32 ? 0xffffffffu : ((1u << count) - 1u)) << first;
        const int queued = gfx_gpu_queued_front_supported();
        if (gfx_gpu_async_supported() || queued || (gfx_triangle_front_supported() && gfx_gpu_bram_import_supported())) {
            load_lazy_first = first;
            load_lazy_slots = loaded;
            gfx_front_stats[9] += count;
        } else {
            load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],LOAD_ALIAS_VIEW,count);
        }
        if(queued || gfx_gpu_bram_import_supported()) {
            load_bram_first=first;
            load_bram_generation=((const volatile uint32_t *)LOAD_ALIAS_REGS)[0x0e];
            load_bram_slots=(count==32?0xffffffffu:((1u<<count)-1u))<<first;
            if (queued) {
                load_queued_safe = TRI_FRONT_REGS[0x01b] << first;
                for (unsigned i = 0; i < 4; ++i) load_queued_hash[i] = TRI_FRONT_REGS[0x01c+i] << first;
            }
        }
        load_alias_transaction=0;
        load_driver_stats[2]++;load_driver_stats[3]+=count;return 1;
    }
    load_alias_transaction=0;
    gfx_gpu_async_raw_release_if_pending();
    load_driver_stats[10]++;return 0;
}
static int load_alias_vertex(unsigned count,unsigned first,const Vtx *vertices,
                             float aspect,int eager_fog) {
    if(gfx_gpu_async_supported()) {
        if(async_armed&&gfx_async_try_load(count,first,vertices,aspect,eager_fog))return 1;
        /* Source upload and pending submission can wait. The legacy driver
         * acquires only after those operations, immediately before context. */
    }
    return load_alias_vertex_legacy(count,first,vertices,aspect,eager_fog);
}
#else
#define LOAD_ALIAS_ENABLED 0
void gfx_async_materialize_raw(void) {}
void gfx_async_forget_sources(void) {}
#endif

static __attribute__((noinline)) void gfx_sp_vertex(size_t n_vertices, size_t dest_index, const Vtx *vertices) {
    SM64_PERF_SCOPE(SM64_PERF_VTX); SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_VERTEX);
#ifdef TARGET_POCKET
    uint32_t _pv = PROF_TMR();
#endif
    if (n_vertices == 0) {
#if GFX_LOAD_SUBMIT_OVERLAP
        if (load_submit_count) {
            load_submit_stats[2]++;
            gfx_cpu_submit_pending();
        }
#endif
        return;
    }
#if LOAD_ALIAS_ENABLED
    if(rsp.matrix_changed)load_alias_dirty|=1;
    if((rsp.geometry_mode&G_LIGHTING)&&rsp.lights_changed)load_alias_dirty|=2;
#endif
#if GFX_CPU_DEFERRED_FOG
    uint32_t overwritten = dest_index >= 32 ? 0 : n_vertices >= 32 - dest_index
        ? (0xffffffffu << dest_index) : ((1u << n_vertices) - 1u) << dest_index;
#if LOAD_ALIAS_ENABLED
    /* Do this for EVERY G_VTX, including software-only/ineligible LOADs.
     * Preserve old survivors before raw ownership or fog coefficients change;
     * overwritten lazy values are dead and can be dropped without reading. */
    load_alias_materialize(~overwritten);
    load_lazy_slots &= ~overwritten;
    if(!load_lazy_slots)gfx_gpu_async_raw_release_if_pending();
    /* All preceding runs have captured this generation in the ring. End
     * snapshot ownership before replacing any producer vertex state. */
    gfx_gpu_queued_front_release();
#endif
    gfx_cpu_flush_fog(~overwritten);
    cpu_fog_pending &= ~overwritten;
    const bool fog_active = configEnableFog && (rsp.geometry_mode & G_FOG);
    const bool defer_fog = fog_active && (rdp.other_mode_l >> 30) == G_BL_CLR_FOG
        && dest_index < 32 && n_vertices <= 32 - dest_index;
    const bool eager_fog = fog_active && !defer_fog;
    if (defer_fog) {
        cpu_fog_mul = rsp.fog_mul;
        cpu_fog_offset = rsp.fog_offset;
        cpu_fog_pending = overwritten;
    }
#endif
#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
    uint32_t overwritten=dest_index>=32?0:n_vertices>=32-dest_index?
        (0xffffffffu<<dest_index):((1u<<n_vertices)-1u)<<dest_index;
    gfx_flush_vertex_attributes(~overwritten);
    vertex_attributes_pending&=~overwritten;
    vertex_cull_release(dest_index,n_vertices);
#endif
    if (rsp.matrix_changed) {
        SM64_PERF_SCOPE(SM64_PERF_VTX_MATRIX);
#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
        ++geometry_mvp_epoch;
#endif
        gfx_matrix_mul(rsp.MP_matrix,
                       rsp.modelview_matrix_stack[rsp.modelview_matrix_stack_size - 1],
                       rsp.P_matrix);
        rsp.matrix_changed = false;
    }
    if (rsp.geometry_mode & G_LIGHTING) {
        SM64_PERF_SCOPE(SM64_PERF_VTX_ATTR);
        if (rsp.lights_changed) {
#ifdef TARGET_OPENFPGA
            ++lighting_epoch;
#endif
            for (int i = 0; i < rsp.current_num_lights - 1; i++)
                calculate_normal_dir(&rsp.current_lights[i], rsp.current_lights_coeffs[i]);
            rsp.lights_changed = false;
        }
        if ((rsp.geometry_mode & G_TEXTURE_GEN) && rsp.lookat_changed) {
            static const Light_t lookat_x = {{0, 0, 0}, 0, {0, 0, 0}, 0, {127, 0, 0}, 0};
            static const Light_t lookat_y = {{0, 0, 0}, 0, {0, 0, 0}, 0, {0, 127, 0}, 0};
            calculate_normal_dir(&lookat_x, rsp.current_lookat_coeffs[0]);
            calculate_normal_dir(&lookat_y, rsp.current_lookat_coeffs[1]);
            rsp.lookat_changed = false;
        }
    }
#ifdef TARGET_OPENFPGA
    /* G_VTX rewrites these slots: their GPU cache copies (if any) go stale.
     * (Captured before the loop below advances dest_index.) */
    if (dest_index < 32) {
        size_t n = n_vertices;
        if (n > 32 - dest_index) n = 32 - dest_index;
        vc_slot_stale |= (n >= 32 ? 0xFFFFFFFFu : ((1u << n) - 1u)) << dest_index;
#if LOAD_ALIAS_ENABLED
        load_bram_slots &= ~((n >= 32 ? 0xFFFFFFFFu : ((1u << n) - 1u)) << dest_index);
#endif
    }
#endif
    const float aspect_scale = gfx_vertex_aspect_scale;
#if LOAD_ALIAS_ENABLED
    if(load_alias_vertex(n_vertices,dest_index,vertices,aspect_scale,eager_fog)) {
#ifdef TARGET_POCKET
        prof_vtx += PROF_TMR() - _pv;
#endif
        return;
    }
#endif
#if GFX_LOAD_SUBMIT_OVERLAP
    if (load_submit_count) {
        load_submit_stats[2]++;
        gfx_cpu_submit_pending();
    }
#endif
#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
    const int geometry_result=geometry_prepare(vertices,n_vertices,dest_index,aspect_scale);
    if(geometry_result==2) {SM64_PERF_COUNT(SM64_PERF_OUT,n_vertices);return;}
    if(geometry_result==1) {
        SM64_PERF_COUNT(SM64_PERF_RAW,n_vertices);
        vertex_attributes_pending=overwritten;
        vertex_attributes_source=geometry_inputs;vertex_attributes_first=dest_index;
        vertex_attributes_count=n_vertices;
        for(unsigned i=0;i<n_vertices;i++) {
            rsp.loaded_vertices[dest_index+i].clip_rej=0;
            rsp.loaded_vertices[dest_index+i].color.a=geometry_inputs[i].v.cn[3];
        }
        return;
    }
#endif
    SM64_PERF_COUNT(SM64_PERF_CPU,n_vertices);
    { /* Time the complete fallback loop, including its UV/lighting/fog work.
       * No per-vertex clock reads are added. */
    SM64_PERF_SCOPE(SM64_PERF_VTX_CPU);
    // Hoist the loop-invariant MVP matrix into locals: d = &rsp.loaded_vertices[]
    // points *into* the rsp struct, so under -fno-strict-aliasing the per-vertex
    // d->color writes alias rsp.MP_matrix and force a 16-load reload every vertex.
    // True locals break that, letting the compiler keep the matrix in FP regs.
    const float m00 = rsp.MP_matrix[0][0], m01 = rsp.MP_matrix[0][1], m02 = rsp.MP_matrix[0][2], m03 = rsp.MP_matrix[0][3];
    const float m10 = rsp.MP_matrix[1][0], m11 = rsp.MP_matrix[1][1], m12 = rsp.MP_matrix[1][2], m13 = rsp.MP_matrix[1][3];
    const float m20 = rsp.MP_matrix[2][0], m21 = rsp.MP_matrix[2][1], m22 = rsp.MP_matrix[2][2], m23 = rsp.MP_matrix[2][3];
    const float m30 = rsp.MP_matrix[3][0], m31 = rsp.MP_matrix[3][1], m32 = rsp.MP_matrix[3][2], m33 = rsp.MP_matrix[3][3];
    for (size_t i = 0; i < n_vertices; i++, dest_index++) {
        const Vtx_t *v = &vertices[i].v;
        const Vtx_tn *vn = &vertices[i].n;
        struct LoadedVertex *d = &rsp.loaded_vertices[dest_index];

        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
        float x = (ox * m00 + oy * m10 + oz * m20 + m30) * aspect_scale;
        float y = ox * m01 + oy * m11 + oz * m21 + m31;
        float z = ox * m02 + oy * m12 + oz * m22 + m32;
        float w = ox * m03 + oy * m13 + oz * m23 + m33;

        short U = v->tc[0] * rsp.texture_scaling_factor.s >> 16;
        short V = v->tc[1] * rsp.texture_scaling_factor.t >> 16;

        if (rsp.geometry_mode & G_LIGHTING) {
#ifdef TARGET_OPENFPGA
            const uint32_t normal=gfx_lighting_normal_key(v->cn);
            struct LightingMemo *memo=&lighting_memo[(normal^(normal>>11))&255u];
            if(memo->epoch==lighting_epoch && memo->normal==normal) {
                /* The final fog/source-alpha assignment below overwrites
                 * this cached alpha before any consumer sees this vertex. */
                d->color = memo->color;
            } else {
#endif
            int r = rsp.current_lights[rsp.current_num_lights - 1].col[0];
            int g = rsp.current_lights[rsp.current_num_lights - 1].col[1];
            int b = rsp.current_lights[rsp.current_num_lights - 1].col[2];

#ifdef OF_DBG_FACE
            /* OF_DBG_FACE probe: detect the beige Mario-skin diffuse light
             * (col ~ (254,193,121)) and record its n.coeffs/127 intensity, so
             * we can tell whether the face's SHADE is a BRIGHT diffuse result
             * or only the DARK ambient term. Bounded by of_face_lines. */
            int   of_face_hit = 0;
            float of_face_int = 0.0f;
#endif
            for (int i = 0; i < rsp.current_num_lights - 1; i++) {
                float intensity = 0;
                intensity += vn->n[0] * RSPV_TO_FLOAT(rsp.current_lights_coeffs[i][0]);
                intensity += vn->n[1] * RSPV_TO_FLOAT(rsp.current_lights_coeffs[i][1]);
                intensity += vn->n[2] * RSPV_TO_FLOAT(rsp.current_lights_coeffs[i][2]);
                intensity /= 127.0f;
#ifdef OF_DBG_FACE
                {
                    int lr = rsp.current_lights[i].col[0];
                    int lg = rsp.current_lights[i].col[1];
                    int lb = rsp.current_lights[i].col[2];
                    if (lr > 240 && lg >= 180 && lg <= 210 &&
                        lb >= 110 && lb <= 130) {
                        of_face_hit = 1;
                        of_face_int = intensity;
                    }
                }
#endif
                if (intensity > 0.0f) {
                    r += intensity * rsp.current_lights[i].col[0];
                    g += intensity * rsp.current_lights[i].col[1];
                    b += intensity * rsp.current_lights[i].col[2];
                }
            }

            d->color.r = r < 0 ? 0 : r > 255 ? 255 : r;
            d->color.g = g < 0 ? 0 : g > 255 ? 255 : g;
            d->color.b = b < 0 ? 0 : b > 255 ? 255 : b;
#ifdef OF_DBG_FACE
            if (of_face_hit) {
                static int of_face_lines = 0;
                if (of_face_lines < 100) {
                    of_face_lines++;
                    int amb_r = rsp.current_lights[rsp.current_num_lights - 1].col[0];
                    int amb_g = rsp.current_lights[rsp.current_num_lights - 1].col[1];
                    int amb_b = rsp.current_lights[rsp.current_num_lights - 1].col[2];
                    printf("OF_DBG_FACE: intensity=%.4f  amb=(%d,%d,%d)  "
                           "shade=(%d,%d,%d)  %s\n",
                           of_face_int, amb_r, amb_g, amb_b,
                           d->color.r, d->color.g, d->color.b,
                           of_face_int > 0.0f ? "BRIGHT(diffuse)" : "DARK(ambient-only)");
                    fflush(stdout);
                }
            }
#endif

#ifdef TARGET_OPENFPGA
                memo->epoch=lighting_epoch;memo->normal=normal;
                memo->color.r=d->color.r;memo->color.g=d->color.g;memo->color.b=d->color.b;
            }
#endif

            if (rsp.geometry_mode & G_TEXTURE_GEN) {
                float dotx = 0, doty = 0;
                dotx += vn->n[0] * RSPV_TO_FLOAT(rsp.current_lookat_coeffs[0][0]);
                dotx += vn->n[1] * RSPV_TO_FLOAT(rsp.current_lookat_coeffs[0][1]);
                dotx += vn->n[2] * RSPV_TO_FLOAT(rsp.current_lookat_coeffs[0][2]);
                doty += vn->n[0] * RSPV_TO_FLOAT(rsp.current_lookat_coeffs[1][0]);
                doty += vn->n[1] * RSPV_TO_FLOAT(rsp.current_lookat_coeffs[1][1]);
                doty += vn->n[2] * RSPV_TO_FLOAT(rsp.current_lookat_coeffs[1][2]);

                U = (int32_t)((dotx / 127.0f + 1.0f) / 4.0f * rsp.texture_scaling_factor.s);
                V = (int32_t)((doty / 127.0f + 1.0f) / 4.0f * rsp.texture_scaling_factor.t);
            }
        } else {
            d->color.r = v->cn[0];
            d->color.g = v->cn[1];
            d->color.b = v->cn[2];
        }

        d->u = RSPV_FROM_INT(U);
        d->v = RSPV_FROM_INT(V);

        // trivial clip rejection
        d->clip_rej = 0;
        if (x < -w) d->clip_rej |= CLIP_LEFT;
        if (x >  w) d->clip_rej |= CLIP_RIGHT;
        if (y < -w) d->clip_rej |= CLIP_BOTTOM;
        if (y >  w) d->clip_rej |= CLIP_TOP;
        if (z < -w) d->clip_rej |= CLIP_FAR;
        if (z >  w) d->clip_rej |= CLIP_NEAR;

        d->x = x;
        d->y = y;
        d->z = z;
        d->w = w;
        d->w_inv = RSPV_RCP(w);

#if GFX_CPU_DEFERRED_FOG
        if (eager_fog) {
#else
        if (configEnableFog && (rsp.geometry_mode & G_FOG)) {
#endif
            float wf = w;
            float zf = z;
            float winv = (wf == 0.f) ? 1.f / 0.001f : d->w_inv;
            if (winv < 0.0f) winv = 32767.0f;
            float fog_z = zf * winv * rsp.fog_mul + rsp.fog_offset;
            int fog_i = (int)fog_z;
            d->color.a = (fog_i < 0) ? 0 : (fog_i > 255) ? 255 : fog_i;
        } else {
            d->color.a = v->cn[3];
        }
    }
    }
#ifdef TARGET_POCKET
    prof_vtx += PROF_TMR() - _pv;
#endif
}

static inline struct ColorCombiner *gfx_pick_combiner(bool *out_use_fog, bool *out_use_alpha) {
    uint32_t cc_id = rdp.combine_mode;

    bool use_alpha = (rdp.other_mode_l & (G_BL_A_MEM << 18)) == 0;
    const bool use_fog = configEnableFog && (rdp.other_mode_l >> 30) == G_BL_CLR_FOG;
    const bool texture_edge = (rdp.other_mode_l & CVG_X_ALPHA) == CVG_X_ALPHA;
    const bool use_noise = (rdp.other_mode_l & G_AC_DITHER) == G_AC_DITHER;

    if (texture_edge) {
        use_alpha = true;
    }

    if (use_alpha) cc_id |= SHADER_OPT_ALPHA;
    if (use_fog) cc_id |= SHADER_OPT_FOG;
    if (texture_edge) cc_id |= SHADER_OPT_TEXTURE_EDGE;
    if (use_noise) cc_id |= SHADER_OPT_NOISE;

    if (!use_alpha) {
        cc_id &= ~0xfff000;
    }

    struct ColorCombiner *comb = gfx_lookup_or_create_color_combiner(cc_id);
    struct ShaderProgram *prg = comb->prg;
    if (prg != rendering_state.shader_program) {
#ifdef TARGET_POCKET
        g_fln_comb++;
#endif
        gfx_flush();
        gfx_rapi->unload_shader(rendering_state.shader_program);
        gfx_rapi->load_shader(prg);
        rendering_state.shader_program = prg;
    }
    if (use_alpha != rendering_state.alpha_blend) {
#ifdef TARGET_POCKET
        g_fln_comb++;
#endif
        gfx_flush();
        gfx_rapi->set_use_alpha(use_alpha);
        rendering_state.alpha_blend = use_alpha;
    }
    /* ALPHA_CVG_SEL is independent of use_alpha (see gfx_rendering_api.h): an
     * opaque surface sets it yet reports use_alpha false, so a backend that
     * discards transparent texels by value has to be told separately. */
    if (gfx_rapi->set_alpha_cvg_sel != NULL) {
        const bool alpha_cvg_sel = (rdp.other_mode_l & ALPHA_CVG_SEL) == ALPHA_CVG_SEL;
        if (alpha_cvg_sel != rendering_state.alpha_cvg_sel) {
            gfx_flush();
            gfx_rapi->set_alpha_cvg_sel(alpha_cvg_sel);
            rendering_state.alpha_cvg_sel = alpha_cvg_sel;
        }
    }

    if (out_use_fog) *out_use_fog = use_fog;
    if (out_use_alpha) *out_use_alpha = use_alpha;

    return comb;
}

static inline bool gfx_update_textures(const bool used_textures[2], const bool linear_filter) {
    SM64_PERF_SCOPE(SM64_PERF_TEX);
    for (int i = 0; i < 2; i++) {
        if (used_textures[i]) {
            if (rdp.textures_changed[i]) {
                /* Dedup: only break the batch + re-import when the texture actually
                 * differs from the last import on this tile (addr/fmt/siz/line).  A
                 * pure re-bind of the same texture keeps g_cur_tex/rendering_state
                 * pointing at it, so skipping is binding-safe and grows batches. */
                const uint8_t *taddr = rdp.loaded_texture[i].addr;
                uint8_t  tfmt = rdp.texture_tile.fmt, tsiz = rdp.texture_tile.siz;
                uint32_t tline = rdp.texture_tile.line_size_bytes;
                uint32_t tsize = rdp.loaded_texture[i].size_bytes;
                const uint8_t *tpalette = tfmt == G_IM_FMT_CI ? rdp.palette : NULL;
                if (taddr != s_tex_sig_addr[i] || tfmt != s_tex_sig_fmt[i]
                    || tsiz != s_tex_sig_siz[i] || tline != s_tex_sig_line[i]
                    || tsize != s_tex_sig_size[i] || tpalette != s_tex_sig_palette[i]) {
#ifdef TARGET_POCKET
                    g_fln_tex++;
#endif
                    gfx_flush();
#ifdef TARGET_POCKET
                    uint32_t _pi = PROF_TMR();
#endif
                    import_texture(i);
#ifdef TARGET_POCKET
                    prof_tex_import += PROF_TMR() - _pi;
#endif
                    s_tex_sig_addr[i] = taddr; s_tex_sig_fmt[i] = tfmt;
                    s_tex_sig_siz[i]  = tsiz;  s_tex_sig_line[i] = tline;
                    s_tex_sig_size[i] = tsize; s_tex_sig_palette[i] = tpalette;
                }
                rdp.textures_changed[i] = false;
            }
            if (linear_filter != rendering_state.textures[i]->linear_filter || rdp.texture_tile.cms != rendering_state.textures[i]->cms || rdp.texture_tile.cmt != rendering_state.textures[i]->cmt) {
#ifdef TARGET_POCKET
                g_fln_tex++;
#endif
                gfx_flush();
                gfx_rapi->set_sampler_parameters(i, linear_filter, rdp.texture_tile.cms, rdp.texture_tile.cmt);
                rendering_state.textures[i]->linear_filter = linear_filter;
                rendering_state.textures[i]->cms = rdp.texture_tile.cms;
                rendering_state.textures[i]->cmt = rdp.texture_tile.cmt;
            }
        }
    }
    return used_textures[0] || used_textures[1];
}

#ifdef TARGET_OPENFPGA
/* Resolve one combiner colour input for a vertex, mirroring the buf_vbo fill
 * switch in gfx_push_triangle (PRIM/SHADE/ENV/LOD; the LOD distance comes
 * from the triangle's FIRST vertex, exactly like the legacy path). */
static inline const struct RGBA *gfx_vc_resolve(uint8_t ccmux,
                                                const struct LoadedVertex *v,
                                                const struct LoadedVertex *v1,
                                                struct RGBA *tmp) {
    switch (ccmux) {
        case CC_PRIM:  return &rdp.prim_color;
        case CC_SHADE: return &v->color;
        case CC_ENV:   return &rdp.env_color;
        case CC_LOD: {
            float distance_frac = (RSPV_TO_FLOAT(v1->w) - 3000.0f) / 3000.0f;
            if (distance_frac < 0.0f) distance_frac = 0.0f;
            if (distance_frac > 1.0f) distance_frac = 1.0f;
            tmp->r = tmp->g = tmp->b = tmp->a = distance_frac * 255.0f;
            return tmp;
        }
        default:
            memset(tmp, 0, sizeof(*tmp));
            return tmp;
    }
}

/* These inputs have the same lifetime as triangle_material_ready. A cold
 * material refreshes eligibility after all backend setters and defers the
 * texture signature check until the first eligible cached triangle/run. */
static int triangle_vc_elig, triangle_vc_rgb_input, triangle_vc_alpha_input;
static bool triangle_vc_texture_ready;

static inline __attribute__((always_inline)) bool gfx_vc_texture_state(const struct ColorCombiner *comb, int textured, float bias) {
        if (triangle_vc_texture_ready) return false;
        triangle_vc_texture_ready = true;
        const uint8_t *taddr = textured ? rdp.loaded_texture[0].addr : NULL;
        const uint8_t  tbias = bias != 0.0f;
        if (taddr != vc_tex_sig.addr ||
            comb->cc_id != vc_tex_sig.cc_id ||
            rdp.texture_tile.uls != vc_tex_sig.uls ||
            rdp.texture_tile.ult != vc_tex_sig.ult ||
            rsp.texture_scaling_factor.s != vc_tex_sig.scale_s ||
            rsp.texture_scaling_factor.t != vc_tex_sig.scale_t ||
            (uint8_t)(textured != 0) != vc_tex_sig.textured ||
            tbias != vc_tex_sig.bias ||
            rdp.texture_tile.cms != vc_tex_sig.cms ||
            rdp.texture_tile.cmt != vc_tex_sig.cmt) {
            vc_slot_stale = 0xFFFFFFFFu;
            vc_tex_sig.addr     = taddr;
            vc_tex_sig.cc_id    = comb->cc_id;
            vc_tex_sig.uls      = rdp.texture_tile.uls;
            vc_tex_sig.ult      = rdp.texture_tile.ult;
            vc_tex_sig.scale_s  = rsp.texture_scaling_factor.s;
            vc_tex_sig.scale_t  = rsp.texture_scaling_factor.t;
            vc_tex_sig.textured = (uint8_t)(textured != 0);
            vc_tex_sig.bias     = tbias;
            vc_tex_sig.cms      = rdp.texture_tile.cms;
            vc_tex_sig.cmt      = rdp.texture_tile.cmt;
            return true;
        }
    return false;
}

/* GPU vertex-cache fast path: try to draw this triangle as a 1-word 0x54
 * against the resident G_VTX slots, first uploading any stale slot via 0x56
 * (clip x/y/w + the s/t and rgb565 the 0x4E path would have computed).
 * Returns 1 when emitted; 0 -> caller uses the flattened buf_vbo/0x4E path.
 * Per-tri requirements on top of the backend's material eligibility (caps
 * bit 29, truecolor, not HILITE/cd, not decal, not blend/XLU): all 3 verts
 * must live in rsp.loaded_vertices[0..31] (clipper-manufactured and rect
 * scratch verts fail the address-range test) and w must fit Q16.16 (the
 * backend applies a further 1/256 pre-scale, so 32767 is conservative). */
/* Only call after the unchanged byte-range check for the 32 cache slots.
 * For the 36-byte layout, 0 <= offset < 1152 and q=offset>>2 is at most
 * 287. floor(q*57/512) is exactly floor(q/9), hence floor(offset/36).
 * Keep the ordinary quotient if another build changes LoadedVertex. */
static inline __attribute__((always_inline)) size_t gfx_vc_slot_index(uintptr_t offset) {
    if (sizeof(struct LoadedVertex) == 36)
        return ((offset >> 2) * 57u) >> 9;
    return offset / sizeof(struct LoadedVertex);
}

static int gfx_vc_try_push(const struct LoadedVertex *v1,
                           const struct LoadedVertex *v2,
                           const struct LoadedVertex *v3,
                           const struct ColorCombiner *comb, bool use_fog,
                           bool raw_geometry) {
#if !GFX_GPU_GEOMETRY_WORKER_APP
    /* No caller can submit deferred vertices in this executable. Keep the
     * ordinary clip-space upload path and let the compiler drop raw mode. */
    raw_geometry = false;
#endif
    const int rgb_in = triangle_vc_rgb_input, alpha_in = triangle_vc_alpha_input;
    const int elig = triangle_vc_elig;
    bool direct = false;
    const struct LoadedVertex *v_arr[3] = {v1, v2, v3};
    if (!elig)
        goto per_tri_fallback;
    if(raw_geometry && ((rgb_in>=0 &&
            comb->shader_input_mapping[0][rgb_in]==CC_LOD)
            || (alpha_in>=0 && comb->shader_input_mapping[1][alpha_in]==CC_LOD)))
        return 0;
    uint8_t slot[3];
    unsigned dirty = 0;
    float w_first = 0.0f;
    int   w_uniform = 1;
    for (int i = 0; i < 3; i++) {
        /* Clipped vertices can live on the stack. Subtracting unrelated
         * pointers is undefined; range-check byte addresses first. */
        const uintptr_t offset = (uintptr_t)v_arr[i] - (uintptr_t)rsp.loaded_vertices;
        if (offset >= 32 * sizeof(struct LoadedVertex))
            goto per_tri_direct;            /* no stable slot */
        const size_t idx = gfx_vc_slot_index(offset);
        if(!raw_geometry) {
            const float w = RSPV_TO_FLOAT(v_arr[i]->w);
            if (!(w > 0.0f && w < 32767.0f))
                goto per_tri_direct;
            if (i == 0) w_first = w;
            else if (w != w_first) w_uniform = 0;
        }
        slot[i] = (uint8_t)idx;
        if (vc_slot_stale & (1u << idx))
            dirty |= 1u << i;
    }

    /* Fog interpolation is not consumed by this GPU backend. Its shade
     * alpha still matters when a later material disables fog; materialize
     * that exceptional triangle before it uses the saved fog snapshot. */
    if(raw_geometry && !use_fog && alpha_in>=0 &&
       comb->shader_input_mapping[1][alpha_in]==CC_SHADE &&
       (geometry_vertices[slot[0]].fog || geometry_vertices[slot[1]].fog || geometry_vertices[slot[2]].fog))
        return 0;

    /* ORTHOGRAPHIC TRIANGLES MUST NOT RIDE THE CACHE.
     *
     * The cached path ships clip-space x/y/w (struct gfx_vc_vtx) and lets the
     * GPU do the divide and derive its planes from the 1/w gradients.  An ortho
     * projection has a zero fourth column -- segment2.c's matrix_fullscreen is
     * m03=m13=m23=0, m33=1 -- so gfx_sp_vertex's
     *     w = ox*m03 + oy*m13 + oz*m23 + m33
     * yields exactly 1.0 for every vertex.  Constant w means d(1/w)/dx and
     * d(1/w)/dy are both zero, the derive has no gradient to work from, and the
     * triangle collapses: the intro's "(c) 1996 Nintendo" strip rendered as a
     * line.  (It only showed up there because HUD/menu rects reach the GPU via
     * gfx_draw_rectangle, whose scratch verts already fail the loaded_vertices
     * address test above -- the intro strip is loaded by a real gsSPVertex and
     * is the rare 2D draw that gets this far.)
     *
     * The flattened 0x4E path projects on the CPU and sends screen-space
     * coordinates plus an explicit zi, so it has no such degeneracy.  Exact
     * float equality is the right test: these w's are all literally m33, while
     * three bit-identical w's in perspective geometry effectively never happen,
     * so this costs no measurable cached-triangle throughput. */
    if (!raw_geometry && w_uniform)
        goto per_tri_direct;
    goto prepare_payload;

per_tri_direct:
    /* Deferred worker vertices require the original materialization path.
     * This optimization only changes the CPU-computed representation. */
#if GFX_GPU_GEOMETRY_WORKER_APP
    goto per_tri_fallback;
#endif
    if (raw_geometry) goto per_tri_fallback;
    direct = true;
    dirty = 7u;
    if (buf_vbo_len && !buf_vbo_direct) gfx_flush();

prepare_payload: ;
    /* Surface alpha: the legacy path takes the csel input's ALPHA-cycle value
     * of the batch's first vertex (with fog, shade alpha carries the fog
     * intensity, so it reads as opaque — same exception as the fill loop). */
    uint8_t surf_a = 255;
    if (alpha_in >= 0) {
        struct RGBA tmpa;
        const struct RGBA *ac =
            gfx_vc_resolve(comb->shader_input_mapping[1][alpha_in], v1, v1, &tmpa);
        surf_a = (use_fog && ac == &v1->color) ? 255 : ac->a;
    }

    struct gfx_vc_vtx cached_vertices[3];
    struct gfx_vc_vtx *vc = direct
        ? &buf_vbo_storage.direct[3 * buf_vbo_num_tris] : cached_vertices;
    const int   textured = elig & GFX_VC_TEXTURED;
    const float uofs = textured ? (float)(rdp.texture_tile.uls * 8) : 0.0f;
    const float vofs = textured ? (float)(rdp.texture_tile.ult * 8) : 0.0f;
    const float bias = (textured &&
                        (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT)
                       ? 0.5f : 0.0f;

    /* Texture-affecting state changed since the last cached batch?  Wipe
     * ALL slots — their baked s/t are relative to the OLD tile (see the
     * vc_tex_sig note; uls/ult moves arrive with no flush at all, so the
     * material dirty flag alone does not cover this). */
    if (gfx_vc_texture_state(comb, textured, bias)) dirty = 7u;

    /* LOD shade is derived from the first vertex of each triangle, so it
     * cannot be reused as a property of a shared GPU slot. */
    if (rgb_in >= 0 && comb->shader_input_mapping[0][rgb_in] == CC_LOD)
        dirty = 7u;
    if(raw_geometry) {
        uint32_t attributes=0;
        for(unsigned i=0;i<3;i++)if(dirty&(1u<<i))attributes|=1u<<slot[i];
        gfx_flush_vertex_attributes(attributes);
    }
    vc[0].a = surf_a;
    for (int i = 0; i < 3; i++) {
        if (!(dirty & (1u << i)))
            continue;
        const struct LoadedVertex *v = v_arr[i];
        vc[i].use_exact_depth=0;
        if(!raw_geometry) {
            vc[i].cx = RSPV_TO_FLOAT(v->x);
            vc[i].cy = RSPV_TO_FLOAT(v->y);
            vc[i].cw = RSPV_TO_FLOAT(v->w);
            vc[i].w_inv = RSPV_TO_FLOAT(v->w_inv);
            if(!direct && (geometry_valid & (1u<<slot[i]))) {
                vc[i].use_exact_depth=1;
                vc[i].exact_depth=geometry_depth[slot[i]];
            }
        }
        if (textured) {
            vc[i].u = (RSPV_TO_FLOAT(v->u) - uofs) / 32.0f + bias;
            vc[i].v = (RSPV_TO_FLOAT(v->v) - vofs) / 32.0f + bias;
        } else {
            vc[i].u = vc[i].v = 0.0f;       /* untextured truecolor: s=t=0 */
        }
        if (rgb_in >= 0) {
            struct RGBA tmpc;
            const struct RGBA *c =
                gfx_vc_resolve(comb->shader_input_mapping[0][rgb_in], v, v1, &tmpc);
            vc[i].r = c->r; vc[i].g = c->g; vc[i].b = c->b;
        } else {
            vc[i].r = vc[i].g = vc[i].b = 255;  /* backend sends 0xFFFF anyway */
        }
        vc[i].a = surf_a;
    }

    if (direct) {
        /* Keep exactly the old fallback batching and flush boundaries.
         * len remains an occupied-float count for the shared pending test. */
        buf_vbo_direct = true;
        buf_vbo_len += 3 * sizeof(struct gfx_vc_vtx) / sizeof(float);
        if (++buf_vbo_num_tris == MAX_BUFFERED) gfx_flush();
        return 1;
    }

    /* Ring draw order: buffered 0x4E triangles must land before this one
     * (rare — only when clipped-fan fallbacks interleave with cached tris
     * inside one material batch). */
    if (buf_vbo_len > 0)
        gfx_flush();

    if(raw_geometry) {
        const int16_t *positions[3];const int32_t *matrices[3];uint32_t serial[3];
        for(int i=0;i<3;i++) {
            positions[i]=geometry_vertices[slot[i]].pos;
            const struct GeometryContext *context=&geometry_contexts[geometry_vertices[slot[i]].context];
            matrices[i]=context->matrix;serial[i]=context->serial;
        }
        unsigned cull=((rsp.geometry_mode&G_CULL_FRONT)?1u:0u)
                     |((rsp.geometry_mode&G_CULL_BACK)?2u:0u);
        gfx_gpu_geometry_tri(vc,slot,dirty,positions,matrices,serial,cull);
        sm64_geometry_triangles++;
    } else gfx_gpu_vtx_cache_tri(vc, slot, dirty);
    if (dirty & 1u) vc_slot_stale &= ~(1u << slot[0]);
    if (dirty & 2u) vc_slot_stale &= ~(1u << slot[1]);
    if (dirty & 4u) vc_slot_stale &= ~(1u << slot[2]);
    return 1;

per_tri_fallback:
    if(raw_geometry)return 0;
    if (buf_vbo_len && buf_vbo_direct) gfx_flush();
    /* Preserve original coordinates even when a whole material falls back. */
    if (buf_vbo_num_tris < GFX_VC_MAX_BUFFERED) {
        struct gfx_vc_true_xyw *tv = &gfx_vc_true[buf_vbo_num_tris];
        tv->depth_mask=0;
        for (int i = 0; i < 3; i++) {
            tv->x[i] = RSPV_TO_FLOAT(v_arr[i]->x);
            tv->y[i] = RSPV_TO_FLOAT(v_arr[i]->y);
            tv->w[i] = RSPV_TO_FLOAT(v_arr[i]->w);
            uintptr_t offset=(uintptr_t)v_arr[i]-(uintptr_t)rsp.loaded_vertices;
            if(offset<32*sizeof(struct LoadedVertex)) {
                unsigned slot=gfx_vc_slot_index(offset);
                if(geometry_valid&(1u<<slot)) {
                    tv->exact_depth[i]=geometry_depth[slot];
                    tv->depth_mask|=1u<<i;
                }
            }
        }
        tv->valid = 1;
    }
    return 0;
}
#endif /* TARGET_OPENFPGA */

#ifdef TARGET_OPENFPGA
/* Vertex loads, matrix commands and display-list calls preserve material
 * state. Commands that can change the material invalidate this memo. */
static bool triangle_material_ready;
static struct ColorCombiner *triangle_material_comb;
static bool triangle_material_fog,triangle_material_alpha,triangle_material_texture;
#endif

static inline __attribute__((always_inline)) struct ColorCombiner *gfx_prepare_triangle_material(bool *fog_out, bool *alpha_out, bool *texture_out) {
    bool use_fog, use_alpha, use_texture;
    struct ColorCombiner *comb;
#ifdef TARGET_OPENFPGA
    if(triangle_material_ready) {
        comb=triangle_material_comb;use_fog=triangle_material_fog;
        use_alpha=triangle_material_alpha;use_texture=triangle_material_texture;
    } else {
#endif
    SM64_PERF_SCOPE(SM64_PERF_MAT);
    const bool depth_test = (rsp.geometry_mode & G_ZBUFFER) == G_ZBUFFER;
    if (depth_test != rendering_state.depth_test) {
#ifdef TARGET_POCKET
        g_fln_zst++;
#endif
        gfx_flush();
        gfx_rapi->set_depth_test(depth_test);
        rendering_state.depth_test = depth_test;
    }

    const bool z_upd = (rdp.other_mode_l & Z_UPD) == Z_UPD;
    if (z_upd != rendering_state.depth_mask) {
#ifdef TARGET_POCKET
        g_fln_zst++;
#endif
        gfx_flush();
        gfx_rapi->set_depth_mask(z_upd);
        rendering_state.depth_mask = z_upd;
    }

    const bool zmode_decal = (rdp.other_mode_l & ZMODE_DEC) == ZMODE_DEC;
    if (zmode_decal != rendering_state.decal_mode) {
#ifdef TARGET_POCKET
        g_fln_zst++;
#endif
        gfx_flush();
        gfx_rapi->set_zmode_decal(zmode_decal);
        rendering_state.decal_mode = zmode_decal;
    }

    if (rdp.viewport_or_scissor_changed) {
        if (memcmp(&rdp.viewport, &rendering_state.viewport, sizeof(rdp.viewport)) != 0) {
#ifdef TARGET_POCKET
            g_fln_vp++;
#endif
            gfx_flush();
            gfx_rapi->set_viewport(rdp.viewport.x, rdp.viewport.y, rdp.viewport.width, rdp.viewport.height);
            rendering_state.viewport = rdp.viewport;
        }
        if (memcmp(&rdp.scissor, &rendering_state.scissor, sizeof(rdp.scissor)) != 0) {
#ifdef TARGET_POCKET
            g_fln_vp++;
#endif
            gfx_flush();
            gfx_rapi->set_scissor(rdp.scissor.x, rdp.scissor.y, rdp.scissor.width, rdp.scissor.height);
            rendering_state.scissor = rdp.scissor;
        }
        rdp.viewport_or_scissor_changed = false;
    }

    comb = gfx_pick_combiner(&use_fog, &use_alpha);

    const bool linear_filter = configFiltering && (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT;
#ifdef TARGET_POCKET
    uint32_t _pt = PROF_TMR();
#endif
    use_texture = gfx_update_textures(comb->used_textures, linear_filter);
#ifdef TARGET_POCKET
    prof_tex += PROF_TMR() - _pt;
#endif

#ifdef TARGET_OPENFPGA
        triangle_vc_rgb_input = triangle_vc_alpha_input = -1;
        triangle_vc_elig = gfx_gpu_vtx_cache_begin(&triangle_vc_rgb_input,
                                                &triangle_vc_alpha_input);
        triangle_vc_texture_ready = false;
        triangle_material_comb=comb;triangle_material_fog=use_fog;
        triangle_material_alpha=use_alpha;triangle_material_texture=use_texture;
        triangle_material_ready=true;
    }
#endif
    *fog_out=use_fog; *alpha_out=use_alpha; *texture_out=use_texture;
    return comb;
}

static inline int gfx_push_triangle(const struct LoadedVertex *restrict v1, const struct LoadedVertex *restrict v2, const struct LoadedVertex *restrict v3, bool geometry_only) {
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
    geometry_only = false;
#endif
    const struct LoadedVertex *v_arr[3] = {v1, v2, v3};

    bool use_fog, use_alpha, use_texture;
    struct ColorCombiner *comb=gfx_prepare_triangle_material(&use_fog,&use_alpha,&use_texture);
    const uint8_t num_inputs=comb->num_inputs;
    const bool *used_textures=comb->used_textures;

#ifdef OF_DBG_SHADE
    /* G_CC_SHADE main face: exactly one color input, no texture, and that input
     * is SHADE (per-vertex color). Print the 3 vertices' SHADE RGB. Bounded to
     * frames 0..2 and ~400 lines so the title-screen head is fully captured but
     * output stays small. */
    if (of_dbg_frame >= 0 && of_dbg_frame < 3 && of_dbg_lines < 400 &&
        num_inputs == 1 && !use_texture &&
        comb->shader_input_mapping[0][0] == CC_SHADE) {
        fprintf(stderr,
            "SHADE f%d v0=(%3u,%3u,%3u) v1=(%3u,%3u,%3u) v2=(%3u,%3u,%3u)\n",
            of_dbg_frame,
            v_arr[0]->color.r, v_arr[0]->color.g, v_arr[0]->color.b,
            v_arr[1]->color.r, v_arr[1]->color.g, v_arr[1]->color.b,
            v_arr[2]->color.r, v_arr[2]->color.g, v_arr[2]->color.b);
        of_dbg_lines++;
    }
#endif
#ifdef TARGET_OPENFPGA
    /* GPU vertex-cache fast path: G_VTX-resident verts upload once (0x56),
     * the triangle goes out as a 1-word 0x54.  Falls through to the flattened
     * buf_vbo/0x4E path when the material or the verts are ineligible. */
    if (gfx_vc_try_push(v1, v2, v3, comb, use_fog, geometry_only))
        return 1;
    if(geometry_only)return 0;
#else
    /* Tile-rect dims used to NORMALIZE texels to [0,1] for the desktop softras.
     * The GPU path (TARGET_OPENFPGA) emits RAW texels instead — see below. */
    const uint32_t tex_width = (rdp.texture_tile.lrs - rdp.texture_tile.uls + 4) / 4;
    const uint32_t tex_height = (rdp.texture_tile.lrt - rdp.texture_tile.ult + 4) / 4;
#endif

#ifndef GFX_W_PREMULT
    const bool z_is_from_0_to_1 = gfx_rapi->z_is_from_0_to_1();
#endif

    for (int i = 0; i < 3; i++) {
#ifdef GFX_W_PREMULT
        const rspv_t w = v_arr[i]->w;
        const rspv_t w_inv = v_arr[i]->w_inv;   /* cached per vertex, not per triangle */
        buf_vbo[buf_vbo_len++] = RSPV_MUL(v_arr[i]->x, w_inv);
        buf_vbo[buf_vbo_len++] = RSPV_MUL(v_arr[i]->y, w_inv);
        buf_vbo[buf_vbo_len++] = RSPV_MUL(v_arr[i]->z + w, RSPV_MUL(RSPV_HALF, w_inv));
        buf_vbo[buf_vbo_len++] = w_inv;
#else
        rspv_t z = v_arr[i]->z, w = v_arr[i]->w;
        if (z_is_from_0_to_1) {
            z = RSPV_MUL(z + w, RSPV_HALF);
        }
        buf_vbo[buf_vbo_len++] = v_arr[i]->x;
        buf_vbo[buf_vbo_len++] = v_arr[i]->y;
        buf_vbo[buf_vbo_len++] = z;
        buf_vbo[buf_vbo_len++] = w;
#endif

        if (use_texture) {
            float u = (RSPV_TO_FLOAT(v_arr[i]->u) - rdp.texture_tile.uls * 8) / 32.0f;
            float v = (RSPV_TO_FLOAT(v_arr[i]->v) - rdp.texture_tile.ult * 8) / 32.0f;
            if ((rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT) {
                u += 0.5f;
                v += 0.5f;
            }
#ifdef TARGET_OPENFPGA
            /* GPU path: emit RAW tile-relative texels (u,v are already in texels).
             * gfx_gpu scales by 65536 only, and the GPU strides the texel fetch by
             * the true uploaded width (st.tex_width = t->w), so a tile-rect vs
             * uploaded-size mismatch can no longer stretch/squash the texture.  The
             * old path (normalize by tile size here, re-multiply by uploaded size in
             * gfx_gpu) scaled texels by tile/upload, which differs from 1 whenever a
             * tile's sampled rect != its loaded dimensions. */
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(u);
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(v);
#else
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(u / tex_width);
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(v / tex_height);
#endif
        }

        if (use_fog) {
#ifndef GFX_NO_FOG_COLOR
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(rdp.fog_color.r));
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(rdp.fog_color.g));
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(rdp.fog_color.b));
#endif
            buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(v_arr[i]->color.a));
        }

        for (int j = 0; j < num_inputs; j++) {
            const struct RGBA *color;
            struct RGBA tmp;
            for (int k = 0; k < 1 + (use_alpha ? 1 : 0); k++) {
                switch (comb->shader_input_mapping[k][j]) {
                    case CC_PRIM:
                        color = &rdp.prim_color;
                        break;
                    case CC_SHADE:
                        color = &v_arr[i]->color;
                        break;
                    case CC_ENV:
                        color = &rdp.env_color;
                        break;
                    case CC_LOD:
                    {
                        float distance_frac = (RSPV_TO_FLOAT(v1->w) - 3000.0f) / 3000.0f;
                        if (distance_frac < 0.0f) distance_frac = 0.0f;
                        if (distance_frac > 1.0f) distance_frac = 1.0f;
                        tmp.r = tmp.g = tmp.b = tmp.a = distance_frac * 255.0f;
                        color = &tmp;
                        break;
                    }
                    default:
                        memset(&tmp, 0, sizeof(tmp));
                        color = &tmp;
                        break;
                }
                if (k == 0) {
                    buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(color->r));
                    buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(color->g));
                    buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(color->b));
                } else {
                    if (use_fog && color == &v_arr[i]->color) {
                        buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_ONE);
                    } else {
                        buf_vbo[buf_vbo_len++] = GFX_OUT_PROP(GFX_COLOR_CONVERT(color->a));
                    }
                }
            }
        }
    }
    if (++buf_vbo_num_tris == MAX_BUFFERED) {
        gfx_flush();
    }
    return 1;
}

#ifdef GFX_MANUAL_CLIPPING
static inline rspv_t rspv_lerp(const rspv_t v0, const rspv_t v1, const rspv_t t) {
    return v0 + RSPV_MUL(t, v1 - v0);
}

static inline struct RGBA rgba_lerp(const struct RGBA c0, const struct RGBA c1, const rspv_t t) {
    return (struct RGBA){
        c0.r + RSPV_TO_INT(RSPV_MUL(t, RSPV_FROM_INT(c1.r - c0.r))),
        c0.g + RSPV_TO_INT(RSPV_MUL(t, RSPV_FROM_INT(c1.g - c0.g))),
        c0.b + RSPV_TO_INT(RSPV_MUL(t, RSPV_FROM_INT(c1.b - c0.b))),
        c0.a + RSPV_TO_INT(RSPV_MUL(t, RSPV_FROM_INT(c1.a - c0.a))),
    };
}

static inline bool gfx_clip_triangle(struct LoadedVertex *v1, struct LoadedVertex *v2, struct LoadedVertex *v3, const uint8_t clip_and) {
    const uint8_t clip_or = v1->clip_rej | v2->clip_rej | v3->clip_rej;

    if (!clip_or) return false;

    /* Only populated polygon entries are read. Avoid clearing the
     * other 21 vertices before copying the three input vertices. */
    struct LoadedVertex v_buf[2][12];
    v_buf[0][0] = *v1; v_buf[0][1] = *v2; v_buf[0][2] = *v3;
    int v_num[2] = { 3, 0 };
    int v_idx = 0;

    uint8_t plane_idx = 0;
    for (uint8_t clip_mask = 1; clip_mask < 64; clip_mask <<= 1, ++plane_idx) {
        if (!(clip_or & clip_mask)) continue;

        const int num_verts = v_num[v_idx];
        const int outidx = !v_idx;
        const struct LoadedVertex *v_in = v_buf[v_idx];
        struct LoadedVertex *v_out = v_buf[outidx];
        /* Select the actual float member by its byte offset; each offset
         * is aligned and points to x, y or z within this vertex object. */
        const size_t axis = plane_idx < 2 ? offsetof(struct LoadedVertex, z)
            : plane_idx < 4 ? offsetof(struct LoadedVertex, y) : offsetof(struct LoadedVertex, x);
        const bool positive = (plane_idx & 1u) != 0;
        rspv_t coord = *(const rspv_t *)((const uint8_t *)v_in + axis);
        rspv_t d1 = positive ? coord + v_in[0].w : v_in[0].w - coord;

        for (int i = 0; i < num_verts; ++i) {
            const struct LoadedVertex *vthis = &v_in[i];
            const struct LoadedVertex *vnext = &v_in[i + 1 == num_verts ? 0 : i + 1];
            coord = *(const rspv_t *)((const uint8_t *)vnext + axis);
            const rspv_t d2 = positive ? coord + vnext->w : vnext->w - coord;
            const bool this_in = d1 > 0;
            const bool next_in = d2 > 0;
            if (this_in) v_out[v_num[outidx]++] = *vthis;
            if (this_in ^ next_in) {
                struct LoadedVertex *xv = &v_out[v_num[outidx]++];
                xv->clip_rej = 0; /* Scratch vertices go directly to submission. */
                if (this_in) {
                    const rspv_t t = RSPV_DIV(d1, d1 - d2);
                    xv->x = rspv_lerp(vthis->x, vnext->x, t);
                    xv->y = rspv_lerp(vthis->y, vnext->y, t);
                    xv->z = rspv_lerp(vthis->z, vnext->z, t);
                    xv->w = rspv_lerp(vthis->w, vnext->w, t);
                    xv->u = rspv_lerp(vthis->u, vnext->u, t);
                    xv->v = rspv_lerp(vthis->v, vnext->v, t);
                    xv->color = rgba_lerp(vthis->color, vnext->color, t);
                    xv->w_inv = RSPV_RCP(xv->w);   /* w was just interpolated */
                } else {
                    const rspv_t t = RSPV_DIV(d2, d2 - d1);
                    xv->x = rspv_lerp(vnext->x, vthis->x, t);
                    xv->y = rspv_lerp(vnext->y, vthis->y, t);
                    xv->z = rspv_lerp(vnext->z, vthis->z, t);
                    xv->w = rspv_lerp(vnext->w, vthis->w, t);
                    xv->u = rspv_lerp(vnext->u, vthis->u, t);
                    xv->v = rspv_lerp(vnext->v, vthis->v, t);
                    xv->color = rgba_lerp(vnext->color, vthis->color, t);
                    xv->w_inv = RSPV_RCP(xv->w);   /* w was just interpolated */
                }
            }
            d1 = d2;
        }

        if (v_num[outidx] < 3) return true;

        v_idx = outidx;
        v_num[!v_idx] = 0;
    }

    const int n = v_num[v_idx] - 1;
    const struct LoadedVertex *in = v_buf[v_idx];
    for (int i = 1; i < n; ++i)
        gfx_push_triangle(in + 0, in + i, in + i + 1, false);

    return true;
}
#endif

static void gfx_sp_tri1(uint8_t vtx1_idx, uint8_t vtx2_idx, uint8_t vtx3_idx) {
    SM64_PERF_SCOPE(SM64_PERF_TRI); SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_TRIANGLE);
#if LOAD_ALIAS_ENABLED
    load_alias_materialize_triangle(vtx1_idx, vtx2_idx, vtx3_idx);
#endif
    struct LoadedVertex *v1 = &rsp.loaded_vertices[vtx1_idx];
    struct LoadedVertex *v2 = &rsp.loaded_vertices[vtx2_idx];
    struct LoadedVertex *v3 = &rsp.loaded_vertices[vtx3_idx];

#ifdef TARGET_POCKET
    tri_diag.total++;
#endif

#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
    if((vtx1_idx|vtx2_idx|vtx3_idx)<32 && culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        vertex_cull_materialize(vtx1_idx);vertex_cull_materialize(vtx2_idx);vertex_cull_materialize(vtx3_idx);
    }
    if((vtx1_idx|vtx2_idx|vtx3_idx)<32) {
        uint32_t mask=(1u<<vtx1_idx)|(1u<<vtx2_idx)|(1u<<vtx3_idx);
        if(geometry_valid&mask) {
            if(!GFX_GPU_GEOMETRY_REFERENCE && (geometry_valid&mask)==mask) {
                if(geometry_reject(vtx1_idx,vtx2_idx,vtx3_idx))return;
                if(gfx_push_triangle(v1,v2,v3,true))return;
            }
            if(geometry_pending&mask) {
                gfx_flush_vertex_attributes(mask);
                geometry_materialize(vtx1_idx);
                geometry_materialize(vtx2_idx);
                geometry_materialize(vtx3_idx);
            }
        }
    }
#endif

    const uint8_t clip_and = v1->clip_rej & v2->clip_rej & v3->clip_rej;
    if (clip_and) {
#ifdef TARGET_POCKET
        tri_diag.clip_reject++;
#endif
        return;
    }

    if ((rsp.geometry_mode & G_CULL_BOTH) != 0) {
        rspv_t w1_inv = v1->w_inv;   /* cached in gfx_sp_vertex / the clipper */
        rspv_t w2_inv = v2->w_inv;
        rspv_t w3_inv = v3->w_inv;
        rspv_t dx1 = RSPV_MUL(v1->x, w1_inv) - RSPV_MUL(v2->x, w2_inv);
        rspv_t dy1 = RSPV_MUL(v1->y, w1_inv) - RSPV_MUL(v2->y, w2_inv);
        rspv_t dx2 = RSPV_MUL(v3->x, w3_inv) - RSPV_MUL(v2->x, w2_inv);
        rspv_t dy2 = RSPV_MUL(v3->y, w3_inv) - RSPV_MUL(v2->y, w2_inv);
        float cross = dx1 * dy2 - dy1 * dx2;

        if ((v1->w < 0) ^ (v2->w < 0) ^ (v3->w < 0))
            cross = -cross;

        switch (rsp.geometry_mode & G_CULL_BOTH) {
            case G_CULL_FRONT:
                if (cross <= 0.0f) {
#ifdef TARGET_POCKET
                    tri_diag.cull_reject++;
#endif
                    return;
                }
                break;
            case G_CULL_BACK:
                if (cross >= 0.0f) {
#ifdef TARGET_POCKET
                    tri_diag.cull_reject++;
#endif
                    return;
                }
                break;
            case G_CULL_BOTH:
#ifdef TARGET_POCKET
                tri_diag.cull_reject++;
#endif
                return;
        }
    }

#ifdef GFX_MANUAL_CLIPPING
    if (!gfx_clip_triangle(v1, v2, v3, clip_and))
#endif
    {
#ifdef TARGET_POCKET
        const uint8_t clip_or = v1->clip_rej | v2->clip_rej | v3->clip_rej;
        if (clip_or)
            tri_diag.clipped++;
        else
            tri_diag.direct++;
#endif
        gfx_push_triangle(v1, v2, v3, false);
    }
#ifdef TARGET_POCKET
    else {
        tri_diag.clipped++;
    }
#endif
}

static void gfx_sp_geometry_mode(uint32_t clear, uint32_t set) {
#ifdef TARGET_OPENFPGA
    ++vertex_state_epoch;
#endif
    rsp.geometry_mode &= ~clear;
    rsp.geometry_mode |= set;
}

static void gfx_calc_and_set_viewport(const Vp_t *viewport) {
    // 2 bits fraction
    float width = 2.0f * viewport->vscale[0] / 4.0f;
    float height = 2.0f * viewport->vscale[1] / 4.0f;
    float x = (viewport->vtrans[0] / 4.0f) - width / 2.0f;
    float y = SCREEN_HEIGHT - ((viewport->vtrans[1] / 4.0f) + height / 2.0f);

    width *= ratio_x;
    height *= ratio_y;
    x *= ratio_x;
    y *= ratio_y;

    rdp.viewport.x = x;
    rdp.viewport.y = y;
    rdp.viewport.width = width;
    rdp.viewport.height = height;

    rdp.viewport_or_scissor_changed = true;
}

static void gfx_sp_movemem(uint8_t index, uint8_t offset, const void* data) {
#ifdef TARGET_OPENFPGA
    ++vertex_state_epoch;
#endif
    switch (index) {
        case G_MV_VIEWPORT:
            gfx_calc_and_set_viewport((const Vp_t *) data);
            break;
#if 0
        case G_MV_LOOKATY:
        case G_MV_LOOKATX:
            memcpy(rsp.current_lookat + (index - G_MV_LOOKATY) / 2, data, sizeof(Light_t));
            //rsp.lights_changed = 1;
            break;
#endif
#ifdef F3DEX_GBI_2
        case G_MV_LIGHT: {
            int lightidx = offset / 24 - 2;
            if (lightidx >= 0 && lightidx <= MAX_LIGHTS) { // skip lookat
                // NOTE: reads out of bounds if it is an ambient light
                memcpy(rsp.current_lights + lightidx, data, sizeof(Light_t));
                rsp.lights_changed = true;
            }
            break;
        }
#else
        case G_MV_L0:
        case G_MV_L1:
        case G_MV_L2:
            // NOTE: reads out of bounds if it is an ambient light
            memcpy(rsp.current_lights + (index - G_MV_L0) / 2, data, sizeof(Light_t));
            rsp.lights_changed = true;
            break;
#endif
    }
}

static void gfx_sp_moveword(uint8_t index, uint16_t offset, uint32_t data) {
#ifdef TARGET_OPENFPGA
    ++vertex_state_epoch;
#endif
    switch (index) {
        case G_MW_NUMLIGHT:
#ifdef F3DEX_GBI_2
            rsp.current_num_lights = data / 24 + 1; // add ambient light
#else
            // Ambient light is included
            // The 31th bit is a flag that lights should be recalculated
            rsp.current_num_lights = (data - 0x80000000U) / 32;
#endif
            rsp.lights_changed = 1;
            break;
        case G_MW_FOG:
            rsp.fog_mul = (int16_t)(data >> 16);
            rsp.fog_offset = (int16_t)data;
            break;
    }
}

static void gfx_sp_texture(uint16_t sc, uint16_t tc, uint8_t level, uint8_t tile, uint8_t on) {
#ifdef TARGET_OPENFPGA
    ++vertex_state_epoch;
#endif
    rsp.texture_scaling_factor.s = sc;
    rsp.texture_scaling_factor.t = tc;
}

static void gfx_dp_set_scissor(uint32_t mode, uint32_t ulx, uint32_t uly, uint32_t lrx, uint32_t lry) {
    float x = ulx / 4.0f * ratio_x;
    float y = (SCREEN_HEIGHT - lry / 4.0f) * ratio_y;
    float width = (lrx - ulx) / 4.0f * ratio_x;
    float height = (lry - uly) / 4.0f * ratio_y;

    rdp.scissor.x = x;
    rdp.scissor.y = y;
    rdp.scissor.width = width;
    rdp.scissor.height = height;

    rdp.viewport_or_scissor_changed = true;
}

static void gfx_dp_set_texture_image(uint32_t format, uint32_t size, uint32_t width, const void* addr) {
    rdp.texture_to_load.addr = addr;
    rdp.texture_to_load.siz = size;
}

static void gfx_dp_set_tile(uint8_t fmt, uint32_t siz, uint32_t line, uint32_t tmem, uint8_t tile, uint32_t palette, uint32_t cmt, uint32_t maskt, uint32_t shiftt, uint32_t cms, uint32_t masks, uint32_t shifts) {
    if (tile == G_TX_RENDERTILE) {
        SUPPORT_CHECK(palette == 0); // palette should set upper 4 bits of color index in 4b mode
        rdp.texture_tile.fmt = fmt;
        rdp.texture_tile.siz = siz;
        rdp.texture_tile.cms = cms;
        rdp.texture_tile.cmt = cmt;
        rdp.texture_tile.line_size_bytes = line * 8;
        rdp.textures_changed[0] = true;
        rdp.textures_changed[1] = true;
    }

    if (tile == G_TX_LOADTILE) {
        rdp.texture_to_load.tile_number = tmem / 256;
    }
}

static void gfx_dp_set_tile_size(uint8_t tile, uint16_t uls, uint16_t ult, uint16_t lrs, uint16_t lrt) {
    if (tile == G_TX_RENDERTILE) {
        rdp.texture_tile.uls = uls;
        rdp.texture_tile.ult = ult;
        rdp.texture_tile.lrs = lrs;
        rdp.texture_tile.lrt = lrt;
        rdp.textures_changed[0] = true;
        rdp.textures_changed[1] = true;
    }
}

static void gfx_dp_load_tlut(uint8_t tile, uint32_t high_index) {
    SUPPORT_CHECK(tile == G_TX_LOADTILE);
    SUPPORT_CHECK(rdp.texture_to_load.siz == G_IM_SIZ_16b);
    rdp.palette = rdp.texture_to_load.addr;
    rdp.textures_changed[0] = rdp.textures_changed[1] = true;
}

static void gfx_dp_load_block(uint8_t tile, uint32_t uls, uint32_t ult, uint32_t lrs, uint32_t dxt) {
    if (tile == 1) return;
    SUPPORT_CHECK(tile == G_TX_LOADTILE);
    SUPPORT_CHECK(uls == 0);
    SUPPORT_CHECK(ult == 0);

    // The lrs field rather seems to be number of pixels to load
    uint32_t word_size_shift;
    switch (rdp.texture_to_load.siz) {
        case G_IM_SIZ_4b:
            word_size_shift = 0; // Or -1? It's unused in SM64 anyway.
            break;
        case G_IM_SIZ_8b:
            word_size_shift = 0;
            break;
        case G_IM_SIZ_16b:
            word_size_shift = 1;
            break;
        case G_IM_SIZ_32b:
            word_size_shift = 2;
            break;
    }
    uint32_t size_bytes = (lrs + 1) << word_size_shift;
    rdp.loaded_texture[rdp.texture_to_load.tile_number].size_bytes = size_bytes;
    assert(size_bytes <= 4096 && "bug: too big texture");
    rdp.loaded_texture[rdp.texture_to_load.tile_number].addr = rdp.texture_to_load.addr;

    rdp.textures_changed[rdp.texture_to_load.tile_number] = true;
}

static void gfx_dp_load_tile(uint8_t tile, uint32_t uls, uint32_t ult, uint32_t lrs, uint32_t lrt) {
    if (tile == 1) return;
    SUPPORT_CHECK(tile == G_TX_LOADTILE);
    SUPPORT_CHECK(uls == 0);
    SUPPORT_CHECK(ult == 0);

    uint32_t word_size_shift;
    switch (rdp.texture_to_load.siz) {
        case G_IM_SIZ_4b:
            word_size_shift = 0;
            break;
        case G_IM_SIZ_8b:
            word_size_shift = 0;
            break;
        case G_IM_SIZ_16b:
            word_size_shift = 1;
            break;
        case G_IM_SIZ_32b:
            word_size_shift = 2;
            break;
    }

    uint32_t size_bytes = (((lrs >> G_TEXTURE_IMAGE_FRAC) + 1) * ((lrt >> G_TEXTURE_IMAGE_FRAC) + 1)) << word_size_shift;
    rdp.loaded_texture[rdp.texture_to_load.tile_number].size_bytes = size_bytes;

    assert(size_bytes <= 4096 && "bug: too big texture");
    rdp.loaded_texture[rdp.texture_to_load.tile_number].addr = rdp.texture_to_load.addr;
    rdp.texture_tile.uls = uls;
    rdp.texture_tile.ult = ult;
    rdp.texture_tile.lrs = lrs;
    rdp.texture_tile.lrt = lrt;

    rdp.textures_changed[rdp.texture_to_load.tile_number] = true;
}


static uint8_t color_comb_component(uint32_t v) {
    switch (v) {
        case G_CCMUX_TEXEL0:
            return CC_TEXEL0;
        case G_CCMUX_TEXEL1:
            return CC_TEXEL1;
        case G_CCMUX_PRIMITIVE:
            return CC_PRIM;
        case G_CCMUX_SHADE:
            return CC_SHADE;
        case G_CCMUX_ENVIRONMENT:
            return CC_ENV;
        case G_CCMUX_TEXEL0_ALPHA:
            return CC_TEXEL0A;
        case G_CCMUX_LOD_FRACTION:
            return CC_LOD;
        default:
            return CC_0;
    }
}

static inline uint32_t color_comb(uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
    return color_comb_component(a) |
           (color_comb_component(b) << 3) |
           (color_comb_component(c) << 6) |
           (color_comb_component(d) << 9);
}

static void gfx_dp_set_combine_mode(uint32_t rgb, uint32_t alpha) {
    rdp.combine_mode = rgb | (alpha << 12);
}

static void gfx_dp_set_env_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
#ifdef TARGET_OPENFPGA
    if (rdp.env_color.r != r || rdp.env_color.g != g || rdp.env_color.b != b)
        vc_slot_stale = 0xFFFFFFFFu;
#endif
    rdp.env_color.r = r;
    rdp.env_color.g = g;
    rdp.env_color.b = b;
    rdp.env_color.a = a;
}

static void gfx_dp_set_prim_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
#ifdef TARGET_OPENFPGA
    if (rdp.prim_color.r != r || rdp.prim_color.g != g || rdp.prim_color.b != b)
        vc_slot_stale = 0xFFFFFFFFu;
#endif
    rdp.prim_color.r = r;
    rdp.prim_color.g = g;
    rdp.prim_color.b = b;
    rdp.prim_color.a = a;
}

static void gfx_dp_set_fog_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    rdp.fog_color.r = r;
    rdp.fog_color.g = g;
    rdp.fog_color.b = b;
    rdp.fog_color.a = a;
    if (gfx_rapi->set_fog_color) gfx_rapi->set_fog_color(&rdp.fog_color.r);
}

static void gfx_dp_set_fill_color(uint32_t packed_color) {
    uint16_t col16 = (uint16_t)packed_color;
    uint32_t r = col16 >> 11;
    uint32_t g = (col16 >> 6) & 0x1f;
    uint32_t b = (col16 >> 1) & 0x1f;
    uint32_t a = col16 & 1;
    rdp.fill_color.r = SCALE_5_8(r);
    rdp.fill_color.g = SCALE_5_8(g);
    rdp.fill_color.b = SCALE_5_8(b);
    rdp.fill_color.a = a * 255;
}

static void gfx_draw_rectangle(int32_t ulx, int32_t uly, int32_t lrx, int32_t lry) {
    SM64_PERF_SCOPE(SM64_PERF_TRI); SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_TRIANGLE);
    uint32_t saved_other_mode_h = rdp.other_mode_h;
    uint32_t cycle_type = (rdp.other_mode_h & (3U << G_MDSFT_CYCLETYPE));

    if (cycle_type == G_CYC_COPY) {
        rdp.other_mode_h = (rdp.other_mode_h & ~(3U << G_MDSFT_TEXTFILT)) | G_TF_POINT;
    }

    // U10.2 coordinates
    struct LoadedVertex* ul = &rsp.loaded_vertices[MAX_VERTICES + 0];
    struct LoadedVertex* ll = &rsp.loaded_vertices[MAX_VERTICES + 1];
    struct LoadedVertex* lr = &rsp.loaded_vertices[MAX_VERTICES + 2];
    struct LoadedVertex* ur = &rsp.loaded_vertices[MAX_VERTICES + 3];

    float ulxf = ulx;
    float ulyf = uly;
    float lrxf = lrx;
    float lryf = lry;

    ulxf = ulxf / (4.0f * HALF_SCREEN_WIDTH) - 1.0f;
    ulyf = -(ulyf / (4.0f * HALF_SCREEN_HEIGHT)) + 1.0f;
    lrxf = lrxf / (4.0f * HALF_SCREEN_WIDTH) - 1.0f;
    lryf = -(lryf / (4.0f * HALF_SCREEN_HEIGHT)) + 1.0f;
    ulxf = gfx_adjust_x_for_aspect_ratio(ulxf);
    lrxf = gfx_adjust_x_for_aspect_ratio(lrxf);

    ul->x = ulxf; ul->y = ulyf; ul->z = -1.0f; ul->w = 1.0f;
    ll->x = ulxf; ll->y = lryf; ll->z = -1.0f; ll->w = 1.0f;
    lr->x = lrxf; lr->y = lryf; lr->z = -1.0f; lr->w = 1.0f;
    ur->x = lrxf; ur->y = ulyf; ur->z = -1.0f; ur->w = 1.0f;

    // These vertices bypass the ordinary vertex loader. Initialize its cached
    // reciprocal and clip state too, so HUD rectangles retain valid depth/UVs.
    for (int i = MAX_VERTICES; i < MAX_VERTICES + 4; i++) {
        rsp.loaded_vertices[i].w_inv = 1.0f;
        rsp.loaded_vertices[i].clip_rej = 0;
    }

    // The coordinates for texture rectangle shall bypass the viewport setting
    struct XYWidthHeight default_viewport = {0, 0, gfx_current_dimensions.width, gfx_current_dimensions.height};
    struct XYWidthHeight viewport_saved = rdp.viewport;
    uint32_t geometry_mode_saved = rsp.geometry_mode;

    rdp.viewport = default_viewport;
    rdp.viewport_or_scissor_changed = true;
    rsp.geometry_mode = 0;

    gfx_sp_tri1(MAX_VERTICES + 0, MAX_VERTICES + 1, MAX_VERTICES + 3);
    gfx_sp_tri1(MAX_VERTICES + 1, MAX_VERTICES + 2, MAX_VERTICES + 3);

    rsp.geometry_mode = geometry_mode_saved;
    rdp.viewport = viewport_saved;
    rdp.viewport_or_scissor_changed = true;

    if (cycle_type == G_CYC_COPY) {
        rdp.other_mode_h = saved_other_mode_h;
    }
}

static void gfx_dp_texture_rectangle(int32_t ulx, int32_t uly, int32_t lrx, int32_t lry, uint8_t tile, int16_t uls, int16_t ult, int16_t dsdx, int16_t dtdy, bool flip) {
    uint32_t saved_combine_mode = rdp.combine_mode;
    if ((rdp.other_mode_h & (3U << G_MDSFT_CYCLETYPE)) == G_CYC_COPY) {
        // Per RDP Command Summary Set Tile's shift s and this dsdx should be set to 4 texels
        // Divide by 4 to get 1 instead
        dsdx >>= 2;

        // Color combiner is turned off in copy mode
        gfx_dp_set_combine_mode(color_comb(0, 0, 0, G_CCMUX_TEXEL0), color_comb(0, 0, 0, G_ACMUX_TEXEL0));

        // Per documentation one extra pixel is added in this modes to each edge
        lrx += 1 << 2;
        lry += 1 << 2;
    }

    // uls and ult are S10.5
    // dsdx and dtdy are S5.10
    // lrx, lry, ulx, uly are U10.2
    // lrs, lrt are S10.5
    if (flip) {
        dsdx = -dsdx;
        dtdy = -dtdy;
    }
    int16_t width = !flip ? lrx - ulx : lry - uly;
    int16_t height = !flip ? lry - uly : lrx - ulx;
    int lrs = ((uls << 7) + dsdx * width) >> 7;
    int lrt = ((ult << 7) + dtdy * height) >> 7;

    if (gfx_rapi->tex_rect) {
        const bool used_textures[2] = { true, false };
        gfx_pick_combiner(NULL, NULL);
        gfx_update_textures(used_textures, false);
        float ulxf = ulx * ratio_x;
        float ulyf = uly * ratio_y;
        float lrxf = lrx * ratio_x;
        float lryf = lry * ratio_y;
        const float dudx = ((lrs - (float)uls) / (lrxf - ulxf));
        const float dvdy = ((lrt - (float)ult) / (lryf - ulyf));
        ulxf = HALF_SCREEN_WIDTH + gfx_adjust_x_for_aspect_ratio(ulxf / 4.0f - HALF_SCREEN_WIDTH);
        lrxf = HALF_SCREEN_WIDTH + gfx_adjust_x_for_aspect_ratio(lrxf / 4.0f - HALF_SCREEN_WIDTH);
        ulyf = ulyf / 4.0f;
        lryf = lryf / 4.0f;
        gfx_rapi->tex_rect(ulxf, ulyf, lrxf, lryf, uls / 32.f, ult / 32.f, dudx / 8.f, dvdy / 8.f, &rdp.env_color.r);
    } else {
        struct LoadedVertex* ul = &rsp.loaded_vertices[MAX_VERTICES + 0];
        struct LoadedVertex* ll = &rsp.loaded_vertices[MAX_VERTICES + 1];
        struct LoadedVertex* lr = &rsp.loaded_vertices[MAX_VERTICES + 2];
        struct LoadedVertex* ur = &rsp.loaded_vertices[MAX_VERTICES + 3];
        ul->u = RSPV_FROM_INT(uls);
        ul->v = RSPV_FROM_INT(ult);
        lr->u = RSPV_FROM_INT(lrs);
        lr->v = RSPV_FROM_INT(lrt);
        if (!flip) {
            ll->u = RSPV_FROM_INT(uls);
            ll->v = RSPV_FROM_INT(lrt);
            ur->u = RSPV_FROM_INT(lrs);
            ur->v = RSPV_FROM_INT(ult);
        } else {
            ll->u = RSPV_FROM_INT(lrs);
            ll->v = RSPV_FROM_INT(ult);
            ur->u = RSPV_FROM_INT(uls);
            ur->v = RSPV_FROM_INT(lrt);
        }
        gfx_draw_rectangle(ulx, uly, lrx, lry);
    }

    rdp.combine_mode = saved_combine_mode;
}

static void gfx_dp_fill_rectangle(int32_t ulx, int32_t uly, int32_t lrx, int32_t lry) {
    if (rdp.color_image_address == rdp.z_buf_address) {
        // Don't clear Z buffer here since we already did it with glClear
        return;
    }
    uint32_t mode = (rdp.other_mode_h & (3U << G_MDSFT_CYCLETYPE));

    if (mode == G_CYC_COPY || mode == G_CYC_FILL) {
        // Per documentation one extra pixel is added in this modes to each edge
        lrx += 1 << 2;
        lry += 1 << 2;
    }

    const uint32_t saved_combine_mode = rdp.combine_mode;
    gfx_dp_set_combine_mode(color_comb(0, 0, 0, G_CCMUX_SHADE), color_comb(0, 0, 0, G_ACMUX_SHADE));

    if (gfx_rapi->fill_rect) {
        /* Immediate fills must follow any buffered triangles. */
        gfx_flush();
        gfx_pick_combiner(NULL, NULL);
        float ulxf = ulx * ratio_x;
        float ulyf = uly * ratio_y;
        float lrxf = lrx * ratio_x;
        float lryf = lry * ratio_y;
        ulxf = HALF_SCREEN_WIDTH + gfx_adjust_x_for_aspect_ratio(ulxf / 4.0f - HALF_SCREEN_WIDTH);
        lrxf = HALF_SCREEN_WIDTH + gfx_adjust_x_for_aspect_ratio(lrxf / 4.0f - HALF_SCREEN_WIDTH);
        ulyf = ulyf / 4.0f;
        lryf = lryf / 4.0f;
        gfx_rapi->fill_rect(ulxf, ulyf, lrxf, lryf, &rdp.fill_color.r);
    } else {
        for (int i = MAX_VERTICES; i < MAX_VERTICES + 4; i++) {
            struct LoadedVertex* v = &rsp.loaded_vertices[i];
            v->color = rdp.fill_color;
        }
        gfx_draw_rectangle(ulx, uly, lrx, lry);
    }

    rdp.combine_mode = saved_combine_mode;
}

static void gfx_dp_set_z_image(void *z_buf_address) {
    rdp.z_buf_address = z_buf_address;
}

static void gfx_dp_set_color_image(uint32_t format, uint32_t size, uint32_t width, void* address) {
    rdp.color_image_address = address;
}

static void gfx_sp_set_other_mode(uint32_t shift, uint32_t num_bits, uint64_t mode) {
    uint64_t mask = (((uint64_t)1 << num_bits) - 1) << shift;
    uint64_t om = rdp.other_mode_l | ((uint64_t)rdp.other_mode_h << 32);
    om = (om & ~mask) | mode;
#if GFX_CPU_DEFERRED_FOG
    /* The only assignment to other_mode_l: resolve the saved alpha before
     * any later draw can leave the backend's fog/opaque-shade convention. */
    if (cpu_fog_pending && ((uint32_t)om >> 30) != G_BL_CLR_FOG)
        gfx_cpu_flush_fog(0xffffffffu);
#endif
    rdp.other_mode_l = (uint32_t)om;
    rdp.other_mode_h = (uint32_t)(om >> 32);
}

static inline void *seg_addr(uintptr_t w1) {
    return (void *) w1;
}

#ifdef TARGET_OPENFPGA
/* Decode whole display-list commands so a two-triangle opcode is never half
 * consumed when the next triangle requires clipping, another alpha, or a
 * fallback material. Stop at every state change and keep painter order. */
static unsigned gfx_geometry_decode(const Gfx *cmd, unsigned slot[6]) {
    unsigned opcode=cmd->words.w0>>24;
    uint32_t word;
    unsigned count=1, divisor=2;
    if(opcode==(uint8_t)G_TRI1) {
#ifdef F3DEX_GBI_2
        word=cmd->words.w0;
#else
        word=cmd->words.w1;
#if !defined(F3DEX_GBI) && !defined(F3DLP_GBI)
        divisor=10;
#endif
#endif
#if defined(F3DEX_GBI) || defined(F3DLP_GBI)
    } else if(opcode==(uint8_t)G_TRI2) {
        word=cmd->words.w0; count=2;
        slot[3]=((cmd->words.w1>>16)&255u)/2;
        slot[4]=((cmd->words.w1>>8)&255u)/2;
        slot[5]=(cmd->words.w1&255u)/2;
#endif
    } else return 0;
    slot[0]=((word>>16)&255u)/divisor;
    slot[1]=((word>>8)&255u)/divisor;
    slot[2]=(word&255u)/divisor;
    return count;
}

static void gfx_geometry_submit(const struct ColorCombiner *comb,int elig,int rgb_in,
                                uint32_t used,const uint32_t *triangles,unsigned triangle_count,
                                uint8_t alpha) {
    const int textured=elig&GFX_VC_TEXTURED;
    const float bias=(textured &&
        (rdp.other_mode_h&(3U<<G_MDSFT_TEXTFILT))!=G_TF_POINT)?0.5f:0.0f;
    gfx_vc_texture_state(comb,textured,bias);
    uint32_t dirty=used&vc_slot_stale;
    struct gfx_geometry_upload uploads[32];
    unsigned count=0;
    const int32_t du=textured?-rdp.texture_tile.uls*16384+(bias!=0.0f?32768:0):0;
    const int32_t dv=textured?-rdp.texture_tile.ult*16384+(bias!=0.0f?32768:0):0;
    gfx_flush_vertex_attributes(dirty);
    uint32_t remaining=dirty;
    for(unsigned i=0;remaining;i++,remaining>>=1) if(remaining&1u) {
        const struct LoadedVertex *v=&rsp.loaded_vertices[i];
        const struct GeometryVertex *gv=&geometry_vertices[i];
        const struct GeometryContext *context=&geometry_contexts[gv->context];
        struct gfx_geometry_upload *upload=&uploads[count++];
        uint16_t rgb=0xffff;
        if(rgb_in>=0) {
            struct RGBA tmp;
            const struct RGBA *color=gfx_vc_resolve(comb->shader_input_mapping[0][rgb_in],v,v,&tmp);
            rgb=((uint16_t)(color->r>>3)<<11)|((uint16_t)(color->g>>2)<<5)|(color->b>>3);
        }
        upload->words[0]=i;
        upload->words[1]=(uint16_t)gv->pos[0]|((uint32_t)(uint16_t)gv->pos[1]<<16);
        upload->words[2]=(uint16_t)gv->pos[2]|((uint32_t)rgb<<16);
        upload->words[3]=textured?(uint32_t)((int32_t)v->u*2048+du):0;
        upload->words[4]=textured?(uint32_t)((int32_t)v->v*2048+dv):0;
        upload->matrix=context->matrix; upload->serial=context->serial;
    }
    if(buf_vbo_len>0)gfx_flush();
    gfx_gpu_geometry_batch(uploads,count,triangles,triangle_count,alpha);
    vc_slot_stale&=~dirty;
    sm64_geometry_triangles+=triangle_count;
}
#include "gfx_geometry_cache.h"

static Gfx *gfx_geometry_run(Gfx *first) {
    SM64_PERF_SCOPE(SM64_PERF_TRI);
    Gfx *cached=gfx_geometry_cached_run(first);
    if(cached!=first)return cached;
    Gfx *cmd=first;
    struct ColorCombiner *comb=NULL;
    int rgb_in=-1, alpha_in=-1, elig=0;
    bool use_fog,use_alpha,use_texture;
    uint32_t triangles[64], used=0;
    unsigned triangle_count=0, consumed=0;
    uint8_t alpha=255;
    unsigned cull=((rsp.geometry_mode&G_CULL_FRONT)?1u:0u)
                 |((rsp.geometry_mode&G_CULL_BACK)?2u:0u);
    for(;;) {
        unsigned slot[6];
        unsigned n=gfx_geometry_decode(cmd,slot);
        if(!n || triangle_count+n>64)break;
        uint32_t mask=0;
        for(unsigned i=0;i<3*n;i++) {
            if(slot[i]>=32)goto submit; /* original path handles this opcode */
            mask|=1u<<slot[i];
        }
        if((geometry_valid&mask)!=mask)break;
        if(!comb) {
            comb=gfx_prepare_triangle_material(&use_fog,&use_alpha,&use_texture);
            elig=gfx_gpu_vtx_cache_begin(&rgb_in,&alpha_in);
            if(!elig
                || (rgb_in>=0 && comb->shader_input_mapping[0][rgb_in]==CC_LOD)
                || (alpha_in>=0 && comb->shader_input_mapping[1][alpha_in]==CC_LOD))
                return first;
        }
        if(!use_fog && alpha_in>=0 && comb->shader_input_mapping[1][alpha_in]==CC_SHADE)
            for(unsigned i=0;i<3*n;i++)if(geometry_vertices[slot[i]].fog)goto submit;
        unsigned reject[2]; uint8_t candidate_alpha[2];
        uint8_t next_alpha=alpha;
        unsigned next_count=triangle_count;
        for(unsigned i=0;i<n;i++) {
            unsigned *v=&slot[3*i];
            reject[i]=geometry_reject(v[0],v[1],v[2]);
            candidate_alpha[i]=255;
            if(reject[i])continue;
            if(alpha_in>=0) {
                struct RGBA tmp;
                const struct LoadedVertex *vertex=&rsp.loaded_vertices[v[0]];
                candidate_alpha[i]=(use_fog && comb->shader_input_mapping[1][alpha_in]==CC_SHADE)
                    ?255:gfx_vc_resolve(comb->shader_input_mapping[1][alpha_in],vertex,vertex,&tmp)->a;
            }
            if(next_count && candidate_alpha[i]!=next_alpha)goto submit;
            next_alpha=candidate_alpha[i]; next_count++;
        }
        for(unsigned i=0;i<n;i++) if(!reject[i]) {
            unsigned *v=&slot[3*i];
            triangles[triangle_count++]=v[0]|(v[1]<<5)|(v[2]<<10)|(cull<<15);
            used|=(1u<<v[0])|(1u<<v[1])|(1u<<v[2]);
        }
        alpha=next_alpha; consumed+=n; cmd++;
    }
submit:
#ifdef TARGET_POCKET
    tri_diag.total+=consumed;
#endif
    if(!triangle_count)return cmd;
    gfx_geometry_submit(comb,elig,rgb_in,used,triangles,triangle_count,alpha);
    return cmd;
}
#endif


#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
/* Return the original clip/cull rejection class, without counters or draws.
 * The caller commits counters only after accepting the WHOLE TRI command. */
static inline int gfx_cpu_batch_reject(const struct LoadedVertex *v1,
                                      const struct LoadedVertex *v2,
                                      const struct LoadedVertex *v3) {
    if (v1->clip_rej & v2->clip_rej & v3->clip_rej) return 1;
    if ((rsp.geometry_mode & G_CULL_BOTH) != 0) {
        rspv_t w1_inv = v1->w_inv;
        rspv_t w2_inv = v2->w_inv;
        rspv_t w3_inv = v3->w_inv;
        rspv_t dx1 = RSPV_MUL(v1->x, w1_inv) - RSPV_MUL(v2->x, w2_inv);
        rspv_t dy1 = RSPV_MUL(v1->y, w1_inv) - RSPV_MUL(v2->y, w2_inv);
        rspv_t dx2 = RSPV_MUL(v3->x, w3_inv) - RSPV_MUL(v2->x, w2_inv);
        rspv_t dy2 = RSPV_MUL(v3->y, w3_inv) - RSPV_MUL(v2->y, w2_inv);
        float cross = dx1 * dy2 - dy1 * dx2;
        if ((v1->w < 0) ^ (v2->w < 0) ^ (v3->w < 0)) cross = -cross;
        switch (rsp.geometry_mode & G_CULL_BOTH) {
            case G_CULL_FRONT: if (cross <= 0.0f) return 2; break;
            case G_CULL_BACK: if (cross >= 0.0f) return 2; break;
            case G_CULL_BOTH: return 2;
        }
    }
    return 0;
}

/* A prepared slot has exactly the seven immutable load payload words.
 * Preserve the cached path's float operations and rounding before storing;
 * projection uses x/y/w scaled by 1/256, but depth uses the true reciprocal.
 * This bounded run never supplies an externally computed exact depth. */
static inline void gfx_cpu_prepare_clip(struct gfx_vc_prepared *d,
                                       const struct LoadedVertex *v,
                                       float u, float tex_v, uint16_t rgb) {
    float df = RSPV_TO_FLOAT(v->w_inv) * 1073741824.0f;
    if (df < 1.0f) df = 1.0f;
    else if (df > 2147483520.0f) df = 2147483520.0f;
    int32_t qx = (int32_t)lrintf(RSPV_TO_FLOAT(v->x) * 256.0f);
    int32_t qy = (int32_t)lrintf(RSPV_TO_FLOAT(v->y) * 256.0f);
    int32_t qw = (int32_t)lrintf(RSPV_TO_FLOAT(v->w) * 256.0f);
    int32_t qu = (int32_t)lrintf(u * 65536.0f);
    int32_t qv = (int32_t)lrintf(tex_v * 65536.0f);
    uint32_t qd = (uint32_t)lrintf(df);
#if defined(__riscv)
    /* Keep the existing conversion scheduling barrier; no changed rounding. */
    __asm__ volatile ("" : "+r"(qx), "+r"(qy), "+r"(qw),
                          "+r"(qu), "+r"(qv), "+r"(qd));
#endif
    d->x = qx; d->y = qy; d->w = qw;
    d->u = qu; d->v = qv; d->rgb = rgb; d->depth = qd;
}

#if LOAD_ALIAS_ENABLED
/* Hardware importer deliberately rejects nonfinite/subnormal clip inputs.
 * Keep those rare values on the existing CPU conversion path before taking
 * a snapshot; valid zero X/Y, ordinary finite W and reciprocal use RNE exactly. */
static int gfx_bram_vertex_supported(const struct LoadedVertex *v) {
    uint32_t x,y,w,wi;
    memcpy(&x,&v->x,4);memcpy(&y,&v->y,4);
    memcpy(&w,&v->w,4);memcpy(&wi,&v->w_inv,4);
    x&=0x7fffffffu;y&=0x7fffffffu;
    return !v->clip_rej && (!x||(x>=0x00800000u&&x<0x7f800000u))
        && (!y||(y>=0x00800000u&&y<0x7f800000u))
        && w>=0x00800000u&&w<0x46fffe00u
        && wi>=0x00800000u&&wi<0x7f800000u;
}
#endif

/* Complete one fallback triangle using an already computed rejection
 * class. The exact original post-cull clip/push tail follows below. */
static void gfx_cpu_push_checked(unsigned a, unsigned b, unsigned c, unsigned reject) {
    struct LoadedVertex *v1 = &rsp.loaded_vertices[a];
    struct LoadedVertex *v2 = &rsp.loaded_vertices[b];
    struct LoadedVertex *v3 = &rsp.loaded_vertices[c];
#ifdef TARGET_POCKET
    tri_diag.total++;
    if (reject == 1) tri_diag.clip_reject++;
    else if (reject == 2) tri_diag.cull_reject++;
#endif
    if (reject) return;
    const uint8_t clip_and = 0;
#ifdef GFX_MANUAL_CLIPPING
    if (!gfx_clip_triangle(v1, v2, v3, clip_and))
#endif
    {
#ifdef TARGET_POCKET
        const uint8_t clip_or = v1->clip_rej | v2->clip_rej | v3->clip_rej;
        if (clip_or)
            tri_diag.clipped++;
        else
            tri_diag.direct++;
#endif
        gfx_push_triangle(v1, v2, v3, false);
    }
#ifdef TARGET_POCKET
    else {
        tri_diag.clipped++;
    }
#endif
}

#if LOAD_ALIAS_ENABLED
/* AJ01 admits a complete immutable task before executing its CPU LOAD. The
 * cached suffix proves these canonical slots are overwritten before any CPU
 * reader; unsupported geometry is repaired from the job's immutable context.
 * Cold materials retain the ordinary first-live-triangle setup path. */
static __attribute__((noinline)) int gfx_async_try_load(unsigned count,unsigned first,
        const Vtx *vertices,float aspect,int eager_fog) {
    ++gfx_async_stats[0];
    if(async_armed->prefer_legacy){++gfx_async_hint_stats[3];return 0;}
    if(!gfx_async_task_proof(async_armed)){++gfx_async_stats[1];return 0;}
    if(!triangle_material_ready||!triangle_vc_elig){++gfx_async_stats[2];return 0;}
    const uintptr_t p=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    const unsigned lit=(rsp.geometry_mode&G_LIGHTING)!=0;
    unsigned frm;__asm__ volatile("csrr %0,frm" : "=r"(frm));
    if(!count||count>32||first||(p&3u)||p<(uintptr_t)&_start||p>=end||
       count>(end-p)/sizeof(Vtx)||eager_fog||frm||
       (rsp.geometry_mode&G_TEXTURE_GEN)||(lit&&rsp.current_num_lights!=2)) {
        ++gfx_async_stats[3];return 0;
    }
    const struct ColorCombiner *comb=triangle_material_comb;
    const int elig=triangle_vc_elig,rgb_in=triangle_vc_rgb_input;
    const int alpha_in=triangle_vc_alpha_input;
    const unsigned rgb_source=rgb_in>=0?comb->shader_input_mapping[0][rgb_in]:0;
    uint8_t alpha=255;
    if(rgb_in>=0&&rgb_source==CC_LOD)goto material_fallback;
    if(alpha_in>=0) {
        const unsigned source=comb->shader_input_mapping[1][alpha_in];
        if(source==CC_LOD||(source==CC_SHADE&&!triangle_material_fog))goto material_fallback;
        if(source!=CC_SHADE) {
            struct RGBA tmp;
            alpha=gfx_vc_resolve(source,NULL,NULL,&tmp)->a;
        }
    }
    const int textured=elig&GFX_VC_TEXTURED;
    const unsigned bias=textured&&(rdp.other_mode_h&(3U<<G_MDSFT_TEXTFILT))!=G_TF_POINT;
    struct gfx_bram_import material={0};
    material.u_offset=textured?(int32_t)(rdp.texture_tile.uls*8):0;
    material.v_offset=textured?(int32_t)(rdp.texture_tile.ult*8):0;
    material.flags_rgb=(textured?GFX_BRAM_TEXTURED:0)|(bias?GFX_BRAM_HALF_TEXEL:0)|0xffffu;
    if(rgb_in>=0) {
        if(rgb_source==CC_SHADE)material.flags_rgb|=GFX_BRAM_SOURCE_RGB;
        else {
            struct RGBA tmp;
            const struct RGBA *c=gfx_vc_resolve(rgb_source,NULL,NULL,&tmp);
            material.flags_rgb=(material.flags_rgb&~0xffffu)|
                ((uint32_t)(c->r>>3)<<11)|((uint32_t)(c->g>>2)<<5)|(c->b>>3);
        }
    }
    const unsigned cull=((rsp.geometry_mode&G_CULL_FRONT)?1u:0u)|
                        ((rsp.geometry_mode&G_CULL_BACK)?2u:0u);
    const struct AsyncGroup *group=gfx_async_group_sources(async_armed,cull);
    /* Source allocation may service an earlier repair and change a route
     * hint. Never retain a just-invalidated group's CPU bookkeeping pointer. */
    if(async_armed->prefer_legacy){++gfx_async_hint_stats[3];return 0;}
    const unsigned members=group?group->members:1u;
    const unsigned triangles=group?group->triangles:async_armed->triangles;
    const uint32_t loaded_mask=group?group->loaded_mask:
        (count==32?0xffffffffu:((1u<<count)-1u));
    const uint32_t descriptor_offset=group?group->descriptor_offset:0;
    Gfx *const selected_next=group?group->next:async_armed->next;
    uint32_t vertex_offset=0,command_offset=0;
    if(!group&&!gfx_async_sources(async_armed,vertices,count,&vertex_offset,&command_offset)) {
        ++gfx_async_stats[3];return 0;
    }
    struct am_transform transform;
    for(unsigned i=0;i<16;++i)transform.matrix[i]=load_alias_bits(rsp.MP_matrix[i/4][i%4]);
    transform.aspect=load_alias_bits(aspect);
    transform.scales=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    transform.lighting=lit;
    if(lit) {
        for(unsigned i=0;i<3;++i)transform.coefficient[i]=load_alias_bits(rsp.current_lights_coeffs[0][i]);
        const uint8_t *a=rsp.current_lights[1].col,*d=rsp.current_lights[0].col;
        transform.ambient=a[0]|((uint32_t)a[1]<<8)|((uint32_t)a[2]<<16);
        transform.diffuse=d[0]|((uint32_t)d[1]<<8)|((uint32_t)d[2]<<16);
    } else {
        transform.coefficient[0]=transform.coefficient[1]=transform.coefficient[2]=0;
        transform.ambient=transform.diffuse=0;
    }
    /* gfx_sp_vertex has already copied survivors and dropped overwritten
     * lazy rows. No CPU alias dependency remains when the worker takes raw. */
    gfx_gpu_async_raw_release();
#if GFX_LOAD_SUBMIT_OVERLAP
    gfx_cpu_submit_pending();
#endif
    gfx_vc_texture_state(comb,textured,bias?0.5f:0.0f);
    if(buf_vbo_len)gfx_flush();
    if(group) {
        gfx_gpu_async_group(descriptor_offset,members,&transform,alpha,&material);
        ++gfx_async_group_stats[0];gfx_async_group_stats[1]+=members;
        gfx_async_group_stats[7]+=members-1u;
        gfx_async_stats[0]+=members-1u;
    } else {
        gfx_gpu_async_job(vertex_offset,async_armed->source->words.w0,command_offset,
             async_armed->commands,cull,vertices,async_armed->source+1,&transform,alpha,&material);
    }
    vc_slot_stale=0xffffffffu;
    load_bram_slots=0;load_queued_safe=0;
    /* These values are never read before their proven overwrite; retaining
     * deferred fog here would turn an unused vertex into a spurious reader. */
    cpu_fog_pending&=~loaded_mask;
    async_next=selected_next;
    async_submitted=1;
    gfx_async_stats[4]+=members;gfx_async_stats[5]+=triangles;
#ifdef TARGET_POCKET
    tri_diag.total+=triangles;
#endif
    return 1;
material_fallback:
    ++gfx_async_stats[2];return 0;
}

/* Finish the immutable VTX/TRI task before the display-list state advances.
 * Any late material or numeric rejection retains the completed raw LOAD and
 * sends the original TRI words through their unchanged CPU handlers. */
static __attribute__((noinline)) Gfx *gfx_mesh_finish(Gfx *cmd) {
    Gfx *fallback=cmd+1;
    if(!mesh_raw_completed)return fallback;
    mesh_raw_completed=0;
    const unsigned status=MESH_REGS[0x21];
    const unsigned live=(status>>8)&127u;
    const unsigned input=MESH_REGS[0x2e];
    const unsigned clipped=MESH_REGS[0x29],culled=MESH_REGS[0x2a];
    if(!(status&MESH_OWNER)||input!=mesh_armed->triangles||clipped+culled+live!=input) {
        ++gfx_mesh_stats[14];gfx_mesh_discard();return fallback;
    }
    ++gfx_mesh_stats[5];gfx_mesh_stats[6]+=input;
    if(!live) {
        ++gfx_mesh_stats[12];gfx_mesh_discard();
#ifdef TARGET_POCKET
        tri_diag.total+=input;tri_diag.clip_reject+=clipped;tri_diag.cull_reject+=culled;
#endif
        return mesh_armed->next;
    }
    bool use_fog,use_alpha,use_texture;
    struct ColorCombiner *comb=gfx_prepare_triangle_material(&use_fog,&use_alpha,&use_texture);
    const int elig=triangle_vc_elig,rgb_in=triangle_vc_rgb_input;
    const int alpha_in=triangle_vc_alpha_input;
    const unsigned rgb_source=rgb_in>=0?comb->shader_input_mapping[0][rgb_in]:0;
    uint8_t alpha=255;
    if(!elig||(rgb_in>=0&&rgb_source==CC_LOD))goto material_fallback;
    if(alpha_in>=0) {
        const unsigned source=comb->shader_input_mapping[1][alpha_in];
        if(source==CC_LOD||(source==CC_SHADE&&!use_fog))goto material_fallback;
        if(source!=CC_SHADE) {
            struct RGBA tmp;
            alpha=gfx_vc_resolve(source,NULL,NULL,&tmp)->a;
        }
    }
    const int textured=elig&GFX_VC_TEXTURED;
    const unsigned bias=textured&&(rdp.other_mode_h&(3U<<G_MDSFT_TEXTFILT))!=G_TF_POINT;
    struct gfx_bram_import context={0};
    context.u_offset=textured?(int32_t)(rdp.texture_tile.uls*8):0;
    context.v_offset=textured?(int32_t)(rdp.texture_tile.ult*8):0;
    context.flags_rgb=(textured?GFX_BRAM_TEXTURED:0)|(bias?GFX_BRAM_HALF_TEXEL:0)|0xffffu;
    if(rgb_in>=0) {
        if(rgb_source==CC_SHADE)context.flags_rgb|=GFX_BRAM_SOURCE_RGB;
        else {
            struct RGBA tmp;
            const struct RGBA *c=gfx_vc_resolve(rgb_source,NULL,NULL,&tmp);
            const uint16_t rgb=((uint16_t)(c->r>>3)<<11)|((uint16_t)(c->g>>2)<<5)|(c->b>>3);
            context.flags_rgb=(context.flags_rgb&~0xffffu)|rgb;
        }
    }
    /* The packet converter validates every used raw vertex before any ring
     * marker or output. These uniforms then remain immutable until copied. */
    MESH_REGS[0x2b]=context.flags_rgb;
    MESH_REGS[0x2c]=(uint32_t)context.u_offset;
    MESH_REGS[0x2d]=(uint32_t)context.v_offset;
    __asm__ volatile("fence rw,rw" ::: "memory");
    MESH_REGS[0x26]=8;
    unsigned checked;
    do {checked=MESH_REGS[0x21];}
    while((checked&MESH_OWNER)&&!(checked&(MESH_VALIDATED|MESH_PACKET_ERROR|MESH_PROTOCOL_ERROR)));
    if(!(checked&MESH_OWNER)||!(checked&MESH_VALIDATED)||(checked&(MESH_PACKET_ERROR|MESH_PROTOCOL_ERROR))) {
        ++gfx_mesh_stats[11];gfx_mesh_discard();return fallback;
    }
    /* MTF2 copies into its private packet FIFO independently of the marker.
     * ARM may release raw ownership before the marker reaches the GPU. Keep
     * the serial and uniforms locally; do not reread owner after this point. */
    const uint32_t packet_serial=mesh_serial;
    MESH_REGS[0x26]=16;
    __asm__ volatile("fence rw,rw" ::: "memory");
    gfx_vc_texture_state(comb,textured,bias?0.5f:0.0f);
    if(buf_vbo_len)gfx_flush();
    gfx_gpu_mesh_packet(packet_serial,alpha,&context);
    ++gfx_mesh_stats[7];gfx_mesh_stats[8]+=live;
    vc_slot_stale=0xffffffffu;
#ifdef TARGET_POCKET
    tri_diag.total+=input;tri_diag.clip_reject+=clipped;tri_diag.cull_reject+=culled;
    tri_diag.direct+=live;
#endif
    return mesh_armed->next;
material_fallback:
    ++gfx_mesh_stats[10];gfx_mesh_discard();return fallback;
}

/* QGF admission has no vertex readback or cull result dependency. Metadata
 * proves every queued triangle can use opaque indexed rendering; different
 * fingerprints prove W is not equal at all three corners (collisions merely
 * choose the unchanged CPU path). The snapshot and command bytes own all
 * inputs needed after subsequent display-list state or LOAD changes. */
static __attribute__((noinline)) Gfx *gfx_queued_front_run(Gfx *cmd) {
    Gfx *first = cmd;
    if (!load_bram_slots || !triangle_material_ready || !triangle_vc_elig
        || !gfx_gpu_queued_front_supported()) return first;
    unsigned frm; __asm__ volatile("csrr %0,frm" : "=r"(frm));
    if (frm) return first;
    ++gfx_queued_stats[0];
#if GFX_LOAD_SUBMIT_OVERLAP
    if (load_submit_count) return first;
    uint32_t *jobs = load_submit_jobs;
#else
    uint32_t jobs[64];
#endif
    struct ColorCombiner *comb = triangle_material_comb;
    const int rgb_in = triangle_vc_rgb_input, alpha_in = triangle_vc_alpha_input;
    const unsigned rgb_source = rgb_in >= 0 ? comb->shader_input_mapping[0][rgb_in] : 0;
    uint8_t alpha = 255;
    if (rgb_in >= 0 && rgb_source == CC_LOD) { ++gfx_queued_stats[8]; return first; }
    if (alpha_in >= 0) {
        const unsigned source = comb->shader_input_mapping[1][alpha_in];
        if (source == CC_LOD || (source == CC_SHADE && !triangle_material_fog)) {
            ++gfx_queued_stats[8]; return first;
        }
        if (source != CC_SHADE) {
            struct RGBA tmp;
            alpha = gfx_vc_resolve(source, NULL, NULL, &tmp)->a;
        }
    }
    const uint32_t owned = load_bram_slots & load_queued_safe;
    const unsigned raw_first = load_bram_first;
    const uint32_t h0 = load_queued_hash[0], h1 = load_queued_hash[1];
    const uint32_t h2 = load_queued_hash[2], h3 = load_queued_hash[3];
    unsigned count = 0;
    for (;;) {
        unsigned slots[6];
        unsigned n = gfx_geometry_decode(cmd, slots);
        if (!n || count + n > 64) break;
        /* Validate the entire command before recording either TRI2 half. */
        for (unsigned t = 0; t < n; ++t) {
            const unsigned *v = &slots[t * 3];
            if ((v[0] | v[1] | v[2]) >= 32) goto collected;
            const uint32_t used = (1u << v[0]) | (1u << v[1]) | (1u << v[2]);
            if ((owned & used) != used) goto collected;
            const uint32_t p0 = h0 & used, p1 = h1 & used, p2 = h2 & used, p3 = h3 & used;
            if ((!p0 || p0 == used) && (!p1 || p1 == used)
                && (!p2 || p2 == used) && (!p3 || p3 == used)) goto collected;
        }
        for (unsigned t = 0; t < n; ++t) {
            const unsigned *v = &slots[t * 3];
            jobs[count++] = (v[0] - raw_first) | ((v[1] - raw_first) << 5) | ((v[2] - raw_first) << 10);
        }
        ++cmd;
    }
collected:
    if (!count) { ++gfx_queued_stats[7]; return first; }
    if (!gfx_gpu_queued_front_acquire(load_bram_generation)) return first;
    const int textured = triangle_vc_elig & GFX_VC_TEXTURED;
    const unsigned bias = textured && (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT;
    struct gfx_bram_import context = {0};
    context.generation = load_bram_generation; context.first = raw_first;
    context.u_offset = textured ? (int32_t)(rdp.texture_tile.uls * 8) : 0;
    context.v_offset = textured ? (int32_t)(rdp.texture_tile.ult * 8) : 0;
    context.flags_rgb = (textured ? GFX_BRAM_TEXTURED : 0) | (bias ? GFX_BRAM_HALF_TEXEL : 0) | 0xffffu;
    if (rgb_in >= 0) {
        if (rgb_source == CC_SHADE) context.flags_rgb |= GFX_BRAM_SOURCE_RGB;
        else {
            struct RGBA tmp;
            const struct RGBA *c = gfx_vc_resolve(rgb_source, NULL, NULL, &tmp);
            const uint16_t rgb = ((uint16_t)(c->r >> 3) << 11) | ((uint16_t)(c->g >> 2) << 5) | (c->b >> 3);
            context.flags_rgb = (context.flags_rgb & ~0xffffu) | rgb;
        }
    }
    /* Painter order is preserved even when the previous batch used legacy
     * buffered records. Material/viewport setters are emitted by the backend
     * before the queued opcode, exactly as for ordinary indexed draws. */
    if (buf_vbo_len) gfx_flush();
    const unsigned cull = ((rsp.geometry_mode & G_CULL_FRONT) ? 1u : 0u)
                        | ((rsp.geometry_mode & G_CULL_BACK) ? 2u : 0u);
    gfx_gpu_queued_front_batch(jobs, count, alpha, cull, &context);
    /* Completion is asynchronous: do not guess which slots/corners survived.
     * The next CPU path safely reloads any slot it needs in stream order. */
    vc_slot_stale = 0xffffffffu;
#ifdef TARGET_POCKET
    tri_diag.total += count; /* queued keep/drop counters are hardware-owned */
#endif
    return cmd;
}
#endif

#if LOAD_ALIAS_ENABLED
/* Ordered preflight has no render side effects. It consumes only raw-local
 * indices and the already installed opaque material; hardware uses the same
 * clip/cull arithmetic as the CPU fallback. Every unsupported job sends the
 * WHOLE batch back through that fallback before importing or drawing. */
static __attribute__((noinline)) Gfx *gfx_triangle_front_run(Gfx *cmd) {
    SM64_PERF_SCOPE(SM64_PERF_TRI); SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_TRIANGLE);
    Gfx *first = cmd;
    if (!load_bram_slots || !triangle_material_ready || !triangle_vc_elig
        || !gfx_triangle_front_supported() || !gfx_gpu_bram_import_supported()) return first;
    if (!gfx_gpu_bram_import_available()) { gfx_front_stats[8]++; return first; }
#if GFX_LOAD_SUBMIT_OVERLAP
    if (load_submit_count) return first;
    struct gfx_vc_prepared *payload = load_submit_payload;
    uint32_t *jobs = load_submit_jobs;
#else
    struct gfx_vc_prepared payload[32];
    uint32_t jobs[64];
#endif
    struct ColorCombiner *comb = triangle_material_comb;
    const int rgb_in = triangle_vc_rgb_input, alpha_in = triangle_vc_alpha_input;
    unsigned rgb_source = rgb_in >= 0 ? comb->shader_input_mapping[0][rgb_in] : 0;
    uint8_t alpha = 255;
    if (rgb_in >= 0 && rgb_source == CC_LOD) { gfx_front_stats[14]++; return first; }
    if (alpha_in >= 0) {
        const unsigned source = comb->shader_input_mapping[1][alpha_in];
        if (source == CC_LOD || (source == CC_SHADE && !triangle_material_fog)) {
            gfx_front_stats[14]++; return first;
        }
        if (source != CC_SHADE) {
            struct RGBA tmp;
            /* Only PRIM/ENV/default reach here: no canonical vertex read. */
            alpha = gfx_vc_resolve(source, NULL, NULL, &tmp)->a;
        }
    }
    unsigned count = 0;
    for (;;) {
        unsigned slots[6];
        unsigned n = gfx_geometry_decode(cmd, slots);
        if (!n || count + n > 64) break;
        uint32_t used = 0;
        for (unsigned i = 0; i < n * 3; ++i) {
            if (slots[i] >= 32) goto collected;
            used |= 1u << slots[i];
        }
        if ((load_bram_slots & used) != used) break;
        /* Both halves pass ownership before a TRI2 is added. */
        for (unsigned t = 0; t < n; ++t) {
            unsigned *s = &slots[t * 3];
            jobs[count] = s[0] | (s[1] << 5) | (s[2] << 10);
            TRI_FRONT_REGS[0x200 + count] = (s[0] - load_bram_first)
                | ((s[1] - load_bram_first) << 5) | ((s[2] - load_bram_first) << 10);
            ++count;
        }
        ++cmd;
    }
collected:
    if (!count) { gfx_front_stats[14]++; return first; }
    gfx_front_stats[0]++; gfx_front_stats[1] += count;
    const unsigned cull = ((rsp.geometry_mode & G_CULL_FRONT) ? 1u : 0u)
                        | ((rsp.geometry_mode & G_CULL_BACK) ? 2u : 0u);
    TRI_FRONT_REGS[0x12] = load_bram_generation;
    TRI_FRONT_REGS[0x13] = count | (cull << 8);
    __asm__ volatile("fence rw,rw" ::: "memory");
    TRI_FRONT_REGS[0x14] = 1u;
    unsigned polls = 0, status;
    do { status = TRI_FRONT_REGS[0x14]; ++polls; } while ((status & 1u) && polls < 65536u);
    const unsigned timed_out = (status & 1u) != 0;
    /* Never abandon accepted raw/FMA ownership. A timeout disables future
     * preflights only after the current one drains, then uses CPU fallback. */
    if (timed_out) do { status = TRI_FRONT_REGS[0x14]; ++polls; } while (status & 1u);
    gfx_front_stats[12] += polls;
    if (timed_out || (status & 0xau) != 2u) {
        gfx_front_stats[13]++; sm64_triangle_front_capability = 2u; return first;
    }
    const uint32_t keep_lo = TRI_FRONT_REGS[0x15], keep_hi = TRI_FRONT_REGS[0x16];
    const uint32_t fallback_lo = TRI_FRONT_REGS[0x17], fallback_hi = TRI_FRONT_REGS[0x18];
    if ((status & 4u) || fallback_lo || fallback_hi) { gfx_front_stats[7]++; return first; }
    const unsigned kept = (unsigned)__builtin_popcount(keep_lo) + (unsigned)__builtin_popcount(keep_hi);
    const unsigned clipped = TRI_FRONT_REGS[0x19];
    if (kept > count || clipped > count - kept) {
        gfx_front_stats[13]++; sm64_triangle_front_capability = 2u; return first;
    }
    if (kept) {
        const int textured = triangle_vc_elig & GFX_VC_TEXTURED;
        const float bias = textured && (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT ? 0.5f : 0.0f;
        gfx_vc_texture_state(comb, textured, bias);
        uint32_t stale = vc_slot_stale, prepared = 0;
        unsigned output = 0;
        for (unsigned i = 0; i < count; ++i) {
            if (!((i < 32 ? keep_lo >> i : keep_hi >> (i - 32)) & 1u)) continue;
            uint32_t job = jobs[i];
            const unsigned a = job & 31u, b = (job >> 5) & 31u, c = (job >> 10) & 31u;
            const uint32_t used = (1u << a) | (1u << b) | (1u << c);
            const unsigned dirty = ((stale >> a) & 1u) | (((stale >> b) & 1u) << 1)
                                 | (((stale >> c) & 1u) << 2);
            prepared |= stale & used;
            stale &= ~used;
            jobs[output++] = job | (dirty << 15);
        }
        struct gfx_bram_import import = {0};
        import.slots = prepared;
        import.generation = load_bram_generation; import.first = load_bram_first;
        import.u_offset = textured ? (int32_t)(rdp.texture_tile.uls * 8) : 0;
        import.v_offset = textured ? (int32_t)(rdp.texture_tile.ult * 8) : 0;
        import.flags_rgb = (textured ? GFX_BRAM_TEXTURED : 0)
                         | (bias != 0.0f ? GFX_BRAM_HALF_TEXEL : 0) | 0xffffu;
        if (rgb_in >= 0) {
            if (rgb_source == CC_SHADE) import.flags_rgb |= GFX_BRAM_SOURCE_RGB;
            else {
                struct RGBA tmp;
                const struct RGBA *c = gfx_vc_resolve(rgb_source, NULL, NULL, &tmp);
                const uint16_t rgb = ((uint16_t)(c->r >> 3) << 11) | ((uint16_t)(c->g >> 2) << 5) | (c->b >> 3);
                import.flags_rgb = (import.flags_rgb & ~0xffffu) | rgb;
            }
        }
        /* A rejected snapshot CLAIM owns nothing. No draw or canonical slot
         * has changed; the original indexed path still handles the whole run. */
        if (prepared && !gfx_gpu_bram_import_acquire(import.generation)) {
            gfx_front_stats[8]++; return first;
        }
        if (buf_vbo_len) gfx_flush();
#if GFX_LOAD_SUBMIT_OVERLAP
        if ((cmd->words.w0 >> 24) == (uint8_t)G_VTX) {
            load_submit_import = import;
            load_submit_alpha = alpha;
            load_submit_count = kept;
            load_submit_stats[0]++;
        } else
#endif
        {
            if (prepared) gfx_gpu_vtx_cache_import_batch(payload, jobs, kept, alpha, &import);
            else gfx_gpu_vtx_cache_batch(payload, jobs, kept, alpha);
        }
        vc_slot_stale = stale;
    }
    gfx_front_stats[2]++; gfx_front_stats[3] += count;
    gfx_front_stats[4] += kept; gfx_front_stats[5] += clipped;
    gfx_front_stats[6] += count - kept - clipped;
#ifdef TARGET_POCKET
    tri_diag.total += count;
    tri_diag.clip_reject += clipped;
    tri_diag.cull_reject += count - kept - clipped;
    tri_diag.direct += kept;
#endif
    return cmd;
}
#endif

/* A run ends at EVERY non-triangle command, including G_VTX. Loaded slots,
 * material, viewport, texture and alpha are therefore immutable throughout.
 * Store the ORIGINAL per-triangle dirty masks, not one reordered union of
 * uploads: backend packets remain load[0],load[1],load[2],draw for each tri. */
static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(Gfx *cmd) {
    SM64_PERF_SCOPE(SM64_PERF_TRI); SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_TRIANGLE);
#if LOAD_ALIAS_ENABLED
    if (load_lazy_slots) load_alias_materialize_all();
#endif
#if GFX_LOAD_SUBMIT_OVERLAP
    struct gfx_vc_prepared *payload = load_submit_payload;
    uint32_t *jobs = load_submit_jobs;
#else
    struct gfx_vc_prepared payload[32];
    uint32_t jobs[64];
#endif
    unsigned count = 0, consumed = 0;
    unsigned slot[6], reject[2], n = 0, checked = 0;
    bool fallback = false;
    uint32_t stale = 0, prepared = 0;
    struct ColorCombiner *comb = NULL;
    int textured = 0, rgb_in = -1;
    uint8_t alpha = 255;
    float uofs = 0.0f, vofs = 0.0f, bias = 0.0f;
    for (;;) {
        n = gfx_geometry_decode(cmd, slot);
        checked = 0;
        if (!n || consumed + n > 64) break;
        unsigned live = 0;
        for (unsigned t = 0; t < n; ++t) {
            unsigned *s = &slot[3*t];
            if ((s[0] | s[1] | s[2]) >= 32) goto fallback_command;
            const struct LoadedVertex *v1 = &rsp.loaded_vertices[s[0]];
            const struct LoadedVertex *v2 = &rsp.loaded_vertices[s[1]];
            const struct LoadedVertex *v3 = &rsp.loaded_vertices[s[2]];
            reject[t] = gfx_cpu_batch_reject(v1, v2, v3);
            checked = t + 1;
            if (reject[t]) continue;
            if (v1->clip_rej | v2->clip_rej | v3->clip_rej) goto fallback_command;
            const float w1 = RSPV_TO_FLOAT(v1->w);
            const float w2 = RSPV_TO_FLOAT(v2->w);
            const float w3 = RSPV_TO_FLOAT(v3->w);
            /* Positive IEEE binary32 encodings are strictly ordered.
             * This accepts exactly 0 < w < 32767, excluding both zeros,
             * infinities and every NaN. Equality is then bit equality.
             * memcpy avoids aliasing assumptions; no projection math changes. */
            uint32_t wb1, wb2, wb3;
            memcpy(&wb1, &w1, sizeof(wb1));
            memcpy(&wb2, &w2, sizeof(wb2));
            memcpy(&wb3, &w3, sizeof(wb3));
            if (wb1 - 1u >= 0x46fffdffu ||
                wb2 - 1u >= 0x46fffdffu ||
                wb3 - 1u >= 0x46fffdffu ||
                (wb1 == wb2 && wb1 == wb3)) goto fallback_command;
            ++live;
        }
        if (live && !comb) {
            bool use_fog, use_alpha, use_texture;
            comb = gfx_prepare_triangle_material(&use_fog, &use_alpha, &use_texture);
            const int elig = triangle_vc_elig;
            rgb_in = triangle_vc_rgb_input;
            const int alpha_in = triangle_vc_alpha_input;
            if (!elig || (rgb_in >= 0 && comb->shader_input_mapping[0][rgb_in] == CC_LOD))
                goto fallback_command;
            if (alpha_in >= 0) {
                const uint8_t source = comb->shader_input_mapping[1][alpha_in];
                if (source == CC_LOD || (source == CC_SHADE && !use_fog)) goto fallback_command;
                if (source != CC_SHADE) {
                    struct RGBA tmp;
                    const struct LoadedVertex *v = &rsp.loaded_vertices[slot[0]];
                    alpha = gfx_vc_resolve(source, v, v, &tmp)->a;
                }
            }
            textured = elig & GFX_VC_TEXTURED;
            uofs = textured ? (float)(rdp.texture_tile.uls * 8) : 0.0f;
            vofs = textured ? (float)(rdp.texture_tile.ult * 8) : 0.0f;
            bias = (textured &&
                (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT) ? 0.5f : 0.0f;
            gfx_vc_texture_state(comb, textured, bias);
            stale = vc_slot_stale;
        }
        for (unsigned t = 0; t < n; ++t) {
#ifdef TARGET_POCKET
            tri_diag.total++;
            if (reject[t] == 1) tri_diag.clip_reject++;
            else if (reject[t] == 2) tri_diag.cull_reject++;
            else tri_diag.direct++;
#endif
            if (reject[t]) continue;
            unsigned *s = &slot[3*t];
            unsigned dirty = ((stale >> s[0]) & 1u) |
                             (((stale >> s[1]) & 1u) << 1) |
                             (((stale >> s[2]) & 1u) << 2);
            const uint32_t used = (1u << s[0]) | (1u << s[1]) | (1u << s[2]);
            prepared |= stale & used;
            jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15);
            stale &= ~used;
        }
        consumed += n;
        ++cmd;
    }
    goto submit;
fallback_command:
    /* Reject work has no side effects. Complete only the unvisited TRI2
     * half, then submit the accepted prefix before this whole fallback
     * command. A material change cannot intervene inside a TRI command. */
    for (unsigned t = checked; t < n; ++t) {
        const unsigned *s = &slot[3*t];
        reject[t] = gfx_cpu_batch_reject(&rsp.loaded_vertices[s[0]],
                                       &rsp.loaded_vertices[s[1]],
                                       &rsp.loaded_vertices[s[2]]);
    }
    fallback = true;
submit:
    if (count) {
        struct gfx_bram_import import={0};
#if LOAD_ALIAS_ENABLED
        import.slots=prepared&load_bram_slots;
        if(import.slots&&gfx_gpu_bram_import_supported()
           &&gfx_gpu_bram_import_available()) {
            /* All material, viewport, source rows and canonical vertices are
             * immutable for this run. Reject unsupported rows independently. */
            for(unsigned i=0;i<32;++i)
                if((import.slots&(1u<<i))&&!gfx_bram_vertex_supported(&rsp.loaded_vertices[i]))
                    import.slots&=~(1u<<i);
            import.generation=load_bram_generation;import.first=load_bram_first;
            import.u_offset=textured?(int32_t)(rdp.texture_tile.uls*8):0;
            import.v_offset=textured?(int32_t)(rdp.texture_tile.ult*8):0;
            import.flags_rgb=(textured?GFX_BRAM_TEXTURED:0)
                |(bias!=0.0f?GFX_BRAM_HALF_TEXEL:0)|0xffffu;
            if(rgb_in>=0) {
                const unsigned source=comb->shader_input_mapping[0][rgb_in];
                if(source==CC_SHADE)import.flags_rgb|=GFX_BRAM_SOURCE_RGB;
                else {
                    struct RGBA tmp;
                    const struct RGBA *c=gfx_vc_resolve(source,&rsp.loaded_vertices[0],&rsp.loaded_vertices[0],&tmp);
                    const uint16_t rgb=((uint16_t)(c->r>>3)<<11)|((uint16_t)(c->g>>2)<<5)|(c->b>>3);
                    import.flags_rgb=(import.flags_rgb&~0xffffu)|rgb;
                }
            }
            /* Flush legacy draws before snapshot ownership starts. A failed claim has
             * no import packets and restores ordinary CPU preparation. */
            if(import.slots&&buf_vbo_len)gfx_flush();
            if(import.slots&&!gfx_gpu_bram_import_acquire(import.generation))import.slots=0;
        } else import.slots=0;
#endif
        /* Only triangle commands have been consumed: slots and material are
         * still unchanged. Prepare unique dirty slots away from the hot job
         * scan; jobs retain every original load/draw ordering decision. */
        for (unsigned i = 0; prepared; ++i, prepared >>= 1) {
            if (!(prepared & 1u)) continue;
            if(import.slots&(1u<<i))continue;
            const struct LoadedVertex *v = &rsp.loaded_vertices[i];
            float u = 0.0f, tex_v = 0.0f;
            if (textured) {
                u = (RSPV_TO_FLOAT(v->u) - uofs) / 32.0f + bias;
                tex_v = (RSPV_TO_FLOAT(v->v) - vofs) / 32.0f + bias;
            }
            /* Preserve the float-record boundary before Q16 conversion:
             * fast-math must not fuse/reassociate UV scale and rounding. */
#if defined(__riscv)
            __asm__ volatile ("" : "+f"(u), "+f"(tex_v));
#endif
            uint16_t rgb = 0xffff;
            if (rgb_in >= 0) {
                struct RGBA tmp;
                const struct RGBA *c = gfx_vc_resolve(comb->shader_input_mapping[0][rgb_in], v, v, &tmp);
                rgb = ((uint16_t)(c->r >> 3) << 11) |
                      ((uint16_t)(c->g >> 2) << 5) | (c->b >> 3);
            }
            gfx_cpu_prepare_clip(&payload[i], v, u, tex_v, rgb);
        }
        if (buf_vbo_len) gfx_flush();
#if GFX_LOAD_SUBMIT_OVERLAP
        if (!fallback && (cmd->words.w0 >> 24) == (uint8_t)G_VTX) {
            load_submit_import = import;
            load_submit_alpha = alpha;
            load_submit_count = count;
            load_submit_stats[0]++;
        } else
#endif
        {
            if(import.slots)gfx_gpu_vtx_cache_import_batch(payload,jobs,count,alpha,&import);
            else gfx_gpu_vtx_cache_batch(payload, jobs, count, alpha);
        }
        vc_slot_stale = stale;
    }
    if (fallback) {
        for (unsigned t = 0; t < n; ++t) {
            const unsigned *s = &slot[3*t];
            gfx_cpu_push_checked(s[0], s[1], s[2], reject[t]);
        }
        ++cmd;
    }
    return cmd;
}
#endif

#define C0(pos, width) ((cmd->words.w0 >> (pos)) & ((1U << width) - 1))
#define C1(pos, width) ((cmd->words.w1 >> (pos)) & ((1U << width) - 1))

/* Ordinary state commands cannot prepare a new triangle material. Only
 * rectangles need a second invalidation after their internal triangles.
 * The optional worker keeps its original outer classification/dispatch. */
static inline __attribute__((always_inline)) void gfx_dl_invalidate_material(void) {
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
    triangle_material_ready = false;
#endif
}

static __attribute__((noinline)) void gfx_run_dl(Gfx* cmd,Gfx *return_command) {
#if LOAD_ALIAS_ENABLED
    /* A display-list entry is a useful repair boundary. Ordinary commands
     * need no MMIO poll: raw LOAD, ring/fence waits, subsequent list entries
     * and frame completion all retain their own progress points. */
    gfx_gpu_queued_front_service();
#endif
    for (;;) {
        uint32_t opcode = cmd->words.w0 >> 24;
#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
        const bool triangle_opcode=(opcode==(uint8_t)G_TRI1
#if defined(F3DEX_GBI_2) || defined(F3DEX_GBI) || defined(F3DLP_GBI)
           || opcode==(uint8_t)G_TRI2
#endif
        );
        const bool material_opcode=!triangle_opcode
            && opcode!=(uint8_t)G_VTX && opcode!=(uint8_t)G_MTX
            && opcode!=(uint8_t)G_POPMTX && opcode!=(uint8_t)G_DL
            && opcode!=(uint8_t)G_ENDDL;
        if(material_opcode)triangle_material_ready=false;
#if GFX_GPU_GEOMETRY_WORKER_APP
        if(vertex_attributes_pending && (opcode==(uint8_t)G_MTX || opcode==(uint8_t)G_POPMTX
           || opcode==(uint8_t)G_MOVEMEM || opcode==(uint8_t)G_MOVEWORD
           || opcode==(uint8_t)G_TEXTURE
#ifdef F3DEX_GBI_2
           || opcode==(uint8_t)G_GEOMETRYMODE
#else
           || opcode==(uint8_t)G_SETGEOMETRYMODE || opcode==(uint8_t)G_CLEARGEOMETRYMODE
#endif
           ))gfx_flush_vertex_attributes(0xffffffffu);
        if(!GFX_GPU_GEOMETRY_REFERENCE && triangle_opcode && geometry_valid) {
            Gfx *next=gfx_geometry_run(cmd);
            if(next!=cmd) {cmd=next;continue;}
        }
#endif /* GFX_GPU_GEOMETRY_WORKER_APP */
#endif

        switch (opcode) {
            // RSP commands:
            case G_MTX:
#ifdef F3DEX_GBI_2
                gfx_sp_matrix(C0(0, 8) ^ G_MTX_PUSH, (const int32_t *) seg_addr(cmd->words.w1));
#else
                gfx_sp_matrix(C0(16, 8), (const int32_t *) seg_addr(cmd->words.w1));
#endif
                break;
            case (uint8_t)G_POPMTX:
#ifdef F3DEX_GBI_2
                gfx_sp_pop_matrix(cmd->words.w1 / 64);
#else
                gfx_sp_pop_matrix(1);
#endif
                break;
            case G_MOVEMEM:
                gfx_dl_invalidate_material();
#ifdef F3DEX_GBI_2
                gfx_sp_movemem(C0(0, 8), C0(8, 8) * 8, seg_addr(cmd->words.w1));
#else
                gfx_sp_movemem(C0(16, 8), 0, seg_addr(cmd->words.w1));
#endif
                break;
            case (uint8_t)G_MOVEWORD:
                gfx_dl_invalidate_material();
#ifdef F3DEX_GBI_2
                gfx_sp_moveword(C0(16, 8), C0(0, 16), cmd->words.w1);
#else
                gfx_sp_moveword(C0(0, 8), C0(8, 16), cmd->words.w1);
#endif
                break;
            case (uint8_t)G_TEXTURE:
                gfx_dl_invalidate_material();
#ifdef F3DEX_GBI_2
                gfx_sp_texture(C1(16, 16), C1(0, 16), C0(11, 3), C0(8, 3), C0(1, 7));
#else
                gfx_sp_texture(C1(16, 16), C1(0, 16), C0(11, 3), C0(8, 3), C0(0, 8));
#endif
                break;
            case G_VTX:
#ifdef F3DEX_GBI_2
#if LOAD_ALIAS_ENABLED
                mesh_raw_completed=0;async_submitted=0;
                async_armed=NULL;
                if(gfx_gpu_async_supported()) {
                    mesh_armed=NULL;async_return_command=return_command;async_armed=gfx_mesh_lookup(cmd);
                } else mesh_armed=gfx_mesh_lookup(cmd);
#endif
                gfx_sp_vertex(C0(12, 8), C0(1, 7) - C0(12, 8), seg_addr(cmd->words.w1));
#if LOAD_ALIAS_ENABLED
                if(async_submitted) {
                    cmd=async_next;async_armed=NULL;continue;
                }
                async_armed=NULL;
                if(mesh_armed) {
                    Gfx *next=gfx_mesh_finish(cmd);
                    mesh_armed=NULL;cmd=next;continue;
                }
#endif
#elif defined(F3DEX_GBI) || defined(F3DLP_GBI)
                gfx_sp_vertex(C0(10, 6), C0(16, 8) / 2, seg_addr(cmd->words.w1));
#else
                gfx_sp_vertex((C0(0, 16)) / sizeof(Vtx), C0(16, 4), seg_addr(cmd->words.w1));
#endif
                break;
            case G_DL:
                if (C0(16, 1) == 0) {
                    // Push return address
                    gfx_run_dl((Gfx *)seg_addr(cmd->words.w1),cmd+1);
                } else {
                    cmd = (Gfx *)seg_addr(cmd->words.w1);
                    --cmd; // increase after break
                }
                break;
            case (uint8_t)G_ENDDL:
                return;
#ifdef F3DEX_GBI_2
            case G_GEOMETRYMODE:
                gfx_dl_invalidate_material();
                gfx_sp_geometry_mode(~C0(0, 24), cmd->words.w1);
                break;
#else
            case (uint8_t)G_SETGEOMETRYMODE:
                gfx_dl_invalidate_material();
                gfx_sp_geometry_mode(0, cmd->words.w1);
                break;
            case (uint8_t)G_CLEARGEOMETRYMODE:
                gfx_dl_invalidate_material();
                gfx_sp_geometry_mode(cmd->words.w1, 0);
                break;
#endif
            case (uint8_t)G_TRI1:
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
                /* A valid ineligible material cannot emit a cached run.
                 * Keep its original handler without speculative culling. */
                if (!triangle_material_ready || triangle_vc_elig) {
#if LOAD_ALIAS_ENABLED
                    Gfx *queued_next = gfx_queued_front_run(cmd);
                    if (queued_next != cmd) { cmd = queued_next; continue; }
                    Gfx *front_next = gfx_triangle_front_run(cmd);
                    if (front_next != cmd) { cmd = front_next; continue; }
#endif
                    Gfx *next = gfx_cpu_indexed_run(cmd);
                    if (next != cmd) { cmd = next; continue; }
                }
#endif
#ifdef F3DEX_GBI_2
                gfx_sp_tri1(C0(16, 8) / 2, C0(8, 8) / 2, C0(0, 8) / 2);
#elif defined(F3DEX_GBI) || defined(F3DLP_GBI)
                gfx_sp_tri1(C1(16, 8) / 2, C1(8, 8) / 2, C1(0, 8) / 2);
#else
                gfx_sp_tri1(C1(16, 8) / 10, C1(8, 8) / 10, C1(0, 8) / 10);
#endif
                break;
#if defined(F3DEX_GBI) || defined(F3DLP_GBI)
            case (uint8_t)G_TRI2:
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
                /* A valid ineligible material cannot emit a cached run.
                 * Keep its original handler without speculative culling. */
                if (!triangle_material_ready || triangle_vc_elig) {
#if LOAD_ALIAS_ENABLED
                    Gfx *queued_next = gfx_queued_front_run(cmd);
                    if (queued_next != cmd) { cmd = queued_next; continue; }
                    Gfx *front_next = gfx_triangle_front_run(cmd);
                    if (front_next != cmd) { cmd = front_next; continue; }
#endif
                    Gfx *next = gfx_cpu_indexed_run(cmd);
                    if (next != cmd) { cmd = next; continue; }
                }
#endif
                gfx_sp_tri1(C0(16, 8) / 2, C0(8, 8) / 2, C0(0, 8) / 2);
                gfx_sp_tri1(C1(16, 8) / 2, C1(8, 8) / 2, C1(0, 8) / 2);
                break;
#endif
            case (uint8_t)G_SETOTHERMODE_L:
                gfx_dl_invalidate_material();
#ifdef F3DEX_GBI_2
                gfx_sp_set_other_mode(31 - C0(8, 8) - C0(0, 8), C0(0, 8) + 1, cmd->words.w1);
#else
                gfx_sp_set_other_mode(C0(8, 8), C0(0, 8), cmd->words.w1);
#endif
                break;
            case (uint8_t)G_SETOTHERMODE_H:
                gfx_dl_invalidate_material();
#ifdef F3DEX_GBI_2
                gfx_sp_set_other_mode(63 - C0(8, 8) - C0(0, 8), C0(0, 8) + 1, (uint64_t) cmd->words.w1 << 32);
#else
                gfx_sp_set_other_mode(C0(8, 8) + 32, C0(0, 8), (uint64_t) cmd->words.w1 << 32);
#endif
                break;

            // RDP Commands:
            case G_SETTIMG:
                gfx_dl_invalidate_material();
                gfx_dp_set_texture_image(C0(21, 3), C0(19, 2), C0(0, 10), seg_addr(cmd->words.w1));
                break;
            case G_LOADBLOCK:
                gfx_dl_invalidate_material();
                gfx_dp_load_block(C1(24, 3), C0(12, 12), C0(0, 12), C1(12, 12), C1(0, 12));
                break;
            case G_LOADTILE:
                gfx_dl_invalidate_material();
                gfx_dp_load_tile(C1(24, 3), C0(12, 12), C0(0, 12), C1(12, 12), C1(0, 12));
                break;
            case G_SETTILE:
                gfx_dl_invalidate_material();
                gfx_dp_set_tile(C0(21, 3), C0(19, 2), C0(9, 9), C0(0, 9), C1(24, 3), C1(20, 4), C1(18, 2), C1(14, 4), C1(10, 4), C1(8, 2), C1(4, 4), C1(0, 4));
                break;
            case G_SETTILESIZE:
                gfx_dl_invalidate_material();
                gfx_dp_set_tile_size(C1(24, 3), C0(12, 12), C0(0, 12), C1(12, 12), C1(0, 12));
                break;
            case G_LOADTLUT:
                gfx_dl_invalidate_material();
                gfx_dp_load_tlut(C1(24, 3), C1(14, 10));
                break;
            case G_SETENVCOLOR:
                gfx_dl_invalidate_material();
                gfx_dp_set_env_color(C1(24, 8), C1(16, 8), C1(8, 8), C1(0, 8));
                break;
            case G_SETPRIMCOLOR:
                gfx_dl_invalidate_material();
                gfx_dp_set_prim_color(C1(24, 8), C1(16, 8), C1(8, 8), C1(0, 8));
                break;
            case G_SETFOGCOLOR:
                gfx_dl_invalidate_material();
                gfx_dp_set_fog_color(C1(24, 8), C1(16, 8), C1(8, 8), C1(0, 8));
                break;
            case G_SETFILLCOLOR:
                gfx_dl_invalidate_material();
                gfx_dp_set_fill_color(cmd->words.w1);
                break;
            case G_SETCOMBINE:
                gfx_dl_invalidate_material();
                gfx_dp_set_combine_mode(
                    color_comb(C0(20, 4), C1(28, 4), C0(15, 5), C1(15, 3)),
                    color_comb(C0(12, 3), C1(12, 3), C0(9, 3), C1(9, 3)));
                    /*color_comb(C0(5, 4), C1(24, 4), C0(0, 5), C1(6, 3)),
                    color_comb(C1(21, 3), C1(3, 3), C1(18, 3), C1(0, 3)));*/
                break;
            // G_SETPRIMCOLOR, G_CCMUX_PRIMITIVE, G_ACMUX_PRIMITIVE, is used by Goddard
            // G_CCMUX_TEXEL1, LOD_FRACTION is used in Bowser room 1
            case G_TEXRECT:
            case G_TEXRECTFLIP:
            {
                gfx_dl_invalidate_material();
                int32_t lrx, lry, tile, ulx, uly;
                uint32_t uls, ult, dsdx, dtdy;
#ifdef F3DEX_GBI_2E
                lrx = (int32_t)(C0(0, 24) << 8) >> 8;
                lry = (int32_t)(C1(0, 24) << 8) >> 8;
                ++cmd;
                ulx = (int32_t)(C0(0, 24) << 8) >> 8;
                uly = (int32_t)(C1(0, 24) << 8) >> 8;
                ++cmd;
                uls = C0(16, 16);
                ult = C0(0, 16);
                dsdx = C1(16, 16);
                dtdy = C1(0, 16);
#else
                lrx = C0(12, 12);
                lry = C0(0, 12);
                tile = C1(24, 3);
                ulx = C1(12, 12);
                uly = C1(0, 12);
                ++cmd;
                uls = C1(16, 16);
                ult = C1(0, 16);
                ++cmd;
                dsdx = C1(16, 16);
                dtdy = C1(0, 16);
#endif
                gfx_dp_texture_rectangle(ulx, uly, lrx, lry, tile, uls, ult, dsdx, dtdy, opcode == G_TEXRECTFLIP);
                gfx_dl_invalidate_material();
                break;
            }
            case G_FILLRECT:
                gfx_dl_invalidate_material();
#ifdef F3DEX_GBI_2E
            {
                int32_t lrx, lry, ulx, uly;
                lrx = (int32_t)(C0(0, 24) << 8) >> 8;
                lry = (int32_t)(C1(0, 24) << 8) >> 8;
                ++cmd;
                ulx = (int32_t)(C0(0, 24) << 8) >> 8;
                uly = (int32_t)(C1(0, 24) << 8) >> 8;
                gfx_dp_fill_rectangle(ulx, uly, lrx, lry);
                gfx_dl_invalidate_material();
                break;
            }
#else
                gfx_dp_fill_rectangle(C1(12, 12), C1(0, 12), C0(12, 12), C0(0, 12));
                gfx_dl_invalidate_material();
                break;
#endif
            case G_SETSCISSOR:
                gfx_dl_invalidate_material();
                gfx_dp_set_scissor(C1(24, 2), C0(12, 12), C0(0, 12), C1(12, 12), C1(0, 12));
                break;
            case G_SETZIMG:
                gfx_dl_invalidate_material();
                gfx_dp_set_z_image(seg_addr(cmd->words.w1));
                break;
            case G_SETCIMG:
                gfx_dl_invalidate_material();
                gfx_dp_set_color_image(C0(21, 3), C0(19, 2), C0(0, 11), seg_addr(cmd->words.w1));
                break;
            default:
                /* Unknown/no-op commands used to invalidate the memo too.
                 * GBI2 calls TRI2 a triangle in the old classifier even
                 * though this switch does not implement that opcode. */
#ifdef F3DEX_GBI_2
                if (opcode != (uint8_t)G_TRI2)
#endif
                    gfx_dl_invalidate_material();
                break;
        }
#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
        // Rectangles restore temporary combiner state after their internal
        // triangles, so invalidate again before the next display-list opcode.
        if(material_opcode)triangle_material_ready=false;
#endif
        ++cmd;
    }
}

static void gfx_sp_reset() {
#ifdef TARGET_OPENFPGA
#if GFX_GPU_GEOMETRY_WORKER_APP
    gfx_flush_vertex_attributes(0xffffffffu);
#endif
    triangle_material_ready=false;
    ++vertex_state_epoch;
#endif
    rsp.modelview_matrix_stack_size = 1;
    rsp.current_num_lights = 2;
    rsp.lights_changed = true;
    rsp.lookat_changed = true;
}

void gfx_get_dimensions(uint32_t *width, uint32_t *height) {
    gfx_wapi->get_dimensions(width, height);
}

void gfx_init(struct GfxWindowManagerAPI *wapi, struct GfxRenderingAPI *rapi, const char *game_name, bool start_in_fullscreen) {
    gfx_wapi = wapi;
    gfx_rapi = rapi;
    gfx_wapi->init(game_name, start_in_fullscreen);
    gfx_wapi->get_dimensions(&gfx_current_dimensions.width, &gfx_current_dimensions.height);
    gfx_rapi->init();

    // Used in the 120 star TAS
    static uint32_t precomp_shaders[] = {
        0x01200200,
        0x00000045,
        0x00000200,
        0x01200a00,
        0x00000a00,
        0x01a00045,
        0x00000551,
        0x01045045,
        0x05a00a00,
        0x01200045,
        0x05045045,
        0x01045a00,
        0x01a00a00,
        0x0000038d,
        0x01081081,
        0x0120038d,
        0x03200045,
        0x03200a00,
        0x01a00a6f,
        0x01141045,
        0x07a00a00,
        0x05200200,
        0x03200200,
        0x09200200,
        0x0920038d,
        0x09200045
    };
    for (size_t i = 0; i < sizeof(precomp_shaders) / sizeof(uint32_t); i++) {
        gfx_lookup_or_create_shader_program(precomp_shaders[i]);
    }
}

void gfx_shutdown(void) {
    if (gfx_rapi && gfx_rapi->shutdown) gfx_rapi->shutdown();
    if (gfx_wapi && gfx_wapi->shutdown) gfx_wapi->shutdown();
    gfx_rapi = NULL;
    gfx_wapi = NULL;
}

struct GfxRenderingAPI *gfx_get_current_rendering_api(void) {
    return gfx_rapi;
}

void gfx_start_frame(void) {
#if LOAD_ALIAS_ENABLED
    load_alias_dirty=3;load_alias_controls_valid=0;
#endif
#ifdef TARGET_OPENFPGA
    ++vertex_state_epoch;
#endif
    gfx_wapi->handle_events();
    gfx_wapi->get_dimensions(&gfx_current_dimensions.width, &gfx_current_dimensions.height);
    if (gfx_current_dimensions.height == 0) {
        // Avoid division by zero
        gfx_current_dimensions.height = 1;
    }
    ratio_x = (float)gfx_current_dimensions.width / (float)SCREEN_WIDTH;
    ratio_y = (float)gfx_current_dimensions.height / (float)SCREEN_HEIGHT;
    inv_ratio_x = (float)SCREEN_WIDTH / (float)gfx_current_dimensions.width;
    inv_ratio_y = (float)SCREEN_HEIGHT / (float)gfx_current_dimensions.height;
    gfx_current_dimensions.aspect_ratio = (float)gfx_current_dimensions.width / (float)gfx_current_dimensions.height;
    /* Preserve the original rounded aspect before the quotient: fast-math
     * must not reassociate it through the width/height expression above. */
    volatile float rounded_aspect = gfx_current_dimensions.aspect_ratio;
    gfx_vertex_aspect_scale = (4.0f / 3.0f) / rounded_aspect;
}

void gfx_run(Gfx *commands) {
    gfx_sp_reset();

#ifdef OF_DBG_SHADE
    of_dbg_frame++;
    if (of_dbg_frame == 3)
        fprintf(stderr, "SHADE: captured %d main-face lines (done)\n", of_dbg_lines);
#endif

    //puts("New frame");

    if (!gfx_wapi->start_frame()) {
        dropped_frame = true;
        return;
    }
    dropped_frame = false;
#ifdef TARGET_OPENFPGA
    uint32_t perf_render_begin=sm64_perf_clock();
    sm64_perf_render_begin(perf_render_begin);
#endif

#ifdef TARGET_POCKET
    uint32_t _t0 = PROF_TMR();
    prof_vtx = prof_mtx = prof_emit = prof_tex = 0;
    prof_tex_import = g_flush_n = g_flush_sub = g_flush_tris = 0;
    g_fln_comb = g_fln_tex = g_fln_zst = g_fln_vp = 0;
#endif
    gfx_rapi->start_frame();
#ifdef TARGET_OPENFPGA
    vc_slot_stale = 0xFFFFFFFFu;   /* frame start invalidates the GPU vertex cache */
#endif
#ifdef TARGET_POCKET
    uint32_t _t1 = PROF_TMR();
#endif
    { SM64_PERF_SCOPE(SM64_PERF_DL); gfx_run_dl(commands,NULL); }
#if LOAD_ALIAS_ENABLED
    gfx_gpu_queued_front_release();
    gfx_gpu_queued_front_service();
#endif
#if GFX_CPU_DEFERRED_FOG
    /* Fog configuration can change between frames. Finish the last pending
     * batch now; earlier overwritten batches avoided the unused alpha work. */
    gfx_cpu_flush_fog(0xffffffffu);
#endif
#if LOAD_ALIAS_ENABLED
    if(sm64_async_front_capability==1u) {
        /* rsp.loaded_vertices survives frame reset: retain its last values
         * before returning the raw bank, even when fog had no pending read. */
        load_alias_materialize_all();
        gfx_gpu_async_raw_release();
    }
#endif
#ifdef TARGET_POCKET
    uint32_t _t2 = PROF_TMR();
#endif
    gfx_flush();
#ifdef TARGET_POCKET
    uint32_t _t3 = PROF_TMR();
#endif
    gfx_rapi->end_frame();
    gfx_wapi->swap_buffers_begin();
#ifdef TARGET_OPENFPGA
    sm64_perf_render_end(perf_render_begin,sm64_perf_clock());
#endif
#ifdef TARGET_POCKET
    uint32_t _t4 = PROF_TMR();
    uint32_t frame_ticks = _t0 - prof_prev_t0;   /* true frame-to-frame period */
    prof_prev_t0 = _t0;
    /* Sample ring backpressure EVERY frame (the getter resets per-call), so the
     * printed spin/free belong to THIS frame, aligned with its timings. */
    unsigned rspin, rfree;
    gpu_ring_prof_get(&rspin, &rfree);
    /* Present is now mostly idle: split it so `acq` (blocked waiting for a free
     * draw buffer) is not mistaken for CPU work. */
    unsigned pflip, pacq;
    gpu_present_prof_get(&pflip, &pacq);
    /* Vertex-cache wire cost, sampled per frame like the ring counters:
     * words emitted on the draw path, tris via cache (0x54) vs flattened
     * (0x4E/0x4B), and 0x56 vertex uploads. */
    unsigned vcw, vcc, vcl, vcv;
    gpu_vc_prof_get(&vcw, &vcc, &vcl, &vcv);
    if (tri_diag.frame % 60 == 0) {
        /* Tick rate comes from the caps descriptor, NOT a constant: the
         * 0x40000004 counter runs at the CPU clock, and this build reports
         * cpu_freq_hz = 90 MHz.  The old hardcoded 110000 ticks/ms made every
         * printed ms 22% LOW and every fps 22% HIGH -- which is why light frames
         * read "36fps/27ms", i.e. faster than the 30 Hz sim cap they were
         * actually sitting on (33.3 ms).  Confirmed independently by `acq`,
         * which lands on one 60 Hz field once scaled correctly.
         * other = everything outside gfx_run (game logic + audio + sim-cap
         * idle); render = the F3DEX walk + emit; then flush and present; plus
         * drawn tris and avg GPU batch (emit efficiency). */
        const uint32_t hz = PROF_HZ();
        const uint32_t tps_ms = hz / 1000u;
        #define MS(t) ((t) / tps_ms)
        uint32_t run = _t4 - _t0;
        uint32_t other = frame_ticks > run ? frame_ticks - run : 0u;
        printf("PERF f%d: %ufps %ums | other %u  render %u [vtx%u emit%u tex%u]  "
               "flush %u  present %u [flip%u acq%u]  audio %u | tris %d batch %u  ring spin%u free%u"
               "  vc c%u l%u v%u w%u\n",
            tri_diag.frame,
            frame_ticks ? hz / frame_ticks : 0u, MS(frame_ticks),
            MS(other), MS(_t2 - _t1), MS(prof_vtx), MS(prof_emit), MS(prof_tex),
            MS(_t3 - _t2), MS(_t4 - _t3), MS(pflip), MS(pacq), MS(g_prof_audio),
            tri_diag.direct, g_flush_sub ? g_flush_tris / g_flush_sub : 0u,
            rspin, rfree,
            vcc, vcl, vcv, vcw);
        #undef MS
    }
    g_prof_audio = 0;
#endif
}

void gfx_end_frame(void) {
    if (!dropped_frame) {
        gfx_rapi->finish_render();
        gfx_wapi->swap_buffers_end();
    }
#ifdef TARGET_POCKET
    ++tri_diag.frame;
    tri_diag.total = tri_diag.clip_reject = tri_diag.cull_reject =
        tri_diag.clipped = tri_diag.direct = 0;
#endif
}
