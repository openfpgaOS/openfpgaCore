/*
 * gfx_gpu.c -- openfpgaOS (os30) hardware GPU rendering backend for SM64.
 *
 * Replaces the software rasterizer with the os30 hardware vertex-triangle
 * path.  The CPU only transforms/projects vertices (already done by
 * gfx_pc.c into buf_vbo); this backend converts each vertex to the GPU's
 * fixed-point format and streams whole triangles via of_gpu_draw_vert_tri.
 * The GPU edge-walks, perspective-divides, samples a CI8 texture, shades
 * through the palookup colormap, and depth-tests against an SDRAM z-buffer.
 *
 * Two rendering models, chosen at boot by of_has_feature(OF_HW_GPU_VCOLOR):
 *
 *   TRUECOLOR (os30 with INCLUDE_DIRECT_COLOR — the preferred path):
 *     - framebuffer: 16-bit RGB565 direct colour
 *     - textures:    RGBA32 -> RGB565; a<0x80 -> 0x0000 (transparent)
 *     - shading:     RGB565 texel * per-vertex brightness (interpolated light,
 *                    OF_GPU_SPAN_TRUECOLOR); untextured -> 1x1 white texel
 *                    (vertex hue currently collapses to brightness — full
 *                    per-vertex RGB Gouraud would need the derive RGB planes)
 *
 *   PALETTIZED (fallback on cores without OF_HW_GPU_VCOLOR):
 *     - framebuffer: 8-bit indexed, palette == RGB332 (index encodes colour)
 *     - textures:    RGBA32 -> CI8 (nearest RGB332 index); a<0x80 -> index 0
 *     - shading:     per-vertex luminance -> palookup shade row (0..63)
 *     - untextured:  256x1 identity ramp carries the vertex colour via s
 *
 * of_gpu.h holds static ring state and MUST be included from exactly one
 * TU — this is that TU.
 */

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Diagnostic-only SDK wait episodes. Cumulative uint32 ticks wrap naturally;
 * callers take modular differences without resetting or touching MMIO.
 * The timer is read twice per blocking episode, never once per spin. The
 * first unsuccessful MMIO poll and final counter update are outside timing.
 * Reset follows SDK initialization, not the frame/snapshot boundaries. */
#if defined(SM64_PERF_DIAG) && SM64_PERF_DIAG && !defined(OF_PC)
static uint32_t gpu_wait_prof_started[3], gpu_wait_prof_ticks[3];
/* Submission timing is a subset of host render time. CPU-ring batches cover
 * staging loads, MMIO stores, status check and publish. DMA batches cover
 * source-copy/cache-visibility work only, excluding descriptor/idle waits.
 * One pair of reads per nonempty batch; counter updates are outside timing.
 * Compiler barriers keep ordinary staging loads inside the measured region.
 * These counters share the wait counter initialization epoch and wrap mod 2^32. */
static uint32_t gpu_submit_prof_started, gpu_submit_prof_ticks;
static uint32_t gpu_submit_prof_words, gpu_submit_prof_batches;
static uint32_t gpu_submit_prof_cpu_batches;
/* Includes source loads, MMIO stores and final drain. Cold upload calls only;
 * independent cumulative counters preserve the existing HOST/COPY meanings. */
static uint32_t gpu_upload_prof_started, gpu_upload_prof_ticks;
static uint32_t gpu_upload_prof_words, gpu_upload_prof_calls, gpu_upload_prof_burst;
static inline void gpu_upload_prof_begin(void) {
    gpu_upload_prof_started = *(volatile uint32_t *)0x40000004;
    __asm__ volatile("" ::: "memory");
}
static inline void gpu_upload_prof_end(uint32_t words, unsigned burst) {
    __asm__ volatile("" ::: "memory");
    const uint32_t now = *(volatile uint32_t *)0x40000004;
    gpu_upload_prof_ticks += now - gpu_upload_prof_started;
    gpu_upload_prof_words += words;
    gpu_upload_prof_calls++;
    gpu_upload_prof_burst = burst;
}
#define OF_GPU_UPLOAD_PROFILE_BEGIN() gpu_upload_prof_begin()
#define OF_GPU_UPLOAD_PROFILE_END(words, burst) gpu_upload_prof_end(words, burst)
static inline void gpu_submit_prof_begin(void) {
    gpu_submit_prof_started = *(volatile uint32_t *)0x40000004;
    __asm__ volatile("" ::: "memory");
}
static inline void gpu_submit_prof_end(unsigned cpu_ring, uint32_t words) {
    __asm__ volatile("" ::: "memory");
    const uint32_t now = *(volatile uint32_t *)0x40000004;
    gpu_submit_prof_ticks += now - gpu_submit_prof_started;
    gpu_submit_prof_words += words;
    gpu_submit_prof_batches++;
    gpu_submit_prof_cpu_batches += cpu_ring != 0;
}
#define OF_GPU_SUBMIT_PROFILE_BEGIN(cpu_ring) gpu_submit_prof_begin()
#define OF_GPU_SUBMIT_PROFILE_END(cpu_ring, words) \
    gpu_submit_prof_end(cpu_ring, words)
static inline void gpu_wait_prof_begin(unsigned kind) {
    gpu_wait_prof_started[kind] = *(volatile uint32_t *)0x40000004;
}
static inline void gpu_wait_prof_end(unsigned kind) {
    const uint32_t now = *(volatile uint32_t *)0x40000004;
    gpu_wait_prof_ticks[kind] += now - gpu_wait_prof_started[kind];
}
static inline void gpu_wait_prof_reset(void) {
    for (unsigned i = 0; i < 3; i++)
        gpu_wait_prof_started[i] = gpu_wait_prof_ticks[i] = 0;
    gpu_submit_prof_started = gpu_submit_prof_ticks = 0;
    gpu_submit_prof_words = gpu_submit_prof_batches = 0;
    gpu_submit_prof_cpu_batches = 0;
    gpu_upload_prof_started = gpu_upload_prof_ticks = 0;
    gpu_upload_prof_words = gpu_upload_prof_calls = gpu_upload_prof_burst = 0;
}
#define OF_GPU_WAIT_PROFILE_BEGIN(kind, first) \
    do { if (first) gpu_wait_prof_begin(kind); } while (0)
#define OF_GPU_WAIT_PROFILE_END(kind, waited) \
    do { if (waited) gpu_wait_prof_end(kind); } while (0)
#define OF_GPU_WAIT_PROFILE_RESET() gpu_wait_prof_reset()
#endif

#include "of.h"
#include "of_caps.h"
#include "of_cache.h"
void gfx_gpu_queued_front_service(void);
void gfx_gpu_async_service(void);
void gfx_gpu_wait_service(void);
#define OF_GPU_WAIT_HOOK() gfx_gpu_wait_service()
#include "of_gpu.h"
#include "of_video.h"

#include "gfx_cc.h"
#include "gfx_rendering_api.h"
#include "gfx_gpu.h"
#include "pocket/perf_pocket.h"

/* Keep the exported mode query consistent with gfx_pc.c's compile-time
 * renderer selection. Hardware capability detection for ordinary triangles,
 * clip-space vertex loads, textures and blending is unchanged. */
#ifndef GFX_GPU_GEOMETRY_WORKER_APP
#define GFX_GPU_GEOMETRY_WORKER_APP 0
#endif
#ifndef GFX_GPU_GEOMETRY
#define GFX_GPU_GEOMETRY 1
#endif
static int g_geometry;
static int g_geometry_matrix_valid;
static uint32_t g_geometry_serial;
int gfx_gpu_geometry_enabled(void) { return GFX_GPU_GEOMETRY_WORKER_APP && g_geometry; }

#define SCR_W       320
#define SCR_H       240
#define FB_STRIDE   SCR_W
#define CMAP_ROWS   64
#define ZBUF_ELEM   2            /* 16-bit z entries */
#define RAMP_W      256          /* identity ramp for untextured tris */

/* The HW vert-tri derive forms s*zi in Q16.16, fits an affine plane per
 * attribute (du = sat32((num*rdet) >>> 44), Q16.16 gradient), and per-pixel
 * perspective-divides texel = (s*zi)/zi.  The plane gradient carries a FIXED
 * 1/65536 ULP, but rdet's reciprocal scale (2^44) is magnitude-independent, so
 * the number of SIGNIFICANT gradient bits that survive the >>>44 grows with the
 * magnitude of zi/szi.  SM64's large world units make raw 1/w land at only
 * ~3..655 in Q16.16 — a starved magnitude, so each of two edge-adjacent
 * triangles re-derives its own coarse plane with its own rounding/anchor and
 * the two reconstructions disagree by the residual along the shared diagonal:
 * the faint perspective seam.
 *
 * Fix: scale zi up so the derived planes use the full Q16.16 window.  The scale
 * CANCELS in (s*zi)/zi (texel = interp(u/w)/interp(1/w)), so it is free to vary
 * PER TRIANGLE — adjacent triangles with different scales still reconstruct the
 * identical shared-edge texel, each just nearer the true value.  Rather than one
 * global constant (which underflows on far surfaces and overflows on near/tiled
 * ones), gpu_draw_triangles picks a per-triangle zscale that lands the largest
 * derived attribute (szi/tzi/zi) at ZI_DERIVE_TARGET.  Depth is decoupled
 * (depth[] = 1/w*2^30, GPU reads sp_depth_value not zi), so this never touches
 * the z-buffer.  ZI_SCALE is the fallback for degenerate (zero-magnitude) tris. */
#define ZI_SCALE    64

/* Peak |value| (NOT raw Q16.16) the per-triangle scale targets for the largest
 * of szi/tzi/zi.  szi value = (u/w)*zscale; with all three attrs held <= 8192
 * the raw Q16.16 stays ~2^29 — comfortably inside signed int32 AND below the
 * derive's int32 saturation of the per-attribute difference da = a_k - a0
 * (deriv_sat33 in gpu_core.v, which needs the value range < 16384).  8192 keeps
 * ~1 bit of headroom; pushing toward 16384 buys only one more bit. */
#define ZI_DERIVE_TARGET    8192.0f

/* Give decals a small offset for quantized depth and screen coordinates.
 * A 0.1% relative offset covers a few compressed depth codes; the half-pixel
 * slope allowance handles differently tessellated receiver surfaces. Large
 * offsets lift ground shadows in front of character geometry: the previous
 * 1.2% plus four-pixel offset darkened Mario's overalls and Bowser's feet. */
#define DECAL_Z_BIAS        1.001f
#define DECAL_SLOPE_PIXELS  0.5f

#define MAX_TEX     4096
#define MAX_SHADERS 64

/* ================================================================
 * RGB332 helpers (palette index == colour)
 * ================================================================ */

static inline uint8_t rgb332(int r, int g, int b) {
    if (r < 0) r = 0; else if (r > 255) r = 255;
    if (g < 0) g = 0; else if (g > 255) g = 255;
    if (b < 0) b = 0; else if (b > 255) b = 255;
    return (uint8_t)((r & 0xE0) | ((g >> 3) & 0x1C) | (b >> 6));
}

static inline void rgb332_expand(uint8_t i, int *r, int *g, int *b) {
    int r3 = (i >> 5) & 7, g3 = (i >> 2) & 7, b2 = i & 3;
    *r = (r3 << 5) | (r3 << 2) | (r3 >> 1);
    *g = (g3 << 5) | (g3 << 2) | (g3 >> 1);
    *b = (b2 << 6) | (b2 << 4) | (b2 << 2) | b2;
}

/* Mirrors gfx_pc.c's SCALE_5_8 (5-bit -> 8-bit channel widening).  Kept in sync
 * by construction: both are the exact multiply-shift form of (v * 0xFF) / 0x1F,
 * bit-identical for every 5-bit input.  Used by the RGBA16 direct upload below,
 * which must reproduce gfx_pc's two-stage result exactly. */
#define SCALE_5_8_GPU(V_) (((V_) * 1053u) >> 7)

static inline uint16_t rgba_to_565(int r, int g, int b) {
    if (r < 0) r = 0; else if (r > 255) r = 255;
    if (g < 0) g = 0; else if (g > 255) g = 255;
    if (b < 0) b = 0; else if (b > 255) b = 255;
    return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

/* ================================================================
 * State
 * ================================================================ */

struct GTex {
    uint8_t  *ci8;          /* current pixel buffer = slot[cur]; NULL until first upload */
    uint8_t  *slot[2];      /* double-buffered store: a re-upload lands in the OTHER slot */
    uint8_t  *alpha_mask[2]; /* complementary binary-alpha texture, built on demand */
    uint32_t  slot_epoch[2]; /* last GPU use, for safe reuse within a frame */
    uint32_t  slotcap[2];   /* allocated bytes per slot */
    uint8_t   cur;          /* which slot ci8 / addr currently point at */
    uint16_t  w, h;
    uint16_t  wmask, hmask;
    uint32_t  addr;         /* CPU/AXI byte address the GPU samples from */
    uint8_t   cms, cmt;     /* N64 wrap mode (G_TX_MIRROR=1 / G_TX_CLAMP=2) per axis */
    /* Keep the original hot fields at their established offsets. */
    uint32_t  fast_addr[2], fast_capacity[2];
    uint32_t  fast_mask_addr[2], fast_mask_capacity[2];
};

struct ShaderProgram {
    uint32_t shader_id;
    struct ShaderProgram *next;
    struct CCFeatures cc;
    int stride;             /* floats per vertex in buf_vbo */
    int coff;               /* float offset of first colour input (RGB) */
    int used;
};

static struct GTex   g_tex[MAX_TEX];
static uint32_t      g_tex_count;
static uint32_t      g_tex_epoch = 1;
static struct GTex  *g_cur_tex[2];
static int           g_cur_tmu;

static struct ShaderProgram g_shaders[MAX_SHADERS];
static struct ShaderProgram *g_extra_shaders;
static int           g_shader_count;
static struct ShaderProgram *g_cur_shader;

static uint8_t       g_colormap[CMAP_ROWS * 256];
static uint8_t      *g_ramp;            /* RAMP_W x1 identity texture */
static uint32_t      g_ramp_addr;
static uint16_t     *g_zbuf;

static int           g_draw_idx;
static uint32_t      g_draw_fb;
static int           g_fb_active;
static uint32_t      g_flip_token;   /* GPU fence of the last submitted flip */
static int           g_flip_pending; /* flip submitted, buffer not yet re-acquired */

/* Split the old single "present" number into its two halves, because they have
 * completely different cures: flip = submitting the flip command (CPU work),
 * acq = of_video_acquire_next blocking until a draw buffer frees (pure idle --
 * a display-cadence / OS-side wait the app cannot shorten, only overlap). */
#if defined(TARGET_OPENFPGA) && !defined(OF_PC)
#define PROF_PRESENT_TMR() (*(volatile uint32_t *)0x40000004)
static uint32_t prof_flip_ticks, prof_acq_ticks, prof_flip_t0, prof_acq_t0;
#define PROF_FLIP_BEGIN()  (prof_flip_t0 = PROF_PRESENT_TMR())
#define PROF_FLIP_END()    (prof_flip_ticks += PROF_PRESENT_TMR() - prof_flip_t0)
#define PROF_ACQ_BEGIN()   (prof_acq_t0 = PROF_PRESENT_TMR())
#define PROF_ACQ_END()     (prof_acq_ticks += PROF_PRESENT_TMR() - prof_acq_t0)
#else
#define PROF_FLIP_BEGIN()  ((void)0)
#define PROF_FLIP_END()    ((void)0)
#define PROF_ACQ_BEGIN()   ((void)0)
#define PROF_ACQ_END()     ((void)0)
#endif
static int           g_has_gpu;
static int           g_truecolor;       /* RGB565 direct-color path (OF_HW_GPU_VCOLOR) */
static int           g_combine;         /* full texel*C+D combiner (OF_HW_GPU_COMBINE);
                                         * 0 on the lean os30 (EXCLUDE_COMBINE) -> HILITE
                                         * surfaces fall back to plain texel*shade */
/* Compile-time kill switch for the 0x56/0x54 vertex-cache fast path: build with
 * -DGFX_GPU_VTX_CACHE=0 to force the legacy per-triangle 0x4E stream even on a
 * bitstream that advertises OF_HW_GPU_CLIP_LOAD (byte-identical old behavior). */
#ifndef GFX_GPU_VTX_CACHE
#define GFX_GPU_VTX_CACHE 1
#endif
static int           g_clip_load;       /* 0x56 clip-load + 0x54 indexed-tri available
                                         * (OF_HW_GPU_VERT_TRI && OF_HW_GPU_CLIP_LOAD) */

#ifndef GFX_GPU_BRAM_IMPORT_APP
#define GFX_GPU_BRAM_IMPORT_APP 1
#endif
/* BIM2 snapshot experiment: capability-gated and independent of the old geometry
 * worker. Stats: claim attempts/successes, imported vertices, releases,
 * raw-copy waits/polls, failures, unsupported capability. */
uint32_t gfx_bram_stats[8];
/* Public probe state permits an identical captured ELF/state to probe the
 * actual model's capability after replay restore:0 unknown,1 BIM2,2 absent. */
unsigned sm64_bram_import_capability;
static unsigned g_bram_disabled;
#ifndef OF_PC
#define BRAM_GEO_REGS ((volatile uint32_t *)0x4b000000u)
#endif
/* MTF2 same-ELF fixture probe and bounded diagnostic counters. */
unsigned sm64_mesh_front_capability;
uint32_t gfx_mesh_stats[16];
unsigned sm64_async_front_capability;
unsigned sm64_async_group_capability;
/* candidates, dead-suffix rejects, material rejects, LOAD rejects, queued jobs,
 * queued input triangles, context words, repairs, repair output words,
 * recomputed vertices, legacy leases, lease polls, drain polls, faults,
 * reset cancellations, absent probes. These are not emitted-triangle counts. */
uint32_t gfx_async_stats[16];
static uint32_t async_sequence,async_ticket,async_epoch,async_epoch_valid;
static uint32_t async_servicing,async_yielding;
static uint32_t async_finished_repair_token;
static unsigned async_finished_repair_valid,async_legacy_held,async_legacy_quiescent;

int gfx_gpu_async_supported(void) {
#ifndef OF_PC
    if(!g_clip_load||!g_truecolor)return 0;
    if(!sm64_async_front_capability) {
        const uint32_t capability=BRAM_GEO_REGS[0x20];
        sm64_async_front_capability=(capability==0x414a3032u||capability==0x414a3033u)?1u:2u;
        sm64_async_group_capability=capability==0x414a3033u?1u:2u;
        if(sm64_async_front_capability==2u)++gfx_async_stats[15];
    }
    return sm64_async_front_capability==1u;
#else
    return 0;
#endif
}
int gfx_gpu_async_group_supported(void) {
    return gfx_gpu_async_supported()&&sm64_async_group_capability==1u;
}
static void gpu_async_fault(void);
static int gpu_async_epoch_check(void) {
#ifndef OF_PC
    const uint32_t now=BRAM_GEO_REGS[0x2e];
    if(!async_epoch_valid||now!=async_epoch) {
        const int changed=async_epoch_valid;
        async_epoch=now;async_epoch_valid=1;
        async_sequence=BRAM_GEO_REGS[0x23];
        async_finished_repair_valid=0;
        /* Consumer reset preserves a granted legacy lease and raw bank. */
        if(async_legacy_held && (!(BRAM_GEO_REGS[0x21]&16u)||
           BRAM_GEO_REGS[0x2f]!=async_ticket))gpu_async_fault();
        return !changed;
    }
#endif
    return 1;
}
static void gpu_async_fault(void) {
    ++gfx_async_stats[13];
    __builtin_trap();
}
/* AJ02 can lend the raw bank before older queued jobs finish. Ordinary
 * commands still forward under a lease, so a completed sequence proves there
 * is no blocked async dependency and permits retaining consecutive LOADs. */
static int gpu_async_raw_blocks_jobs(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u||!async_legacy_held||async_legacy_quiescent)return 0;
    gpu_async_epoch_check();
    if(BRAM_GEO_REGS[0x23]!=async_sequence)return 1;
    /* No new async job can be submitted while this same lease is held:
     * enqueue explicitly releases first. Consumer reset preserves the lease
     * and cancels async work, so it cannot invalidate this no-blocker fact. */
    async_legacy_quiescent=1;
    return 0;
#else
    return 0;
#endif
}
void gfx_gpu_async_raw_release_if_pending(void) {
    if(gpu_async_raw_blocks_jobs())gfx_gpu_async_raw_release();
}
void gfx_gpu_async_before_wait(void) {
#ifndef OF_PC
    if(async_yielding||!gpu_async_raw_blocks_jobs())return;
    async_yielding=1;
    /* Pure frontend copy: no command emission, allocation or GPU wait. The
     * driver has completed and registered its raw result before any such wait. */
    gfx_async_materialize_raw();
    gfx_gpu_async_raw_release();
    async_yielding=0;
#endif
}
void gfx_gpu_wait_service(void) {
    gfx_gpu_async_before_wait();
    gfx_gpu_queued_front_service();
}
struct AsyncRepairSink { uint32_t epoch,token; };
static int gpu_async_repair_word(void *opaque,uint32_t word) {
#ifndef OF_PC
    const struct AsyncRepairSink *sink=opaque;
    for(;;) {
        const uint32_t status=BRAM_GEO_REGS[0x21];
        /* READY includes the matching acknowledged repair token. Reset
         * removes that gate, so no old word can enter a later unacked job.
         * The caller also rechecks epoch/token before sending END. */
        if(status&64u) {
            BRAM_GEO_REGS[0x28]=word;
            return 1;
        }
        if(BRAM_GEO_REGS[0x2e]!=sink->epoch||BRAM_GEO_REGS[0x25]!=sink->token)return 0;
        if(status&4u)gpu_async_fault();
        if(!(status&2u))return 0;
    }
#else
    (void)opaque;(void)word;return 0;
#endif
}
void gfx_gpu_async_service(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u||async_servicing)return;
    const uint32_t status=BRAM_GEO_REGS[0x21];
    if(status&4u)gpu_async_fault();
    if(!(status&2u))return;
    if(!gpu_async_epoch_check())return;
    struct AsyncRepairSink sink={async_epoch,BRAM_GEO_REGS[0x25]};
    if(async_finished_repair_valid&&async_finished_repair_token==sink.token)return;
    async_servicing=1;
    uint32_t fields[36];
    for(unsigned i=0;i<36;i++) {
        BRAM_GEO_REGS[0x26]=i;
        fields[i]=BRAM_GEO_REGS[0x27];
    }
    struct am_repair_job job;
    memset(&job,0,sizeof(job));
    memcpy(&job.transform,fields,sizeof(job.transform));
    job.vertices=(const unsigned char *)(uintptr_t)fields[28];
    job.commands=(const uint32_t (*)[2])(uintptr_t)fields[29];
    job.count=(fields[30]>>12)&255u;
    job.command_count=fields[31]&127u;job.cull=(fields[31]>>8)&3u;
    job.material.flags_rgb=fields[32];job.material.u_offset=fields[33];
    job.material.v_offset=fields[34];job.material.alpha=fields[35];
    job.material.xcenter=(int32_t)fields[24];job.material.ycenter=(int32_t)fields[25];
    job.material.xscale=(int32_t)fields[26];job.material.yscale=(int32_t)fields[27];
    job.raw_valid=(BRAM_GEO_REGS[0x24]>>8)&1u;
    uint32_t raw[32][8];
    if(!job.count||job.count>32)gpu_async_fault();
    if(job.raw_valid) {
        __asm__ volatile("fence rw,rw" ::: "memory");
        for(uintptr_t p=0x13f80000u,end=p+job.count*32u;p<end;p+=64u)
            __asm__ volatile(".insn i 0x0f,2,x0,%0,0" :: "r"(p) : "memory");
        __asm__ volatile("fence rw,rw" ::: "memory");
        const volatile uint32_t *view=(const volatile uint32_t *)0x13f80000u;
        for(unsigned i=0;i<job.count*8u;i++)((uint32_t *)raw)[i]=view[i];
        job.raw=(const uint32_t (*)[8])raw;
    }
    if(BRAM_GEO_REGS[0x2e]!=sink.epoch||BRAM_GEO_REGS[0x25]!=sink.token) {
        ++gfx_async_stats[14];async_servicing=0;return;
    }
    BRAM_GEO_REGS[0x29]=sink.token;
    struct am_repair_result result;
    const int code=am_repair(&job,gpu_async_repair_word,&sink,&result);
    if(code==AM_REPAIR_BAD_INPUT)gpu_async_fault();
    if(code==AM_REPAIR_CANCELLED||BRAM_GEO_REGS[0x2e]!=sink.epoch||
       BRAM_GEO_REGS[0x25]!=sink.token) {
        ++gfx_async_stats[14];async_servicing=0;return;
    }
    BRAM_GEO_REGS[0x2a]=1;
    gfx_async_note_repair(job.commands);
    async_finished_repair_token=sink.token;async_finished_repair_valid=1;
    ++gfx_async_stats[7];gfx_async_stats[8]+=result.emitted_words;
    gfx_async_stats[9]+=result.recomputed_vertices;
    async_servicing=0;
