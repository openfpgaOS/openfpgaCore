/*
 * Quake II openfpgaOS GPU submit layer.
 *
 * Only openfpga/of_emit_q2.c includes of_gpu.h. Renderer translation units use
 * this narrow mirror API so the SDK's file-static GPU ring state has exactly
 * one owner.
 */

#ifndef OPENFPGA_OF_EMIT_Q2_H
#define OPENFPGA_OF_EMIT_Q2_H

#include <stdint.h>

#define OFQ2_EMIT_COLORMAP  (1 << 0)
#define OFQ2_EMIT_BLEND     (1 << 1)	/* OF_GPU_SPAN_BLEND: src-over by const_alpha */
#define OFQ2_EMIT_SKIP_ZERO (1 << 2)
#define OFQ2_EMIT_PERSP     (1 << 5)
#define OFQ2_EMIT_TRANSLUC  (1 << 6)
#define OFQ2_EMIT_TRUECOLOR (1 << 7)	/* OF_GPU_SPAN_TRUECOLOR: RGB565 direct color */

#define OFQ2_PARAM_ATTR_AFFINE    0
#define OFQ2_PARAM_ATTR_PERSP     1
#define OFQ2_PARAM_ATTR_SOLID     2
#define OFQ2_PARAM_ATTR_PERSP_Q29 3

#define OFQ2_PARAM_AXIS_X 0
#define OFQ2_PARAM_AXIS_Y 1

#define OFQ2_PARAM_Z_NONE       0
#define OFQ2_PARAM_Z_WRITE_ZI   1
#define OFQ2_PARAM_Z_TEST_ZI    2
#define OFQ2_PARAM_Z_TEST_WRITE 3

#define OFQ2_PARAM_SPAN_MAX_RECORDS 512u

typedef struct ofq2_param_span_record_s {
	uint16_t u;
	uint16_t v;
	uint16_t count;
} ofq2_param_span_record_t;

typedef struct ofq2_param_span_list_s {
	uint32_t fb_base;
	int32_t fb_major_step;
	int32_t fb_minor_step;

	uint32_t tex_addr;
	uint16_t tex_width;
	uint16_t tex_w_mask;
	uint16_t tex_h_mask;

	uint8_t flags;
	uint8_t colormap_id;
	uint8_t attr_mode;
	uint8_t span_axis;
	uint8_t z_mode;
	uint8_t q29_attr_shift;
	uint8_t reserved[2];

	int32_t attr_origin[3];
	int32_t attr_du[3];
	int32_t attr_dv[3];

	int32_t light_origin;
	int32_t light_du;
	int32_t light_dv;

	int32_t clamp_min[3];
	int32_t clamp_max[3];

	uint32_t z_base;
	int32_t z_major_step;
	int32_t z_minor_step;
} ofq2_param_span_list_t;

/* Mirror of of_texture_t (of_texture.h) — an opaque GPU texture handle the
 * renderer embeds in image_t. Layout MUST match of_texture_t; of_emit_q2.c
 * static-asserts it and casts between them. The renderer reads only `addr`
 * (the GPU byte address for tex_addr records) and treats the rest as opaque;
 * all state-mutating ops go through the OFQ2_Tex* wrappers below, because the
 * of_texture manager — like of_gpu — is header-only static and must be driven
 * from the single of_emit_q2.c translation unit. */
typedef struct ofq2_texture_s {
	uint16_t width;
	uint16_t height;
	uint32_t addr;      /* GPU byte address (fast offset or SDRAM GPU addr) */
	const void *src;
	uint8_t  flags;
} ofq2_texture_t;

/* Mirror of of_gpu_tri_state_t — sticky per-skin surface state for the
 * hardware vertex-triangle path (CMD_SET_TRI_STATE / CMD_DRAW_VERT_TRI).
 * The GPU derives all four planes from the raw per-vertex values, so
 * there is no attr/light plane or q29 shift here. */
