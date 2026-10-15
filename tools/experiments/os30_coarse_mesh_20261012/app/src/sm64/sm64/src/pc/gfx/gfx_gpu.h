#ifndef GFX_GPU_H
#define GFX_GPU_H

/*
 * gfx_gpu.h -- openfpgaOS hardware (os30) GPU rendering backend for SM64.
 *
 * Implements GfxRenderingAPI by emitting of_gpu vertex-triangle commands
 * (CMD_SET_TRI_STATE 0x4A + CMD_DRAW_VERT_TRI 0x4B).  Output is an 8-bit
 * indexed (RGB332) framebuffer shaded through a 64-row palookup colormap.
 *
 * gfx_gpu owns all of_gpu.h state (that header must be included from a
 * single TU), so the window-manager backend drives boot/present through
 * the small surface below rather than touching of_gpu directly.
 */

#include "gfx_rendering_api.h"
#include "async_mesh_repair.h"

extern struct GfxRenderingAPI gfx_gpu_api;

/* Called once after of_video_init(): of_gpu_init, palette + colormap
 * upload, z-buffer alloc, first draw-buffer acquire. */
void gfx_gpu_boot(void);

/* Present the frame just drawn: GPU-triggered flip + acquire next draw
 * buffer (triple-buffered).  Called from the WM swap_buffers hook. */
void gfx_gpu_present(void);

/* ---- GPU vertex-cache fast path (0x56 LOAD_VERT_CLIP + 0x54 DRAW_INDEXED_TRI)
 *
 * gfx_pc.c owns vertex IDENTITY (the G_VTX slot index dies before buf_vbo), so
 * it drives this path; the backend owns MATERIAL policy (truecolor/HILITE/
 * decal classification, rgb565 encoding, the sticky 0x50 viewport + 0x4A
 * surface state).  Protocol, per candidate triangle:
 *
 *   1. gfx_gpu_vtx_cache_begin() — per-batch eligibility (memoized until any
 *      material state changes; truecolor only, no HILITE/cd, no decal, no
 *      blend/XLU).  Returns 0 -> use the flattened 0x4E path.
 *      Nonzero return carries GFX_VC_TEXTURED (fill u/v with the final
 *      tile-relative texel coords, exactly as the 0x4E path would emit them).
 *      *rgb_input   = which color input (0-based) is the per-vertex shade,
 *                     -1 = texel rides unmodulated (backend sends 0xFFFF).
 *      *alpha_input = which color input carries the surface alpha (resolved
 *                     from the ALPHA-cycle mapping), -1 = opaque 255.
 *   2. gfx_gpu_vtx_cache_tri(v, slot, dirty_mask) — re-derives the sticky
 *      0x50/0x4A per tri, dedup'd against the SHARED last-emitted memo that
 *      the legacy path also writes (single source of truth: no path-local
 *      emission edge), uploads the dirty slots (0x56, clip x/y/w + s/t
 *      Q16.16 + rgb565 + explicit (1/w)*2^30 depth) and draws the 1-word
 *      0x54.
 *
 * Caller guarantees: slots < 32, 0 < w < 32767 (Q16.16 fit), and that any
 * buffered 0x4E triangles were flushed first (ring draw order). */
#define GFX_VC_TEXTURED  2

struct gfx_vc_vtx {
    float cx, cy, cw;     /* clip-space x,y,w (LoadedVertex x/y/w) */
    float w_inv;          /* reciprocal already computed by the vertex loader */
    float u, v;           /* final tile-relative texels (0 when untextured) */
    uint8_t r, g, b;      /* resolved shade input, 0..255 (unused when white) */
    uint8_t a;            /* resolved surface alpha (v[0] only is consumed) */
    uint32_t exact_depth;
    uint8_t use_exact_depth;
};

/* Preserve clip coordinates for every fallback triangle. Shared vertices must
 * project identically across material changes, clipping and cached draws. */
#define GFX_VC_MAX_BUFFERED 256   /* must equal gfx_pc.c's MAX_BUFFERED */
struct gfx_vc_true_xyw {
    float x[3], y[3], w[3];
    uint8_t valid;
    uint32_t exact_depth[3];
    uint8_t depth_mask;
};
extern struct gfx_vc_true_xyw gfx_vc_true[GFX_VC_MAX_BUFFERED];

int  gfx_gpu_vtx_cache_begin(int *rgb_input, int *alpha_input);
void gfx_gpu_vtx_cache_tri(const struct gfx_vc_vtx v[3], const uint8_t slot[3],
                           unsigned dirty_mask);
/* GPU-ready words after the load command header and slot number.
 * Only the bounded CPU run uses these; clipped/direct records stay floats. */
struct gfx_vc_prepared {
    int32_t x, y, w, u, v;
    uint32_t rgb, depth;
};

/* CPU-only unchanged-state run. Each job packs slots at bits0/5/10 and
 * its ORIGINAL per-corner dirty mask at bit15. Only dirty payloads are read.
 * Caller keeps material, viewport, alpha and all32slots fixed during the run. */
void gfx_gpu_vtx_cache_batch(const struct gfx_vc_prepared vertices[32],
                             const uint32_t *jobs, unsigned count, uint8_t alpha);
/* Experimental BIM1. Only current raw LOAD rows may be imported. CPU culling
 * and canonical vertices remain unchanged. All imports precede this immutable
 * run's draws; RELEASE then lets the next LOAD overlap GPU rasterization. */