#endif
}
void gfx_gpu_async_drain(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u)return;
    gfx_gpu_async_before_wait();
    gpu_async_epoch_check();
    if(BRAM_GEO_REGS[0x23]!=async_sequence)of_gpu_kick_now();
    for(;;) {
        if(!gpu_async_epoch_check())return;
        if(BRAM_GEO_REGS[0x23]==async_sequence)return;
        gfx_gpu_async_service();++gfx_async_stats[12];
    }
#endif
}
int gfx_gpu_async_raw_acquire(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u)return 0;
    if(async_legacy_held&&async_legacy_quiescent)return 0;
    gpu_async_epoch_check();
    if(async_legacy_held)return 0;
    if(BRAM_GEO_REGS[0x23]!=async_sequence)of_gpu_kick_now();
retry:
    gpu_async_epoch_check();
    if(!++async_ticket)++async_ticket;
    BRAM_GEO_REGS[0x2b]=async_sequence;
    BRAM_GEO_REGS[0x2c]=async_ticket;
    BRAM_GEO_REGS[0x2d]=1;
    for(;;) {
        if(!gpu_async_epoch_check())goto retry;
        const uint32_t status=BRAM_GEO_REGS[0x21];
        if(status&4u)gpu_async_fault();
        if((status&16u)&&BRAM_GEO_REGS[0x2f]==async_ticket)break;
        if(status&2u)gfx_gpu_async_service();
        ++gfx_async_stats[11];
    }
    async_legacy_held=1;async_legacy_quiescent=0;
    ++gfx_async_stats[10];return 1;
#else
    return 0;
#endif
}
void gfx_gpu_async_raw_release(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u||!async_legacy_held)return;
    gpu_async_epoch_check();
    __asm__ volatile("fence rw,rw" ::: "memory");
    BRAM_GEO_REGS[0x2d]=2;
    async_legacy_held=0;async_legacy_quiescent=0;
#endif
}
int gfx_gpu_mesh_supported(void) {
#ifndef OF_PC
    if(!g_clip_load||!g_truecolor)return 0;
    if(!sm64_mesh_front_capability) {
        sm64_mesh_front_capability=BRAM_GEO_REGS[0x20]==0x4d544632u?1u:2u;
        if(sm64_mesh_front_capability==2u)++gfx_mesh_stats[15];
    }
    return sm64_mesh_front_capability==1u;
#else
    return 0;
#endif
}
void gfx_gpu_mesh_wait_source(void) {
#ifndef OF_PC
    if(sm64_mesh_front_capability!=1u)return;
    /* A validated owner can be waiting for packet credit held by older staged
     * markers. Publish once before waiting; after copy starts, its complete
     * reservation makes progress independent of GPU consumption. */
    const uint32_t status=BRAM_GEO_REGS[0x21];
    if((status&0xa1u)==0x21u)of_gpu_kick_now();
    while(BRAM_GEO_REGS[0x21]&1u)++gfx_mesh_stats[13];
#endif
}
/* QGF1 probe and counters are exported for paired native fixture restore.
 * considered runs, queued runs/triangles, claims attempted/accepted, releases,
 * reused claims, metadata rejects, material rejects, bank backpressure,
 * repairs/kept repairs, raw-copy waits/polls, protocol errors, absent probes. */
unsigned sm64_queued_front_capability;
uint32_t gfx_queued_stats[16];
static uint32_t queued_owned_generation;
static unsigned queued_generation_owned;
int gfx_gpu_queued_front_supported(void) {
#ifndef OF_PC
    if (!g_clip_load || !g_truecolor) return 0;
    if (!sm64_queued_front_capability) {
        sm64_queued_front_capability = BRAM_GEO_REGS[0x01a] == 0x51474631u ? 1u : 2u;
        if (sm64_queued_front_capability == 2u) ++gfx_queued_stats[15];
    }
    return sm64_queued_front_capability == 1u;
#else
    return 0;
#endif
}

/* Arithmetic-only repair never reads mutable renderer state or emits GPU
 * commands. RNE and the nine-op fallback DAG are explicit, and caller FCSR
 * is restored because this can run in an unrelated ring/fence wait. */
void gfx_gpu_queued_front_service(void) {
#ifndef OF_PC
    gfx_gpu_async_service();
    if (sm64_queued_front_capability != 1u) return;
    const unsigned status = BRAM_GEO_REGS[0x020];
    if (status & 4u) { ++gfx_queued_stats[14]; __builtin_trap(); }
    if (!(status & 2u)) return;
    const uint32_t serial = BRAM_GEO_REGS[0x021];
    const unsigned cull = BRAM_GEO_REGS[0x023] & 3u;
    float v[9];
    for (unsigned i = 0; i < 9; ++i) {
        BRAM_GEO_REGS[0x024] = i;
        const uint32_t word = BRAM_GEO_REGS[0x025];
        memcpy(&v[i], &word, sizeof(word));
    }
    unsigned saved_fcsr;
    __asm__ volatile("csrr %0,fcsr" : "=r"(saved_fcsr));
    float center_x, center_y, dx0, dy0, dx2, dy2, product0, product1, cross;
    __asm__ volatile(
        "fmul.s %[cx],%[x1],%[iw1],rne\n\t"
        "fmul.s %[cy],%[y1],%[iw1],rne\n\t"
        "fmsub.s %[dx0],%[x0],%[iw0],%[cx],rne\n\t"
        "fmsub.s %[dy0],%[y0],%[iw0],%[cy],rne\n\t"
        "fmsub.s %[dx2],%[x2],%[iw2],%[cx],rne\n\t"
        "fmsub.s %[dy2],%[y2],%[iw2],%[cy],rne\n\t"
        "fmul.s %[p0],%[dx0],%[dy2],rne\n\t"
        "fmul.s %[p1],%[dy0],%[dx2],rne\n\t"
        "fsub.s %[cross],%[p0],%[p1],rne"
        : [cx] "=&f"(center_x), [cy] "=&f"(center_y),
          [dx0] "=&f"(dx0), [dy0] "=&f"(dy0), [dx2] "=&f"(dx2), [dy2] "=&f"(dy2),
          [p0] "=&f"(product0), [p1] "=&f"(product1), [cross] "=&f"(cross)
        : [x0] "f"(v[0]), [y0] "f"(v[1]), [iw0] "f"(v[2]),
          [x1] "f"(v[3]), [y1] "f"(v[4]), [iw1] "f"(v[5]),
          [x2] "f"(v[6]), [y2] "f"(v[7]), [iw2] "f"(v[8]));
    unsigned reject = cull == 3u;
    /* Preserve the original ordered comparison even for exceptional NaNs:
     * -ffast-math must not turn !(cross<=0) into cross>0. */
    if (cull == 1u)
        __asm__ volatile("fle.s %0,%1,%2" : "=r"(reject) : "f"(cross), "f"(0.0f));
    else if (cull == 2u)
        __asm__ volatile("fle.s %0,%1,%2" : "=r"(reject) : "f"(0.0f), "f"(cross));
    const unsigned keep = !reject;
    /* Finish the comparisons before restoring flags. */
    __asm__ volatile("" : : "r"(keep) : "memory");
    __asm__ volatile("csrw fcsr,%0" : : "r"(saved_fcsr) : "memory");
    BRAM_GEO_REGS[0x02e] = serial;
    __asm__ volatile("fence rw,rw" ::: "memory");
    BRAM_GEO_REGS[0x02d] = 1u | (keep << 1);
    ++gfx_queued_stats[10]; gfx_queued_stats[11] += keep;
#endif
}
int gfx_gpu_queued_front_acquire(uint32_t generation) {
#ifndef OF_PC
    if (!gfx_gpu_queued_front_supported()) return 0;
    if (queued_generation_owned) {
        if (queued_owned_generation != generation) { ++gfx_queued_stats[14]; __builtin_trap(); }
        ++gfx_queued_stats[6]; return 1;
    }
    gfx_gpu_queued_front_service();
    const uint32_t status = BRAM_GEO_REGS[1];
    if (!(status & (3u << 11)) || (status & (1u | (1u << 9)))) {
        ++gfx_queued_stats[9]; return 0;
    }
    if ((status & (1u << 10)) || (status & 6u) != 6u || BRAM_GEO_REGS[0x0e] != generation) {
        ++gfx_queued_stats[14]; __builtin_trap();
    }
    ++gfx_queued_stats[3];
    __asm__ volatile("fence rw,rw" ::: "memory");
    BRAM_GEO_REGS[0x0f] = generation;
    __asm__ volatile("fence rw,rw" ::: "memory");
    if (!(BRAM_GEO_REGS[0x10] & 1u)) { ++gfx_queued_stats[9]; return 0; }
    queued_owned_generation = generation; queued_generation_owned = 1;
    ++gfx_queued_stats[4]; return 1;
#else
    (void)generation; return 0;
#endif
}
void gfx_gpu_queued_front_release(void) {
#ifndef OF_PC
    if (!queued_generation_owned) return;
    _gpu_stream_reserve_words(2u);
    uint32_t *packet = _gpu_ring_claim();
    *packet++ = (0x61u << 24) | 1u;
    *packet = queued_owned_generation;
    _gpu_ring_commit(2u);
    queued_generation_owned = 0;
    ++gfx_queued_stats[5];
    of_gpu_kick_now();
#endif
}
void gfx_gpu_queued_front_wait_source(void) {
#ifndef OF_PC
    if (sm64_queued_front_capability != 1u) return;
    unsigned polls = 0;
    while (BRAM_GEO_REGS[1] & (1u << 9)) {
        gfx_gpu_queued_front_service(); ++polls;
    }
    if (polls) { ++gfx_queued_stats[12]; gfx_queued_stats[13] += polls; }
#endif
}

int gfx_gpu_bram_import_supported(void) {
#ifndef OF_PC
    if (!GFX_GPU_BRAM_IMPORT_APP || !g_clip_load || !g_truecolor || g_bram_disabled)
        return 0;
    /* QGF has a producer-owned generation lease. Mixing the older automatic
     * per-batch RELEASE path would free a snapshot still referenced by runs. */
    if (gfx_gpu_queued_front_supported()) return 0;
    if (!sm64_bram_import_capability) {
        sm64_bram_import_capability = BRAM_GEO_REGS[0x0d] == OF_GPU_BRAM_IMPORT_CAP ? 1u : 2u;
        if (sm64_bram_import_capability == 2u) ++gfx_bram_stats[7];
    }
    return sm64_bram_import_capability == 1u;
#else
    return 0;
#endif
}

/* Probe before scanning vertices/building context. The CPU is the only claim
 * producer; a GPU release can only increase free snapshot capacity. This is
 * an optimization hint: acquire still checks source state and CLAIM result. */
int gfx_gpu_bram_import_available(void) {
#ifndef OF_PC
    const uint32_t status = BRAM_GEO_REGS[1];
    return (status & (3u << 11)) && !(status & (1u | (1u << 9)));
#else
    return 0;
#endif
}

void gfx_gpu_bram_import_wait(void) {
#ifndef OF_PC
    if (sm64_bram_import_capability != 1u) return;
    if (BRAM_GEO_REGS[1] & (1u << 9)) {
        /* BIM2 bit9 reserves the raw rows only while copying into a snapshot.
         * GPU-held READY banks do not block the next raw LOAD. */
        of_gpu_kick_now();
        unsigned polls = 0;
        while (BRAM_GEO_REGS[1] & (1u << 9)) ++polls;
        ++gfx_bram_stats[4]; gfx_bram_stats[5] += polls;
    }
#endif
}

int gfx_gpu_bram_import_acquire(uint32_t generation) {
#ifndef OF_PC
    if (!gfx_gpu_bram_import_supported()) return 0;
    ++gfx_bram_stats[0];
    const unsigned status = BRAM_GEO_REGS[1];
    /* Busy copier/producer is ordinary backpressure. Keep CPU fallback
     * available without waiting or disabling the feature. */
    if (status & (1u | (1u << 9))) return 0;
    if ((status & (1u << 10)) || (status & 6u) != 6u ||
        BRAM_GEO_REGS[0x0e] != generation) {
        ++gfx_bram_stats[6]; g_bram_disabled = 1; return 0;
    }
    __asm__ volatile("fence rw,rw" ::: "memory");
    BRAM_GEO_REGS[0x0f] = generation;
    __asm__ volatile("fence rw,rw" ::: "memory");
    const unsigned claim = BRAM_GEO_REGS[0x10];
    /* Duplicate generation, no free bank, or busy copier means no ownership.
     * In particular, never queue RELEASE for a rejected claim. */
    if (!(claim & 1u)) return 0;
    ++gfx_bram_stats[1]; return 1;
#else
    (void)generation; return 0;
#endif
}


/* ---- vertex-cache (0x56/0x54) sticky-state + per-frame wire counters ----
 * g_vc_state_dirty is an ELIGIBILITY-MEMO invalidator ONLY: set by every rapi
 * setter that feeds an eligibility ingredient (shader, texture binding/upload,
 * decal, z-write, alpha, viewport/scissor) and at frame start, cleared when
 * gfx_gpu_vtx_cache_begin re-derives the verdict.  It plays NO part in 0x4A
 * EMISSION coherence any more (round-4 audit): the wire's sticky-state truth
 * is emit_tri_state's shared g_st_cache memo, which BOTH paths build against
 * and update on every emit attempt. */
static int      g_vc_state_dirty = 1;
static int      g_vc_elig;           /* memoized verdict for the current material */
static int      g_vc_textured;       /* memoized: batch samples a real texture */
static int      g_vc_rgb_input;      /* memoized: shade colour input (-1 = white) */
static int      g_vc_alpha_input;    /* memoized: surface-alpha input (-1 = 255) */
static float    g_vc_vp[4];          /* last-programmed 0x50 viewport (cx,cy,hw,hh) */
static int      g_vc_vp_valid;
static uint32_t g_vc_kick;           /* cached tris since the last of_gpu_kick */
static uint32_t g_vc_words;          /* per-frame ring words emitted (draw path) */
static uint32_t g_vc_tris_cached;    /* per-frame tris drawn via 0x54 */
static uint32_t g_vc_tris_legacy;    /* per-frame tris drawn via 0x4E/0x4B */
static uint32_t g_vc_loads;          /* per-frame 0x56 vertex uploads */

/* Pre-scale on the 0x56 slot clip words cx/cy/cw (and, coherently, on the
 * fallback projection in gpu_draw_triangles) — see the rationale at the
 * upload site in gfx_gpu_vtx_cache_tri. */
#define VC_W_PRESCALE (1.0f / 256.0f)

/* Original clip coordinates for cached/fallback projection coherence. */
struct gfx_vc_true_xyw gfx_vc_true[GFX_VC_MAX_BUFFERED];
static int           g_fb_bpp = 1;      /* framebuffer bytes/pixel (1 CI8, 2 RGB565) */
/* 1x1 white RGB565 for untextured truecolor; owns its whole cache line so no
 * CPU-written neighbour's writeback can land on the texel the GPU samples. */
static uint16_t      g_white_tex[32] __attribute__((aligned(64)));
static uint32_t      g_white_addr;

/* viewport (NDC -> screen), set by set_viewport — mirrors gfx_soft */
static float g_cx = SCR_W * 0.5f, g_cy = SCR_H * 0.5f;
static float g_hw = SCR_W * 0.5f, g_hh = SCR_H * 0.5f;
/* Quantized viewport coefficients change only at set_viewport. The clip
 * projection consumes these exact integers for every fallback vertex. */
static int32_t g_clip_cx = SCR_W / 2, g_clip_cy = (SCR_H / 2) * 16;
static int32_t g_clip_hw = SCR_W / 2, g_clip_hh = (SCR_H / 2) * 16;

/* scissor / depth state */
static int16_t g_clip_x0, g_clip_y0, g_clip_x1 = SCR_W, g_clip_y1 = SCR_H;
static int g_z_test, g_z_write;

/* sticky-state dedup: skip re-emitting an identical SET_TRI_STATE. */
static of_gpu_tri_state_t g_st_cache;
static int g_st_cache_valid;
static int g_tri_state_dirty = 1;
static int g_st_textured, g_st_cd, g_st_subpix, g_st_alpha_mask;
static int g_alpha_mask;
static uint8_t g_st_alpha;

/* Every material setter invalidates both eligibility and the shared state
 * memo. Dynamic per-draw fields are checked in emit_tri_state itself, so
 * cached triangles, clipped fallbacks and rectangles can safely alternate. */
static void gpu_material_changed(void) {
    g_vc_state_dirty = 1;
    g_tri_state_dirty = 1;
}


/* ---- Dedicated RGB565 texture storage, with permanent SDRAM fallback ----
 * Every texture keeps its original SDRAM copy. The fetch route is global,
 * so it stays fast for the whole application until allocation exhausts the
 * bounded arena; then one drain switches ALL subsequent draws to SDRAM.
 * Per-generation ownership remains gpu_tex_slot_begin's existing contract. */
static uint32_t g_fast_tex_top, g_fast_tex_size;
static int g_fast_tex_active;