typedef struct ofq2_tri_state_s {
	uint32_t fb_base;
	int32_t fb_major_step;
	int32_t fb_minor_step;

	uint32_t tex_addr;
	uint16_t tex_width;
	uint16_t tex_w_mask;
	uint16_t tex_h_mask;

	uint8_t flags;
	uint8_t colormap_id;
	uint8_t z_mode;

	int32_t clamp_min[2];
	int32_t clamp_max[2];

	uint32_t z_base;
	int32_t z_major_step;
	int32_t z_minor_step;

	int16_t clip_x0, clip_x1;
	int16_t clip_y0, clip_y1;

	/* sticky attr mode: 0 = PERSP (default; REQUIRED for the 0x4B
	 * vert-tri path, whose derivation emits plain persp planes),
	 * 1 = PERSP_Q29 (for the 0x4D records path with CPU-solved Q29
	 * planes; the per-triangle shift rides in the 0x4D payload, only
	 * the MODE bit is sticky).  memset-zero keeps the old behavior. */
	uint8_t attr_q29;

	/* Truecolor extras — 0x4A control bits 28..31 plus payload word 16.
	 * These mirror the like-named of_gpu_tri_state_t fields ONE FOR ONE and
	 * MUST all be forwarded: the SDK builds the control word from them, so a
	 * field left unset here lands as a live GPU mode bit.  (Historic bug: the
	 * emit layer built its of_gpu_tri_state_t field-by-field on the stack and
	 * these five were never written, so mirror/combine/subpixel-Y were driven
	 * by whatever the stack held — random texture mirroring and a texel*C+D
	 * combine reading payload words that were never sent.) */
	uint8_t mirror_s, mirror_t;	/* G_TX_MIRROR-style reflect addressing */
	uint8_t const_alpha;		/* src alpha 0..255 for OFQ2_EMIT_BLEND */
	uint8_t cd_combine;		/* 1 = clamp(texel*C + D); needs the 22-word 0x4E */
	uint8_t subpix_y;		/* 1 = vertex y is Q12.4, not an integer scanline */
} ofq2_tri_state_t;

#define OFQ2_CAP_SPAN             (1u << 0)
#define OFQ2_CAP_PERSP            (1u << 1)
#define OFQ2_CAP_ALPHA            (1u << 2)
#define OFQ2_CAP_FLIP             (1u << 3)
#define OFQ2_CAP_PARAM_SPAN_LIST  (1u << 4)
#define OFQ2_CAP_PARAM_SPAN_Z     (1u << 5)
#define OFQ2_CAP_PARAM_SPAN_ZTEST (1u << 6)
#define OFQ2_CAP_Q29_SCALE        (1u << 7)
#define OFQ2_CAP_PARAM_TRI        (1u << 8)
#define OFQ2_CAP_VERT_TRI         (1u << 9)
#define OFQ2_CAP_PARAM_TRI_RECS   (1u << 10)
#define OFQ2_CAP_XFORM_TRI        (1u << 11)
#define OFQ2_CAP_VCOLOR           (1u << 12)	/* per-vertex RGB565 truecolor */
#define OFQ2_CAP_XFORM_RGB        (1u << 13)	/* 0x52 xform_tri_rgb truecolor cluster */
#define OFQ2_CAP_VERT_TRI_RGB     (1u << 14)	/* 0x4E vert_tri_rgb: screen-space truecolor tri */
#define OFQ2_CAP_CLIP_LOAD        (1u << 15)	/* 0x56 load_vert_clip + 0x54 draw_indexed_tri */
#define OFQ2_CAP_COMBINE          (1u << 16)	/* texel*C+D combiner (22-word 0x4E) */
#define OFQ2_CAP_GEOMETRY         (1u << 17)	/* packed raw geometry, 0x59 + 0x5B */
#define OFQ2_CAP_GEOMETRY_WORKER  (1u << 18)	/* independent worker: up to 32 raw vertices/packet */

/* Up to 32 packed vertices, five words each: slot, signed XY, signed Z/RGB,
 * S, T. Fan indices and culling are submitted in one ring reservation.
 * Large upload packets require the separately advertised worker capability;
 * inline geometry cores retain their three-vertex packet limit. */