#define GFX_BRAM_TEXTURED  (1u << 16)
#define GFX_BRAM_HALF_TEXEL (1u << 17)
#define GFX_BRAM_SOURCE_RGB (1u << 18)
struct gfx_bram_import {
    uint32_t generation, slots;
    int32_t u_offset, v_offset;
    uint32_t flags_rgb;
    unsigned first;
};
/* QGF1 immutable queued run. Indices are raw-local; context is copied into
 * the ordered command stream. Snapshot ownership spans runs until release. */
int gfx_gpu_queued_front_supported(void);
int gfx_gpu_queued_front_acquire(uint32_t generation);
void gfx_gpu_queued_front_release(void);
void gfx_gpu_queued_front_wait_source(void);
void gfx_gpu_queued_front_service(void);
void gfx_gpu_queued_front_batch(const uint32_t *jobs, unsigned count,
                                uint8_t alpha, unsigned cull,
                                const struct gfx_bram_import *context);
extern uint32_t gfx_queued_stats[16];
int gfx_gpu_bram_import_supported(void);
int gfx_gpu_bram_import_available(void);
int gfx_gpu_bram_import_acquire(uint32_t generation);
void gfx_gpu_bram_import_wait(void);
void gfx_gpu_vtx_cache_import_batch(const struct gfx_vc_prepared vertices[32],
                                    const uint32_t *jobs, unsigned count, uint8_t alpha,
                                    const struct gfx_bram_import *import);
/* Private immutable cached ELF Vtx source to LOD5 CRAM byte offset.
 * Caller must drain LOAD before a miss can upload; zero returns CPU fallback. */
int gfx_gpu_cram_vertex_source(const void *source,unsigned count,uint32_t *offset);
uint32_t gfx_gpu_cram_source_generation(void);
void gfx_async_forget_sources(void);
extern uint32_t gfx_async_source_stats[4];
/* MTF1 uses immutable original TRI words beside the existing Vtx source
 * arena. Both returned offsets belong to the same retained pool generation. */
int gfx_gpu_mesh_sources(const void *vertices,unsigned count,const void *commands,
                         unsigned command_count,uint32_t *vertex_offset,
                         uint32_t *command_offset);
int gfx_gpu_mesh_supported(void);
void gfx_gpu_mesh_wait_source(void);
void gfx_gpu_mesh_packet(uint32_t serial,uint8_t alpha,
                         const struct gfx_bram_import *context);
extern unsigned sm64_mesh_front_capability;
extern uint32_t gfx_mesh_stats[16];
/* AJ01 owns only immutable raw inputs and dead canonical slots. Material and
 * viewport commands precede each job in the same ordered ingress stream. */
int gfx_gpu_async_supported(void);
/* Conservative topology routing feedback after a completed async repair. */
void gfx_async_note_repair(const void *commands);
extern uint32_t gfx_async_hint_stats[4];
void gfx_gpu_async_service(void);
void gfx_gpu_async_drain(void);
int gfx_gpu_async_raw_acquire(void);
void gfx_gpu_async_raw_release(void);
/* Nonemitting canonical copy, invoked only at a completed legacy LOAD boundary. */
void gfx_async_materialize_raw(void);
void gfx_gpu_async_before_wait(void);
void gfx_gpu_async_raw_release_if_pending(void);
void gfx_gpu_async_job(uint32_t vertex_offset,uint32_t vtx_word,
                      uint32_t command_offset,unsigned command_count,unsigned cull,
                      const void *vertices,const void *commands,
                      const struct am_transform *transform,uint8_t alpha,
                      const struct gfx_bram_import *material);
int gfx_gpu_async_group_supported(void);
int gfx_gpu_async_group_upload(const uint32_t *descriptors,unsigned members,
                              uint32_t generation,uint32_t *offset);
void gfx_gpu_async_group(uint32_t descriptor_offset,unsigned members,
                        const struct am_transform *transform,uint8_t alpha,
                        const struct gfx_bram_import *material);
extern unsigned sm64_async_front_capability;
extern unsigned sm64_async_group_capability;
extern uint32_t gfx_async_stats[16];
extern uint32_t gfx_async_group_stats[8];

int gfx_gpu_geometry_enabled(void);
void gfx_gpu_geometry_tri(const struct gfx_vc_vtx v[3], const uint8_t slot[3],
                         unsigned dirty, const int16_t *pos[3],
                         const int32_t *matrix[3], const uint32_t serial[3], unsigned cull);

/* Software batching uses the existing 1..3-vertex 0x59 commands. The caller
 * packs each dirty slot once and preserves triangle order and surface alpha. */
struct gfx_geometry_upload {
    uint32_t words[5];
    const int32_t *matrix;
    uint32_t serial;
};
void gfx_gpu_geometry_batch(const struct gfx_geometry_upload *upload, unsigned count,
                            const uint32_t *triangles, unsigned triangle_count,
                            uint8_t alpha);

/* Per-frame wire-cost counters (reset on read, like gpu_ring_prof_get):
 * words = ring words emitted by the draw path (0x4A/0x50/0x4E/0x4B/0x56/0x54),
 * ctris/ltris = triangles drawn via the cache (0x54) vs flattened (0x4E/0x4B),
 * loads = 0x56 vertex uploads. */
void gpu_vc_prof_get(unsigned *words, unsigned *ctris, unsigned *ltris,
                     unsigned *loads);

#endif /* GFX_GPU_H */