/* Private LOD5 source pool. Immutable ELF Vtx data only. Capacity misses may
 * recycle the complete idle pool, at most once per120 rendered frames after
 * the first recycle. Texture-route fallback preserves this reserved arena. */
#define CRAM_VERTEX_BEGIN 0x00fc0000u
#define CRAM_VERTEX_END   0x01000000u
#define CRAM_VERTEX_ENTRIES 1024u
#define CRAM_VERTEX_PROBES 16u
struct CramVertexSource { uintptr_t source; uint32_t offset_count; };
static struct CramVertexSource g_cram_vertex_source[CRAM_VERTEX_ENTRIES];
static uint32_t g_cram_vertex_top=CRAM_VERTEX_BEGIN;
static uint32_t g_cram_source_generation=1;
uint32_t gfx_gpu_cram_source_generation(void) { return g_cram_source_generation; }
/* Entries bind immutable TRI command bytes to one physical arena generation.
 * Cold uploads only; steady task hits never rescan or repack triangle words. */
struct CramMeshSource { uintptr_t source; uint32_t offset_count; };
static struct CramMeshSource g_cram_mesh_source[512];
static unsigned g_cram_vertex_enabled;
static uint32_t g_cram_vertex_frame,g_cram_vertex_last_recycle;
static unsigned g_cram_vertex_recycled;
/* Recycles and throttled capacity misses; cold-path counters only. */
uint32_t cram_vertex_recycle_stats[2];
static __attribute__((cold,noinline)) int gpu_cram_vertex_recycle(void) {
    if(g_cram_vertex_recycled &&
       (uint32_t)(g_cram_vertex_frame-g_cram_vertex_last_recycle)<120u) {
        cram_vertex_recycle_stats[1]++;return 0;
    }
    gfx_gpu_async_drain();
    gfx_gpu_mesh_wait_source();
    /* The synchronous caller has drained LOAD and copied or abandoned its
     * BRAM result. GPU packets contain values, never source-pool pointers.
     * No renderer/interrupt concurrently uses this CPU-only descriptor table;
     * clear every old key before any later upload can reuse its storage. */
    memset(g_cram_vertex_source,0,sizeof(g_cram_vertex_source));
    memset(g_cram_mesh_source,0,sizeof(g_cram_mesh_source));
    ++g_cram_source_generation;
    /* Invalidate parallel task-offset hints on every real recycle, including
     * generation wrap. This does not discard immutable topology/repair hints. */
    gfx_async_forget_sources();
    g_cram_vertex_top=CRAM_VERTEX_BEGIN;
    g_cram_vertex_last_recycle=g_cram_vertex_frame;
    g_cram_vertex_recycled=1;cram_vertex_recycle_stats[0]++;
    return 1;
}
/* calls,hits,uploads,uploaded words,capacity failures,probe failures,probes,
 * unavailable. These include the real client work in every measured build. */
uint32_t cram_vertex_stats[8];
int gfx_gpu_cram_vertex_source(const void *source,unsigned count,uint32_t *offset) {
    cram_vertex_stats[0]++;
    if(!g_cram_vertex_enabled||!count||count>32||((uintptr_t)source&3)) {
        cram_vertex_stats[7]++;return 0;
    }
    const uintptr_t p=(uintptr_t)source;
    const unsigned h=((p>>4)^(p>>14)^(count*33u))&(CRAM_VERTEX_ENTRIES-1);
retry_after_recycle:
    for(unsigned probe=0;probe<CRAM_VERTEX_PROBES;probe++) {
        struct CramVertexSource *e=&g_cram_vertex_source[(h+probe)&(CRAM_VERTEX_ENTRIES-1)];
        cram_vertex_stats[6]++;
        if(e->source==p&&(e->offset_count>>24)==count) {
            *offset=e->offset_count&0xffffffu;cram_vertex_stats[1]++;return 1;
        }
        if(!e->source) {
            const unsigned bytes=count*24u,allocation=(bytes+63u)&~63u;
            if(allocation>CRAM_VERTEX_END-g_cram_vertex_top) {
                if(gpu_cram_vertex_recycle())goto retry_after_recycle;
                cram_vertex_stats[4]++;return 0;
            }
            /* Caller has drained any prior LOAD. Upload helper drains the final
             * word before publication of this immutable descriptor or START. */
            _of_gpu_fast_tex_upload(g_cram_vertex_top,(const uint32_t *)source,count*6u);
            e->offset_count=g_cram_vertex_top|(count<<24);
            e->source=p;
            *offset=g_cram_vertex_top;g_cram_vertex_top+=allocation;
            cram_vertex_stats[2]++;cram_vertex_stats[3]+=count*6u;return 1;
        }
    }
    cram_vertex_stats[5]++;return 0;
}

int gfx_gpu_mesh_sources(const void *vertices,unsigned count,const void *commands,
                         unsigned command_count,uint32_t *vertex_offset,
                         uint32_t *command_offset) {
    if(!command_count||command_count>64||((uintptr_t)commands&3))return 0;
    gfx_gpu_mesh_wait_source();
    for(unsigned attempt=0;attempt<3;++attempt) {
        if(!gfx_gpu_cram_vertex_source(vertices,count,vertex_offset))return 0;
        const uint32_t generation=g_cram_source_generation;
        const uintptr_t p=(uintptr_t)commands;
        const unsigned h=((p>>3)^(p>>13)^(command_count*33u))&511u;
        unsigned probe;
        for(probe=0;probe<16;++probe) {
            struct CramMeshSource *e=&g_cram_mesh_source[(h+probe)&511u];
            if(e->source==p&&(e->offset_count>>24)==command_count) {
                *command_offset=e->offset_count&0xffffffu;return 1;
            }
            if(!e->source) {
                const unsigned allocation=(command_count*8u+63u)&~63u;
                if(allocation>CRAM_VERTEX_END-g_cram_vertex_top) {
                    if(!gpu_cram_vertex_recycle())return 0;
                    break;
                }
                _of_gpu_fast_tex_upload(g_cram_vertex_top,
                                        (const uint32_t *)commands,command_count*2u);
                e->offset_count=g_cram_vertex_top|(command_count<<24);
                e->source=p;
                *command_offset=g_cram_vertex_top;g_cram_vertex_top+=allocation;
                return 1;
            }
        }
        if(generation==g_cram_source_generation)return 0;
        /* Command allocation recycled the arena after resolving the vertex
         * source. Resolve BOTH again; never return that stale vertex offset. */
    }
    return 0;
}

/* The descriptors contain physical pool offsets, so an allocation recycle
 * invalidates the WHOLE vector. The caller retries every member instead of
 * uploading stale earlier offsets. The upload drains before publication. */
int gfx_gpu_async_group_upload(const uint32_t *descriptors,unsigned members,
                              uint32_t generation,uint32_t *offset) {
    if(!members||members>32||generation!=g_cram_source_generation)return 0;
    const unsigned bytes=members*24u,allocation=(bytes+63u)&~63u;
    if(allocation>CRAM_VERTEX_END-g_cram_vertex_top) {
        (void)gpu_cram_vertex_recycle();return 0;
    }
    _of_gpu_fast_tex_upload(g_cram_vertex_top,descriptors,members*6u);
    *offset=g_cram_vertex_top;g_cram_vertex_top+=allocation;
    return 1;
}


static __attribute__((cold, noinline)) void gpu_fast_tex_fallback(void) {
    if (!g_fast_tex_active) return;
    /* This can run inside a lazy alpha-mask creation. Already emitted draws
     * must retire; remaining CPU-side triangles select SDRAM at emission. */
    of_gpu_finish();
    _of_gpu_route_fast_tex(0);
    g_fast_tex_active = 0;
    g_white_addr = (uint32_t)(uintptr_t)g_white_tex;
    g_st_cache_valid = 0;
    gpu_material_changed();
}

static __attribute__((cold, noinline)) int gpu_fast_tex_upload(uint32_t *address, uint32_t *capacity,
                               const void *source, uint32_t bytes) {
    if (!g_fast_tex_active) return 0;
    if (bytes == 0 || bytes > UINT32_MAX - 15u) {
        gpu_fast_tex_fallback();
        return 0;
    }
    const uint32_t rounded = (bytes + 15u) & ~15u;
    if (*capacity < rounded) {
        if (rounded > g_fast_tex_size - g_fast_tex_top) {
            gpu_fast_tex_fallback();
            return 0;
        }
        *address = g_fast_tex_top;
        *capacity = rounded;
        g_fast_tex_top += rounded;
    }
    /* CPU texel conversion writes the uncached SDRAM alias. Its cached alias
     * may still hold old clean data, so upload MUST read the uncached copy.
     * Slot allocation owns64-byte padding; ceil(bytes/4) is inside that object.
     * The polled upload drains its last word before any new draw can use it.
     * Disjoint live textures may continue rendering during this upload. */
    _of_gpu_fast_tex_upload(*address,
        (const uint32_t *)of_uncached(source), (bytes + 3u) >> 2);
    return 1;
}

static __attribute__((cold, noinline)) void gpu_fast_tex_init(const struct of_capabilities *caps) {
    g_fast_tex_active = 0;
    g_fast_tex_top = g_fast_tex_size = 0;
#ifndef OF_PC
    if (!g_truecolor || !caps || caps->version < 3 ||
        !(caps->hw_features & OF_HW_GPU_FAST_TEX) ||
        caps->tex_fast_size < sizeof(g_white_tex) ||
        caps->tex_fast_size > 0x01000000u)
        return;
    g_fast_tex_size = caps->tex_fast_size & ~15u;
    /* Reserve the source arena before even the permanent white texture.
     * A prior application LOAD survives CPU reset, so drain it before any
     * upload can overwrite reserved CRAM. Do not reset the per-app cache. */
    if(caps->tex_fast_size==CRAM_VERTEX_END &&
       *(volatile uint32_t *)0x4b000000u==0x4c4f4405u) {
        while(*(volatile uint32_t *)0x4b000004u&1u) { }
        g_fast_tex_size=CRAM_VERTEX_BEGIN;
        g_cram_vertex_enabled=1;
    }
    g_fast_tex_active = 1;
    uint32_t address = 0, capacity = 0;
    /* Boot has no queued draw; upload the permanent white texel before routing
     * untextured triangles and colored rectangles to the dedicated chip. */
    if (gpu_fast_tex_upload(&address, &capacity, g_white_tex, sizeof(g_white_tex))) {
        g_white_addr = address;
        _of_gpu_route_fast_tex(1);
        g_st_cache_valid = 0;
        gpu_material_changed();
    }
#else
    (void)caps;
#endif
}

static uint32_t gpu_tex_active_address(const struct GTex *t, int alpha_mask) {
    if (g_fast_tex_active)
        return alpha_mask ? t->fast_mask_addr[t->cur] : t->fast_addr[t->cur];
    return alpha_mask ? (uint32_t)(uintptr_t)t->alpha_mask[t->cur] : t->addr;
}

static void gpu_tex_clear_epochs(void) {
    /* g_tex_count is an ID sequence, not an array bound, and may wrap. */
    for (uint32_t i = 0; i < MAX_TEX; i++)
        g_tex[i].slot_epoch[0] = g_tex[i].slot_epoch[1] = 0;
}
/* ---- End resident-texture helpers ---- */

/* ================================================================
 * Boot: palette, colormap, ramp, z-buffer, first draw buffer
 * ================================================================ */

static void build_palette(void) {
    for (int i = 0; i < 256; i++) {
        int r, g, b;
        rgb332_expand((uint8_t)i, &r, &g, &b);
        of_video_palette((uint8_t)i, ((uint32_t)r << 16) | ((uint32_t)g << 8) | b);
    }
}

/* row 0 = brightest (identity), row 63 = darkest. */
static void build_colormap(void) {
    for (int row = 0; row < CMAP_ROWS; row++) {
        float s = (float)(CMAP_ROWS - 1 - row) / (float)(CMAP_ROWS - 1);
        for (int c = 0; c < 256; c++) {
            int r, g, b;
            rgb332_expand((uint8_t)c, &r, &g, &b);
            g_colormap[row * 256 + c] =
                rgb332((int)(r * s), (int)(g * s), (int)(b * s));
        }
    }
}

void gfx_gpu_boot(void) {
    const struct of_capabilities *caps = of_get_caps();
    g_has_gpu = caps && caps->gpu_base &&
                (caps->hw_features & OF_HW_GPU_SPAN) &&
                of_has_feature(OF_HW_GPU_VERT_TRI);
    /* Truecolor (RGB565 direct color) when the os30 GPU advertises it. */
    g_truecolor = g_has_gpu && of_has_feature(OF_HW_GPU_VCOLOR);
    /* Full texel*C+D combiner (HILITE/specular).  The lean os30 (EXCLUDE_COMBINE)
     * does NOT advertise it -> g_combine 0 -> classify_combine_cd is never engaged
     * (cd_on stays 0) so HILITE surfaces emit plain RGB565 texel*shade instead of
     * the biased-C/D payload the gated GPU would mis-read. */
    g_combine = g_truecolor && of_has_feature(OF_HW_GPU_COMBINE);
    /* Vertex-cache fast path: 0x56 LOAD_VERT_CLIP + 0x54 DRAW_INDEXED_TRI.
     * Caps-only here (per-batch eligibility additionally requires truecolor,
     * which can still be downgraded below if the RGB565 mode is rejected). */
    g_clip_load = GFX_GPU_VTX_CACHE && g_has_gpu &&
                  of_has_feature(OF_HW_GPU_VERT_TRI) &&
                  of_has_feature(OF_HW_GPU_CLIP_LOAD);
#ifndef OF_PC
    /* One-shot capability dump.  XFORM decides whether the GPU transform
     * front-end is reachable: 0x50 (sticky matrix) + 0x53 LOAD_VERTS (transform
     * one vert into a 32-slot GPU vertex cache) + 0x54 DRAW_INDEXED_TRI (ONE
     * word per triangle).  SM64's display lists are literally "G_VTX loads
     * 16-32 verts, then G_TRI1/G_TRI2 index them", so that path maps 1:1 and
     * would cut the per-triangle wire cost from 20 words to ~2 — which attacks
     * the ring-full backpressure that dominates `emit` — and stop re-projecting
     * every shared vertex once per triangle that uses it.
     *
     * of_gpu.h has advertised APIs the shipped os30 bitstream does not
     * implement before (CHANUTIL), so this is a claim to verify, not a promise:
     * read XFORM below on real hardware BEFORE building anything on it. */
    { extern int printf(const char *fmt, ...);
      /* cpu_freq_hz also settles the PERF scale: gfx_pc.c divides PROF ticks by
       * 110000 to get ms.  If the CPU (and so the 0x40000004 counter) runs at
       * 100 MHz, every printed ms is ~10% LOW and every fps ~10% HIGH. */
      printf("[SM64] gpu caps=%08x cpu=%uHz  truecolor=%d combine=%d XFORM=%d "
             "TRI_RECS=%d FAST_TEX=%d CLIP_LOAD=%d\n",
             (unsigned)(caps ? caps->hw_features : 0u),
             (unsigned)(caps ? caps->cpu_freq_hz : 0u), g_truecolor, g_combine,
             of_has_feature(OF_HW_GPU_XFORM_RGB) ? 1 : 0,
             of_has_feature(OF_HW_GPU_PARAM_TRI_RECS) ? 1 : 0,
             of_has_feature(OF_HW_GPU_FAST_TEX) ? 1 : 0, g_clip_load); }
#endif
    /* Always set the RGB332 palette: harmless under RGB565 scanout (ignored),
     * and the safety net if the RGB565 mode switch is rejected below. */
    build_palette();

    if (g_has_gpu) {
        of_gpu_init();
        /* The route register survives GPU soft reset. Start in SDRAM
         * even if a prior application left the fast domain selected. */
        of_gpu_finish();
        _of_gpu_route_fast_tex(0);
        /* Supported cores accept cached command batches directly into BRAM. */
        of_gpu_use_cpu_ring();
#ifndef OF_PC
        /* GPU_STATUS uses the base address installed by of_gpu_init(). */
        g_geometry = GFX_GPU_GEOMETRY_WORKER_APP && GFX_GPU_GEOMETRY
            && g_clip_load && ((GPU_STATUS & (1u<<10)) != 0);
        printf("[SM64] geometry accelerator=%d\n", g_geometry);
#endif

        if (g_truecolor) {
            /* Direct color needs the scanout in RGB565.  Request it AND verify
             * it took effect — if the kernel rejects it (older os.bin), fall
             * back to the palettized path instead of writing 16-bit pixels into
             * an 8-bit framebuffer (which would render as black/garbage). */
            of_video_mode_t want, norm, cur;
            memset(&want, 0, sizeof(want));
            want.width = SCR_W; want.height = SCR_H;
            want.color_mode = OF_VIDEO_MODE_RGB565;
            if (of_video_check_mode(&want, &norm) == 0)
                of_video_set_mode(&norm);
            of_video_get_mode(&cur);
            if (cur.color_mode == OF_VIDEO_MODE_RGB565) {
                g_fb_bpp = 2;
                g_white_tex[0] = 0xFFFF; /* 1x1 white texel for untextured tris */
                /* The GPU DMA-fetches this texel straight from SDRAM.  A ONE-TIME
                 * cbo.flush at init is writeback-timing-fragile (cache.c documents
                 * that cbo.flush does not wait for the d_axi writeback to reach DRAM;
                 * real textures survive only because they are re-flushed on every
                 * gameplay upload).  When the init writeback didn't land, the GPU read
                 * 0x0000 -> rgb565_gouraud(black, shade) = a BLACK untextured-truecolor
                 * surface: the SM64 Goddard head's MAIN FACE (G_CC_SHADE) rendered
                 * black while the textured shine (G_CC_HILITERGBA) still drew its sheen
                 * = "almost black head with some sheen".  Write the texel through the
                 * UNCACHED SDRAM alias so DRAM is guaranteed white before the first
                 * fetch — the reliable path cache.c prescribes for HW-read buffers. */
                *(volatile uint16_t *)of_uncached(g_white_tex) = 0xFFFF;
                of_cache_flush_range(g_white_tex, sizeof(g_white_tex));
                g_white_addr = (uint32_t)(uintptr_t)g_white_tex;
                gpu_fast_tex_init(caps);
            } else {
                g_truecolor = 0;        /* RGB565 unavailable → palettized */
            }
        }

        if (!g_truecolor) {
            build_colormap();
            of_gpu_palookup_upload(0, g_colormap, sizeof(g_colormap));

            /* identity ramp: texel[i] == i, so an untextured tri can carry a
             * per-vertex RGB332 colour index through its s coordinate. */
            g_ramp = malloc(RAMP_W);
            for (int i = 0; i < RAMP_W; i++) g_ramp[i] = (uint8_t)i;
            of_cache_flush_range(g_ramp, RAMP_W);
            g_ramp_addr = (uint32_t)(uintptr_t)g_ramp;
        }

        g_zbuf = malloc((size_t)SCR_W * SCR_H * ZBUF_ELEM);
        memset(g_zbuf, 0, (size_t)SCR_W * SCR_H * ZBUF_ELEM);
        of_cache_flush_range(g_zbuf, (uint32_t)((size_t)SCR_W * SCR_H * ZBUF_ELEM));

        g_draw_idx = of_video_acquire_next(-1, 0);
        g_draw_fb = (uint32_t)(uintptr_t)of_video_buffer_addr(g_draw_idx);
    }
}

void gfx_gpu_present(void) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    if (!g_has_gpu) {
        of_video_flip();
        return;
    }
    /* Submit the flip and return.  The wait for the next free draw buffer is
     * deferred to gpu_start_frame() so the CPU can do useful work during it --
     * see the note there. */
    PROF_FLIP_BEGIN();
    g_flip_token = of_gpu_flip_to(g_draw_idx);
    of_gpu_kick();
    g_flip_pending = 1;
    PROF_FLIP_END();
}

/* ================================================================
 * Shaders
 * ================================================================ */

static int shader_stride(const struct CCFeatures *cc) {
    int s = 4;                                  /* x,y,z,1/w */
    if (cc->used_textures[0]) s += 2;           /* u,v */
    if (cc->opt_fog) s += 1;                    /* fog intensity */
    s += cc->num_inputs * (cc->opt_alpha ? 4 : 3);
    return s;
}

static struct ShaderProgram *gpu_create_and_load_new_shader(uint32_t shader_id) {
    /* Combiners retain shader pointers. Recycling a live slot changes its
     * stride underneath buffered vertices. Keep the usual shaders inline
     * and allocate stable overflow entries only when that pool fills. */
    struct ShaderProgram *p;
    const int overflow = g_shader_count == MAX_SHADERS;
    if (overflow) {
        p = malloc(sizeof(*p));
        if (!p) abort();
    } else {
        p = &g_shaders[g_shader_count++];
    }
    memset(p, 0, sizeof(*p));
    if (overflow) {
        p->next = g_extra_shaders;
        g_extra_shaders = p;
    }
    p->shader_id = shader_id;
    gfx_cc_get_features(shader_id, &p->cc);
    p->stride = shader_stride(&p->cc);
    p->coff = 4 + (p->cc.used_textures[0] ? 2 : 0) + (p->cc.opt_fog ? 1 : 0);
    p->used = 1;
    g_cur_shader = p;
    gpu_material_changed();
    return p;
}