int OFQ2_DrawGeometryFan(const uint32_t *vertices, unsigned count, unsigned cull);
/* Eight words per changed slot: slot, clip X/Y/W, S/T, RGB565, depth.
 * The packed index word includes the two hardware winding-rejection bits. */
int OFQ2_DrawClipIndexed(const uint32_t *vertices, unsigned count, uint32_t triangle);

/* Transform-stage object state (CMD_SET_OBJECT_STATE 0x50): sticky matrix +
 * projection consts.  Mirrors of_gpu_object_state_t.  Emitted on top of an
 * OFQ2_SetTriState() surface; feeds OFQ2_DrawXformTri(). */
typedef struct {
	int32_t matrix[5][4];	/* row-major Q16.16; rows 0-2 cam, 3-4 s/t (N=5) */
	int32_t xcenter, ycenter;
	int32_t xscale, yscale;
	int32_t near_clip;	/* Q16.16 min cam.z */
	uint8_t rows;		/* 3 (alias) or 5 (world) */
	uint8_t q29_en;		/* world: Q29-scale the derived planes */
	uint8_t q29_shift;	/* CPU conservative magnitude shift (0..31) */
} ofq2_object_state_t;

int OFQ2_Init(int fb_stride);
void OFQ2_Shutdown(void);
int OFQ2_Supports(uint32_t cap);
/* 1 if the GPU has dedicated fast texture memory (CRAM); 0 = SDRAM-only, where
 * GPU textures consume the app heap in place. */
int OFQ2_HasFastTexMem(void);

void OFQ2_BindFramebuffer(uint8_t *fb, int stride);
void OFQ2_Kick(void);
/* Publish immediately; leaves pending work for the existing Finish barriers. */
void OFQ2_KickNow(void);
void OFQ2_Finish(void);
void OFQ2_PrepareForCPU(void);
void OFQ2_TextureCacheFlush(void);

/* GPU-queued page flip (CMD_FLIP): returns the fence token to pass to
 * of_video_acquire_next(). Call OFQ2_NotifyFlipRetired() once acquire
 * succeeds — the retired flip proves all earlier raster work retired. */
uint32_t OFQ2_FlipTo(int idx);
void OFQ2_NotifyFlipRetired(void);

void OFQ2_CacheClean(const void *addr, uint32_t size);
void OFQ2_CacheCleanOnce(const void *addr, uint32_t size);
void OFQ2_CacheCleanForget(const void *addr, uint32_t size);

int OFQ2_DrawParamSpanList(const ofq2_param_span_list_t *params,
		const ofq2_param_span_record_t *records, uint32_t record_count);

/* Hardware triangle edge walker (CMD_DRAW_PARAM_TRI): same plane/
 * control/z header as a span list, but the GPU generates the
 * per-scanline records from three vertices. vx[] is signed Q12.4
 * subpixel x, vy[] is the signed integer scanline. Clips to
 * [clip_x0, clip_x1) x [clip_y0, clip_y1), ceil fill both edges.
 * One triangle = 37 ring words. Gate on OFQ2_CAP_PARAM_TRI.
 *
 * When OFQ2_CAP_PARAM_TRI_RECS is present and the attr mode is
 * PERSP/PERSP_Q29, this transparently lowers to the records-only form
 * (0x4A sticky surface state + 17-word 0x4D per triangle) and only
 * re-arms the 0x4A when a surface-relevant field or the clip rect
 * changed — the cmdlog/stat/return contract is unchanged. */
int OFQ2_DrawParamTri(const ofq2_param_span_list_t *params,
		const int16_t vx[3], const int16_t vy[3],
		int16_t clip_x0, int16_t clip_x1,
		int16_t clip_y0, int16_t clip_y1);

/* ---- Fast-texture management (wraps of_texture.h; driven from of_emit_q2.c).
 * Portable: on a target without dedicated fast texture memory (e.g. MiSTer,
 * tex_fast_size == 0) every texture stays in SDRAM and `addr` is the same
 * GPU-space value the renderer derived from raw pointers before — no #ifdef. */

/* Once, after OFQ2_Init()/GPU init (OFQ2_Init calls this internally). */
void OFQ2_TexInit(void);