static struct ShaderProgram *gpu_lookup_shader(uint32_t shader_id) {
    for (int i = 0; i < g_shader_count && i < MAX_SHADERS; i++)
        if (g_shaders[i].used && g_shaders[i].shader_id == shader_id)
            return &g_shaders[i];
    for (struct ShaderProgram *p = g_extra_shaders; p; p = p->next)
        if (p->shader_id == shader_id)
            return p;
    return NULL;
}

static void gpu_load_shader(struct ShaderProgram *prg)  { g_cur_shader = prg; gpu_material_changed(); }
static void gpu_unload_shader(struct ShaderProgram *old) { (void)old; g_cur_shader = NULL; gpu_material_changed(); }

static void gpu_shader_get_info(struct ShaderProgram *prg, uint8_t *num_inputs,
                                bool used_textures[2]) {
    *num_inputs = (uint8_t)prg->cc.num_inputs;
    used_textures[0] = prg->cc.used_textures[0];
    used_textures[1] = prg->cc.used_textures[1];
}

static bool gpu_z_is_from_0_to_1(void) { return true; }

/* ================================================================
 * Textures (RGBA32 -> CI8)
 * ================================================================ */

static uint32_t gpu_new_texture(void) {
    /* Recycle ids modulo the pool instead of saturating.  gfx_pc calls
     * new_texture() on EVERY import — and on pool overflow (512 nodes) it
     * wraps pool_pos, implicitly invalidating every id it ever handed out —
     * so ids are minted unboundedly.  The old `id = MAX_TEX-1` saturation
     * aliased ALL imports after the 4096th into ONE mutually-overwriting
     * slot: the "same texture repeated across character parts" artifact
     * (world textures import early and keep unique slots; churny character
     * part-textures live in the re-import storm and all landed on slot 4095).
     * Modulo reuse is safe: at most 512 ids are live at once (the gfx_pc
     * pool), so a recycled id is >= 8 pool-generations dead; in-flight GPU
     * reads of a recycled slot are covered by the per-slot double buffer
     * (the next upload writes the OTHER half). */
    uint32_t id = g_tex_count++ % MAX_TEX;
    return id;
}

static void gpu_select_texture(int tile, uint32_t texture_id) {
    if (texture_id >= MAX_TEX) texture_id = MAX_TEX - 1;
    g_cur_tmu = tile & 1;
    g_cur_tex[g_cur_tmu] = &g_tex[texture_id];
    gpu_material_changed();
}

/* One RGBA32 source pixel -> GPU texel.  a<0x80 -> 0 transparent (SKIP_ZERO);
 * in 565 an opaque true-black texel is nudged to 0x0001 so it can't collide
 * with the transparent key. */
static inline uint16_t tex565_texel(const uint8_t *rgba32, uint32_t i) {
    uint8_t r = rgba32[i * 4 + 0], g = rgba32[i * 4 + 1];
    uint8_t b = rgba32[i * 4 + 2], a = rgba32[i * 4 + 3];
    uint16_t c = (a < 0x80) ? 0x0000 : rgba_to_565(r, g, b);
    if (a >= 0x80 && c == 0x0000) c = 0x0001;
    return c;
}

static inline uint8_t tex332_texel(const uint8_t *rgba32, uint32_t i) {
    uint8_t r = rgba32[i * 4 + 0], g = rgba32[i * 4 + 1];
    uint8_t b = rgba32[i * 4 + 2], a = rgba32[i * 4 + 3];
    return (a < 0x80) ? 0 : rgb332(r, g, b);
}

/* One N64 RGBA16 texel (RGB5551, big-endian) -> RGB565, direct.
 * Identical output to expanding through RGBA8888 and calling tex565_texel:
 * SCALE_5_8 followed by >>3 is the identity on a 5-bit field, so r5 and b5 land
 * unchanged and only green widens 5->6.  a=0 is the transparent key (SKIP_ZERO);
 * an opaque texel that lands on 0x0000 is nudged to 0x0001 so it can't collide
 * with that key.  Verified equal for all 65536 inputs. */
static inline uint32_t tex565_from_rgba16(const uint8_t *p) {
    uint32_t col16 = ((uint32_t)p[0] << 8) | p[1];
    if (!(col16 & 1u)) return 0u;                       /* transparent */
    uint32_t g6 = (uint32_t)(SCALE_5_8_GPU((col16 >> 6) & 0x1fu) >> 2);
    uint32_t c  = (col16 & 0xF800u) | (g6 << 5) | ((col16 >> 1) & 0x1fu);
    return c ? c : 0x0001u;
}

/* Texture storage owns complete cache lines. A cached read after flushing
 * drains writeback on the same AXI master before uncached pixel stores begin.
 * Reused storage is only accessed through its uncached alias. */
static uint8_t *gpu_tex_alloc(uint32_t bytes) {
    uint32_t capacity = (bytes + 63u) & ~63u;
    void *storage = NULL;
    if (posix_memalign(&storage, 64, capacity) != 0) return NULL;
    uint8_t *p = storage;
    of_cache_flush_range(p, capacity);
    for (uint32_t i = 0; i < capacity; i += 64)
        (void)*(volatile uint8_t *)(p + i);
    return p;
}

static int gpu_tex_slot_begin(struct GTex *t, uint32_t bytes) {
    if (bytes == 0 || bytes > UINT32_MAX - 63u) return 0;
    uint8_t slotc = t->cur ^ 1u;
    /* Two buffers cover the usual upload pattern. A third use in the same
     * frame must wait before overwriting pixels still referenced by commands.
     * Earlier frames have retired: gpu_start_frame waits for their flip. */
    if (t->slot_epoch[slotc] == g_tex_epoch) {
        {
            SM64_PERF_SCOPE(SM64_PERF_SYNC);
            of_gpu_finish();
        }
        gpu_tex_clear_epochs();
        t->slot_epoch[slotc] = 0;
    }
    if (t->slot[slotc] == NULL || t->slotcap[slotc] < bytes) {
        uint8_t *fresh = gpu_tex_alloc(bytes);
        if (!fresh) return 0;
        free(t->slot[slotc]);
        t->slot[slotc] = fresh;
        t->slotcap[slotc] = (bytes + 63u) & ~63u;
    }
    free(t->alpha_mask[slotc]);
    t->alpha_mask[slotc] = NULL;
    t->cur = slotc;
    t->ci8 = t->slot[slotc];
    return 1;
}

/* Uploads change SDRAM behind the GPU's private texture cache. The flush
 * request drains outstanding cache responses before invalidating its tags. */
static void gpu_tex_publish(void) {
#ifndef OF_PC
    __asm__ volatile("fence" ::: "memory");
#endif
    GPU_TEX_FLUSH = 1;
}

/* BLENDRGBFADEA selects SHADE where texel alpha is zero and TEXEL where it
 * is one. Disjoint masks retain that behavior even during a surface fade:
 * each covered pixel blends exactly once with the framebuffer. */
static int gpu_tex_alpha_mask(struct GTex *t) {
    if (!t || !t->ci8) return 0;
    if (t->alpha_mask[t->cur]) return 1;
    uint32_t n = (uint32_t)t->w * t->h;
    uint8_t *p = gpu_tex_alloc(n * 2u);
    if (!p) return 0;
    const volatile uint16_t *src = of_uncached(t->ci8);
    volatile uint16_t *dst = of_uncached(p);
    for (uint32_t i = 0; i < n; i++) dst[i] = src[i] == 0 ? 0xffff : 0;
    t->alpha_mask[t->cur] = p;
    gpu_fast_tex_upload(&t->fast_mask_addr[t->cur],
                        &t->fast_mask_capacity[t->cur], p, n * 2u);
    gpu_tex_publish();
    return 1;
}

/* Publish the freshly written slot: dims, wrap masks, GPU-visible address.
 * Power-of-two dims use the GPU's bitmask wrap; non-power-of-two dims set a
 * no-op 0xFFFF mask and engage the GPU's real clamp unit (in emit_tri_state)
 * instead.  The bitmask `s & (width-1)` corrupts non-pow2 widths (e.g. the
 * 80x20 title/ending/game-over backgrounds: s & 0x4F drops bits 16/32),
 * collapsing the image into a garbled band — the "texture zoomed/cropped inside
 * the rect" symptom.  SM64's non-pow2 textures are all G_TX_CLAMP.
 * No trailing cache flush: texels went to DRAM via the uncached alias, and
 * flushing here would only re-write-back stale (clean) lines. */
static void gpu_tex_slot_finish(struct GTex *t, int width, int height) {
    t->w = (uint16_t)width;
    t->h = (uint16_t)height;
    t->wmask = ((width  & (width  - 1)) == 0) ? (uint16_t)(width  - 1) : 0xFFFF;
    t->hmask = ((height & (height - 1)) == 0) ? (uint16_t)(height - 1) : 0xFFFF;
    t->addr = (uint32_t)(uintptr_t)t->ci8;
    gpu_fast_tex_upload(&t->fast_addr[t->cur], &t->fast_capacity[t->cur],
                        t->ci8, (uint32_t)width * (uint32_t)height * g_fb_bpp);
    gpu_tex_publish();
    gpu_material_changed();    /* new tex_addr must reach the sticky 0x4A */
}

static void gpu_upload_texture(const uint8_t *rgba32, int width, int height) {
    struct GTex *t = g_cur_tex[g_cur_tmu];
    uint32_t n = (uint32_t)width * (uint32_t)height;
    uint32_t bytes = n * (uint32_t)g_fb_bpp;   /* 1 byte CI8 / 2 bytes RGB565 */
    if (n == 0) return;
    if (!gpu_tex_slot_begin(t, bytes)) return;
    if (g_truecolor) {
        volatile uint32_t *dw = (volatile uint32_t *)of_uncached(t->ci8);
        uint32_t i = 0;
        for (; i + 1 < n; i += 2) {
            uint16_t c0 = tex565_texel(rgba32, i);
            uint16_t c1 = tex565_texel(rgba32, i + 1);
            dw[i >> 1] = (uint32_t)c0 | ((uint32_t)c1 << 16);
        }
        if (i < n)
            ((volatile uint16_t *)dw)[i] = tex565_texel(rgba32, i);
    } else {
        volatile uint32_t *dw = (volatile uint32_t *)of_uncached(t->ci8);
        uint32_t i = 0;
        for (; i + 3 < n; i += 4) {
            dw[i >> 2] = (uint32_t)tex332_texel(rgba32, i)
                       | ((uint32_t)tex332_texel(rgba32, i + 1) << 8)
                       | ((uint32_t)tex332_texel(rgba32, i + 2) << 16)
                       | ((uint32_t)tex332_texel(rgba32, i + 3) << 24);
        }
        for (; i < n; i++)
            ((volatile uint8_t *)dw)[i] = tex332_texel(rgba32, i);
    }
    gpu_tex_slot_finish(t, width, height);
}

/* ── RGBA16 direct upload (fast path) ─────────────────────────────────────
 * The generic upload_texture contract is RGBA8888, so an N64 RGBA16 texture used
 * to be expanded to RGBA8888 in a stack buffer by gfx_pc.c's import_texture_rgba16
 * and immediately squeezed back down to RGB565 here: ~10 bytes of memory traffic
 * per texel (2 read + 4 written + 4 read back + 2 written) to move 2 bytes, plus
 * a stack buffer big enough to evict the whole D-cache.  Truecolor is already
 * 16-bit, so convert in ONE pass straight from the ROM texels.
 *
 * BIT-IDENTICAL to the old two-stage chain, brute-force verified over all 65536
 * RGBA16 inputs: r5 and b5 pass straight through (SCALE_5_8 then >>3 is the
 * identity on 5 bits), only green needs the 5->6 widening, and the alpha bit
 * still keys transparency with the same 0x0000 -> 0x0001 nudge for opaque black.
 *
 * Returns 0 (caller falls back to upload_texture) when the framebuffer is not
 * 16-bit, i.e. the palettized path, whose texel is a CI8 palette index. */
static int gpu_upload_texture_rgba16(const uint8_t *src, int width, int height) {
    if (!g_truecolor)
        return 0;
    struct GTex *t = g_cur_tex[g_cur_tmu];
    uint32_t n = (uint32_t)width * (uint32_t)height;
    if (n == 0) return 1;
    if (!gpu_tex_slot_begin(t, n * 2u))
        return 1;

    volatile uint32_t *dw = (volatile uint32_t *)of_uncached(t->ci8);
    uint32_t i = 0;
    /* Two texels per uncached 32-bit store: each store is a separate transaction
     * on a single-outstanding bus, so halving their count matters more than the
     * arithmetic does. */
    for (; i + 1 < n; i += 2) {
        uint32_t c0 = tex565_from_rgba16(src + i * 2);
        uint32_t c1 = tex565_from_rgba16(src + i * 2 + 2);
        dw[i >> 1] = c0 | (c1 << 16);
    }
    if (i < n)
        ((volatile uint16_t *)dw)[i] = (uint16_t)tex565_from_rgba16(src + i * 2);

    gpu_tex_slot_finish(t, width, height);
    return 1;
}

static void gpu_set_sampler_parameters(int tile, bool linear, uint32_t cms,
                                       uint32_t cmt) {
    (void)linear;   /* nearest sampling only (no bilinear) */
    /* Capture the N64 wrap mode so emit_tri_state can drive the GPU's clamp
     * unit (non-pow2 / G_TX_CLAMP) and mirror addressing (G_TX_MIRROR). */
    struct GTex *t = g_cur_tex[tile & 1];
    if (t) { t->cms = (uint8_t)cms; t->cmt = (uint8_t)cmt; }
    gpu_material_changed();
}

static void gpu_set_depth_test(bool depth_test) { g_z_test = depth_test; gpu_material_changed(); }
static void gpu_set_depth_mask(bool z_upd)       { g_z_write = z_upd; gpu_material_changed(); }
static int g_decal;
static void gpu_set_zmode_decal(bool decal)      { g_decal = decal; gpu_material_changed(); }
static int g_alpha_cvg;
static void gpu_set_alpha_cvg_sel(bool sel)      { g_alpha_cvg = sel; gpu_material_changed(); }

static void gpu_set_viewport(int x, int y, int width, int height) {
    g_hw = (float)(width >> 1);
    g_hh = (float)(height >> 1);
    g_cx = (float)x + g_hw;
    /* RenderingAPI uses bottom-left Y; the framebuffer uses top-left Y. */
    g_cy = (float)(SCR_H - y - height) + g_hh;
    /* Resolve viewport changes here, instead of four floating comparisons
     * on every cached triangle. Other object-state writers update the same
     * saved viewport and validity; frame start also invalidates it. */
    if (g_vc_vp[0] != g_cx || g_vc_vp[1] != g_cy ||
        g_vc_vp[2] != g_hw || g_vc_vp[3] != g_hh)
        g_vc_vp_valid = 0;
    g_clip_cx = (int32_t)lrintf(g_cx);
    g_clip_cy = (int32_t)lrintf(g_cy * 16.0f);
    g_clip_hw = (int32_t)lrintf(g_hw);
    g_clip_hh = (int32_t)lrintf(g_hh * 16.0f);
    gpu_material_changed();   /* also refreshes the sticky 0x50 (dedup'd below) */
}

static void gpu_set_scissor(int x, int y, int width, int height) {
    y = SCR_H - y - height;
    int x1 = x + width, y1 = y + height;
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x1 > SCR_W) x1 = SCR_W;
    if (y1 > SCR_H) y1 = SCR_H;
    g_clip_x0 = (int16_t)x;  g_clip_y0 = (int16_t)y;
    g_clip_x1 = (int16_t)x1; g_clip_y1 = (int16_t)y1;
    gpu_material_changed();
}

static int     g_use_alpha;
static uint8_t g_surf_alpha = 255;   /* per-surface src alpha for blending */
static void gpu_set_use_alpha(bool use_alpha) { g_use_alpha = use_alpha; gpu_material_changed(); }
static void gpu_set_fog_color(const uint8_t *rgb) { (void)rgb; }

/* ================================================================
 * Triangle emission
 * ================================================================ */

static uint8_t shade_row(float r, float g, float b) {
    int lum = (int)(0.30f * r + 0.59f * g + 0.11f * b);
    if (lum < 0) lum = 0; else if (lum > 255) lum = 255;
    /* (255-lum) >> 2 maps bright->row 0, dark->row 63 with no divide. */
    return (uint8_t)((255 - lum) >> 2);
}

/* Truecolor brightness: luminance >> 2 -> 0..63 (brighter = higher), the
 * sense the RTL's rgb565_modulate expects (texel * (light+1) >> 6). */
static uint8_t brightness_row(float r, float g, float b) {
    int lum = (int)(0.30f * r + 0.59f * g + 0.11f * b);
    if (lum < 0) lum = 0; else if (lum > 255) lum = 255;
    return (uint8_t)(lum >> 2);
}

static int gpu_z_mode(void) {
    if (g_z_test)  return g_z_write ? OF_GPU_PARAM_Z_TEST_WRITE : OF_GPU_PARAM_Z_TEST_ZI;
    if (g_z_write) return OF_GPU_PARAM_Z_WRITE_ZI;
    return OF_GPU_PARAM_Z_NONE;
}

/* ----------------------------------------------------------------------------
 * Full combiner emulation: clamp(texel*C + D).
 *
 * The GPU computes, per pixel, clamp(texel*C + D) with C and D per-vertex RGB.
 * We reduce the RGB color cycle (a-b)*c+d to that canonical form, treating
 * TEXEL0 as the free (per-pixel) variable.  We only engage it for the HILITE
 * class — c == TEXEL0 with a real subtraction or additive term — which the
 * legacy texel*C path (unsigned C, no add) cannot represent.  Everything else
 * (MODULATE/DECAL/SHADE/world geometry) stays on the byte-exact legacy path.
 *
 * Combiner inputs resolve straight out of buf_vbo by INDEX: cc.c[0][*] holds
 * SHADER_INPUT_n tokens, and SHADER_INPUT_n's resolved color already lives at
 * buf_vbo input slot (n-1).  So no PRIM/SHADE/ENV identity (shader_input_mapping)
 * is needed — the equation is symbolic in the input slots.
 *
 * C-field encoding (RTL reads per-vertex RGB565 words 12-14 as BIASED-UNSIGNED,
 * then subtracts the bias before the signed texel*C product):
 *   R/B 5-bit: field = clamp(round(C*32),-16,15) + 16  (0..31, bias 16).
 *   G   6-bit: field = clamp(round(C*64),-32,31) + 32  (0..63, bias 32).
 * Biased so C interpolates monotonically (unsigned) across the triangle; a raw
 * two's-comp field would wrap at C's zero-crossings and facet the head.
 * So |C| saturates near 0.5 (R/B) — the accepted soft-highlight limit for now.
 * D is the unsigned per-vertex color (R/B 0..31, G 0..63) on words 19-21.
 * ---------------------------------------------------------------------------- */

/* Map a combiner token to a buf_vbo color-input index. idx=-1 means constant 0.
 * Returns 0 (ineligible) for TEXEL0/TEXEL1/alpha tokens. */
static int cc_input_idx(uint8_t tok, int *idx) {
    if (tok == SHADER_0) { *idx = -1; return 1; }
    if (tok >= SHADER_INPUT_1 && tok <= SHADER_INPUT_4) { *idx = (int)(tok - SHADER_INPUT_1); return 1; }
    return 0;
}

/* Classify the RGB color cycle.  On the HILITE form (c==TEXEL0) fills the input
 * indices for a/b/d and returns 1; otherwise returns 0 (use legacy). */
static int classify_combine_cd(const struct CCFeatures *cc,
                               int *a_in, int *b_in, int *d_in) {
    uint8_t a = cc->c[0][0], b = cc->c[0][1], c = cc->c[0][2], d = cc->c[0][3];
    int ai, bi, di;
    if (c != SHADER_TEXEL0) return 0;                 /* need TEXEL0 as multiplicand */
    if (!cc_input_idx(a, &ai)) return 0;
    if (!cc_input_idx(b, &bi)) return 0;
    if (!cc_input_idx(d, &di)) return 0;
    if (b == SHADER_0 && d == SHADER_0) return 0;     /* pure texel*C -> legacy */
    *a_in = ai; *b_in = bi; *d_in = di;
    return 1;
}

/* Resolve a buf_vbo color input (or constant 0) at one vertex into 0..1 floats.
 * f = vertex float base.  Colors arrive UN-premultiplied on TARGET_OPENFPGA
 * (gfx_pc.c's GFX_OUT_PROP is identity there — see the long note on that macro),
 * so there is no 1/w to undo and the old `w` multiplier is gone.
 * GFX_DONT_SCALE_COLORS (TARGET_OPENFPGA) stores colors 0..255, so still divide
 * by 255 — the enc_C/enc_D encoders below expect 0..1 (without this the C and D
 * fields saturate and the HILITE head blows out to white). */
static void cc_val_rgb(const float *f, int coff, int istride, int idx, float out[3]) {
    if (idx < 0) { out[0] = out[1] = out[2] = 0.0f; return; }
    const float s = (1.0f / 255.0f);
    out[0] = f[coff + idx * istride + 0] * s;
    out[1] = f[coff + idx * istride + 1] * s;
    out[2] = f[coff + idx * istride + 2] * s;
}

/* C is BIASED-UNSIGNED (not two's-complement): signed -16..+15 -> field 0..31
 * (bias 16); signed -32..+31 -> field 0..63 (bias 32).  The RTL interpolates the
 * field as unsigned (correct, monotonic) and subtracts the bias before the signed
 * texel*C product.  Two's-comp here would interpolate-as-unsigned and wrap at C's
 * zero-crossings, faceting the Goddard head. */
static inline uint8_t enc_C5(float c) {   /* biased-unsigned 5-bit (bias 16) */
    int v = (int)lrintf(c * 32.0f);
    if (v < -16) v = -16; else if (v > 15) v = 15;
    return (uint8_t)(v + 16);
}
static inline uint8_t enc_C6(float c) {   /* biased-unsigned 6-bit (bias 32) */
    int v = (int)lrintf(c * 64.0f);
    if (v < -32) v = -32; else if (v > 31) v = 31;
    return (uint8_t)(v + 32);
}
static inline uint8_t enc_D5(float d) {   /* unsigned 5-bit 0..31 */
    int v = (int)lrintf(d * 31.0f);
    if (v < 0) v = 0; else if (v > 31) v = 31;
    return (uint8_t)v;
}
static inline uint8_t enc_D6(float d) {   /* unsigned 6-bit 0..63 */
    int v = (int)lrintf(d * 63.0f);
    if (v < 0) v = 0; else if (v > 63) v = 63;
    return (uint8_t)v;
}

/* Does the RGB colour cycle consume a per-vertex colour input?  (See the long
 * texel-only-white note at the use site in gpu_draw_triangles.)  Shared by the
 * 0x4E path and the vertex-cache path so both encode the identical rgb565. */
static inline int cc_rgb_has_input(const struct CCFeatures *cc) {
    return (cc->c[0][0] >= SHADER_INPUT_1 && cc->c[0][0] <= SHADER_INPUT_4) ||
           (cc->c[0][1] >= SHADER_INPUT_1 && cc->c[0][1] <= SHADER_INPUT_4) ||
           (cc->c[0][2] >= SHADER_INPUT_1 && cc->c[0][2] <= SHADER_INPUT_4) ||
           (cc->c[0][3] >= SHADER_INPUT_1 && cc->c[0][3] <= SHADER_INPUT_4);
}

/* RGB and alpha have independent input maps. An alpha-only second input
 * must not replace the first RGB input (for example MODULATERGBFADEA). */
static int cc_cycle_input(const struct CCFeatures *cc, int cycle) {
    for (int k = 3; k >= 0; k--) {
        int v = cc->c[cycle][k];
        if (v >= SHADER_INPUT_1 && v <= SHADER_INPUT_4) return v - SHADER_INPUT_1;
    }
    return -1;
}

/* Binary-alpha interpolation between a texture and one shaded input. */
static inline int cc_blend_texel_alpha(const struct CCFeatures *cc) {
    return cc->c[0][0] == SHADER_TEXEL0 && cc->c[0][2] == SHADER_TEXEL0A &&
           cc->c[0][1] == cc->c[0][3] &&
           (cc->c[0][1] >= SHADER_INPUT_1 && cc->c[0][1] <= SHADER_INPUT_4);
}

static unsigned g_cd_fallbacks;      /* combiners that didn't fit texel*C+D */
static int      g_cd_active;         /* set by gpu_draw_triangles for emit_tri_state */
static int      g_subpix_tri;        /* 1 = 3D vert-tri sends Q12.4 subpixel Y (control bit 31) */

/* Emit the sticky surface state for the current batch. */
static void emit_tri_state(int textured) {
    if (textured && g_cur_tex[0])
        g_cur_tex[0]->slot_epoch[g_cur_tex[0]->cur] = g_tex_epoch;
    if (g_st_cache_valid && !g_tri_state_dirty &&
        g_st_textured == textured && g_st_cd == g_cd_active &&
        g_st_subpix == g_subpix_tri && g_st_alpha == g_surf_alpha &&
        g_st_alpha_mask == g_alpha_mask)
        return;
    g_st_textured = textured;
    g_st_cd = g_cd_active;
    g_st_subpix = g_subpix_tri;
    g_st_alpha_mask = g_alpha_mask;
    g_st_alpha = g_surf_alpha;
    g_tri_state_dirty = 0;
    struct GTex *t = textured ? g_cur_tex[0] : NULL;
    of_gpu_tri_state_t st;
    memset(&st, 0, sizeof(st));
    st.fb_base       = g_draw_fb;
    st.fb_major_step = FB_STRIDE * g_fb_bpp;   /* bytes per scanline */
    st.fb_minor_step = g_fb_bpp;               /* bytes per pixel */
    int skip_zero = g_cur_shader && (g_cur_shader->cc.opt_alpha ||
                                     g_cur_shader->cc.opt_texture_edge ||
                                     cc_blend_texel_alpha(&g_cur_shader->cc));
    /* ALPHA_CVG_SEL: alpha acts as coverage, so a zero-alpha texel is never
     * written on hardware.  None of the three tests above catch it -- the mode
     * rides on OPAQUE surfaces, whose G_BL_A_MEM blender input makes gfx_pc
     * strip the alpha cycle (opt_alpha false), and it is ALPHA_CVG_SEL (0x2000)
     * rather than CVG_X_ALPHA (0x1000), so opt_texture_edge is false too.
     * Without this the transparent background of such a texture gets written as
     * the reserved key value, i.e. black: the intro's 128x16 copyright strip
     * turned into a solid bar the frame its fade counter reached 255 and
     * geo_fade_transition() swapped G_RM_AA_XLU_SURF for G_RM_AA_OPA_SURF.
     * Truecolor only: tex565_texel nudges opaque black to 0x0001 so texel 0
     * means transparent and nothing else, whereas RGB332 has no reserved key
     * (rgb332(black) == 0) and widening the discard there would punch holes. */
    if (g_truecolor && g_alpha_cvg) skip_zero = 1;
    if (g_truecolor) {
        if (textured && t && t->ci8) {
            st.tex_addr  = gpu_tex_active_address(t, g_alpha_mask);
            st.tex_width = t->w;
            st.tex_w_mask = t->wmask;
            st.tex_h_mask = t->hmask;
        } else {
            st.tex_addr  = g_white_addr;   /* 1x1 white RGB565 */
            st.tex_width = 1;
            st.tex_w_mask = 0;
            st.tex_h_mask = 0;
        }
        st.flags = OF_GPU_SPAN_TRUECOLOR;
        if (skip_zero) st.flags |= OF_GPU_SPAN_SKIP_ZERO;
        st.colormap_id = 0;
    } else {
        if (textured && t && t->ci8) {
            st.tex_addr  = t->addr;
            st.tex_width = t->w;
            st.tex_w_mask = t->wmask;
            st.tex_h_mask = t->hmask;
        } else {
            st.tex_addr  = g_ramp_addr;
            st.tex_width = RAMP_W;
            st.tex_w_mask = RAMP_W - 1;
            st.tex_h_mask = 0;
        }
        st.flags = OF_GPU_SPAN_COLORMAP;
        if (skip_zero) st.flags |= OF_GPU_SPAN_SKIP_ZERO;
        st.colormap_id = 0;
    }
    /* Non-power-of-two textures: engage the GPU's real clamp unit (clamp_max in
     * Q16.16 texels) so the post-clamp `& mask` (mask = 0xFFFF, no-op) passes
     * the clamped 0..dim-1 index through.  Matches gfx_soft's G_TX_CLAMP. */
    if (textured && t && t->ci8) {
        if (t->wmask == 0xFFFF) st.clamp_max[0] = (uint32_t)(t->w - 1) << 16;
        if (t->hmask == 0xFFFF) st.clamp_max[1] = (uint32_t)(t->h - 1) << 16;
        /* G_TX_CLAMP (bit 1) on POWER-OF-TWO axes: the RDP clamps at the tile
         * edge; the bitmask wrap is only correct for G_TX_WRAP.  Masking a
         * clamped axis sends out-of-range coordinates back to texel row/col 0
         * — SM64's face textures keep the eyes/mouth rows at the top and rely
         * on bottom-edge clamp for the jaw/chin triangles, so wrap paints the
         * eyes and mouth stretched across the chin (Peach intro closeup;
         * present since the vert-tri path shipped — clamp was only wired for
         * the non-pow2 backgrounds above).  Same shape as gfx_soft's per-axis
         * iclamp0w sampling.  dim==1 keeps mask addressing (mask 0 already
         * pins the index) since clamp_max 0 with clamp_min 0 means DISABLED
         * per the of_gpu.h contract.  Mirrored axes keep mirror addressing. */
        if ((t->cms & 0x2) && !(t->cms & 0x1) && t->w > 1) {
            st.tex_w_mask = 0xFFFF;
            st.clamp_max[0] = (uint32_t)(t->w - 1) << 16;
        }
        if ((t->cmt & 0x2) && !(t->cmt & 0x1) && t->h > 1) {
            st.tex_h_mask = 0xFFFF;
            st.clamp_max[1] = (uint32_t)(t->h - 1) << 16;
        }
        /* G_TX_MIRROR (bit 0 of the N64 wrap mode): the GPU reflects the texel
         * index every W texels instead of wrapping — needed for the symmetric
         * star/circle stage transitions and Mario's shadow (quarter texture
         * mirrored to a full shape).  Mirror axes are power-of-two, so they
         * never collide with the non-pow2 clamp above. */
        if (t->cms & 0x1) st.mirror_s = 1;
        if (t->cmt & 0x1) st.mirror_t = 1;
    }

    /* Src-over alpha blend (truecolor): translucent surfaces (water, lava,
     * screen fades, Boos / Mario vanish) blend against the framebuffer by the
     * per-surface constant alpha instead of writing opaque.  SKIP_ZERO stays on
     * so a fully-transparent texel still drops out.  Untextured translucent
     * surfaces (white-texel fades) blend too, hence outside the textured gate. */
    /* Const-alpha src-over blend ONLY for genuinely TRANSLUCENT (XLU) surfaces.
     * Opaque/AA geometry also reports opt_alpha (coverage alpha) and can carry a
     * material alpha < 255, but it writes depth and must stay opaque — blending it
     * sank the title Mario head and the Peach letter into the dark background.
     * XLU surfaces (water, lava, screen fades, Boos) conventionally disable
     * z-write, so gate on !g_z_write to separate them from opaque AA geometry. */
    /* Zero is the transparent endpoint of the same fade. Treating it as
     * opaque flashes overlays on the first and last frames of a fade. */
    if (g_truecolor && g_cur_shader && g_cur_shader->cc.opt_alpha
        && g_surf_alpha < 255 && !g_z_write) {
        st.flags |= OF_GPU_SPAN_BLEND;
        st.const_alpha = g_surf_alpha;
    }

    /* Full combiner emulation (texel*C+D): enabled per-draw by gpu_draw_triangles
     * (g_cd_active) when the combiner is the HILITE class.  Truecolor only. */
    if (g_truecolor && g_cd_active)
        st.cd_combine = 1;

    /* Subpixel-Y (control bit 31): set for the 3D vert-tri path (which sends
     * Q12.4 Y), cleared for 2D rects (pixel-aligned integer Y). */
    st.subpix_y = (uint8_t)g_subpix_tri;

    st.z_mode = (uint8_t)gpu_z_mode();
    if (st.z_mode != OF_GPU_PARAM_Z_NONE) {
        st.z_base = (uint32_t)(uintptr_t)g_zbuf;
        st.z_major_step = SCR_W * ZBUF_ELEM;
        st.z_minor_step = ZBUF_ELEM;
    }
    st.clip_x0 = g_clip_x0; st.clip_x1 = g_clip_x1;
    st.clip_y0 = g_clip_y0; st.clip_y1 = g_clip_y1;

    /* Skip redundant sticky-state commands (st is fully memset, so the
     * padding is zero and the compare is well-defined).
     * SINGLE SOURCE OF TRUTH: g_st_cache mirrors the last 0x4A image put on
     * the wire, whichever path emitted it — the legacy batch path, tex_rect,
     * AND the vertex-cache path all build their image here and memcmp against
     * this one memo, so neither path can ever skip an emission while the
     * GPU's sticky state belongs to the other (the two images always differ
     * when their surface state differs). */
    if (g_st_cache_valid && memcmp(&st, &g_st_cache, sizeof(st)) == 0)
        return;
    g_st_cache = st;
    g_st_cache_valid = 1;
    of_gpu_set_tri_state(&st);
    g_vc_words += 18;                  /* 0x4A: 1 header + 17 payload */
}

/* Per-triangle Q29 zi-precision override (0x4E word 15: {q29_en[5], shift[4:0]}).
 * SM64's large world units leave 1/w with few significant bits on near/far
 * surfaces, so the perspective derive snaps ("texture jump").  A per-tri shift
 * rescales zi into the derive's precision window.  Formula wired once the RTL
 * w15 semantics are confirmed; 0 = legacy precision (no change). */
static inline uint32_t gpu_q29_word(const int32_t zi[3]) {
    (void)zi;
    return 0u;
}

#ifdef TARGET_OPENFPGA
/* Minimal emit-backpressure readout (the only piece of the old GPU profiler kept):
 * ring spin-iters = how long the CPU blocked on a FULL command ring (= GPU
 * fill-rate limit, the thing that inflates "emit"); min_free = closest the ring
 * got to full over the window (0 = saturated).  The _gpu_dbg_* counters are
 * static-in-header, so only this TU (which does the ring writes) sees the live
 * values.  spins is the delta since the last call; min_free is reset here. */
/* Per-frame present split, in PROF ticks; both counters reset on read.
 * flip = CPU cost of submitting the flip; acq = idle blocked in
 * of_video_acquire_next waiting for a free draw buffer. */
void gpu_present_prof_get(unsigned *flip, unsigned *acq) {
#if defined(TARGET_OPENFPGA) && !defined(OF_PC)
    *flip = prof_flip_ticks; *acq = prof_acq_ticks;
    prof_flip_ticks = prof_acq_ticks = 0;
#else
    *flip = 0; *acq = 0;
#endif
}

void gpu_ring_prof_get(unsigned *spins, unsigned *min_free) {
#ifdef OF_PC
    /* The _gpu_dbg_* ring counters live behind #ifndef OF_PC in of_gpu.h, so on
     * the desktop build there is no live ring to sample. */
    *spins = 0;
    *min_free = 0;
#else
    static uint32_t prev;
    *spins = _gpu_dbg_ring_spin_iters - prev;
    prev = _gpu_dbg_ring_spin_iters;
    *min_free = _gpu_dbg_min_ring_free;
    _gpu_dbg_min_ring_free = OF_GPU_RING_SIZE;
#endif
}

#if SM64_PERF_DIAG
/* Cumulative, non-destructive reads; no clock/MMIO accesses. */
void gpu_upload_prof_read(uint32_t *ticks, uint32_t *words,
                         uint32_t *calls, uint32_t *burst) {
#ifndef OF_PC
    *ticks = gpu_upload_prof_ticks;
    *words = gpu_upload_prof_words;
    *calls = gpu_upload_prof_calls;
    *burst = gpu_upload_prof_burst;
#else
    *ticks = *words = *calls = *burst = 0;
#endif
}

void gpu_submit_prof_read(uint32_t *ticks, uint32_t *words,
                          uint32_t *batches, uint32_t *cpu_batches) {
#ifndef OF_PC
    *ticks = gpu_submit_prof_ticks;
    *words = gpu_submit_prof_words;
    *batches = gpu_submit_prof_batches;
    *cpu_batches = gpu_submit_prof_cpu_batches;
#else
    *ticks = *words = *batches = *cpu_batches = 0;
#endif
}

void gpu_wait_prof_read(uint32_t *ring, uint32_t *dma, uint32_t *fence) {
#ifndef OF_PC
    *ring = gpu_wait_prof_ticks[OF_GPU_WAIT_PROFILE_RING];
    *dma = gpu_wait_prof_ticks[OF_GPU_WAIT_PROFILE_DMA];
    *fence = gpu_wait_prof_ticks[OF_GPU_WAIT_PROFILE_FENCE];
#else
    *ring = *dma = *fence = 0;
#endif
}
#endif

#endif

/* ================================================================
 * Vertex-cache fast path (0x56 LOAD_VERT_CLIP + 0x54 DRAW_INDEXED_TRI)
 * ================================================================ */

/* Per-frame wire counters, reset on read (same pattern as gpu_ring_prof_get). */
void gpu_vc_prof_get(unsigned *words, unsigned *ctris, unsigned *ltris,
                     unsigned *loads) {
    *words = g_vc_words;       g_vc_words = 0;
    *ctris = g_vc_tris_cached; g_vc_tris_cached = 0;
    *ltris = g_vc_tris_legacy; g_vc_tris_legacy = 0;
    *loads = g_vc_loads;       g_vc_loads = 0;
}

/* Per-batch eligibility for the cached path, memoized until any material
 * state changes (g_vc_state_dirty).  Nonzero -> GFX_VC_* flags; 0 -> the
 * caller must use the flattened 0x4E path.  Ineligible: no caps bit /
 * kill switch (g_clip_load), palettized (g_truecolor), HILITE texel*C+D
 * class (no rgb_d slot exists in the cache), decal (the per-vertex depth
 * bias cannot ride a shared slot — GPU derives depth from w alone), and
 * blend/XLU (opt_alpha with z-write off — see below). */
int gfx_gpu_vtx_cache_begin(int *rgb_input, int *alpha_input) {
    if (!g_clip_load || !g_truecolor || !g_cur_shader)
        return 0;
    if (!g_vc_state_dirty) {
        *rgb_input = g_vc_rgb_input;
        *alpha_input = g_vc_alpha_input;
        return g_vc_elig;
    }
    const struct CCFeatures *cc = &g_cur_shader->cc;
    const int textured = cc->used_textures[0] && g_cur_tex[0] && g_cur_tex[0]->ci8;
    int cd_a, cd_b, cd_d;
    const int cd_on = textured ? classify_combine_cd(cc, &cd_a, &cd_b, &cd_d) : 0;
    /* Blend/XLU ineligible (hardware A/B: FRAMERATE fix, not correctness).
     * SM64's XLU surfaces are largely decals (already ineligible), so a
     * blend batch ping-pongs cached-immediate vs buffered-fallback tris,
     * and EVERY alternation forces a gfx_flush + a fresh 18-word 0x4A —
     * word amplification that showed up as the transparency fps drop.
     * Gate on the same XLU convention emit_tri_state's blend uses
     * (opt_alpha && !z_write), minus the per-vertex alpha test, so the
     * verdict stays memoizable per material.  Opaque AA geometry
     * (opt_alpha with z-write) stays cached and never blends.
     * TODO(v2): an order-preserving cached-tri queue (defer 0x54s next to
     * the buffered 0x4E stream and interleave at flush) would let XLU ride
     * the cache without the flush ping-pong; v1 punts because transparency
     * is a small fraction of SM64's tris and the queue complicates the
     * exact painter's-order guarantee blending relies on. */
    const int xlu = cc->opt_alpha && !g_z_write;

    g_vc_state_dirty = 0;
    g_vc_textured = textured;
    g_vc_elig = 0;
    g_vc_rgb_input = -1;
    g_vc_alpha_input = -1;
    if (!cd_on && !g_decal && !xlu && !cc_blend_texel_alpha(cc)) {
        g_vc_rgb_input = cc_cycle_input(cc, 0);
        if (cc->opt_alpha) g_vc_alpha_input = cc_cycle_input(cc, 1);
        g_vc_elig = 1 | (textured ? GFX_VC_TEXTURED : 0);
    }
    *rgb_input = g_vc_rgb_input;
    *alpha_input = g_vc_alpha_input;
    return g_vc_elig;
}

/* Draw one triangle from the GPU vertex cache: lazily (re)emit the sticky
 * 0x50 viewport + 0x4A surface state, upload the dirty slots (0x56), then
 * the 1-word 0x54.  Caller (gfx_pc) guarantees eligibility, slots < 32,
 * 0 < w < 32767, and that buffered 0x4E tris were flushed first. */
void gfx_gpu_vtx_cache_tri(const struct gfx_vc_vtx v[3], const uint8_t slot[3],
                           unsigned dirty_mask) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    /* The shared emitter checks material changes and dynamic draw fields
     * before building state. Every path still passes through that one memo. */
    g_cd_active = 0;      /* eligible => never the texel*C+D class */
    g_subpix_tri = 1;     /* viewport Y constants are scaled to Q12.4 */
    g_surf_alpha = v[0].a;
    emit_tri_state(g_vc_textured);

    /* X projection returns Q12.4. Scaling both Y viewport constants by 16
     * gives matching subpixel precision without changing the command ABI. */
    if (!g_vc_vp_valid) {
        of_gpu_object_state_t os;
        memset(&os, 0, sizeof(os));
        os.xcenter   = (int32_t)lrintf(g_cx);
        os.ycenter   = (int32_t)lrintf(g_cy * 16.0f);
        os.xscale    = (int32_t)lrintf(g_hw);
        os.yscale    = (int32_t)lrintf(g_hh * 16.0f);
        os.near_clip = 256;
        os.rows      = 3;
        of_gpu_set_object_state(&os);
        g_geometry_matrix_valid=0;
        g_vc_words += 27;              /* 0x50: 1 header + 26 payload */
        g_vc_vp[0] = g_cx; g_vc_vp[1] = g_cy;
        g_vc_vp[2] = g_hw; g_vc_vp[3] = g_hh;
        g_vc_vp_valid = 1;
    }