/* Per-level reset of the fast allocator + colormap; bumps the generation so
 * stale handles can be detected and lazily re-created. Call after D_FlushCaches. */
void OFQ2_TexReset(void);

/* Current generation; image_t stores the gen its ftex was built in so a
 * cache-hit image whose gen is stale can be re-created (R_FindImage). */
uint32_t OFQ2_TexGeneration(void);

/* Create/refresh a STATIC texture handle from a contiguous pixel+mip block
 * (nbytes). Packs into fast memory if it fits, else points at SDRAM in place
 * (pixels must stay resident). Idempotent to call again after a reset. */
void OFQ2_TexCreate(ofq2_texture_t *t, const void *pixels,
		uint16_t w, uint16_t h, uint32_t nbytes);

/* Set the active colormap (palookup); co-locates it in fast + SDRAM and
 * programs GPU_PALOOKUP_BASE for the active domain. Replaces OFQ2_PalookupUpload
 * for the single-slot (vid_colormap) case. `cm` must be SDRAM. */
void OFQ2_TexSetColormap(const void *cm, uint32_t nbytes);

/* Route the GPU fetch domain to where this texture lives (fast vs SDRAM),
 * moving the colormap with it; drains only when the domain actually changes.
 * Call once per render phase that switches tiers (statics packed together =
 * one flip). Safe no-op on SDRAM-only targets. */
void OFQ2_TexBind(const ofq2_texture_t *t);

/* Domain-only variant for renderers that pack tex_addr directly into span/tri
 * RECORDS (and read of_texture_gpu_addr/.addr): routes the fetch domain + moves
 * the colormap, but emits NO SET_TEXTURE. Drains only on an actual tier change,
 * so it is a cheap no-op when called per-surface within one domain. */
void OFQ2_TexBindDomain(const ofq2_texture_t *t);

/* Route the fetch domain back to SDRAM (and the SDRAM colormap copy). Call
 * after a fast-memory render phase so subsequent SDRAM-addressed draws (e.g.
 * legacy raw-pointer tex_addr) fetch correctly. No-op on SDRAM-only targets. */
void OFQ2_TexDomainSDRAM(void);

/* Hardware vertex-triangle path (CMD_DRAW_VERT_TRI): the GPU derives
 * the four perspective planes (s*zi, t*zi, zi, light) from raw
 * per-vertex values — no CPU plane solve and no CPU edge walk. Set the
 * sticky surface state once per skin via OFQ2_SetTriState, then stream
 * triangles. OFQ2_SetTriState is idempotent: it re-emits the GPU state
 * only when the descriptor changed or another command type dirtied the
 * sticky state. Returns 0 (no draw) if the cap is absent.
 *
 * Per vertex: x Q12.4 subpixel, y integer scanline, s/t/zi signed
 * Q16.16 RAW, light Q6 colormap row (0..63). */
int OFQ2_SetTriState(const ofq2_tri_state_t *st);
int OFQ2_DrawVertTri(const int16_t x[3], const int16_t y[3],
		const int32_t s[3], const int32_t t[3],
		const int32_t zi[3], const uint8_t light[3]);

/* Transform-stage triangle (CMD_DRAW_XFORM_TRI 0x51): the GPU does the M*v
 * transform + perspective projection + plane derive.  Hand RAW model/world
 * verts (signed Q16.16) instead of screen verts.  Needs OFQ2_SetObjectState()
 * (matrix/proj) + OFQ2_SetTriState() (surface) armed.  s/t are passthrough for
 * rows==3 (alias); ignored (matrix-computed) for rows==5 (world).  light[] Q6
 * rows (0..63).  Returns 0 (no draw) if the cap is absent / state not armed. */
int OFQ2_SetObjectState(const ofq2_object_state_t *st);
/* Invalidate the 0x50 object-state shadow (call once per frame at the start of
 * the world tri pass) so the next SetObjectState re-arms the sticky HW. */
void OFQ2_ObjectStateReset(void);
int OFQ2_DrawXformTri(const int32_t vx[3], const int32_t vy[3],
		const int32_t vz[3], const int32_t s[3], const int32_t t[3],
		const uint8_t light[3]);