#ifndef OF_PC
    /* One reservation preserves the same ordered 0x56 loads and 0x54 draw.
     * Material and viewport commands above have already been staged. */
    /* gfx_gpu_vtx_cache_begin already proves both VERT_TRI and CLIP_LOAD.
     * Its only caller invokes this routine after that eligibility check. */
    const unsigned dirty_count = (dirty_mask & 1u) + ((dirty_mask >> 1) & 1u)
                               + ((dirty_mask >> 2) & 1u);
    const unsigned packet_words = 9u * dirty_count + 2u;
    _gpu_stream_reserve_words(packet_words);
    uint32_t *packet = _gpu_ring_claim();
#endif

    /* w PRE-SCALE (hardware-verified fix for misplaced distant triangles):
     * the GPU's reciprocal is INTEGER — zi = floor(2^32 / w_q16) — and its
     * projection ratio is (x_q16 * zi) >> 16.  At SM64's far w (~20000) raw
     * Q16.16 gives zi = 3, and the zi quantization (stepping 4 -> 3 across a
     * triangle's vertices) shifts each projected vertex DIFFERENTLY: up to
     * 12.19 px of per-vertex error, i.e. the "20% of triangles misplaced"
     * A/B artifact.  Scaling cx, cy, cw ALL by 1/256 before the Q16.16
     * conversion leaves x/w (the projection) mathematically unchanged but
     * moves zi into 838..168k over SM64's w range (100..22000) — monotonic,
     * so z-ordering is preserved, and the worst projection error drops to
     * 0.19 px (RTL-exact integer math, verified).  Since FIX E, the z-buffer
     * depth is an explicit slot word (below) — the prescale only shapes the
     * projection and the perspective-interpolation zi. */
    for (int k = 0; k < 3; k++) {
        if (!(dirty_mask & (1u << k)))
            continue;
        const uint16_t rgb = (g_vc_rgb_input < 0)
                           ? 0xFFFF : rgba_to_565(v[k].r, v[k].g, v[k].b);
        /* FIX E (explicit depth, hardware round 3): the GPU-derived slot depth
         * (= zi) flattened far-field z resolution ~64x vs the legacy 2^30
         * scale — the "surfaces pop in/out" regression.  The 0x56 contract now
         * carries an app-computed depth word: (1/w)*2^30 from the TRUE
         * LoadedVertex w, with the SAME clamps + lrintf as the legacy 0x4E
         * loop, so cached and fallback tris z-compare at full legacy
         * precision in the shared z-buffer.  The VC_W_PRESCALE on cx/cy/cw
         * below now affects position + zi (perspective interpolation) ONLY —
         * depth no longer depends on it. */
        float df = v[k].w_inv * 1073741824.0f;   /* 1/w * 2^30 */
        if (df < 1.0f) df = 1.0f;
        else if (df > 2147483520.0f) df = 2147483520.0f;
#ifndef OF_PC
        {
            int32_t qx = (int32_t)lrintf(v[k].cx * (65536.0f * VC_W_PRESCALE));
            int32_t qy = (int32_t)lrintf(v[k].cy * (65536.0f * VC_W_PRESCALE));
            int32_t qw = (int32_t)lrintf(v[k].cw * (65536.0f * VC_W_PRESCALE));
            int32_t qu = (int32_t)lrintf(v[k].u * 65536.0f);
            int32_t qv = (int32_t)lrintf(v[k].v * 65536.0f);
            uint32_t qd = v[k].use_exact_depth ? v[k].exact_depth : (uint32_t)lrintf(df);
#if defined(__riscv)
            /* Expose independent conversions together before their dependent
             * stores, hiding the FPU-to-integer result latency. No arithmetic
             * or conversion mode changes; this emits no instructions. */
            __asm__ volatile ("" : "+r"(qx), "+r"(qy), "+r"(qw),
                                  "+r"(qu), "+r"(qv), "+r"(qd));
#endif
            *packet++ = ((uint32_t)GPU_CMD_LOAD_VERT_CLIP << 24) | 8u;
            *packet++ = (uint32_t)(slot[k] & 0x1fu);
            *packet++ = (uint32_t)qx;
            *packet++ = (uint32_t)qy;
            *packet++ = (uint32_t)qw;
            *packet++ = (uint32_t)qu;
            *packet++ = (uint32_t)qv;
            *packet++ = (uint32_t)rgb;
            *packet++ = qd;
        }
#else
        of_gpu_load_vert_clip(slot[k],
                              (int32_t)lrintf(v[k].cx * (65536.0f * VC_W_PRESCALE)),
                              (int32_t)lrintf(v[k].cy * (65536.0f * VC_W_PRESCALE)),
                              (int32_t)lrintf(v[k].cw * (65536.0f * VC_W_PRESCALE)),
                              (int32_t)lrintf(v[k].u * 65536.0f),
                              (int32_t)lrintf(v[k].v * 65536.0f),
                              rgb,
                              v[k].use_exact_depth ? v[k].exact_depth : (uint32_t)lrintf(df));
#endif
        g_vc_words += 9;                   /* 0x56: 1 header + 8 payload */
        g_vc_loads++;
    }

#ifndef OF_PC
    {
        *packet++ = ((uint32_t)GPU_CMD_DRAW_INDEXED_TRI << 24) | 1u;
        *packet++ = ((uint32_t)(slot[2] & 0x1fu) << 10)
                  | ((uint32_t)(slot[1] & 0x1fu) << 5)
                  | (uint32_t)(slot[0] & 0x1fu);
    }
    _gpu_ring_commit(packet_words);
#else
    of_gpu_draw_indexed_tri(slot[0], slot[1], slot[2]);
#endif
    g_vc_words += 2;                       /* 0x54: 1 header + 1 payload */
    g_vc_tris_cached++;

    /* Keep the GPU fed on long cached-only runs (the legacy path kicks once
     * per material batch; match that cadence without a per-tri doorbell). */
    if (++g_vc_kick >= 32u) {
        g_vc_kick = 0;
        of_gpu_kick();
    }
}

/* Known-index CPU run: same per-triangle packet arithmetic and order. */
static void gfx_gpu_vtx_cache_batch_impl(const struct gfx_vc_prepared vertices[32],
                                         const uint32_t *jobs, unsigned count, uint8_t alpha,
                                         const struct gfx_bram_import *import) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    /* The shared emitter checks material changes and dynamic draw fields
     * before building state. Every path still passes through that one memo. */
    g_cd_active = 0;      /* eligible => never the texel*C+D class */
    g_subpix_tri = 1;     /* viewport Y constants are scaled to Q12.4 */
    g_surf_alpha = alpha;
    emit_tri_state(g_vc_textured);

    /* X projection returns Q12.4. Scaling both Y viewport constants by 16
     * gives matching subpixel precision without changing the command ABI. */
    if (!g_vc_vp_valid) {
        of_gpu_object_state_t os;
        memset(&os, 0, sizeof(os));
        os.xcenter   = (int32_t)lrintf(g_cx);
        os.ycenter   = (int32_t)lrintf(g_cy * 16.0f);
        os.xscale    = (int32_t)lrintf(g_hw);
        os.yscale    = (int32_t)lrintf(g_hh * 16.0f);
        os.near_clip = 256;
        os.rows      = 3;
        of_gpu_set_object_state(&os);
        g_geometry_matrix_valid=0;
        g_vc_words += 27;              /* 0x50: 1 header + 26 payload */
        g_vc_vp[0] = g_cx; g_vc_vp[1] = g_cy;
        g_vc_vp[2] = g_hw; g_vc_vp[3] = g_hh;
        g_vc_vp_valid = 1;
    }

    /* Every slot has one immutable value throughout this run. Import the
     * unique BRAM-backed dirty slots before any draws, then release the
     * snapshot before rasterization. Older partial-LOAD slots still use the normal
     * CPU payload path below. The caller owns a snapshot of this generation. */
#ifndef OF_PC
    if (import && import->slots) {
        unsigned slots = import->slots, imported = 0;
        for (unsigned bits = slots; bits; bits &= bits - 1u) ++imported;
        const unsigned words = 6u + 3u * imported;
        _gpu_stream_reserve_words(words);
        uint32_t *packet = _gpu_ring_claim();
        *packet++ = ((uint32_t)GPU_CMD_BRAM_IMPORT_CONTEXT << 24) | 3u;
        *packet++ = (uint32_t)import->u_offset;
        *packet++ = (uint32_t)import->v_offset;
        *packet++ = import->flags_rgb;
        for (unsigned slot = 0; slots; ++slot, slots >>= 1) {
            if (!(slots & 1u)) continue;
            *packet++ = ((uint32_t)GPU_CMD_BRAM_IMPORT_VERTEX << 24) | 2u;
            *packet++ = import->generation;
            *packet++ = (slot - import->first) | (slot << 5);
        }
        *packet++ = ((uint32_t)GPU_CMD_BRAM_IMPORT_RELEASE << 24) | 1u;
        *packet++ = import->generation;
        _gpu_ring_commit(words);
        g_vc_words += words; g_vc_loads += imported;
        gfx_bram_stats[2] += imported; ++gfx_bram_stats[3];

    }
#endif

    /* No state mutation occurs inside this bounded CPU-only run. */
    for (unsigned tri = 0; tri < count; ++tri) {
        const uint32_t job = jobs[tri];
        const uint8_t slot[3] = {job & 31u, (job >> 5) & 31u, (job >> 10) & 31u};
        unsigned dirty_mask = (job >> 15) & 7u;
        if (import) {
            for (unsigned k = 0; k < 3; ++k)
                if (import->slots & (1u << slot[k])) dirty_mask &= ~(1u << k);
        }
#ifndef OF_PC
    /* One reservation preserves the same ordered 0x56 loads and 0x54 draw.
     * Material and viewport commands above have already been staged. */
    /* gfx_gpu_vtx_cache_begin already proves both VERT_TRI and CLIP_LOAD.
     * Its only caller invokes this routine after that eligibility check. */
    const unsigned dirty_count = (dirty_mask & 1u) + ((dirty_mask >> 1) & 1u)
                               + ((dirty_mask >> 2) & 1u);
    const unsigned packet_words = 9u * dirty_count + 2u;
    _gpu_stream_reserve_words(packet_words);
    uint32_t *packet = _gpu_ring_claim();
#endif

    /* The front end has quantized each unique dirty slot once. Keep the
     * original per-corner dirty mask: duplicate slots still produce duplicate
     * load commands in precisely the same place in the stream. */
    for (int k = 0; k < 3; k++) {
        if (!(dirty_mask & (1u << k))) continue;
        const struct gfx_vc_prepared *vtx = &vertices[slot[k]];
#ifndef OF_PC
        *packet++ = ((uint32_t)GPU_CMD_LOAD_VERT_CLIP << 24) | 8u;
        *packet++ = (uint32_t)(slot[k] & 0x1fu);
        *packet++ = (uint32_t)vtx->x;
        *packet++ = (uint32_t)vtx->y;
        *packet++ = (uint32_t)vtx->w;
        *packet++ = (uint32_t)vtx->u;
        *packet++ = (uint32_t)vtx->v;
        *packet++ = vtx->rgb;
        *packet++ = vtx->depth;
#else
        of_gpu_load_vert_clip(slot[k], vtx->x, vtx->y, vtx->w,
                              vtx->u, vtx->v, (uint16_t)vtx->rgb, vtx->depth);
#endif
        g_vc_words += 9;
        g_vc_loads++;
    }

#ifndef OF_PC
    {
        *packet++ = ((uint32_t)GPU_CMD_DRAW_INDEXED_TRI << 24) | 1u;
        *packet++ = ((uint32_t)(slot[2] & 0x1fu) << 10)
                  | ((uint32_t)(slot[1] & 0x1fu) << 5)
                  | (uint32_t)(slot[0] & 0x1fu);
    }
    _gpu_ring_commit(packet_words);
#else
    of_gpu_draw_indexed_tri(slot[0], slot[1], slot[2]);
#endif
    g_vc_words += 2;                       /* 0x54: 1 header + 1 payload */
    g_vc_tris_cached++;

    /* Keep the GPU fed on long cached-only runs (the legacy path kicks once
     * per material batch; match that cadence without a per-tri doorbell). */
    if (++g_vc_kick >= 32u) {
        g_vc_kick = 0;
        of_gpu_kick();
    }
    }
#ifndef OF_PC
    /* Publish the completed import/release/draw batch so scarce snapshots
     * cannot sit held solely by CPU staging. Deferred submission still overlaps
     * this publication with the next raw LOAD. */
    if (import && import->slots) of_gpu_kick_now();
#endif
}

void gfx_gpu_queued_front_batch(const uint32_t *jobs, unsigned count,
                                uint8_t alpha, unsigned cull,
                                const struct gfx_bram_import *context) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    /* The shared emitter checks material changes and dynamic draw fields
     * before building state. Every path still passes through that one memo. */
    g_cd_active = 0;      /* eligible => never the texel*C+D class */
    g_subpix_tri = 1;     /* viewport Y constants are scaled to Q12.4 */
    g_surf_alpha = alpha;
    emit_tri_state(g_vc_textured);

    /* X projection returns Q12.4. Scaling both Y viewport constants by 16
     * gives matching subpixel precision without changing the command ABI. */
    if (!g_vc_vp_valid) {
        of_gpu_object_state_t os;
        memset(&os, 0, sizeof(os));
        os.xcenter   = (int32_t)lrintf(g_cx);
        os.ycenter   = (int32_t)lrintf(g_cy * 16.0f);
        os.xscale    = (int32_t)lrintf(g_hw);
        os.yscale    = (int32_t)lrintf(g_hh * 16.0f);
        os.near_clip = 256;
        os.rows      = 3;
        of_gpu_set_object_state(&os);
        g_geometry_matrix_valid=0;
        g_vc_words += 27;              /* 0x50: 1 header + 26 payload */
        g_vc_vp[0] = g_cx; g_vc_vp[1] = g_cy;
        g_vc_vp[2] = g_hw; g_vc_vp[3] = g_hh;
        g_vc_vp_valid = 1;
    }

#ifndef OF_PC
    const unsigned words = count + 6u;
    _gpu_stream_reserve_words(words);
    uint32_t *packet = _gpu_ring_claim();
    *packet++ = (0x60u << 24) | (count + 5u);
    *packet++ = context->generation;
    *packet++ = context->first | ((cull & 3u) << 5) | ((uint32_t)alpha << 8) | (count << 16);
    *packet++ = (uint32_t)context->u_offset;
    *packet++ = (uint32_t)context->v_offset;
    *packet++ = context->flags_rgb;
    for (unsigned i = 0; i < count; ++i) *packet++ = jobs[i];
    _gpu_ring_commit(words);
    g_vc_words += words;
    /* Queued triangles are admission counts; actual keep/drop is asynchronous
     * and separately observable in the worker, never guessed here. */
    ++gfx_queued_stats[1]; gfx_queued_stats[2] += count;
    of_gpu_kick_now();
#else
    (void)jobs; (void)count; (void)cull; (void)context;
#endif
}

void gfx_gpu_mesh_packet(uint32_t serial,uint8_t alpha,
                         const struct gfx_bram_import *context) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    g_cd_active=0;
    g_subpix_tri=1;
    g_surf_alpha=alpha;
    emit_tri_state(g_vc_textured);
    if(!g_vc_vp_valid) {
        of_gpu_object_state_t os;
        memset(&os,0,sizeof(os));
        os.xcenter=(int32_t)lrintf(g_cx);
        os.ycenter=(int32_t)lrintf(g_cy*16.0f);
        os.xscale=(int32_t)lrintf(g_hw);
        os.yscale=(int32_t)lrintf(g_hh*16.0f);
        os.near_clip=256;os.rows=3;
        of_gpu_set_object_state(&os);
        g_geometry_matrix_valid=0;g_vc_words+=27;
        g_vc_vp[0]=g_cx;g_vc_vp[1]=g_cy;
        g_vc_vp[2]=g_hw;g_vc_vp[3]=g_hh;g_vc_vp_valid=1;
    }
#ifndef OF_PC
    _gpu_stream_reserve_words(5);
    uint32_t *packet=_gpu_ring_claim();
    *packet++=(0x62u<<24)|4u;
    *packet++=serial;
    *packet++=context->flags_rgb;
    *packet++=(uint32_t)context->u_offset;
    *packet++=(uint32_t)context->v_offset;
    _gpu_ring_commit(5);g_vc_words+=5;
    /* Batch while published CPU-ring work remains, but feed the GPU before
     * that queue runs dry. The staged cursor includes unpublished words;
     * subtract them before comparing against the hardware read pointer. */
    if(!_gpu_cpu_ring)of_gpu_kick_now();
    else if(_gpu_cmd_words>=OF_GPU_KICK_MIN_WORDS||_gpu_unflushed_sync)
        of_gpu_kick();
    else {
        const uint32_t published=(_gpu_wrptr-(_gpu_cmd_words<<2))&_gpu_ring_mask;
        const uint32_t queued=(published-GPU_RING_RDPTR)&_gpu_ring_mask;
        if(queued<512u)of_gpu_kick_now();
    }
#else
    (void)serial;(void)context;
#endif
}

void gfx_gpu_async_job(uint32_t vertex_offset,uint32_t vtx_word,
                      uint32_t command_offset,unsigned command_count,unsigned cull,
                      const void *vertices,const void *commands,
                      const struct am_transform *transform,uint8_t alpha,
                      const struct gfx_bram_import *material) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    g_cd_active=0;g_subpix_tri=1;g_surf_alpha=alpha;
    emit_tri_state(g_vc_textured);
    if(!g_vc_vp_valid) {
        of_gpu_object_state_t os={0};
        os.xcenter=(int32_t)lrintf(g_cx);os.ycenter=(int32_t)lrintf(g_cy*16.0f);
        os.xscale=(int32_t)lrintf(g_hw);os.yscale=(int32_t)lrintf(g_hh*16.0f);
        os.near_clip=256;os.rows=3;of_gpu_set_object_state(&os);
        g_geometry_matrix_valid=0;g_vc_words+=27;
        g_vc_vp[0]=g_cx;g_vc_vp[1]=g_cy;g_vc_vp[2]=g_hw;g_vc_vp[3]=g_hh;
        g_vc_vp_valid=1;
    }
#ifndef OF_PC
    gpu_async_epoch_check();
    _Static_assert(sizeof(struct am_transform)==24u*4u,"AJ01 transform context");
    /* Priority legacy LOAD may overwrite every shared context field while
     * older jobs remain queued. Each job therefore restores one complete
     * immutable context; hardware locks this bundle through JOB acceptance. */
    _gpu_stream_reserve_words(42u);
    uint32_t *packet=_gpu_ring_claim();
    *packet++=0x6300001du;*packet++=0;
    for(unsigned i=0;i<16;++i)*packet++=transform->matrix[i];
    *packet++=transform->aspect;
    for(unsigned i=0;i<3;++i)*packet++=transform->coefficient[i];
    *packet++=transform->ambient;*packet++=transform->diffuse;
    *packet++=transform->scales;*packet++=transform->lighting;
    *packet++=(uint32_t)g_clip_cx;*packet++=(uint32_t)g_clip_cy;
    *packet++=(uint32_t)g_clip_hw;*packet++=(uint32_t)g_clip_hh;
    if(!++async_sequence)++async_sequence;
    *packet++=0x6400000bu;*packet++=async_sequence;*packet++=vertex_offset;
    *packet++=vtx_word;*packet++=command_offset;
    *packet++=command_count|((cull&3u)<<8);*packet++=material->flags_rgb;
    *packet++=(uint32_t)material->u_offset;*packet++=(uint32_t)material->v_offset;
    *packet++=(uint32_t)(uintptr_t)vertices;*packet++=(uint32_t)(uintptr_t)commands;
    *packet++=alpha;
    _gpu_ring_commit(42u);g_vc_words+=42u;gfx_async_stats[6]+=28u;
    /* Publish asynchronous work once complete. This is an input job, not a
     * packet-copy marker: no per-job raw ownership or ARM wait follows. */
    of_gpu_kick();
#else
    (void)vertex_offset;(void)vtx_word;(void)command_offset;(void)command_count;
    (void)cull;(void)vertices;(void)commands;(void)transform;(void)material;
#endif
}

void gfx_gpu_async_group(uint32_t descriptor_offset,unsigned members,
                        const struct am_transform *transform,uint8_t alpha,
                        const struct gfx_bram_import *material) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    g_cd_active=0;g_subpix_tri=1;g_surf_alpha=alpha;
    emit_tri_state(g_vc_textured);
    if(!g_vc_vp_valid) {
        of_gpu_object_state_t os={0};
        os.xcenter=(int32_t)lrintf(g_cx);os.ycenter=(int32_t)lrintf(g_cy*16.0f);
        os.xscale=(int32_t)lrintf(g_hw);os.yscale=(int32_t)lrintf(g_hh*16.0f);
        os.near_clip=256;os.rows=3;of_gpu_set_object_state(&os);
        g_geometry_matrix_valid=0;g_vc_words+=27;
        g_vc_vp[0]=g_cx;g_vc_vp[1]=g_cy;g_vc_vp[2]=g_hw;g_vc_vp[3]=g_hh;
        g_vc_vp_valid=1;
    }
#ifndef OF_PC
    if(!members||members>32)gpu_async_fault();
    gpu_async_epoch_check();
    /* Reserve one contiguous sequence range. No old source may be reused
     * until its final member completes, even if a legacy lease grants early. */
    if(async_sequence>UINT32_MAX-members) {
        gfx_gpu_async_drain();async_sequence=0;
    }
    _gpu_stream_reserve_words(38u);
    uint32_t *packet=_gpu_ring_claim();
    *packet++=0x6300001du;*packet++=0;
    for(unsigned i=0;i<16;++i)*packet++=transform->matrix[i];
    *packet++=transform->aspect;
    for(unsigned i=0;i<3;++i)*packet++=transform->coefficient[i];
    *packet++=transform->ambient;*packet++=transform->diffuse;
    *packet++=transform->scales;*packet++=transform->lighting;
    *packet++=(uint32_t)g_clip_cx;*packet++=(uint32_t)g_clip_cy;
    *packet++=(uint32_t)g_clip_hw;*packet++=(uint32_t)g_clip_hh;
    const uint32_t first=async_sequence+1u;
    async_sequence+=members;
    *packet++=0x65000007u;*packet++=first;*packet++=descriptor_offset;
    *packet++=members;*packet++=material->flags_rgb;
    *packet++=(uint32_t)material->u_offset;*packet++=(uint32_t)material->v_offset;
    *packet++=alpha;
    _gpu_ring_commit(38u);g_vc_words+=38u;gfx_async_stats[6]+=28u;
    of_gpu_kick_now();
#else
    (void)descriptor_offset;(void)members;(void)transform;(void)material;
#endif
}

void gfx_gpu_vtx_cache_batch(const struct gfx_vc_prepared vertices[32],
                             const uint32_t *jobs, unsigned count, uint8_t alpha) {
    gfx_gpu_vtx_cache_batch_impl(vertices, jobs, count, alpha, NULL);
}

void gfx_gpu_vtx_cache_import_batch(const struct gfx_vc_prepared vertices[32],
                                    const uint32_t *jobs, unsigned count, uint8_t alpha,
                                    const struct gfx_bram_import *import) {
    gfx_gpu_vtx_cache_batch_impl(vertices, jobs, count, alpha, import);
}

/* Raw geometry path. Reuses the existing ring/cache and material emitter;
 * never waits for a transform result or fences the rasterizer per vertex. */
void gfx_gpu_geometry_tri(const struct gfx_vc_vtx v[3], const uint8_t slot[3],
                         unsigned dirty, const int16_t *pos[3],
                         const int32_t *matrix[3], const uint32_t serial[3], unsigned cull) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
#ifndef OF_PC
    g_cd_active=0;g_subpix_tri=1;g_surf_alpha=v[0].a;
    emit_tri_state(g_vc_textured);
    for(unsigned k=0;k<3;) {
        if(!(dirty&(1u<<k))) {k++;continue;}
        if(!g_geometry_matrix_valid || serial[k]!=g_geometry_serial
            || !g_vc_vp_valid || g_vc_vp[0]!=g_cx || g_vc_vp[1]!=g_cy
            || g_vc_vp[2]!=g_hw || g_vc_vp[3]!=g_hh) {
            of_gpu_object_state_t os={0};
            memcpy(os.matrix,matrix[k],12*sizeof(int32_t));
            os.xcenter=(int32_t)lrintf(g_cx);os.ycenter=(int32_t)lrintf(g_cy*16.0f);
            os.xscale=(int32_t)lrintf(g_hw);os.yscale=(int32_t)lrintf(g_hh*16.0f);
            os.near_clip=256;os.rows=3;
            of_gpu_set_object_state(&os);
            g_geometry_serial=serial[k];
            g_geometry_matrix_valid=1;g_vc_vp_valid=1;
            g_vc_vp[0]=g_cx;g_vc_vp[1]=g_cy;g_vc_vp[2]=g_hw;g_vc_vp[3]=g_hh;
            g_vc_words+=27;
        }
        unsigned indices[3],n=0,end=k;
        for(;end<3;end++) if(dirty&(1u<<end)) {
            if(serial[k]!=serial[end])break;
            indices[n++]=end;
        }
        _gpu_cmd_header(0x59,5*n);
        uint32_t *w=_gpu_ring_claim();
        for(unsigned j=0;j<n;j++) {
            unsigned i=indices[j];
            uint16_t rgb=g_vc_rgb_input<0?0xffff:rgba_to_565(v[i].r,v[i].g,v[i].b);
            *w++=slot[i];
            *w++=(uint16_t)pos[i][0]|((uint32_t)(uint16_t)pos[i][1]<<16);
            *w++=(uint16_t)pos[i][2]|((uint32_t)rgb<<16);
            *w++=(uint32_t)(int32_t)lrintf(v[i].u*65536.0f);
            *w++=(uint32_t)(int32_t)lrintf(v[i].v*65536.0f);
        }
        _gpu_ring_commit(5*n);g_vc_words+=1+5*n;g_vc_loads+=n;
        k=end;
    }
    _gpu_cmd_header(0x5b,1);
    uint32_t *w=_gpu_ring_claim();
    *w=slot[0]|((uint32_t)slot[1]<<5)|((uint32_t)slot[2]<<10)|((cull&3u)<<15);
    _gpu_ring_commit(1);g_vc_words+=2;g_vc_tris_cached++;
    if(++g_vc_kick>=32u) {g_vc_kick=0;of_gpu_kick();}
#endif
}

/* Reserve the entire run once. Matrix changes and 1..3-vertex raw loads keep
 * their existing ABI; only CPU staging overhead and redundant headers shrink.
 * Bounds: <=32 uploads, <=64 triangles, at most 1184 words (<4095 ring words). */
void gfx_gpu_geometry_batch(const struct gfx_geometry_upload *restrict upload, unsigned count,
                            const uint32_t *restrict triangles, unsigned triangle_count,
                            uint8_t alpha) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
#ifndef OF_PC
    g_cd_active=0; g_subpix_tri=1; g_surf_alpha=alpha;
    emit_tri_state(g_vc_textured);
    int valid=g_geometry_matrix_valid && g_vc_vp_valid
        && g_vc_vp[0]==g_cx && g_vc_vp[1]==g_cy
        && g_vc_vp[2]==g_hw && g_vc_vp[3]==g_hh;
    uint32_t serial=g_geometry_serial;
    unsigned words=2*triangle_count;
    unsigned groups[32], group_count=0;
    for(unsigned i=0;i<count;) {
        unsigned n=1;
        while(n<3 && i+n<count && upload[i+n].serial==upload[i].serial)n++;
        unsigned bind=!valid || serial!=upload[i].serial;
        groups[group_count++]=n|(bind<<8);
        words+=1+5*n+27*bind;
        serial=upload[i].serial; valid=1; i+=n;
    }
    _gpu_stream_reserve_words(words);
    uint32_t *restrict w=_gpu_ring_claim();
    unsigned i=0;
    for(unsigned group=0;group<group_count;group++) {
        unsigned n=groups[group]&255u;
        if(groups[group]&256u) {
            *w++=0x5000001au;
            for(unsigned k=0;k<12;k++)*w++=(uint32_t)upload[i].matrix[k];
            for(unsigned k=0;k<8;k++)*w++=0;
            *w++=(uint32_t)(int32_t)lrintf(g_cx);
            *w++=(uint32_t)(int32_t)lrintf(g_cy*16.0f);
            *w++=(uint32_t)(int32_t)lrintf(g_hw);
            *w++=(uint32_t)(int32_t)lrintf(g_hh*16.0f);
            *w++=256; *w++=3;
        }
        *w++=0x59000000u+5*n;
        for(unsigned j=0;j<n;j++,i++) {
            for(unsigned k=0;k<5;k++)*w++=upload[i].words[k];
        }
    }
    for(unsigned j=0;j<triangle_count;j++) {
        *w++=0x5b000001u; *w++=triangles[j];
    }
    _gpu_ring_commit(words);
    if(count) {
        g_geometry_serial=serial; g_geometry_matrix_valid=1; g_vc_vp_valid=1;
        g_vc_vp[0]=g_cx; g_vc_vp[1]=g_cy; g_vc_vp[2]=g_hw; g_vc_vp[3]=g_hh;
    }
    g_vc_words+=words; g_vc_loads+=count; g_vc_tris_cached+=triangle_count;
    g_vc_kick+=triangle_count;
    if(g_vc_kick>=32u) {g_vc_kick=0;of_gpu_kick();}
#endif
}

/* Match the GPU's quantization, reciprocal and signed shifts on both axes.
 * Do not reconstruct clip coordinates from NDC: that round trip can change a
 * shared vertex at a rounding boundary. */
/* The denominator is clamped to at least 256. Compute floor(2^32 / den)
 * with one RV32 division, correcting the UINT32_MAX numerator exactly. */
static inline uint32_t gpu_clip_reciprocal(uint32_t den) {
    uint32_t q = UINT32_MAX / den;
    /* 2^32 is divisible by den exactly when nonzero den is a power of two. */
    return q + ((den & (den - 1u)) == 0u);
}

static void gpu_project_clip(float cx, float cy, float cw, int16_t *x, int16_t *y) {
    int32_t xq = (int32_t)lrintf(cx * (65536.0f * VC_W_PRESCALE));
    int32_t yq = (int32_t)lrintf(cy * (65536.0f * VC_W_PRESCALE));
    int32_t wq = (int32_t)lrintf(cw * (65536.0f * VC_W_PRESCALE));
    uint32_t den = wq < 256 ? 256u : (uint32_t)wq;
    uint32_t recip = gpu_clip_reciprocal(den);
    int32_t rx = (int32_t)(((int64_t)xq * recip) >> 16);
    int32_t ry = (int32_t)(((int64_t)yq * recip) >> 16);
    int64_t sx = g_clip_cx * 16LL
               + (((int64_t)g_clip_hw * rx) >> 12);
    int64_t sy = g_clip_cy
               - (((int64_t)g_clip_hh * ry) >> 16);
    *x = sx < -32768 ? -32768 : sx > 32767 ? 32767 : (int16_t)sx;
    *y = sy < -32768 ? -32768 : sy > 32767 ? 32767 : (int16_t)sy;
}


/* Fixed-record fallback for the same simple material class as 0x54. This
 * preserves the existing screen-space 0x4E packet, including its per-triangle
 * perspective scale and explicit depth. C+D, decals, XLU and alpha-mask
 * materials keep using gpu_draw_triangles and its complete combiner logic. */
void gfx_gpu_draw_direct_records(const struct gfx_vc_vtx *vertices,
                                 size_t num_tris) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    if (!g_has_gpu || !g_cur_shader || num_tris == 0) return;
    const int textured = g_vc_textured;
    if (textured) g_cd_fallbacks++;
    g_cd_active = 0;
    g_subpix_tri = 1;
    for (size_t tri = 0; tri < num_tris; tri++) {
        const struct gfx_vc_vtx *v = vertices + 3 * tri;
        int16_t x[3], y[3];
        int32_t s[3], t[3], zi[3], depth[3];
        uint16_t rgb[3];
        /* Other material state and texture ownership stay fixed inside
         * this batch. Bind/mark the texture on its first triangle and repeat
         * only when the per-triangle alpha changes. */
        if (tri == 0 || g_surf_alpha != v[0].a) {
            g_surf_alpha = v[0].a;
            emit_tri_state(textured);
        }

        /* Same operation order, comparisons and rounding as the legacy loop.
         * In particular, do not use the cached-slot reciprocal for zi. */
        float zscale = (float)ZI_SCALE;
        if (textured) {
            float m = 0.0f;
            for (int k = 0; k < 3; k++) {
                float a3 = fabsf(v[k].w_inv);
                float a4 = fabsf(v[k].u * v[k].w_inv);
                float a5 = fabsf(v[k].v * v[k].w_inv);
                if (a3 > m) m = a3;
                if (a4 > m) m = a4;
                if (a5 > m) m = a5;
            }
            if (m > 0.0f) zscale = ZI_DERIVE_TARGET / m;
            if (zscale < 1.0f) zscale = 1.0f;
        }
        for (int k = 0; k < 3; k++) {
            gpu_project_clip(v[k].cx, v[k].cy, v[k].cw, &x[k], &y[k]);
            int z = (int)lrintf(v[k].w_inv * (65536.0f * zscale));
            if (z < 1) z = 1;
            zi[k] = z;
            float df = v[k].w_inv * 1073741824.0f;
            if (df < 1.0f) df = 1.0f;
            else if (df > 2147483520.0f) df = 2147483520.0f;
            depth[k] = (int32_t)lrintf(df);
            if (textured) {
                s[k] = (int32_t)lrintf(v[k].u * 65536.0f);
                t[k] = (int32_t)lrintf(v[k].v * 65536.0f);
            } else {
                s[k] = t[k] = 0;
            }
            rgb[k] = (g_vc_rgb_input < 0) ? 0xFFFF
                      : rgba_to_565(v[k].r, v[k].g, v[k].b);
        }
        of_gpu_draw_vert_tri_rgb(x, y, s, t, zi, rgb, gpu_q29_word(zi),
                                 depth, NULL);
        g_vc_words += 18u;
    }
    g_vc_tris_legacy += (uint32_t)num_tris;
    of_gpu_kick();
}