/* Truecolor sibling (0x52): per-vertex RGB565 instead of an 8-bit light row.
 * Same armed-state contract; gate on OFQ2_CAP_XFORM_RGB. */
int OFQ2_DrawXformTriRGB(const int32_t vx[3], const int32_t vy[3],
		const int32_t vz[3], const int32_t s[3], const int32_t t[3],
		const uint16_t rgb[3]);

/* Screen-space truecolor triangle (0x4E vert_tri_rgb): the CPU has already
 * projected to screen space. x Q12.4 subpixel, y integer scanline, s/t Q16.16
 * raw texel, zi=1/z Q16.16 (perspective), rgb[3] RGB565, depth=1/w*2^30 (z
 * test). Needs a 0x4A surface state armed; gate on OFQ2_CAP_VERT_TRI_RGB. */
/* Final 17-word 0x4E payload; all words are required. */
int OFQ2_DrawVertTriRGBPacked(const uint32_t words[17]);
/* Exclusive scope for one opaque alias draw. Only DrawVertTriRGBPacked may
 * issue commands until End; end before changing state, kicking, or waiting. */
void OFQ2_BeginOpaqueTriBatch(void);
void OFQ2_EndOpaqueTriBatch(void);
int OFQ2_DrawVertTriRGB(const int16_t x[3], const int16_t y[3],
		const int32_t s[3], const int32_t t[3], const int32_t zi[3],
		const uint16_t rgb[3], const int32_t depth[3]);
void OFQ2_ClearRect(uint8_t *dst, int stride, int w, int h, uint8_t color);

/* Upload a 64x256 colormap (16 KB) into GPU palookup slot 0-15 so span
 * lists with OFQ2_EMIT_COLORMAP can resolve palookup[light>>16][texel]
 * in hardware. Drains pending GPU work first; safe to call mid-frame. */
void OFQ2_PalookupUpload(int slot, const void *data, uint32_t size);

/* Affine blit through one parametric span list (one record per row).
 * (s_origin, t_origin) and (sstep, tstep) are 16.16 texel coords into
 * `src` (`src_pitch` bytes per row).  When `skip_transparent` is set,
 * source bytes equal to 0xFF are discarded (ref_soft TRANSPARENT_COLOR
 * matches the GPU's SKIP_ZERO key).  Caller clips and cache-cleans the
 * source.  Returns rows submitted (0 = caller must use the CPU path). */
int OFQ2_StretchBlit(uint8_t *dst, int dst_stride, int dst_w, int dst_h,
		const void *src, int src_pitch,
		int32_t s_origin, int32_t t_origin,
		int32_t sstep, int32_t tstep,
		int skip_transparent);
int OFQ2_Blit(uint8_t *dst, int dst_stride, int w, int h,
		const void *src, int src_pitch, int skip_transparent);

int OFQ2_HasPendingWork(void);

/* dump the last 8 GPU submissions (used by the .text watchdog) */
void OFQ2_CmdLogDump(void);

/* perf forensics, read+reset by the renderer heartbeat */
extern uint32_t ofq2_stat_sync_us;
extern uint32_t ofq2_stat_lists;
extern uint32_t ofq2_stat_records;
extern uint8_t ofq2_sync_tag;	/* 0 cpu-draw, 1 2d, 2 turb, 3 present */
extern uint32_t ofq2_stat_sync_site[4];
extern uint32_t ofq2_stat_2d;
extern uint32_t ofq2_stat_verttri;
extern uint32_t ofq2_stat_trirecs;	/* records-only (0x4D) triangles */
extern uint32_t ofq2_stat_tristate;	/* sticky 0x4A (re-)arms emitted */
extern uint32_t ofq2_stat_2d_us;
void OFQ2_RingStats(uint32_t *waits, uint32_t *spins,
	uint32_t *dma_waits, uint32_t *dma_spins);

#if OFQ2_GPU_PROFILE
void OFQ2_GPUProfileToggle(void);
void OFQ2_GPUProfileFrame(int gameplay);
const char *OFQ2_GPUProfileLine(unsigned line);
#endif

#endif