extern bool gfx_gpu_direct_batch;
static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,
                               size_t buf_vbo_num_tris) {
    /* The caller's union contains live gfx_vc_vtx objects in direct mode.
     * The float pointer carries their aligned address; no float is read. */
    if (gfx_gpu_direct_batch) {
        gfx_gpu_draw_direct_records((const struct gfx_vc_vtx *)(const void *)buf_vbo,
                                    buf_vbo_num_tris);
        return;
    }
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    if (!g_has_gpu || !g_cur_shader || buf_vbo_num_tris == 0)
        return;

    const struct ShaderProgram *sh = g_cur_shader;
    const int stride = (int)(buf_vbo_len / (3 * buf_vbo_num_tris));
    const int textured = sh->cc.used_textures[0] && g_cur_tex[0] &&
                         g_cur_tex[0]->ci8;
    const int has_color = sh->cc.num_inputs > 0;
    /* buf_vbo carries RAW tile-relative texel coords (gfx_pc.c, TARGET_OPENFPGA):
     * the recovered per-vertex value IS the texel, so the only scale here is
     * texel->Q16.16 (65536).  Do NOT multiply by the uploaded texture width — the
     * GPU already strides the texel fetch by st.tex_width (= t->w); folding it in
     * here too double-counted the tile/upload size ratio and stretched textures. */
    const float tw_q = 65536.0f;
    const float th_q = 65536.0f;
    const int coff = sh->coff;
    const int rgb_input = cc_cycle_input(&sh->cc, 0);
    const int alpha_input = cc_cycle_input(&sh->cc, 1);
    const int csel = (rgb_input < 0 ? 0 : rgb_input) * (sh->cc.opt_alpha ? 4 : 3);
    /* Does the RGB colour cycle actually consume a per-vertex colour input?  A
     * pure decal (e.g. G_CC_DECALFADEA on the intro Peach letter: RGB cycle =
     * (0,0,0,TEXEL0)) carries its only input (ENV) in the ALPHA cycle, so
     * num_inputs>0 / has_color is true — yet the RGB input map is empty and
     * colour-slot 0 is filled BLACK, so the texel would be modulated by 0x0000
     * and the tan parchment renders dark.  Detect a texel-only RGB cycle and
     * modulate by white (0xFFFF) instead of that black slot. */
    const int rgb_has_input = cc_rgb_has_input(&sh->cc);
    /* Binary-alpha decals need a shaded complementary pass. */
    const int blend_texel_alpha = cc_blend_texel_alpha(&sh->cc);
    const int shade_mask = g_truecolor && textured && blend_texel_alpha
                        && (!sh->cc.opt_alpha || (sh->cc.do_single[1] && alpha_input >= 0))
                        && gpu_tex_alpha_mask(g_cur_tex[0]);

    /* Full combiner emulation: detect the HILITE class (texel*C+D) so the GPU
     * resolves the specular highlight (e.g. the title Mario head) instead of the
     * legacy single-input texel*C.  Only on truecolor + textured surfaces. */
    const int istride = sh->cc.opt_alpha ? 4 : 3;
    int cd_a = -1, cd_b = -1, cd_d = -1;
    int cd_on = (g_truecolor && g_combine && textured)
                ? classify_combine_cd(&sh->cc, &cd_a, &cd_b, &cd_d) : 0;
    if (g_truecolor && textured && !cd_on) g_cd_fallbacks++;
    g_cd_active = cd_on;
    g_subpix_tri = g_truecolor;   /* 3D vert-tri: Q12.4 subpixel Y (truecolor only) */
    /* (No g_vc_state_dirty here: 0x4A emission coherence is owned by the
     * shared g_st_cache memo since the round-4 restructure, and a legacy
     * draw changes no eligibility ingredient.) */

    /* Keep cached and fallback edges identical, including across materials. */
    const int vc_live = g_clip_load && g_truecolor;

    /* buf_vbo layout on TARGET_OPENFPGA: f[0],f[1]=NDC x,y (these DO carry the
     * 1/w divide); f[3]=1/w; f[4],f[5]=RAW texels u,v; colours RAW 0..255.
     * Attributes are no longer premultiplied by 1/w — gfx_pc.c's GFX_OUT_PROP is
     * identity here, because the only thing that ever wanted premultiplied
     * attributes was the (unbuilt) software rasterizer, and this loop used to
     * spend an fdiv.s per vertex undoing it.  Project to screen and emit
     * screen-space 0x4E/0x4B. */
    for (size_t tri = 0; tri < buf_vbo_num_tris; tri++) {
        int16_t x[3], y[3];
        int32_t s[3], t[3], zi[3];
        uint8_t light[3];
        uint16_t rgb[3];
        uint16_t shade_rgb[3];
        uint16_t rgb_d[3];      /* combine: per-vertex additive D (RGB565) */
        int32_t depth[3];

        /* A batch can contain several material alpha values without a
         * shader or texture change. Select the first vertex of THIS
         * triangle; sharing the batch's first alpha flashes fading overlays. */
        g_surf_alpha = 255;
        if (sh->cc.opt_alpha && alpha_input >= 0) {
            const float *f0 = buf_vbo + tri * 3 * stride;
            int a = (int)lrintf(f0[coff + alpha_input * 4 + 3]);
            if (a < 0) a = 0; else if (a > 255) a = 255;
            g_surf_alpha = (uint8_t)a;
        }
        emit_tri_state(textured);

        /* Consume the original coordinates saved before flattening. */
        const struct gfx_vc_true_xyw *tv = NULL;
        if (vc_live && gfx_vc_true[tri].valid) {
            tv = &gfx_vc_true[tri];
            gfx_vc_true[tri].valid = 0;
        }

        /* Per-triangle perspective scale (see ZI_SCALE / ZI_DERIVE_TARGET).
         * The derived planes are szi=(u/w)*K, tzi=(v/w)*K, zi=(1/w)*K, so the
         * largest of |1/w|,|u/w|,|v/w| over the 3 verts bounds all three.  Pick
         * K so the peak lands at ZI_DERIVE_TARGET — maximising the significant
         * gradient bits the HW derive retains, without overflowing the Q16.16
         * window.  1/w is f[3]; u/w and v/w are formed as f[4]*f[3], f[5]*f[3]
         * (f[4],f[5] are raw texels now — see the layout note above).  Scale
         * cancels in szi/zi, so each triangle may use its own K and the shared
         * edge stays consistent.
         *
         * ONLY the textured path: there s carries the raw texel u, so szi=(u/w)*K
         * really is bounded by f[4]*f[3].  The untextured palettized path instead packs
         * the colour index (0..255) into s, so szi=index*(1/w)*K — a large K would
         * overflow it; that (precision-insensitive) ramp path keeps the fixed
         * ZI_SCALE.  Truecolor-untextured sends s=t=0 (no texcoord to sharpen). */
        float zscale = (float)ZI_SCALE;
        if (textured) {
            float m = 0.0f;
            for (int k = 0; k < 3; k++) {
                const float *f = buf_vbo + (tri * 3 + k) * stride;
                /* f[4]/f[5] are now RAW texels u,v (gfx_pc no longer premultiplies
                 * by 1/w on this target), but the bound we need is on the DERIVED
                 * plane values szi=u/w and tzi=v/w — so form them here with one
                 * multiply each.  Two fmul.s replacing an fdiv.s plus the whole
                 * de-premultiply chain below is a large net win. */
                float a3 = fabsf(f[3]);            /* |1/w| (bounds zi)        */
                float a4 = fabsf(f[4] * f[3]);     /* |u/w| (bounds szi)       */
                float a5 = fabsf(f[5] * f[3]);     /* |v/w| (bounds tzi)       */
                if (a3 > m) m = a3;
                if (a4 > m) m = a4;
                if (a5 > m) m = a5;
            }
            if (m > 0.0f) zscale = ZI_DERIVE_TARGET / m;
            if (zscale < 1.0f) zscale = 1.0f;      /* never shrink below 1:1   */
        }

        for (int k = 0; k < 3; k++) {
            const float *f = buf_vbo + (tri * 3 + k) * stride;
            float wi = f[3];                       /* 1/w */
            /* The `w = 1.0f/wi` that used to live here (one non-pipelined
             * fdiv.s per VERTEX) is gone: gfx_pc.c hands us raw, un-premultiplied
             * texcoords and colours on this target, so there is nothing to undo. */

            float sx = f[0] * g_hw + g_cx;
            float sy = g_cy - f[1] * g_hh;          /* flip NDC y-up -> screen y-down */
            x[k] = (int16_t)lrintf(sx * 16.0f);
            if (vc_live && tv) {
                gpu_project_clip(tv->x[k], tv->y[k], tv->w[k], &x[k], &y[k]);
            } else if (g_truecolor) {
                /* Q12.4 subpixel Y (control bit 31) — 1/16-scanline edge
                 * precision so triangle vertices aren't snapped to whole
                 * scanlines (fixes the slightly-displaced character polygons).
                 * Clamp to +/-2047 px so sy*16 fits int16; verts past that are
                 * far outside the viewport and the walker clips them anyway. */
                float syc = (sy < -2047.0f) ? -2047.0f : (sy > 2047.0f ? 2047.0f : sy);
                y[k] = (int16_t)lrintf(syc * 16.0f);
            } else {
                y[k] = (int16_t)lrintf(sy);          /* integer scanline (palettized) */
            }

            int z = (int)lrintf(wi * (65536.0f * zscale));  /* per-tri scaled Q16.16 of 1/w */
            if (z < 1) z = 1;
            zi[k] = z;

            /* Decoupled high-precision depth (0x4E w16-18): 1/w at a large fixed
             * scale, GPU float-compresses it for the z-buffer (independent of the
             * perspective-capped zi above) -> ~10x better far-depth precision.
             * The cached path sends the SAME (1/w)*2^30 value in its 0x56 slot
             * word (FIX E), so both populations z-compare consistently. */
            float df = (tv && (tv->depth_mask&(1u<<k)))
                     ? (float)tv->exact_depth[k] : wi * 1073741824.0f;
            /* Decal (G_ZMODE_DEC: shadows, signs, painting frames) sits on a
             * coplanar surface — bias it toward the camera (larger 1/w = nearer)
             * so it reliably wins the z-test instead of z-fighting/popping.
             * See DECAL_Z_BIAS (tune on hardware). */
            if (g_decal) df *= DECAL_Z_BIAS;
            if (df < 1.0f) df = 1.0f;
            else if (df > 2147483520.0f) df = 2147483520.0f;
            depth[k] = (tv && (tv->depth_mask&(1u<<k)) && !g_decal)
                     ? (int32_t)tv->exact_depth[k] : (int32_t)lrintf(df);

            float cr = 255.0f, cg = 255.0f, cb = 255.0f;
            if (has_color) {
                cr = f[coff + csel + 0];
                cg = f[coff + csel + 1];
                cb = f[coff + csel + 2];
            }

            if (textured) {
                s[k] = (int32_t)lrintf(f[4] * tw_q);       /* raw texel s, Q16.16 */
                t[k] = (int32_t)lrintf(f[5] * th_q);
            } else if (g_truecolor) {
                s[k] = 0;
                t[k] = 0;
            } else {
                s[k] = (int32_t)rgb332((int)cr, (int)cg, (int)cb) << 16;
                t[k] = 0;
            }

            shade_rgb[k] = rgba_to_565((int)cr, (int)cg, (int)cb);
            if (g_truecolor) {
                if (cd_on) {
                    /* texel*C+D: C = val(a)-val(b) (signed), D = val(d).  Encode
                     * C into the signed RGB565 words; D into the rgb_d triple. */
                    float va[3], vb[3], vd[3];
                    cc_val_rgb(f, coff, istride, cd_a, va);
                    cc_val_rgb(f, coff, istride, cd_b, vb);
                    cc_val_rgb(f, coff, istride, cd_d, vd);
                    rgb[k]   = ((uint16_t)enc_C5(va[0] - vb[0]) << 11)
                             | ((uint16_t)enc_C6(va[1] - vb[1]) << 5)
                             |  (uint16_t)enc_C5(va[2] - vb[2]);
                    rgb_d[k] = ((uint16_t)enc_D5(vd[0]) << 11)
                             | ((uint16_t)enc_D6(vd[1]) << 5)
                             |  (uint16_t)enc_D5(vd[2]);
                } else {
                    rgb[k]   = (rgb_has_input && !blend_texel_alpha)
                             ? rgba_to_565((int)cr, (int)cg, (int)cb) : 0xFFFF;
                    rgb_d[k] = 0;
                }
            } else {
                light[k] = (textured && has_color) ? shade_row(cr, cg, cb) : 0;
            }
        }

        /* Slope-scaled decal offset (see DECAL_SLOPE_PIXELS).  Fit the depth
         * plane z(x,y) to the 3 screen verts, take the per-pixel gradient, and
         * push the whole decal toward the camera (larger depth = nearer) by
         * DECAL_SLOPE_PIXELS * |gradient| — so grazing-angle ground shadows win
         * the z-test where the flat DECAL_Z_BIAS floor is dwarfed.  The constant
         * floor (applied per-vertex above) handles the flat-on case; this adds
         * the angle-dependent term on top. */
        if (g_decal) {
            float p0x = x[0] * (1.0f / 16.0f);     /* Q12.4 subpixel -> pixels */
            float p1x = x[1] * (1.0f / 16.0f);
            float p2x = x[2] * (1.0f / 16.0f);
            float e1x = p1x - p0x, e1y = (float)(y[1] - y[0]), e1z = (float)(depth[1] - depth[0]);
            float e2x = p2x - p0x, e2y = (float)(y[2] - y[0]), e2z = (float)(depth[2] - depth[0]);
            if (g_subpix_tri) { e1y *= 1.0f / 16.0f; e2y *= 1.0f / 16.0f; }
            float area = e1x * e2y - e2x * e1y;
            if (fabsf(area) > 0.5f) {              /* skip degenerate/edge-on tris */
                float inv  = 1.0f / area;
                float dzdx = (e1z * e2y - e2z * e1y) * inv;
                float dzdy = (e1x * e2z - e2x * e1z) * inv;
                float slope = fabsf(dzdx);
                float ady   = fabsf(dzdy);
                if (ady > slope) slope = ady;
                float bias = DECAL_SLOPE_PIXELS * slope;
                for (int k = 0; k < 3; k++) {
                    float d = (float)depth[k] + bias;
                    if (d > 2147483520.0f) d = 2147483520.0f;
                    depth[k] = (int32_t)d;
                }
            }
        }

        if (g_truecolor) {
            if (shade_mask) {
                g_alpha_mask = 1;
                emit_tri_state(textured);
                of_gpu_draw_vert_tri_rgb(x, y, s, t, zi, shade_rgb,
                                         gpu_q29_word(zi), depth, NULL);
                g_vc_words += 18u;
                g_alpha_mask = 0;
                emit_tri_state(textured);
            }
            of_gpu_draw_vert_tri_rgb(x, y, s, t, zi, rgb, gpu_q29_word(zi), depth,
                                     cd_on ? rgb_d : NULL);
            g_vc_words += cd_on ? 20u : 18u;
        } else {
            of_gpu_draw_vert_tri(x, y, s, t, zi, light);
            g_vc_words += 15u;
        }
    }
    g_vc_tris_legacy += (uint32_t)buf_vbo_num_tris;
    /* Publish this batch so the GPU rasterizes it while the CPU builds the next. */
    of_gpu_kick();
}

/* ================================================================
 * 2D rects
 * ================================================================ */

static void gpu_fill_rect(int x0, int y0, int x1, int y1, const uint8_t *rgba) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    if (!g_has_gpu) return;
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > SCR_W) x1 = SCR_W;
    if (y1 > SCR_H) y1 = SCR_H;
    if (x1 <= x0 || y1 <= y0) return;
    uint16_t color = rgba_to_565(rgba[0], rgba[1], rgba[2]);
    if (g_truecolor && (uint8_t)color != (uint8_t)(color >> 8)) {
        /* The clear command repeats a byte. Other RGB565 colors use two
         * opaque, depth-independent triangles with a constant shade. */
        of_gpu_tri_state_t st;
        memset(&st, 0, sizeof(st));
        st.fb_base = g_draw_fb;
        st.fb_major_step = FB_STRIDE * 2;
        st.fb_minor_step = 2;
        st.tex_addr = g_white_addr;
        st.tex_width = 1;
        st.flags = OF_GPU_SPAN_TRUECOLOR;
        st.clip_x1 = SCR_W; st.clip_y1 = SCR_H;
        of_gpu_set_tri_state(&st);
        const int16_t xy[4][2] = {{x0*16,y0},{x1*16,y0},{x1*16,y1},{x0*16,y1}};
        const unsigned idx[2][3] = {{0,1,2},{0,2,3}};
        const int32_t zero[3] = {0,0,0}, zi[3] = {65536,65536,65536};
        const uint16_t rgb[3] = {color,color,color};
        for (int t = 0; t < 2; t++) {
            int16_t x[3], y[3];
            for (int k = 0; k < 3; k++) { x[k]=xy[idx[t][k]][0]; y[k]=xy[idx[t][k]][1]; }
            of_gpu_draw_vert_tri_rgb(x,y,zero,zero,zi,rgb,0,zero,NULL);
        }
        g_st_cache_valid = 0;
    } else {
        /* Preserve the fast clear for byte-uniform colors, including white. */
        uint8_t c = g_truecolor ? (uint8_t)color : rgb332(rgba[0], rgba[1], rgba[2]);
        uint32_t addr = g_draw_fb +
                        ((uint32_t)y0 * FB_STRIDE + (uint32_t)x0) * (uint32_t)g_fb_bpp;
        of_gpu_clear_rect_strided(addr, (uint16_t)((x1 - x0) * g_fb_bpp),
                                  (uint16_t)(y1 - y0),
                                  (uint16_t)(FB_STRIDE * g_fb_bpp), c);
    }
}

static void gpu_tex_rect(int x0, int y0, int x1, int y1, float u0, float v0,
                         float dudx, float dvdy, const uint8_t *rgba) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    if (!g_has_gpu || !g_cur_tex[0] || !g_cur_tex[0]->ci8) return;
    if (x1 <= x0 || y1 <= y0) return;

    int modulate = g_cur_shader && g_cur_shader->cc.num_inputs;
    uint8_t row = 0;          /* palettized: shade row */
    uint16_t vc = 0xFFFF;     /* truecolor: per-vertex RGB565 = prim colour */
    if (g_truecolor)
        vc = modulate ? rgba_to_565(rgba[0], rgba[1], rgba[2]) : 0xFFFF;
    else
        row = modulate ? shade_row(rgba[0], rgba[1], rgba[2]) : 0;

    /* 2D rect blend: the prim/env colour's alpha is the surface alpha. */
    g_surf_alpha = modulate ? rgba[3] : 255;

    g_cd_active = 0;   /* 2D blit: never the texel*C+D combine path */
    g_subpix_tri = 0;  /* 2D rects: integer pixel-aligned Y, not Q12.4 */
    emit_tri_state(1); /* shared g_st_cache memo owns emission coherence */

    /* corner texel coords (texel units, like gfx_soft tex_rect) */
    float uL = u0,                  uR = u0 + dudx * (x1 - x0);
    float vT = v0,                  vB = v0 + dvdy * (y1 - y0);
    const int32_t zi1 = 1 << 16;

    int16_t X[4] = { (int16_t)(x0 * 16), (int16_t)(x1 * 16),
                     (int16_t)(x1 * 16), (int16_t)(x0 * 16) };
    int16_t Y[4] = { (int16_t)y0, (int16_t)y0, (int16_t)y1, (int16_t)y1 };
    int32_t S[4] = { (int32_t)lrintf(uL * 65536.0f), (int32_t)lrintf(uR * 65536.0f),
                     (int32_t)lrintf(uR * 65536.0f), (int32_t)lrintf(uL * 65536.0f) };
    int32_t T[4] = { (int32_t)lrintf(vT * 65536.0f), (int32_t)lrintf(vT * 65536.0f),
                     (int32_t)lrintf(vB * 65536.0f), (int32_t)lrintf(vB * 65536.0f) };

    int16_t tx[3]; int16_t ty[3]; int32_t ts[3], tt[3], tzi[3];
    uint8_t tl[3]; uint16_t trgb[3]; int32_t tdepth[3];
    const int idx[2][3] = { {0,1,2}, {0,2,3} };
    for (int q = 0; q < 2; q++) {
        for (int k = 0; k < 3; k++) {
            int i = idx[q][k];
            tx[k] = X[i]; ty[k] = Y[i];
            ts[k] = S[i]; tt[k] = T[i]; tzi[k] = zi1;
            tl[k] = row; trgb[k] = vc;
            tdepth[k] = 0x40000000;   /* constant near depth (2D overlay) */
        }
        if (g_truecolor) {
            of_gpu_draw_vert_tri_rgb(tx, ty, ts, tt, tzi, trgb, 0u, tdepth, NULL);
            g_vc_words += 18u;
        } else {
            of_gpu_draw_vert_tri(tx, ty, ts, tt, tzi, tl);
            g_vc_words += 15u;
        }
        g_vc_tris_legacy++;
    }
}

/* ================================================================
 * Frame hooks
 * ================================================================ */

static void gpu_init(void)        { /* boot happens in gfx_gpu_boot via the WM */ }
static void gpu_on_resize(void)   { }

/* The kernel's acquire waits only ~5 ms for the flip fence, then queues the
 * buffer itself: a slow GPU would present an unfinished frame, and a MiSTer
 * system menu that pauses a queued CMD_FLIP would let rendering fill the
 * command ring and trap.  Wait for the fence first.  The watchdog counts
 * active vblanks, so a paused menu never trips it. */
static void gpu_wait_flip_fence(uint32_t token) {
    uint32_t last, active = 0, spins = 0;

    if (of_gpu_fence_reached(token))
        return;
    last = of_video_vblank_count();
    while (!of_gpu_fence_reached(token)) {
        gfx_gpu_wait_service();
        if (++spins < 4096u)
            continue;
        spins = 0;
        uint32_t now = of_video_vblank_count();
        active += now - last;
        last = now;
        if (active >= 300u)
            __builtin_trap();   /* wedged GPU: trap so its state is dumped */
    }
}

static void gpu_start_frame(void) {
    if (!g_has_gpu) return;
    /* Deferred flip-acquire.  gfx_gpu_present() submits the flip and returns
     * WITHOUT waiting; the wait for the next free draw buffer happens here, at
     * the top of the following frame.  Everything the CPU does in between --
     * the audio pump, the 30 Hz pacing, and the whole of the next frame's game
     * logic -- now overlaps that wait instead of running after it.
     *
     * Safe because nothing between the flip and this point draws: g_draw_fb
     * still names the just-flipped buffer, and the only writers of it
     * (emit_tri_state / the clears below / fill_rect / tex_rect) are reachable
     * solely from gfx_run_dl, which starts after this function.  A dropped
     * frame returns from gfx_run before both this and the present, so the
     * pending flag simply carries to the next rendered frame. */
    if (g_flip_pending) {
        {
        SM64_PERF_SCOPE(SM64_PERF_WAIT);
        PROF_ACQ_BEGIN();
        gpu_wait_flip_fence(g_flip_token);
        /* The fence orders every prior queued repair before this kernel
         * wait, whose implementation cannot call our application hook. */
        gfx_gpu_queued_front_service();
        g_draw_idx = of_video_acquire_next(g_draw_idx, g_flip_token);
        PROF_ACQ_END();
        }
        g_draw_fb = (uint32_t)(uintptr_t)of_video_buffer_addr(g_draw_idx);
        g_flip_pending = 0;
        if (!g_fb_active) {
            g_fb_active = 1;
            of_video_set_display_mode(OF_DISPLAY_FRAMEBUFFER);
        }
    }
    /* Natural unsigned wrap: unlike g_tex_epoch, zero is a valid serial.
     * Dropped frames do not enter this function and do not age the cooldown. */
    ++g_cram_vertex_frame;
    if (++g_tex_epoch == 0) {
        g_tex_epoch = 1;
        gpu_tex_clear_epochs();
    }
    g_st_cache_valid = 0;   /* draw buffer rotated; force a fresh tri-state
                             * (shared memo: covers BOTH 0x4A emit paths) */
    gpu_material_changed();   /* refresh the eligibility memo */
    g_vc_vp_valid = 0;      /* fresh sticky 0x50 each frame */
    g_vc_kick = 0;
    /* Colour clear only the first time each draw buffer is used.  SM64 covers
     * every pixel itself, as on the N64 (which never cleared the colour
     * buffer): skybox or full-screen background fill, screen borders, and
     * clear_viewport()/clear_frame_buffer() whenever no area renders.  The
     * per-frame clear cost the GPU-bound scenes 4-7% (Pocket os30 model:
     * castle 22.5 -> 23.7 FPS, Bowser 18.9 -> 20.2; every compared frame
     * byte-identical).  Build with -DGFX_GPU_CLEAR_COLOR=1 to restore it. */
#ifndef GFX_GPU_CLEAR_COLOR
#define GFX_GPU_CLEAR_COLOR 0
#endif
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    static uint8_t cleared_bufs;
    if (GFX_GPU_CLEAR_COLOR || !(cleared_bufs & (1u << (g_draw_idx & 7)))) {
        cleared_bufs |= (uint8_t)(1u << (g_draw_idx & 7));
        of_gpu_clear_rect_strided(g_draw_fb, (uint16_t)(SCR_W * g_fb_bpp), SCR_H,
                                  (uint16_t)(FB_STRIDE * g_fb_bpp), 0);
    }
    of_gpu_clear_rect_strided((uint32_t)(uintptr_t)g_zbuf,
                              SCR_W * ZBUF_ELEM, SCR_H, SCR_W * ZBUF_ELEM, 0);
    /* Let the GPU clear both surfaces while the CPU processes vertices. */
    of_gpu_kick_now();
}

static void gpu_end_frame(void)    {
    SM64_PERF_SCOPE(SM64_PERF_SEND); if (g_has_gpu) of_gpu_kick(); }
static void gpu_finish_render(void) { }
static void gpu_shutdown(void) {
    if (g_has_gpu) {
        of_gpu_shutdown(); /* drains all draws before restoring route */
        _of_gpu_route_fast_tex(0);
        g_fast_tex_active = 0;
    }
    while (g_extra_shaders) {
        struct ShaderProgram *next = g_extra_shaders->next;
        free(g_extra_shaders);
        g_extra_shaders = next;
    }
}

struct GfxRenderingAPI gfx_gpu_api = {
    gpu_z_is_from_0_to_1,
    gpu_unload_shader,
    gpu_load_shader,
    gpu_create_and_load_new_shader,
    gpu_lookup_shader,
    gpu_shader_get_info,
    gpu_new_texture,
    gpu_select_texture,
    gpu_upload_texture,
    gpu_set_sampler_parameters,
    gpu_set_depth_test,
    gpu_set_depth_mask,
    gpu_set_zmode_decal,
    gpu_set_viewport,
    gpu_set_scissor,
    gpu_set_use_alpha,
    gpu_draw_triangles,
    gpu_init,
    gpu_on_resize,
    gpu_start_frame,
    gpu_end_frame,
    gpu_finish_render,
    gpu_fill_rect,
    /* tex_rect = NULL: gpu_tex_rect feeds 2D glyph rects through the os30
     * vert-tri plane-derive, which transposes/flips axis-aligned rects (menu
     * text came out upside-down/stretched; a magnitude fix had no effect ->
     * not a precision bug).  With this NULL, gfx_dp_texture_rectangle takes its
     * fallback (gfx_pc.c:1615) -> gfx_draw_rectangle -> two gfx_sp_tri1 ->
     * gpu_draw_triangles: the SAME proven triangle pipeline that renders the
     * SCORE/COPY/ERASE/STEREO labels (and that the SW/OG renderer is equivalent
     * to).  Costs a vertex-transform per texrect vs the direct blit; text/HUD
     * are few quads so the impact is negligible. */
    NULL,
    gpu_set_fog_color,
    gpu_shutdown,
    gpu_upload_texture_rgba16,   /* RGBA16 direct upload (skips the RGBA8888 hop) */
    gpu_set_alpha_cvg_sel,       /* alpha-as-coverage: keep discarding texel 0 */
};
