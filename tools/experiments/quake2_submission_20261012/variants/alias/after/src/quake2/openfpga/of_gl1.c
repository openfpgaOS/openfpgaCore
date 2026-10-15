/*
 * of_gl1.c -- openfpgaOS GL platform + mini-GL for the gl1 (GLES1) renderer.
 *
 * The Pocket has no OpenGL. gl1 built with -DYQ2_GL1_GLES resolves every gl*
 * through gladLoadGLES1Loader(RI_GetProcAddress); we return our OWN functions
 * that draw directly. gl1's call sites never change. The command ring is owned
 * by of_emit_q2.c; this file also provides the RI_* window/context glue
 * the pristine, SDL_GL-based gl1_sdl.c used to (gl1_sdl.c is NOT compiled).
 *
 * Framebuffer is RGB565 truecolor, 320x240.
 *
 * 3D (world, models, sky, sprites, particles) goes to the OFQ2 triangle GPU:
 * the CPU transforms + projects to screen space and emits CMD_DRAW_VERT_TRI_RGB
 * (0x4E) under a sticky CMD_SET_TRI_STATE (0x4A) surface state; the GPU derives
 * the perspective planes, samples RGB565 texels and does the z test. Texture
 * data lives in the core's dedicated fast texture memory when it has any.
 * See ofgl_emit_ctri for the two things that path is fussy about: every GPU
 * texture must be power-of-two (the sampler wraps with a bitmask) and the
 * per-vertex 1/z must be scaled per triangle (the plane derive keeps
 * significant bits in proportion to the attribute magnitude).
 *
 * 2D (HUD, menu, console) stays on the CPU: it is all axis-aligned rectangles
 * in screen space, so ofgl_blit_rect writes them through the uncached alias
 * after draining the GPU once per frame. The bulk fills the CPU used to do --
 * the framebuffer and z-buffer clears -- are GPU CLEAR_RECT commands.
 *
 * gl1 in GLES mode always hands textures over as RGBA8888 (local.h redefines
 * GL_COLOR_INDEX8_EXT -> GL_RGBA); glTexImage2D downconverts to RGB565 with
 * 0x0000 as the transparent key (matching OF_GPU_SPAN_SKIP_ZERO).
 *
 * Multitexturing is live on this build, so texture units are tracked
 * separately: gl1 drives unit 1 with the lightmap and unit 0 with the diffuse
 * texture, and only unit 0 is rasterised.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>		/* sinf/cosf/sqrtf (routed to hw by of_mathf.h) */

#include <glad/glad.h>		/* GL types/constants + gladLoadGLES1Loader */

#include "common.h"		/* Com_Printf, qboolean, true/false */
#include "of_video.h"		/* of_video_flip, of_video_surface, palette */
#include "of_timer.h"		/* of_time_ms -- drives the auto-bisect phases */
#include "of_cache.h"		/* of_uncached -- display scans DRAM, not the D-cache */
#include "of_caps.h"
#include "openfpga/of_emit_q2.h"	/* OFQ2 triangle GPU (3D world, Stage 4) */
#include "openfpga/of_world.h"

/* gl1_image.c's master 256-color palette (RGBA, byte order R,G,B,A). */
extern unsigned d_8to24table[256];

/* gl1_main.c reads this (gl1_sdl.c normally owns it); no high-DPI on the
 * Pocket framebuffer, so it stays false. */
qboolean IsHighDPIaware = false;

/* Active framebuffer mode (set in RI_InitContext, reported by RI_GetDrawableSize). */
static int ofgl_fb_width = 320;
static int ofgl_fb_height = 240;
static int ofgl_fb_stride = 320;

/* ---- 3D (Stage 4) state: GL matrix stack + OFQ2 GPU. Declared up here so the
 * 2D blit can drain the GPU before compositing the HUD. ---- */
typedef float ofgl_mat4[16];		/* column-major (OpenGL order) */

/* BOTH matrix modes need a real stack. gl1 push/pops the PROJECTION around the
 * view model to give it its own r_gunfov, and if the pop is a no-op the world
 * projection is never restored -- everything drawn after the weapon (particles,
 * alpha surfaces) then projects through the gun's frustum. */
static ofgl_mat4 ofgl_proj_stack[4];
static int ofgl_proj_sp = 0;
static int ofgl_proj_over;		/* pushes past the stack, so pops stay paired */
static ofgl_mat4 ofgl_mv_stack[16];
static int ofgl_mv_sp = 0;
static int ofgl_mv_over;
static GLenum ofgl_mat_mode = GL_MODELVIEW;
#define OFGL_MV   (ofgl_mv_stack[ofgl_mv_sp])
#define ofgl_proj (ofgl_proj_stack[ofgl_proj_sp])

static int ofgl_vp_x = 0, ofgl_vp_y = 0, ofgl_vp_w = 320, ofgl_vp_h = 240;

static int ofgl_gpu_ok = 0;		/* OFQ2 up + a truecolor 3D path present */
static int ofgl_obj_dirty = 1;		/* projection constants must be rebuilt */
static unsigned ofgl_world_epoch = 1;
static float ofgl_draw_center[3], ofgl_draw_extent[3];
static int ofgl_draw_bounds_valid, ofgl_batch_inside;
static int ofgl_world_draw_valid, ofgl_batch_world;
static int ofgl_world_projection_enabled(void);
static int ofgl_draw_bounds_inside(void);
static cvar_t *ofgl_world_cv, *ofgl_geometry_cv;
static int ofgl_gpu_pending = 0;	/* GPU 3D queued; drain before any CPU 2D blit */
static uint8_t *ofgl_zbuf = NULL;	/* uncached z-buffer alias */
static uint8_t *ofgl_zraw;		/* allocation to release on vid_restart */
#define OFGL_Z_BYTES 2			/* GPU stores compressed 16-bit inverse depth */
static uint32_t ofgl_z_base = 0;
static int ofgl_z_major = 0;
/* CPU-projection constants (vert-tri path), recomputed on matrix change. */
static float ofgl_pc_xc, ofgl_pc_yc, ofgl_pc_xs, ofgl_pc_ys, ofgl_pc_near = 4.0f;

/* ---- GL raster state the 3D path actually consumes ---- */
static int ofgl_depth_test = 1;
static int ofgl_depth_mask = 1;		/* glDepthMask: 0 = test only, no z write */
static int ofgl_blend_on = 0;		/* glEnable(GL_BLEND) */
static int ofgl_cull_on = 0;		/* glEnable(GL_CULL_FACE) */
static GLenum ofgl_cull_face = GL_FRONT;	/* gl1's R_SetupGL default */
static GLenum ofgl_front_face = GL_CCW;		/* GL default; gl1 never changes it */
static float ofgl_depth_scale = 1.0f;		/* glDepthRange view-model hack */

/* Per-triangle perspective scale for the hardware plane derive (0x4B/0x4E).
 *
 * The GPU derives szi = (s*zi)>>16, tzi = (t*zi)>>16 and zi from the RAW
 * per-vertex values, and the derived gradient carries a FIXED 1/65536 ULP —
 * so the number of SIGNIFICANT gradient bits scales with the MAGNITUDE of the
 * derived attributes.  Handing it the natural zi = 1/z leaves 1/z at ~16..655
 * in Q16.16 over Quake2's 4..4096 unit depth range, i.e. ~7 significant bits,
 * and the interpolation error works out to (span_px/65536)*z texels: several
 * texels of swim on any surface past arm's reach.  That is the "textures are
 * wrong" artifact, and it gets worse with distance.
 *
 * The scale CANCELS in texel = interp(s*zi)/interp(zi), so it is free to vary
 * per triangle: pick K so the largest of |1/z|, |s/z|, |t/z| lands at
 * OFGL_ZI_TARGET.  8192 keeps the raw Q16.16 near 2^29 — inside the derive's
 * 33-bit difference saturation (which needs values under 16384) with a bit of
 * headroom.  Same construction SM64's gfx_gpu.c uses (ZI_DERIVE_TARGET). */
#define OFGL_ZI_TARGET   8192.0f
#define OFGL_ZI_FALLBACK 64.0f		/* degenerate (zero-magnitude) triangles */

static ofq2_texture_t ofgl_flat_tex;	/* tiny solid-gray tex for untextured (a) */
static int ofgl_flat_ready = 0;

/* ------------------------------------------------------------------------ *
 *  truecolor: RGB565 packing + blending (the 8-bit palette path is gone)
 * ------------------------------------------------------------------------ */

/* RGB components 0..255 -> RGB565. Quake/gl1 RGBA byte order is R,G,B,A. */
static inline uint16_t
ofgl_rgba_to_565(uint32_t rgba)
{
	uint32_t r = rgba & 0xff, g = (rgba >> 8) & 0xff, b = (rgba >> 16) & 0xff;
	return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

/* src over dst, a = 0..256 (256 = fully opaque). */
static inline uint16_t
ofgl_blend565(uint16_t s, uint16_t d, int a)
{
	int sr = (s >> 11) & 31, sg = (s >> 5) & 63, sb = s & 31;
	int dr = (d >> 11) & 31, dg = (d >> 5) & 63, db = d & 31;
	int r = (sr * a + dr * (256 - a)) >> 8;
	int g = (sg * a + dg * (256 - a)) >> 8;
	int b = (sb * a + db * (256 - a)) >> 8;
	return (uint16_t)((r << 11) | (g << 5) | b);
}

/* 0x0000 (black) is the transparent key (matches OF_GPU_SPAN_SKIP_ZERO); an
 * opaque black texel is nudged to this so it still draws. */
#define OFGL_TEX_TRANSPARENT 0x0000u
#define OFGL_TEX_BLACK       0x0008u

/* ------------------------------------------------------------------------ *
 *  2D renderer state
 * ------------------------------------------------------------------------ */

#define OFGL_MAX_TEX 4096

/* Lightmaps have a separate CPU-side store for per-vertex sampling; keep
 * their reserved texnum range out of the ordinary GPU texture allocator. */
#define OFGL_TEXNUM_LIGHTMAPS 1024
#define OFGL_TEXNUM_SCRAPS    1408

static inline int
ofgl_tex_skip(int texnum)
{
	return texnum >= OFGL_TEXNUM_LIGHTMAPS && texnum < OFGL_TEXNUM_SCRAPS;
}

/* ---- lightmaps -----------------------------------------------------------
 *
 * gl1 lights the world by MULTITEXTURING: unit 0 samples the wall texture,
 * unit 1 samples a 128x128 RGBA lightmap page, and the two are multiplied.
 * The GPU samples one texture per triangle, so we can't do that per pixel --
 * but the lightmap is low-frequency, so sampling it once PER VERTEX on the CPU
 * and handing the result to the GPU as the per-vertex modulation colour gives
 * Gouraud-interpolated lighting for the cost of one lookup per vertex.
 *
 * Without this the world renders fullbright. That is not just "a bit flat":
 * world textures are pre-scaled UP by r_intensity at upload (gl1's
 * R_LightScaleTexture) precisely because the lightmap is expected to scale them
 * back down, so an unlit world comes out uniformly BLAZING -- and correctly-lit
 * alias models then look nearly black next to it.
 *
 * Pages are kept in RGB565 at full resolution: per-vertex sampling lands on the
 * texel centres that gl1's own texcoords aim at (its +8 half-texel inset), and
 * halving the resolution would blend neighbouring surfaces packed into the same
 * page and seam their edges. Only pages the map actually uses get allocated. */
#define OFGL_LM_BLOCKS  128		/* MAX_LIGHTMAPS */
#define OFGL_LM_DIM     128		/* BLOCK_WIDTH / BLOCK_HEIGHT */
#define OFGL_LM_BUDGET  (640u * 1024u)

static uint16_t *ofgl_lm[OFGL_LM_BLOCKS];
static uint32_t ofgl_lm_version[OFGL_LM_BLOCKS], ofgl_lm_serial;
static uint32_t ofgl_lm_bytes;
static int ofgl_lm_dropped;

/* Lightmap page index for a bound texnum, or -1. gl1_lightmapcopies keeps
 * MAX_LIGHTMAPS-sized copies of the whole set to avoid updating a page that is
 * still in flight; every copy holds the same data, so they fold onto one. */
static inline int
ofgl_lm_index(int texnum)
{
	if (texnum < OFGL_TEXNUM_LIGHTMAPS || texnum >= OFGL_TEXNUM_SCRAPS)
	{
		return -1;
	}
	return (texnum - OFGL_TEXNUM_LIGHTMAPS) % OFGL_LM_BLOCKS;
}

static uint16_t *
ofgl_lm_page(int block)
{
	if (block < 0 || block >= OFGL_LM_BLOCKS)
	{
		return NULL;
	}
	if (!ofgl_lm[block])
	{
		uint32_t bytes = OFGL_LM_DIM * OFGL_LM_DIM * 2;

		if (ofgl_lm_bytes + bytes > OFGL_LM_BUDGET)
		{
			if (!ofgl_lm_dropped)
			{
				Com_Printf("of_gl1: lightmap budget %u KB exhausted at page "
					"%d; remaining pages render fullbright\n",
					(unsigned)(OFGL_LM_BUDGET >> 10), block);
			}
			ofgl_lm_dropped++;
			return NULL;	/* over budget: that page stays fullbright */
		}
		ofgl_lm[block] = (uint16_t *)malloc(bytes);
		if (!ofgl_lm[block])
		{
			ofgl_lm_dropped++;
			return NULL;
		}
		memset(ofgl_lm[block], 0xFF, bytes);
		ofgl_lm_bytes += bytes;
		Com_Printf("of_gl1: lightmap page %d (%u KB resident)\n",
			block, (unsigned)(ofgl_lm_bytes >> 10));
	}
	return ofgl_lm[block];
}

/* Copy an RGBA8888 sub-rect of a lightmap page in as RGB565. Used for both the
 * full upload and gl1's per-frame dynamic-light sub-updates. */
static void
ofgl_lm_store(int block, int x0, int y0, int w, int h, const uint32_t *src)
{
	uint16_t *page = ofgl_lm_page(block);

	if (!page || !src)
	{
		return;
	}

	ofgl_lm_version[block] = ++ofgl_lm_serial;
	/* Clip once, retaining the original source row stride. Test disjoint
	 * rectangles before negating an origin, including INT_MIN origins. */
	if (w <= 0 || h <= 0 || x0 >= OFGL_LM_DIM || y0 >= OFGL_LM_DIM ||
		x0 <= -w || y0 <= -h)
	{
		return;
	}
	int first_col = x0 < 0 ? -x0 : 0;
	int first_row = y0 < 0 ? -y0 : 0;
	int dx = x0 + first_col, dy = y0 + first_row;
	int columns = w - first_col, rows = h - first_row;
	if (columns > OFGL_LM_DIM - dx) columns = OFGL_LM_DIM - dx;
	if (rows > OFGL_LM_DIM - dy) rows = OFGL_LM_DIM - dy;
	for (int row = 0; row < rows; row++)
	{
		const uint32_t *in = src + (size_t)(first_row + row) * w + first_col;
		uint16_t *out = page + (dy + row) * OFGL_LM_DIM + dx;
		for (int col = 0; col < columns; col++)
		{
			/* Black is a valid modulation colour, not a transparent key. */
			out[col] = ofgl_rgba_to_565(in[col]);
		}
	}
}

static inline uint16_t
ofgl_lm_sample(const uint16_t *page, float ls, float lt)
{
	int x = (int)(ls * (float)OFGL_LM_DIM);
	int y = (int)(lt * (float)OFGL_LM_DIM);

	if (x < 0) x = 0; else if (x >= OFGL_LM_DIM) x = OFGL_LM_DIM - 1;
	if (y < 0) y = 0; else if (y >= OFGL_LM_DIM) y = OFGL_LM_DIM - 1;
	return page[y * OFGL_LM_DIM + x];
}

typedef struct
{
	uint16_t *texels;	/* RGB565, or NULL. 0x0000 = transparent key */
	uint16_t w, h;
	ofq2_texture_t gtex;	/* GPU handle (truecolor 3D sampler); lazy-built */
	int gtex_valid;
	uint32_t heap_bytes;	/* SDRAM heap bytes charged to the 3D-texture budget */
} ofgl_tex_t;

/* 3D textures (world+skins) live in the app heap when CRAM is off. Cap their
 * total so map load can't OOM; over-budget textures render flat white. */
static uint32_t ofgl_3d_tex_bytes = 0;
#define OFGL_3D_TEX_BUDGET (768u * 1024u)	/* bounded SDRAM texture pool */

static ofgl_tex_t ofgl_tex[OFGL_MAX_TEX];
static GLuint ofgl_gen_next = 1;

/* Per-texture-unit state. gl1 runs with multitexturing ENABLED on this build
 * (rf_opengles10 makes R_GetProcAddress hand back non-NULL glActiveTexture
 * stubs, so gl_config.multitexture ends up true), and it drives TMU1 with the
 * lightmap and TMU0 with the diffuse texture.
 *
 * Collapsing both units onto one binding was silently wrong: R_MBind returns
 * EARLY when the unit already holds the requested texnum, so a batch whose
 * diffuse texture matched the previous batch's never re-issued glBindTexture --
 * leaving the shared binding pointing at the LIGHTMAP that TMU1 had just bound.
 * Lightmap texnums are in the skipped range, so those surfaces fell back to the
 * flat white texture. Same story for glEnable/glDisable(GL_TEXTURE_2D), which
 * R_EnableMultitexture toggles while TMU1 is selected.
 *
 * Everything the renderer draws comes from unit 0; unit 1 is tracked so it
 * cannot corrupt unit 0 (and so lightmap coords are there when we want them). */
#define OFGL_NTMU 2
static int ofgl_tex_bound[OFGL_NTMU];
static int ofgl_active_tmu = 0;		/* glActiveTexture */
static int ofgl_client_tmu = 0;		/* glClientActiveTexture */
#define ofgl_cur_tex (ofgl_tex_bound[0])

/* Set by gl1's R_LoadPic to the imagetype being uploaded:
 *   0 = don't store, 1 = it_pic (2D HUD/menu, CPU-blitted, exact dims),
 *   2 = it_wall, 3 = it_skin, 4 = it_sky, 5 = it_sprite.
 * Everything >= 2 is sampled by the GPU, so it gets a power-of-two resample
 * and an of_texture handle. The scrap atlas (2D pics) is always kept
 * regardless, since it isn't uploaded through a typed R_LoadPic. */
int ofgl_store_texture = 1;

static inline int
ofgl_tex_is3d(int store)
{
	return store >= 2;
}

typedef struct
{
	const uint8_t *ptr;
	int size;
	int stride;		/* bytes; 0 = tightly packed */
	int enabled;
} ofgl_array_t;

static ofgl_array_t ofgl_va_vertex;
static ofgl_array_t ofgl_va_texcoord_tmu[OFGL_NTMU];
static ofgl_array_t ofgl_va_color;

/* Unit 0 is the one that gets rasterised (see the per-TMU note above). */
#define ofgl_va_texcoord (ofgl_va_texcoord_tmu[0])

/* Unit 1 starts DISABLED: gl1's R_SetDefaultState enables GL_TEXTURE_2D with
 * unit 0 selected, and only R_EnableMultitexture turns unit 1 on. */
static int ofgl_tex2d_on[OFGL_NTMU] = {1, 0};
#define ofgl_tex2d_enabled (ofgl_tex2d_on[0])

/* glTexEnvi(GL_TEXTURE_ENV_MODE): GL_REPLACE = texel only, GL_MODULATE /
 * GL_COMBINE = texel * vertex colour. gl1 leaves unit 0 on GL_REPLACE for the
 * world and switches to GL_MODULATE for alias models, particles and the
 * translucent/turb passes -- so this is what decides whether a surface is
 * tinted by the colour array at all. */
static GLint ofgl_texenv[OFGL_NTMU] = {GL_REPLACE, GL_REPLACE};

static inline GLint
ofgl_texenv_mode(int tmu)
{
	return ofgl_texenv[tmu];
}
static float ofgl_cur_color[4] = {1.0f, 1.0f, 1.0f, 1.0f};
static uint16_t ofgl_clear565 = 0;
static uint8_t *ofgl_framebuffer;

/* Only a byte-uniform GPU clear can establish this state. The pointer is
 * invalidated by every framebuffer rebind and CPU/GPU colour drawing path.
 * Keep the eager post-flip clear: it overlaps client work and serves 2D-only
 * frames. This merely avoids clearing its untouched result again in R_Clear. */
static struct
{
	uint16_t *fb;
	int width, height, stride;
	uint16_t color;
} ofgl_fb_clear;

static void
ofgl_refresh_fb(void)
{
	/* The draw buffer changes only at our flips. Resolve it once per frame,
	 * avoiding a kernel service call for every surface and HUD glyph. */
	uint8_t *fb = of_video_surface();
	ofgl_fb_clear.fb = NULL;
	ofgl_framebuffer = fb ? (uint8_t *)of_uncached(fb) : NULL;
}

static inline uint8_t *
ofgl_fb(void)
{
	return ofgl_framebuffer;
}

/* 16-bit (RGB565) view of the current draw buffer. */
static inline uint16_t *
ofgl_fb16(void)
{
	return (uint16_t *)ofgl_fb();
}

/* framebuffer stride in pixels (ofgl_fb_stride is bytes; RGB565 = 2 B/px). */
static inline int
ofgl_fb_stride_px(void)
{
	return ofgl_fb_stride >> 1;
}

/* DIAGNOSTIC test gradient: drives RGB565 directly so we can see whether the
 * panel displays 16-bit output at all + the stride is right. Set to 1 to fill
 * a red(x)/green(y) ramp instead of the real clear color. Remove once 2D is
 * confirmed on the truecolor scanout. */
#define OFGL_DBG_GRADIENT 0

/* Clear the current draw buffer to ofgl_clear565.
 *
 * The GPU's CLEAR_RECT is a DMA fill, so this costs the CPU a 4-word ring
 * command instead of 150 KB of UNCACHED stores -- and the equivalent z-buffer
 * clear costs another 150 KB. Those two memsets were the largest fixed cost in
 * the frame. CLEAR_RECT replicates one BYTE across the fill word, so it can
 * only express byte-uniform RGB565; black (and white) qualify, and gl1 only
 * ever asks for a coloured clear under r_clear, which is off by default. */
static void
ofgl_clear_fb565(void)
{
	uint16_t *fb16 = ofgl_fb16();
	int spx = ofgl_fb_stride_px();
	size_t n, k;

	if (!fb16)
	{
		return;
	}

	if (ofgl_gpu_ok && (ofgl_clear565 & 0xFF) == (ofgl_clear565 >> 8))
	{
		if (ofgl_fb_clear.fb == fb16 &&
			ofgl_fb_clear.color == ofgl_clear565 &&
			ofgl_fb_clear.width == ofgl_fb_width &&
			ofgl_fb_clear.height == ofgl_fb_height &&
			ofgl_fb_clear.stride == ofgl_fb_stride)
		{
			/* The earlier clear may still be queued: preserve pending state. */
			return;
		}
		OFQ2_ClearRect((uint8_t *)fb16, ofgl_fb_stride, ofgl_fb_width * 2,
			ofgl_fb_height, (uint8_t)(ofgl_clear565 & 0xFF));
		ofgl_gpu_pending = 1;
		ofgl_fb_clear.fb = fb16;
		ofgl_fb_clear.color = ofgl_clear565;
		ofgl_fb_clear.width = ofgl_fb_width;
		ofgl_fb_clear.height = ofgl_fb_height;
		ofgl_fb_clear.stride = ofgl_fb_stride;
		return;
	}

	/* CPU fallback/diagnostic fills do not establish reusable GPU state. */
	ofgl_fb_clear.fb = NULL;
#if OFGL_DBG_GRADIENT
	{
		int x, y;

		for (y = 0; y < ofgl_fb_height; y++)
		{
			uint16_t *row = fb16 + (size_t)y * spx;
			int g = (y * 63 / ofgl_fb_height) & 63;

			for (x = 0; x < ofgl_fb_width; x++)
			{
				int r = (x * 31 / ofgl_fb_width) & 31;
				row[x] = (uint16_t)((r << 11) | (g << 5));
			}
		}
		return;
	}
#endif

	if (ofgl_clear565 == 0)
	{
		memset(fb16, 0, (size_t)ofgl_fb_stride * (size_t)ofgl_fb_height);
		return;
	}
	n = (size_t)spx * (size_t)ofgl_fb_height;
	for (k = 0; k < n; k++)
	{
		fb16[k] = ofgl_clear565;
	}
}

static inline int
ofgl_iround(float v)
{
	return (int)(v + (v >= 0.0f ? 0.5f : -0.5f));
}

/* Read screen pos + texcoord of array element `i`. */
static void
ofgl_vertex(int i, float *x, float *y, float *s, float *t)
{
	int vstride = ofgl_va_vertex.stride ? ofgl_va_vertex.stride
		: ofgl_va_vertex.size * (int)sizeof(float);
	const float *v = (const float *)(ofgl_va_vertex.ptr + (size_t)i * vstride);

	*x = v[0];
	*y = v[1];

	if (ofgl_va_texcoord.enabled && ofgl_va_texcoord.ptr)
	{
		int tstride = ofgl_va_texcoord.stride ? ofgl_va_texcoord.stride
			: ofgl_va_texcoord.size * (int)sizeof(float);
		const float *tc = (const float *)(ofgl_va_texcoord.ptr + (size_t)i * tstride);
		*s = tc[0];
		*t = tc[1];
	}
	else
	{
		*s = 0.0f;
		*t = 0.0f;
	}
}

/* Blit one axis-aligned quad. ul = corner v0 (min x,y), dr = corner v2. */
static void
ofgl_blit_rect(float ulx, float uly, float drx, float dry,
	float uls, float ult, float drs, float drt)
{
	uint8_t *fb;
	int x0, y0;

	/* The 2D HUD writes the framebuffer with the CPU; drain any GPU 3D first
	 * (once per frame) so it can't race or get half-overwritten. */
	if (ofgl_gpu_pending)
	{
		OFQ2_Finish();
		ofgl_gpu_pending = 0;
	}

	uint16_t *fb16;
	int spx;

	fb16 = ofgl_fb16();
	spx = ofgl_fb_stride_px();
	x0 = ofgl_iround(ulx); y0 = ofgl_iround(uly);
	int x1 = ofgl_iround(drx), y1 = ofgl_iround(dry);
	int textured;
	int alpha = (int)(ofgl_cur_color[3] * 256.0f);	/* 0..256 */
	const ofgl_tex_t *tex = (ofgl_cur_tex > 0 && ofgl_cur_tex < OFGL_MAX_TEX)
		? &ofgl_tex[ofgl_cur_tex] : NULL;
	int cx0, cy0, cx1, cy1, py;
	float sstep, tstep, s_at_cx0, t_at_cy0;

	(void)fb;
	if (!fb16 || x1 <= x0 || y1 <= y0)
	{
		return;
	}
	if (alpha > 256) alpha = 256; else if (alpha < 0) alpha = 0;

	textured = ofgl_tex2d_enabled && ofgl_va_texcoord.enabled &&
		tex && tex->texels && tex->w && tex->h;

	/* per-pixel texcoord deltas computed on the UNCLIPPED rect */
	sstep = (drs - uls) / (float)(x1 - x0);
	tstep = (drt - ult) / (float)(y1 - y0);

	/* clip to the framebuffer */
	cx0 = x0 < 0 ? 0 : x0;
	cy0 = y0 < 0 ? 0 : y0;
	cx1 = x1 > ofgl_fb_width ? ofgl_fb_width : x1;
	cy1 = y1 > ofgl_fb_height ? ofgl_fb_height : y1;
	if (cx1 <= cx0 || cy1 <= cy0)
	{
		return;
	}

	/* Both solid/alpha fills and textured HUD blits write colour here. */
	ofgl_fb_clear.fb = NULL;
	if (!textured)
	{
		/* solid fill. Real alpha blend now (truecolor) -- this restores the
		 * RDraw_FadeScreen menu dim that the 8-bit path used to drop. */
		uint16_t color = ofgl_rgba_to_565(
			((uint32_t)(ofgl_cur_color[0] * 255.0f) & 0xff) |
			(((uint32_t)(ofgl_cur_color[1] * 255.0f) & 0xff) << 8) |
			(((uint32_t)(ofgl_cur_color[2] * 255.0f) & 0xff) << 16));
		int px;

		for (py = cy0; py < cy1; py++)
		{
			uint16_t *dstrow = fb16 + (size_t)py * spx;

			if (alpha >= 256)
			{
				for (px = cx0; px < cx1; px++) dstrow[px] = color;
			}
			else
			{
				for (px = cx0; px < cx1; px++)
					dstrow[px] = ofgl_blend565(color, dstrow[px], alpha);
			}
		}
		return;
	}

	s_at_cx0 = uls + (cx0 - x0 + 0.5f) * sstep;
	t_at_cy0 = ult + (cy0 - y0 + 0.5f) * tstep;

	/* For an opaque unit-texel walk, every horizontal sample is exact.
	 * A power-of-two width makes the normalized step a binary power;
	 * half-texel start plus in-range indices below 32768 needs at most
	 * 17 significant bits, so every original float add/multiply is exact.
	 * Vertical sampling is unchanged and may still be scaled or clipped. */
	float first_texel = s_at_cx0 * tex->w;
	if (alpha >= 256 && tex->w <= 32768 && !(tex->w & (tex->w - 1)) &&
		sstep * tex->w == 1.0f && first_texel >= 0.5f &&
		first_texel <= (float)tex->w - 0.5f)
	{
		int first = (int)first_texel;
		if (first_texel == (float)first + 0.5f && cx1 - cx0 <= tex->w - first)
		{
			for (py = cy0; py < cy1; py++)
			{
				float tcur = t_at_cy0 + (py - cy0) * tstep;
				int ty = (int)(tcur * tex->h);
				if (ty < 0) ty = 0; else if (ty >= tex->h) ty = tex->h - 1;
				const uint16_t *src = tex->texels + (size_t)ty * tex->w + first;
				uint16_t *dst = fb16 + (size_t)py * spx + cx0;
				for (int px = 0; px < cx1 - cx0; px++)
				{
					uint16_t texel = src[px];
					if (texel != OFGL_TEX_TRANSPARENT) dst[px] = texel;
				}
			}
			return;
		}
	}

	for (py = cy0; py < cy1; py++)
	{
		float tcur = t_at_cy0 + (py - cy0) * tstep;
		int ty = (int)(tcur * tex->h);
		const uint16_t *texrow;
		uint16_t *dstrow = fb16 + (size_t)py * spx;
		float scur = s_at_cx0;
		int px;

		if (ty < 0) ty = 0; else if (ty >= tex->h) ty = tex->h - 1;
		texrow = tex->texels + (size_t)ty * tex->w;

		for (px = cx0; px < cx1; px++)
		{
			int tx = (int)(scur * tex->w);
			uint16_t texel;

			if (tx < 0) tx = 0; else if (tx >= tex->w) tx = tex->w - 1;
			texel = texrow[tx];
			if (texel != OFGL_TEX_TRANSPARENT)
			{
				dstrow[px] = (alpha >= 256) ? texel
					: ofgl_blend565(texel, dstrow[px], alpha);
			}
			scur += sstep;
		}
	}
}

/* ======================================================================== *
 *  3D world on the OFQ2 triangle GPU (Stage 4, milestone (a): flat geometry)
 *
 *  gl1 hands us world-space vertices (glVertexPointer size 3) plus a GL matrix
 *  stack. OFQ2_DrawXformTri takes RAW world verts + a sticky object-state
 *  (camera matrix + projection consts) and does transform + perspective +
 *  z-raster on the GPU. We track the GL matrices, convert modelview+projection
 *  into ofq2_object_state_t, and stream triangles. Everything gates on
 *  OFQ2_CAP_XFORM_TRI: absent -> 3D stays skipped, the 2D HUD is unaffected.
 *  (State globals are declared near the top of this file.)
 * ======================================================================== */

/* Finite test by IEEE-754 bit pattern.
 *
 * The build is -ffast-math, so isfinite()/isnan() fold to constants and plain
 * `<`/`>` clamps let a NaN pass straight through (both comparisons are false).
 * A NaN vertex is not hypothetical in this port -- one reaching the soft
 * renderer's edge scan was the old always-crash-on-new-game -- so test the
 * exponent field directly and drop the triangle rather than hand the GPU a
 * garbage screen coordinate. */
static inline int
ofgl_finite(float v)
{
	uint32_t b;

	memcpy(&b, &v, sizeof(b));
	return (b & 0x7F800000u) != 0x7F800000u;
}

static GLfloat *
ofgl_top_matrix(void)
{
	return (ofgl_mat_mode == GL_PROJECTION) ? ofgl_proj : OFGL_MV;
}

static void
ofgl_mat4_identity(ofgl_mat4 m)
{
	int i;

	for (i = 0; i < 16; i++)
	{
		m[i] = 0.0f;
	}
	m[0] = m[5] = m[10] = m[15] = 1.0f;
}

/* out = a * b, column-major (OpenGL convention) */
static void
ofgl_mat4_mul(ofgl_mat4 out, const ofgl_mat4 a, const ofgl_mat4 b)
{
	ofgl_mat4 r;
	int c, row, k;

	for (c = 0; c < 4; c++)
	{
		for (row = 0; row < 4; row++)
		{
			float s = 0.0f;

			for (k = 0; k < 4; k++)
			{
				s += a[k * 4 + row] * b[c * 4 + k];
			}
			r[c * 4 + row] = s;
		}
	}
	memcpy(out, r, sizeof(r));
}

/* top = top * m */
static void
ofgl_mat_postmul(const ofgl_mat4 m)
{
	GLfloat *top = ofgl_top_matrix();

	ofgl_mat4_mul(top, top, m);
	ofgl_obj_dirty = 1;
}

/* ---- matrix-stack gl* (replace the no-op defaults) ---- */

static void
ofgl_MatrixMode(GLenum mode)
{
	ofgl_mat_mode = mode;
}

static void
ofgl_LoadIdentity(void)
{
	ofgl_mat4_identity(ofgl_top_matrix());
	ofgl_obj_dirty = 1;
}

static void
ofgl_LoadMatrixf(const GLfloat *m)
{
	if (m)
	{
		memcpy(ofgl_top_matrix(), m, 16 * sizeof(GLfloat));
		ofgl_obj_dirty = 1;
	}
}

static void
ofgl_MultMatrixf(const GLfloat *m)
{
	if (m)
	{
		ofgl_mat_postmul((const float *)m);
	}
}

static void
ofgl_PushMatrix(void)
{
	if (ofgl_mat_mode == GL_PROJECTION)
	{
		if (ofgl_proj_sp + 1 < (int)(sizeof(ofgl_proj_stack) / sizeof(ofgl_mat4)))
		{
			memcpy(ofgl_proj_stack[ofgl_proj_sp + 1],
				ofgl_proj_stack[ofgl_proj_sp], sizeof(ofgl_mat4));
			ofgl_proj_sp++;
		}
		else
		{
			ofgl_proj_over++;	/* count it so the matching pop is absorbed */
		}
		return;
	}
	if (ofgl_mv_sp + 1 < (int)(sizeof(ofgl_mv_stack) / sizeof(ofgl_mat4)))
	{
		memcpy(ofgl_mv_stack[ofgl_mv_sp + 1], ofgl_mv_stack[ofgl_mv_sp],
			sizeof(ofgl_mat4));
		ofgl_mv_sp++;
	}
	else
	{
		ofgl_mv_over++;
	}
}

static void
ofgl_PopMatrix(void)
{
	if (ofgl_mat_mode == GL_PROJECTION)
	{
		if (ofgl_proj_over)
		{
			ofgl_proj_over--;
		}
		else if (ofgl_proj_sp > 0)
		{
			ofgl_proj_sp--;
			ofgl_obj_dirty = 1;
		}
		return;
	}
	if (ofgl_mv_over)
	{
		ofgl_mv_over--;
	}
	else if (ofgl_mv_sp > 0)
	{
		ofgl_mv_sp--;
		ofgl_obj_dirty = 1;
	}
}

/* R_SetGL2D installs an ortho projection; without this it stayed the identity
 * matrix left by the preceding glLoadIdentity. */
static int
ofgl_ortho_matrix(ofgl_mat4 m, GLdouble l, GLdouble r, GLdouble b, GLdouble t,
	GLdouble n, GLdouble f)
{

	ofgl_mat4_identity(m);
	if (r == l || t == b || f == n)
	{
		return 0;
	}
	m[0]  = (float)(2.0 / (r - l));
	m[5]  = (float)(2.0 / (t - b));
	m[10] = (float)(-2.0 / (f - n));
	m[12] = (float)(-(r + l) / (r - l));
	m[13] = (float)(-(t + b) / (t - b));
	m[14] = (float)(-(f + n) / (f - n));
	return 1;
}

static void
ofgl_frustum_matrix(ofgl_mat4 m, GLdouble l, GLdouble r, GLdouble b, GLdouble t,
	GLdouble n, GLdouble f)
{

	ofgl_mat4_identity(m);
	m[0]  = (float)(2.0 * n / (r - l));
	m[5]  = (float)(2.0 * n / (t - b));
	m[8]  = (float)((r + l) / (r - l));
	m[9]  = (float)((t + b) / (t - b));
	m[10] = (float)(-(f + n) / (f - n));
	m[11] = -1.0f;
	m[14] = (float)(-2.0 * f * n / (f - n));
	m[15] = 0.0f;
}

static void
ofgl_Ortho(GLdouble l, GLdouble r, GLdouble b, GLdouble t, GLdouble n, GLdouble f)
{
	ofgl_mat4 m;
	if (ofgl_ortho_matrix(m, l, r, b, t, n, f)) ofgl_mat_postmul(m);
}

static void
ofgl_Frustum(GLdouble l, GLdouble r, GLdouble b, GLdouble t, GLdouble n, GLdouble f)
{
	ofgl_mat4 m;
	ofgl_frustum_matrix(m, l, r, b, t, n, f);
	ofgl_mat_postmul(m);
}

typedef struct {
	GLfloat args[6];
	ofgl_mat4 matrix;
	int valid;
} ofgl_projection_cache_t;

/* GLES1 SPELLS THESE DIFFERENTLY. gl1's local.h redefines glFrustum ->
 * glFrustumf, glOrtho -> glOrthof and glDepthRange -> glDepthRangef under
 * YQ2_GL1_GLES, and GLAD asks the loader for those exact names -- so matching
 * only the desktop spelling handed back the no-op stub and the PROJECTION
 * MATRIX WAS NEVER BUILT. It stayed the identity left by glLoadIdentity, which
 * happens to give a roughly-plausible horizontal scale (160 vs 162) and a
 * badly wrong vertical one (120 vs 162), i.e. a ~106-degree vertical FOV
 * instead of 73. Both spellings are accepted now. */
static void
ofgl_Frustumf(GLfloat l, GLfloat r, GLfloat b, GLfloat t, GLfloat n, GLfloat f)
{
	/* The world and weapon repeatedly alternate between two projections.
	 * Keep the original double-precision construction on a cache miss. */
	static ofgl_projection_cache_t cache[2];
	static unsigned next;
	GLfloat args[6] = {l, r, b, t, n, f};
	for (unsigned i = 0; i < 2; i++)
		if (cache[i].valid && !memcmp(args, cache[i].args, sizeof(args)))
		{
			ofgl_mat_postmul(cache[i].matrix);
			return;
		}
	ofgl_projection_cache_t *entry = &cache[next];
	next ^= 1;
	ofgl_frustum_matrix(entry->matrix, l, r, b, t, n, f);
	memcpy(entry->args, args, sizeof(args));
	entry->valid = 1;
	ofgl_mat_postmul(entry->matrix);
}

static void
ofgl_Orthof(GLfloat l, GLfloat r, GLfloat b, GLfloat t, GLfloat n, GLfloat f)
{
	static ofgl_projection_cache_t cache;
	GLfloat args[6] = {l, r, b, t, n, f};
	if (!cache.valid || memcmp(args, cache.args, sizeof(args)))
	{
		if (!ofgl_ortho_matrix(cache.matrix, l, r, b, t, n, f))
		{
			cache.valid = 0;
			return;
		}
		memcpy(cache.args, args, sizeof(args));
		cache.valid = 1;
	}
	ofgl_mat_postmul(cache.matrix);
}

/* glDepthRange is gl1's view-model hack: it squeezes the weapon into the
 * nearest 30% of the depth buffer so the gun can't poke through walls. Our
 * depth is 1/z (larger = nearer) rather than a window-z, so reserve the near
 * field by scaling the weapon's depth up -- uniform, so the gun keeps its own
 * internal ordering while always beating world geometry. */
static void
ofgl_DepthRangef(GLfloat n, GLfloat f)
{
	float span = f - n;

	if (span < 0.0f)
	{
		span = -span;
	}
	ofgl_depth_scale = (span < 0.9f) ? 16.0f : 1.0f;
}

static void
ofgl_Rotatef(GLfloat angle, GLfloat x, GLfloat y, GLfloat z)
{
	/* Upright entities usually have zero pitch and roll. Multiplying by
	 * their identity rotations only repeats the matrix and clip-plane work. */
	if (angle == 0.0f)
	{
		return;
	}
	float rad = angle * (3.14159265358979f / 180.0f);
	float c = cosf(rad), s = sinf(rad);
	float len = sqrtf(x * x + y * y + z * z);
	ofgl_mat4 m;

	if (len < 1e-8f)
	{
		return;
	}
	x /= len; y /= len; z /= len;
	ofgl_mat4_identity(m);
	m[0] = x * x * (1 - c) + c;     m[1] = y * x * (1 - c) + z * s; m[2]  = x * z * (1 - c) - y * s;
	m[4] = x * y * (1 - c) - z * s; m[5] = y * y * (1 - c) + c;     m[6]  = y * z * (1 - c) + x * s;
	m[8] = x * z * (1 - c) + y * s; m[9] = y * z * (1 - c) - x * s; m[10] = z * z * (1 - c) + c;
	ofgl_mat_postmul(m);
}

static void
ofgl_Translatef(GLfloat x, GLfloat y, GLfloat z)
{
	ofgl_mat4 m;

	ofgl_mat4_identity(m);
	m[12] = x; m[13] = y; m[14] = z;
	ofgl_mat_postmul(m);
}

static void
ofgl_Scalef(GLfloat x, GLfloat y, GLfloat z)
{
	ofgl_mat4 m;

	ofgl_mat4_identity(m);
	m[0] = x; m[5] = y; m[10] = z;
	ofgl_mat_postmul(m);
}

static void
ofgl_Viewport(GLint x, GLint y, GLsizei w, GLsizei h)
{
	ofgl_vp_x = x; ofgl_vp_w = w; ofgl_vp_h = h;
	/* GL measures the viewport from the BOTTOM of the window; our framebuffer
	 * addresses rows from the top, and both the projection centre and the GPU
	 * clip rect are in framebuffer rows. Quake2 centres the 3D view vertically,
	 * which makes the two agree by accident -- flip explicitly so an
	 * off-centre view (letterboxed cinematic, split-screen stereo) doesn't
	 * silently render at the wrong height with a mismatched clip rect. */
	ofgl_vp_y = ofgl_fb_height - (y + h);
	if (ofgl_vp_y < 0)
	{
		ofgl_vp_y = 0;
	}
	ofgl_obj_dirty = 1;
}

/* NOTE (0x50/0x52 transform front-end): os30 does implement the GPU transform
 * path, but its projection registers are INTEGER PIXELS -- screen.x =
 * (xcenter<<4) + ((xscale * ratio_x) >>> 12) in gpu_core.v -- while the port's
 * earlier object-state builder handed them Q16.16, which saturates every vertex
 * to the guard band. It also derives zi (and hence the plane precision) itself
 * from the raw camera z, so it cannot use the per-triangle attribute scale the
 * screen-space path below relies on; Quake2's absolute surface texel coords
 * make that scale the difference between sharp and swimming textures. The CPU
 * projection + 0x4E path is the one SM64 validated on this bitstream, so it is
 * the only path here. */

/* 8x8 white RGB565 texture for untextured 3D tris (the GPU always samples a
 * texture; white passes through the per-vertex-color modulation unchanged). */
static void
ofgl_ensure_flat_tex(void)
{
	static uint16_t white[8 * 8];
	int i;

	if (ofgl_flat_ready)
	{
		return;
	}
	for (i = 0; i < 8 * 8; i++)
	{
		white[i] = 0xFFFF;
	}
	OFQ2_TexCreate(&ofgl_flat_tex, white, 8, 8, sizeof(white));
	ofgl_flat_ready = 1;
}

/* A vertex already in OFQ2 camera space: +X right, +Y DOWN, +Z forward (the
 * GL-camera basis flip diag(1,-1,-1) is folded into the transform below), with
 * raw texel coords and the resolved per-vertex modulation colour. */
typedef struct
{
	float x, y, z;
	float s, t;
	float iw, sx, sy;	/* projected once per surviving shared vertex */
	int32_t depth;
	int16_t px, py;
	uint16_t rgb;
	uint8_t a;
	uint8_t clipcode;	/* bit i set = outside ofgl_cplane[i] */
	uint8_t viewcode;	/* conservative viewport rejection, before clipping */
	uint8_t attrs_valid;
	uint8_t projected_valid;	/* 1 = ready, 2 = nonfinite projection */
} ofgl_cvert_t;

/* Camera-space clip planes: keep d(v) = a*x + b*y + c*z + e >= 0.
 *
 * Plane 0 is the near plane. Planes 1-4 are a GUARD BAND around the viewport,
 * expressed in camera space by multiplying the screen test through by z (valid
 * because everything reaching them is already in front of near):
 *   screen_x = xc + xs*(x/z) >= GX0   <=>   xs*x + (xc - GX0)*z >= 0
 *
 * The guard band is what stops a clipped vertex from projecting to infinity.
 * Near-plane clipping puts new vertices exactly ON the near plane, and their
 * x/y can be enormous -- a wall edge running off to the side. Projecting those
 * gives screen coordinates in the tens of thousands, and merely CLAMPING them
 * into int16 range moves the vertex sideways, which re-routes the triangle's
 * edges and smears it across the display. Clipping (rather than clamping) keeps
 * the shape exact and bounds the coordinates by construction. */
typedef struct { float a, b, c, e; } ofgl_plane_t;

#define OFGL_NPLANE 5
#define OFGL_GUARD_X 512.0f
/* The OS30 edge divider stores |dy| in 13 bits, including four fractional
 * bits for subpixel Y. A 512-pixel vertical guard allowed edges taller than
 * 511 15/16 pixels: their divisor wrapped and the edges crossed the screen.
 * At 240 lines, this guard bounds every edge to 496 pixels (7936 subpixels). */
#define OFGL_GUARD_Y 128.0f
#define OFGL_CLIP_MAX 10	/* 3 verts + one new vertex per plane, plus slack */

static ofgl_plane_t ofgl_cplane[OFGL_NPLANE];
static float ofgl_view_left, ofgl_view_right, ofgl_view_top, ofgl_view_bottom;

static void
ofgl_project_cvert(ofgl_cvert_t *v)
{
	v->iw = 1.0f / v->z;
	v->sx = ofgl_pc_xc + ofgl_pc_xs * v->x * v->iw;
	v->sy = ofgl_pc_yc + ofgl_pc_ys * v->y * v->iw;
	/* Shared fan/strip vertices only need these checks once. Use bit tests:
	 * -ffast-math makes the standard isfinite() unsuitable here. */
	v->projected_valid = 2;
	if (!ofgl_finite(v->sx) || !ofgl_finite(v->sy) || !ofgl_finite(v->iw))
	{
		return;
	}
	/* The finite guard above makes hardware min/max equivalent here,
	 * including signed zero, without per-coordinate clamp branches. */
	v->sx = fmaxf(-2047.0f, fminf(v->sx, 2047.0f));
	v->sy = fmaxf(-2047.0f, fminf(v->sy, 2047.0f));
	v->px = (int16_t)lrintf(v->sx * 16.0f);
	v->py = (int16_t)lrintf(v->sy * 16.0f);
	float depth = v->iw * (1073741824.0f * ofgl_depth_scale);
	/* Depth scale is always 1 or 16. Finite iw can overflow this
	 * positive product to infinity but cannot make it NaN. */
	depth = fmaxf(1.0f, fminf(depth, 2147483520.0f));
	v->depth = (int32_t)lrintf(depth);
	v->projected_valid = 1;
}

static void
ofgl_build_clip_planes(void)
{
	float gx0 = ofgl_vp_x - OFGL_GUARD_X;
	float gx1 = ofgl_vp_x + ofgl_vp_w + OFGL_GUARD_X;
	float gy0 = ofgl_vp_y - OFGL_GUARD_Y;
	float gy1 = ofgl_vp_y + ofgl_vp_h + OFGL_GUARD_Y;

	ofgl_view_left = ofgl_pc_xc - ofgl_vp_x;
	ofgl_view_right = ofgl_vp_x + ofgl_vp_w - ofgl_pc_xc;
	ofgl_view_top = ofgl_pc_yc - ofgl_vp_y;
	ofgl_view_bottom = ofgl_vp_y + ofgl_vp_h - ofgl_pc_yc;

	ofgl_cplane[0].a = 0.0f;          ofgl_cplane[0].b = 0.0f;
	ofgl_cplane[0].c = 1.0f;          ofgl_cplane[0].e = -ofgl_pc_near;
	ofgl_cplane[1].a = ofgl_pc_xs;    ofgl_cplane[1].b = 0.0f;
	ofgl_cplane[1].c = ofgl_pc_xc - gx0; ofgl_cplane[1].e = 0.0f;
	ofgl_cplane[2].a = -ofgl_pc_xs;   ofgl_cplane[2].b = 0.0f;
	ofgl_cplane[2].c = gx1 - ofgl_pc_xc; ofgl_cplane[2].e = 0.0f;
	ofgl_cplane[3].a = 0.0f;          ofgl_cplane[3].b = ofgl_pc_ys;
	ofgl_cplane[3].c = ofgl_pc_yc - gy0; ofgl_cplane[3].e = 0.0f;
	ofgl_cplane[4].a = 0.0f;          ofgl_cplane[4].b = -ofgl_pc_ys;
	ofgl_cplane[4].c = gy1 - ofgl_pc_yc; ofgl_cplane[4].e = 0.0f;
}

static inline float
ofgl_plane_dist(const ofgl_plane_t *p, const ofgl_cvert_t *v)
{
	return p->a * v->x + p->b * v->y + p->c * v->z + p->e;
}

/* gl1 submits the world through ONE indexed draw per texture batch, and its
 * fan/strip expansion reuses each vertex ~3x. Memoise the modelview transform
 * on a small direct-mapped cache keyed by array index: fan/strip locality makes
 * the hit rate near-total, so we pay ~1.1 transforms per triangle instead of 3.
 * Tagged per draw call, so a new batch (or a matrix change) costs nothing to
 * invalidate. */
#define OFGL_VMEMO_N 256
/* Resolved once per batch by ofgl_begin3d: the lightmap page unit 1 is bound
 * to, or NULL when this batch isn't a lightmapped world pass. */
static const uint16_t *ofgl_lm_cur;
static int ofgl_lm_active;
static int ofgl_dbg_big;	/* screen-covering-triangle reports emitted */
static uint32_t ofgl_gpu_slots_valid;
static int ofgl_gpu_slots[32];
static int ofgl_indexed_geometry;
static int ofgl_geometry_state_ready;
static int ofgl_geometry_tri(int i0, int i1, int i2,
	ofgl_cvert_t *a, ofgl_cvert_t *b, ofgl_cvert_t *c, float tw, float th);
static const ofq2_texture_t *ofgl_batch_tex;
static int ofgl_batch_alpha;
static float ofgl_inv_tw, ofgl_inv_th;

/* ---- `ofgl_draw` bisect switch --------------------------------------------
 *
 * Set from the Quake2 console to isolate which class of 3D submission is
 * producing an artifact, without a rebuild. Bitmask, default 15 = everything:
 *
 *   1  indexed draws  (glDrawElements: the world and alias models)
 *   2  array draws    (glDrawArrays: sky, particles, beams, null model,
 *                      the R_PolyBlend screen wash)
 *   4  clipped tris   (off = DROP triangles that need near/guard clipping
 *                      instead of clipping them -- the pre-clipper behaviour)
 *   8  lightmaps      (off = fullbright world)
 *
 * e.g. `ofgl_draw 1` leaves only the world+models, `ofgl_draw 11` keeps
 * everything but the clipper. */
#define OFGL_DRAW_INDEXED  1
#define OFGL_DRAW_ARRAYS   2
#define OFGL_DRAW_CLIPPED  4
#define OFGL_DRAW_LIGHTMAP 8
static cvar_t *ofgl_draw_cv;
static int ofgl_draw_mask = 15;		/* effective mask for this frame */

static inline int
ofgl_draw_enabled(int bit)
{
	return ofgl_draw_mask & bit;
}

/* AUTO-BISECT. The Pocket has no keyboard, so there is no console to type
 * `ofgl_draw` into. Instead walk the interesting masks on a timer and announce
 * each one with Com_Printf -- Quake2 echoes recent console lines as notify
 * text at the top of the screen (con_notifytime, 3s), so the current mode is
 * readable in-game with nothing but the display. Disabled during normal play;
 * set ofgl_bisect 1 in config.cfg to enable this diagnostic sequence. */
static cvar_t *ofgl_bisect_cv;
static const struct
{
	int mask;
	const char *what;
} ofgl_bisect_seq[] = {
	{ 15, "1/5 ALL ON" },
	{ 11, "2/5 CLIPPER OFF" },
	{  1, "3/5 WORLD+MODELS ONLY" },
	{  2, "4/5 SKY+FX ONLY" },
	{  7, "5/5 LIGHTMAPS OFF" },
};
#define OFGL_BISECT_N   (int)(sizeof(ofgl_bisect_seq) / sizeof(ofgl_bisect_seq[0]))
#define OFGL_BISECT_MS  6000u		/* per phase */
#define OFGL_BISECT_REP 2000u		/* re-announce, so the label never fades */

static void
ofgl_bisect_tick(void)
{
	static unsigned phase_t0, last_say;
	static int phase = -1;
	unsigned now;

	if (!ofgl_bisect_cv || !ofgl_bisect_cv->value)
	{
		ofgl_draw_mask = ofgl_draw_cv ? (int)ofgl_draw_cv->value : 15;
		phase = -1;
		return;
	}

	now = of_time_ms();
	if (phase < 0 || (now - phase_t0) >= OFGL_BISECT_MS)
	{
		phase = (phase < 0) ? 0 : (phase + 1) % OFGL_BISECT_N;
		phase_t0 = now;
		last_say = now - OFGL_BISECT_REP;	/* announce immediately */
	}
	if ((now - last_say) >= OFGL_BISECT_REP)
	{
		last_say = now;
		Com_Printf("ofgl bisect: %s\n", ofgl_bisect_seq[phase].what);
	}
	ofgl_draw_mask = ofgl_bisect_seq[phase].mask;
}

static ofgl_cvert_t ofgl_vmemo[OFGL_VMEMO_N];
static int32_t ofgl_vmemo_idx[OFGL_VMEMO_N];	/* full index: glDrawArrays can
						 * exceed 16 bits, and a truncated
						 * key would alias two vertices */
static uint8_t ofgl_vmemo_tag[OFGL_VMEMO_N];
static uint8_t ofgl_vmemo_cur;		/* 0 = "no batch yet", never reused */

static void
ofgl_vmemo_new_batch(void)
{
	if (++ofgl_vmemo_cur == 0)
	{
		memset(ofgl_vmemo_tag, 0, sizeof(ofgl_vmemo_tag));
		ofgl_vmemo_cur = 1;
	}
}

static int ofgl_world_project_original(const float xyz[3], ofgl_cvert_t *v);

void
OFGL_SetWorldDraw(int enabled)
{
	ofgl_world_draw_valid = enabled != 0;
}

/* Read array element `i` and transform it into camera space. */
static inline __attribute__((always_inline)) const float *
ofgl_fetch_position(int i, ofgl_cvert_t *out)
{
	const float *mv = OFGL_MV;
	int vstride = ofgl_va_vertex.stride ? ofgl_va_vertex.stride
		: ofgl_va_vertex.size * (int)sizeof(float);
	const float *v = (const float *)(ofgl_va_vertex.ptr + (size_t)i * vstride);
	float wx = v[0], wy = v[1], wz = v[2];

	out->attrs_valid = 0;
	out->projected_valid = 0;

	out->x =   mv[0] * wx + mv[4] * wy + mv[8]  * wz + mv[12];
	out->y = -(mv[1] * wx + mv[5] * wy + mv[9]  * wz + mv[13]);
	out->z = -(mv[2] * wx + mv[6] * wy + mv[10] * wz + mv[14]);
	return v;
}

static inline __attribute__((always_inline)) void
ofgl_classify_cvert(ofgl_cvert_t *out)
{
	float px = ofgl_pc_xs * out->x;
	float py = ofgl_pc_ys * out->y;
	float left = px + ofgl_view_left * out->z;
	float right = -px + ofgl_view_right * out->z;
	float top = py + ofgl_view_top * out->z;
	float bottom = -py + ofgl_view_bottom * out->z;
	float guard_x = OFGL_GUARD_X * out->z;
	float guard_y = OFGL_GUARD_Y * out->z;
	out->clipcode = (out->z < ofgl_pc_near ? 1 : 0) |
		(left + guard_x < 0.0f ? 2 : 0) | (right + guard_x < 0.0f ? 4 : 0) |
		(top + guard_y < 0.0f ? 8 : 0) | (bottom + guard_y < 0.0f ? 16 : 0);
	/* A one-pixel margin keeps this rejection conservative at raster edges. */
	out->viewcode = (left + out->z < 0.0f ? 1 : 0) |
		(right + out->z < 0.0f ? 2 : 0) | (top + out->z < 0.0f ? 4 : 0) |
		(bottom + out->z < 0.0f ? 8 : 0);
}

static inline __attribute__((always_inline)) void
ofgl_fetch_cvert(int i, ofgl_cvert_t *out)
{
	const float *v = ofgl_fetch_position(i, out);
	if (ofgl_batch_world) ofgl_world_project_original(v, out);
	ofgl_classify_cvert(out);
}

/* Attribute reads are deferred until a triangle survives coarse rejection.
 * The position cache is also the attribute cache for shared fan/strip verts. */
enum
{
	OFGL_ATTR_FIXED,
	OFGL_ATTR_RGB,
	OFGL_ATTR_RGBA,
	OFGL_ATTR_LIGHTMAP,
	OFGL_ATTR_CONSTANT_PENDING
};

static struct
{
	const uint8_t *uv_ptr, *color_ptr;
	int uv_stride, color_stride;
	unsigned mode;
	uint16_t rgb;
	uint8_t alpha;
} ofgl_attrs;

/* Array formats and material priority are invariant during a GL draw.
 * Resolve only their views here: reads still wait for a surviving triangle.
 * Do not snapshot the position view: raw world surfaces replace it inside
 * one begin3d/end3d batch. */
static void
ofgl_prepare_attrs(void)
{
	/* Untextured draws may leave a dangling client texcoord array enabled. */
	ofgl_attrs.uv_ptr = (ofgl_tex2d_enabled && ofgl_va_texcoord.enabled)
		? ofgl_va_texcoord.ptr : NULL;
	ofgl_attrs.uv_stride = ofgl_va_texcoord.stride ? ofgl_va_texcoord.stride
		: ofgl_va_texcoord.size * (int)sizeof(float);

	if (ofgl_lm_active && ofgl_lm_cur)
	{
		const ofgl_array_t *lmc = &ofgl_va_texcoord_tmu[1];
		ofgl_attrs.mode = OFGL_ATTR_LIGHTMAP;
		ofgl_attrs.color_ptr = lmc->ptr;
		ofgl_attrs.color_stride = lmc->stride ? lmc->stride
			: lmc->size * (int)sizeof(float);
	}
	else if (ofgl_tex2d_enabled && ofgl_texenv_mode(0) == GL_REPLACE)
	{
		ofgl_attrs.mode = OFGL_ATTR_FIXED;
		ofgl_attrs.rgb = 0xffff;
		ofgl_attrs.alpha = 255;
	}
	else if (ofgl_va_color.enabled && ofgl_va_color.ptr)
	{
		ofgl_attrs.mode = ofgl_va_color.size >= 4 ? OFGL_ATTR_RGBA : OFGL_ATTR_RGB;
		ofgl_attrs.color_ptr = ofgl_va_color.ptr;
		ofgl_attrs.color_stride = ofgl_va_color.stride ? ofgl_va_color.stride
			: ofgl_va_color.size;
	}
	else
	{
		/* Convert constant color at most once, but not for rejected batches. */
		ofgl_attrs.mode = OFGL_ATTR_CONSTANT_PENDING;
	}
}

static void
ofgl_fetch_attrs(int i, float tw, float th, ofgl_cvert_t *out)
{
	if (ofgl_attrs.uv_ptr)
	{
		const float *tc = (const float *)(ofgl_attrs.uv_ptr +
			(size_t)i * ofgl_attrs.uv_stride);
		out->s = tc[0] * tw;
		out->t = tc[1] * th;
	}
	else
	{
		out->s = out->t = 0.0f;
	}

	if (ofgl_attrs.mode == OFGL_ATTR_LIGHTMAP)
	{
		const float *lc = (const float *)(ofgl_attrs.color_ptr +
			(size_t)i * ofgl_attrs.color_stride);
		out->rgb = ofgl_lm_sample(ofgl_lm_cur, lc[0], lc[1]);
		out->a = 255;
	}
	else if (ofgl_attrs.mode == OFGL_ATTR_RGB || ofgl_attrs.mode == OFGL_ATTR_RGBA)
	{
		const uint8_t *c = ofgl_attrs.color_ptr +
			(size_t)i * ofgl_attrs.color_stride;
		out->rgb = (uint16_t)(((c[0] & 0xF8) << 8) | ((c[1] & 0xFC) << 3) |
			(c[2] >> 3));
		out->a = ofgl_attrs.mode == OFGL_ATTR_RGBA ? c[3] : 255;
	}
	else
	{
		if (ofgl_attrs.mode == OFGL_ATTR_CONSTANT_PENDING)
		{
			ofgl_attrs.rgb = ofgl_rgba_to_565(
				((uint32_t)(ofgl_cur_color[0] * 255.0f) & 0xff) |
				(((uint32_t)(ofgl_cur_color[1] * 255.0f) & 0xff) << 8) |
				(((uint32_t)(ofgl_cur_color[2] * 255.0f) & 0xff) << 16));
			ofgl_attrs.alpha = (uint8_t)(ofgl_cur_color[3] * 255.0f + 0.5f);
			ofgl_attrs.mode = OFGL_ATTR_FIXED;
		}
		out->rgb = ofgl_attrs.rgb;
		out->a = ofgl_attrs.alpha;
	}
	out->attrs_valid = 1;
}

/* Bounds apply only to the next submitted batch. Callers bracket an alias
 * draw and clear the hint even if no geometry was submitted. */
void
OFGL_SetDrawBounds(const float mins[3], const float maxs[3])
{
	ofgl_draw_bounds_valid = 0;
	ofgl_batch_inside = 0;
	if (!mins || !maxs) return;
	for (int i = 0; i < 3; i++)
	{
		if (!ofgl_finite(mins[i]) || !ofgl_finite(maxs[i]) || mins[i] > maxs[i]) return;
		ofgl_draw_center[i] = (mins[i] + maxs[i]) * 0.5f;
		ofgl_draw_extent[i] = (maxs[i] - mins[i]) * 0.5f;
		if (!ofgl_finite(ofgl_draw_center[i]) || !ofgl_finite(ofgl_draw_extent[i])) return;
		/* Cover center/extent conversion and transformed-plane rounding. */
		ofgl_draw_extent[i] += 0.125f + fabsf(ofgl_draw_center[i]) * 0.00001f;
	}
	ofgl_draw_bounds_valid = 1;
}

static ofgl_cvert_t *
ofgl_cvert(int i)
{
	int slot = i & (OFGL_VMEMO_N - 1);

	if (ofgl_vmemo_tag[slot] == ofgl_vmemo_cur && ofgl_vmemo_idx[slot] == i)
	{
		return &ofgl_vmemo[slot];
	}
	const float *v = ofgl_fetch_position(i, &ofgl_vmemo[slot]);
	if (ofgl_batch_world) ofgl_world_project_original(v, &ofgl_vmemo[slot]);
	if (ofgl_batch_inside)
		ofgl_vmemo[slot].clipcode = ofgl_vmemo[slot].viewcode = 0;
	else
		ofgl_classify_cvert(&ofgl_vmemo[slot]);
	ofgl_vmemo_idx[slot] = i;
	ofgl_vmemo_tag[slot] = ofgl_vmemo_cur;
	return &ofgl_vmemo[slot];
}

/* Alias position identity is independent of its UV seam identity. Reuse
 * the existing position/projection memo; this small tag identifies which seam
 * owns the optional cached attributes. It is read only after a current-batch
 * geometry hit, so no extra clear or generation counter is needed. */
static uint16_t ofgl_alias_attridx[OFGL_VMEMO_N];

static ofgl_cvert_t *
ofgl_alias_cvert(int i, int xyz)
{
	int slot = xyz & (OFGL_VMEMO_N - 1);
	if (ofgl_vmemo_tag[slot] == ofgl_vmemo_cur && ofgl_vmemo_idx[slot] == xyz)
	{
		if (ofgl_alias_attridx[slot] != i)
		{
			ofgl_vmemo[slot].attrs_valid = 0;
			ofgl_alias_attridx[slot] = i;
		}
		return &ofgl_vmemo[slot];
	}
	ofgl_fetch_position(xyz, &ofgl_vmemo[slot]);
	if (ofgl_batch_inside)
		ofgl_vmemo[slot].clipcode = ofgl_vmemo[slot].viewcode = 0;
	else
		ofgl_classify_cvert(&ofgl_vmemo[slot]);
	ofgl_vmemo_idx[slot] = xyz;
	ofgl_vmemo_tag[slot] = ofgl_vmemo_cur;
	ofgl_alias_attridx[slot] = i;
	return &ofgl_vmemo[slot];
}

/* Sutherland-Hodgman against one plane. s/t/colour interpolate linearly in
 * camera space (they are affine in position on the source surface), so a
 * clipped vertex gets exact texcoords. Returns the new vertex count. */
static int
ofgl_clip_plane(const ofgl_plane_t *p, const ofgl_cvert_t *in, int n,
	ofgl_cvert_t *out)
{
	int m = 0, i;
	float da = ofgl_plane_dist(p, &in[n - 1]);

	for (i = 0; i < n; i++)
	{
		const ofgl_cvert_t *a = &in[(i + n - 1) % n];
		const ofgl_cvert_t *b = &in[i];
		float db = ofgl_plane_dist(p, b);

		if (da >= 0.0f)
		{
			if (m < OFGL_CLIP_MAX)
			{
				out[m++] = *a;
			}
		}
		if ((da >= 0.0f) != (db >= 0.0f) && m < OFGL_CLIP_MAX)
		{
			float d = da - db;
			float f = (d != 0.0f) ? (da / d) : 0.0f;

			out[m].x = a->x + (b->x - a->x) * f;
			out[m].y = a->y + (b->y - a->y) * f;
			out[m].z = a->z + (b->z - a->z) * f;
			out[m].s = a->s + (b->s - a->s) * f;
			out[m].t = a->t + (b->t - a->t) * f;
			/* Lighting and alpha follow the same edge interpolation as UVs.
			 * Taking the outside endpoint's colour made clipped models flash. */
			int ar = (a->rgb >> 11) & 31, br = (b->rgb >> 11) & 31;
			int ag = (a->rgb >> 5) & 63, bg = (b->rgb >> 5) & 63;
			int ab = a->rgb & 31, bb = b->rgb & 31;
			out[m].rgb = (uint16_t)(((int)(ar + (br - ar) * f + 0.5f) << 11) |
				((int)(ag + (bg - ag) * f + 0.5f) << 5) |
				(int)(ab + (bb - ab) * f + 0.5f));
			out[m].a = (uint8_t)(a->a + (b->a - a->a) * f + 0.5f);
			out[m].clipcode = 0;
			ofgl_project_cvert(&out[m]);
			m++;
		}
		da = db;
	}
	return m;
}

/* Clip a triangle against the near plane and the guard band. Returns the
 * polygon vertex count (0 = fully rejected); the caller fans it.
 *
 * Before this existed the code DROPPED any triangle with a vertex nearer than
 * the near plane -- Quake2's world polygons are large, so standing next to a
 * wall deleted the whole surface and you could see through it. */
static int
ofgl_clip_poly(ofgl_cvert_t *poly, int n)
{
	ofgl_cvert_t scratch[OFGL_CLIP_MAX];
	int i;

	for (i = 0; i < OFGL_NPLANE && n >= 3; i++)
	{
		int outside = 0, k;

		/* skip the plane entirely when every vertex is already inside */
		for (k = 0; k < n; k++)
		{
			if (ofgl_plane_dist(&ofgl_cplane[i], &poly[k]) < 0.0f)
			{
				outside++;
			}
		}
		if (outside == 0)
		{
			continue;
		}
		if (outside == n)
		{
			return 0;	/* wholly outside this plane */
		}
		n = ofgl_clip_plane(&ofgl_cplane[i], poly, n, scratch);
		memcpy(poly, scratch, (size_t)n * sizeof(poly[0]));
	}
	return (n >= 3) ? n : 0;
}

static void ofgl_setup_tri_state(const ofq2_texture_t *tex, int alpha);

static int
ofgl_front_visible(const ofgl_cvert_t *a, const ofgl_cvert_t *b,
	const ofgl_cvert_t *c)
{
	if ((a->projected_valid | b->projected_valid | c->projected_valid) != 1)
	{
		return 0;
	}
	float area = (b->sx - a->sx) * (c->sy - a->sy) -
		(c->sx - a->sx) * (b->sy - a->sy);
	/* GL window Y points up, while our screen Y points down. GL_FRONT with
	 * CCW winding therefore keeps positive area here. */
	if (ofgl_cull_on)
	{
		int cull_ccw = (ofgl_cull_face == GL_FRONT) ==
			(ofgl_front_face == GL_CCW);
		return cull_ccw ? area > 0.0f : area < 0.0f;
	}
	return area != 0.0f;
}

/* Keep optional reporting and its screen-coordinate temporaries off the
 * normal triangle path. The caller retains the original diagnostic guard. */
static void __attribute__((noinline, cold))
ofgl_report_big_tri(const ofgl_cvert_t *v0, const ofgl_cvert_t *v1,
	const ofgl_cvert_t *v2, float tw, float th)
{
	const ofgl_cvert_t *v[3] = {v0, v1, v2};
	float fx[3], fy[3];
	int k;
	for (k = 0; k < 3; k++)
	{
		fx[k] = v[k]->sx;
		fy[k] = v[k]->sy;
	}
	float bx0 = fx[0], bx1 = fx[0], by0 = fy[0], by1 = fy[0];

	for (k = 1; k < 3; k++)
	{
		if (fx[k] < bx0) bx0 = fx[k];
		if (fx[k] > bx1) bx1 = fx[k];
		if (fy[k] < by0) by0 = fy[k];
		if (fy[k] > by1) by1 = fy[k];
	}
	if ((bx1 - bx0) >= (float)ofgl_vp_w && (by1 - by0) >= (float)ofgl_vp_h)
	{
		ofgl_dbg_big++;
		Com_Printf("of_gl1: big tri x[%.0f..%.0f] y[%.0f..%.0f] "
			"z %.1f/%.1f/%.1f tex %dx%d lm%d tex%d clr%d blend%d zm%d\n",
			bx0, bx1, by0, by1, v[0]->z, v[1]->z, v[2]->z,
			(int)tw, (int)th, ofgl_lm_active, ofgl_tex2d_enabled,
			(ofgl_va_color.enabled && ofgl_va_color.ptr) ? 1 : 0,
			ofgl_blend_on,
			ofgl_depth_test ? (ofgl_depth_mask ? 3 : 2) : 0);
	}
}

/* Floor the texture-rebase quotient with the F extension. Values below
 * 2^23 fit exactly in both conversion directions; explicit round-down avoids
 * depending on the current floating-point rounding mode. Signed zero is
 * unchanged; floats at least 2^23 are already integral. The bundled RV32
 * floorf also returns infinity/NaN input bits unchanged, so those paths need
 * no call or floating-point operation. Keep the library on other hosts. */
static inline float
ofgl_uv_floor(float v)
{
#if defined(__riscv) && defined(__riscv_flen) && __riscv_flen >= 32
	uint32_t bits;
	memcpy(&bits, &v, sizeof(bits));
	uint32_t magnitude = bits & 0x7fffffffu;
	if (magnitude != 0 && magnitude < 0x4b000000u)
	{
		int32_t integral;
		__asm__("fcvt.w.s %0, %1, rdn" : "=r"(integral) : "f"(v));
		return (float)integral;
	}
	return v;
#else
	return floorf(v);
#endif
}

/* Emit a finite, visible triangle. Returns 1 if it reached the GPU. */
static int
ofgl_emit_ctri(const ofgl_cvert_t *v0, const ofgl_cvert_t *v1,
	const ofgl_cvert_t *v2, float tw, float th)
{
	const ofgl_cvert_t *v[3];
	float iw[3];
	/* The submit owner accepts the final wire layout directly: XY, S, T,
	 * ZI, packed RGB565, then independent depth. */
	uint32_t words[17];
	float base_s, base_t, mag = 0.0f, zscale;
	int k;
	int alpha = ofgl_blend_on ? (v0->a + v1->a + v2->a + 1) / 3 : 255;

	/* Particle batches carry a different alpha for each triangle. A fully
	 * faded particle must not become an opaque, depth-writing triangle. */
	if (alpha == 0)
	{
		return 0;
	}

	v[0] = v0; v[1] = v1; v[2] = v2;

	for (k = 0; k < 3; k++)
	{
		iw[k] = v[k]->iw;
	}

	/* Texcoord rebase. The sampler wraps modulo the (power-of-two) texture
	 * size, so subtracting a whole multiple of it leaves every sampled texel
	 * identical -- while removing the arbitrary, potentially huge texinfo
	 * offset a Quake2 surface carries. That keeps the derived s*zi / t*zi
	 * planes inside the hardware's 33-bit difference saturation, and lets the
	 * per-triangle scale below spend its range on precision instead of
	 * absorbing an offset nobody can see. */
	base_s = v0->s;
	base_t = v0->t;
	for (k = 1; k < 3; k++)
	{
		if (v[k]->s < base_s) base_s = v[k]->s;
		if (v[k]->t < base_t) base_t = v[k]->t;
	}
	base_s = ofgl_uv_floor(base_s * ofgl_inv_tw) * tw;
	base_t = ofgl_uv_floor(base_t * ofgl_inv_th) * th;

	for (k = 0; k < 3; k++)
	{
		float a;

		/* Bound the three derived attribute planes: szi = (s-base)*iw,
		 * tzi = (t-base)*iw, zi = iw. */
		a = fabsf(iw[k]);
		if (a > mag) mag = a;
		a = fabsf((v[k]->s - base_s) * iw[k]);
		if (a > mag) mag = a;
		a = fabsf((v[k]->t - base_t) * iw[k]);
		if (a > mag) mag = a;
	}

	if (!ofgl_finite(mag) || !ofgl_finite(base_s) || !ofgl_finite(base_t))
	{
		return 0;
	}

	/* zi = iw*zscale and szi/tzi = (s-base)*iw*zscale are all <= mag*zscale =
	 * OFGL_ZI_TARGET by construction, so the emitted Q16.16 tops out at 2^29 --
	 * inside the derive's 33-bit difference saturation. The upper clamp only
	 * exists so a pathological near-zero magnitude can't overflow the float
	 * 65536*zscale product on the way there. */
	if (ofgl_bisect_cv && ofgl_bisect_cv->value && ofgl_dbg_big < 20)
	{
		ofgl_report_big_tri(v0, v1, v2, tw, th);
	}

	zscale = (mag > 0.0f) ? (OFGL_ZI_TARGET / mag) : OFGL_ZI_FALLBACK;
	if (zscale < 1.0f)
	{
		zscale = 1.0f;		/* never shrink below 1:1 */
	}
	else if (zscale > 1.0e12f)
	{
		zscale = 1.0e12f;
	}

	for (k = 0; k < 3; k++)
	{
		int32_t z;

		words[k] = ((uint32_t)(uint16_t)v[k]->py << 16) | (uint16_t)v[k]->px;
		words[3 + k] = (uint32_t)(int32_t)lrintf((v[k]->s - base_s) * 65536.0f);
		words[6 + k] = (uint32_t)(int32_t)lrintf((v[k]->t - base_t) * 65536.0f);

		z = (int32_t)lrintf(iw[k] * (65536.0f * zscale));
		words[9 + k] = (uint32_t)((z < 1) ? 1 : z);

		/* Depth is decoupled from zi (0x4E words 14-16): a fixed 1/z * 2^30
		 * that the GPU float-compresses, so the per-triangle zi scale above
		 * can't perturb z ordering between triangles. */
		words[14 + k] = (uint32_t)v[k]->depth;
	}
	words[12] = ((uint32_t)v1->rgb << 16) | v0->rgb;
	words[13] = v2->rgb;

	if (alpha != ofgl_batch_alpha)
	{
		ofgl_setup_tri_state(ofgl_batch_tex, alpha);
		ofgl_batch_alpha = alpha;
	}
	return OFQ2_DrawVertTriRGBPacked(words);
}

static void
ofgl_setup_tri_state(const ofq2_texture_t *tex, int alpha)
{
	ofq2_tri_state_t st;

	memset(&st, 0, sizeof(st));
	st.fb_base = (uint32_t)(uintptr_t)ofgl_fb();
	st.fb_major_step = ofgl_fb_stride;	/* bytes/scanline (RGB565 = width*2) */
	st.fb_minor_step = 2;			/* RGB565: 2 bytes/pixel */
	st.tex_addr = tex->addr;
	st.tex_width = tex->width;
	/* Power-of-two by construction (ofgl_TexImage2D resamples every GPU
	 * texture), so the sampler's bitmask wrap is a true modulo. */
	st.tex_w_mask = (uint16_t)(tex->width - 1);
	st.tex_h_mask = (uint16_t)(tex->height - 1);
	/* TRUECOLOR: sample RGB565 texels + write RGB565 to the framebuffer.
	 * SKIP_ZERO: 0x0000 texels are the transparent key (masked surfaces).
	 * PERSP: perspective-correct s/t (else affine warping on angled surfaces). */
	st.flags = OFQ2_EMIT_TRUECOLOR | OFQ2_EMIT_SKIP_ZERO | OFQ2_EMIT_PERSP;
	st.colormap_id = 0;

	/* Hardware src-over blend: gl1 arms glEnable(GL_BLEND) + a glColor4f alpha
	 * for translucent water/glass, sky, particles and the dynamic-light flash.
	 * Only genuinely translucent surfaces blend -- a fully opaque alpha would
	 * cost the read-modify-write for nothing, and alpha 0 would erase the
	 * surface, which several gl1 paths reach transiently. */
	if (ofgl_blend_on && alpha > 0 && alpha < 255)
	{
		st.flags |= OFQ2_EMIT_BLEND;
		st.const_alpha = (uint8_t)alpha;
	}

	if (!ofgl_depth_test)
	{
		st.z_mode = OFQ2_PARAM_Z_NONE;
	}
	else
	{
		st.z_mode = ofgl_depth_mask ? OFQ2_PARAM_Z_TEST_WRITE
			: OFQ2_PARAM_Z_TEST_ZI;
	}
	st.z_base = ofgl_z_base;
	st.z_major_step = ofgl_z_major;
	st.z_minor_step = OFGL_Z_BYTES;
	st.clip_x0 = (int16_t)ofgl_vp_x;
	st.clip_x1 = (int16_t)(ofgl_vp_x + ofgl_vp_w);
	st.clip_y0 = (int16_t)ofgl_vp_y;
	st.clip_y1 = (int16_t)(ofgl_vp_y + ofgl_vp_h);
	st.attr_q29 = 0;
	/* Vertex y is Q12.4 (control bit 31): 1/16-scanline edge precision instead
	 * of snapping every vertex to a whole scanline. Every triangle this file
	 * emits goes through the same path, so there is no mixed-quantization seam. */
	st.subpix_y = 1;
	OFQ2_SetTriState(&st);
}

/* Arm the GPU for a 3D batch: resolve the texture, refresh the projection
 * constants, emit the sticky surface state. Returns 0 if 3D can't run. */
static int
ofgl_begin3d(const ofq2_texture_t **out_tex, float *out_tw, float *out_th)
{
	const ofq2_texture_t *tex;
	float tw, th;
	int bounds = ofgl_draw_bounds_valid;
	int world = ofgl_world_draw_valid;
	ofgl_draw_bounds_valid = ofgl_world_draw_valid = 0;
	ofgl_batch_inside = ofgl_batch_world = 0;

	if (!ofgl_gpu_ok || !ofgl_va_vertex.ptr)
	{
		return 0;
	}

	/* The projection MUST be a perspective frustum: the whole path below
	 * divides by view-space z. A perspective matrix has proj[11] == -1 (and
	 * proj[15] == 0); ortho has proj[11] == 0. R_SetGL2D installs an ortho
	 * projection for the HUD, and any 3D draw issued under it -- or under a
	 * bare glLoadIdentity, mid-way through gl1 rebuilding the matrix -- would
	 * push world coordinates through a meaningless transform and land
	 * arbitrarily large triangles on screen. Refuse instead of guessing. */
	if (!(ofgl_proj[11] < -0.5f))
	{
		return 0;
	}

	/* Every CPU-prepared, indexed and raw-world GPU draw enters here. */
	ofgl_fb_clear.fb = NULL;
	ofgl_ensure_flat_tex();

	/* Is this a lightmapped world batch? R_EnableMultitexture is what arms
	 * unit 1 (and it declines when r_fullbright is set), so unit 1's
	 * GL_TEXTURE_2D enable is the honest signal -- gl1 leaves unit 1's client
	 * texcoord array enabled after an mtex batch, so that flag alone isn't. */
	ofgl_lm_cur = NULL;
	ofgl_lm_active = 0;
	if (ofgl_draw_enabled(OFGL_DRAW_LIGHTMAP) &&
		ofgl_tex2d_on[1] && ofgl_va_texcoord_tmu[1].enabled &&
		ofgl_va_texcoord_tmu[1].ptr)
	{
		int block = ofgl_lm_index(ofgl_tex_bound[1]);

		if (block >= 0 && ofgl_lm[block])
		{
			ofgl_lm_cur = ofgl_lm[block];
			ofgl_lm_active = 1;
		}
	}

	/* Bind the currently-bound texture's GPU handle (world surface / skin);
	 * fall back to the flat white texture for untextured draws. The GPU handle
	 * is built lazily on first 3D use, so 2D-only pics never consume fast
	 * texture memory. */
	/* glDisable(GL_TEXTURE_2D) means "vertex colour only": gl1 arms it for
	 * powerup shells, dynamic-light flashes and shadow volumes. The GPU always
	 * samples SOMETHING, so point it at the flat white texture -- modulating
	 * white by the vertex colour reproduces the untextured result. Sampling
	 * the stale bound texture instead painted those effects with whatever
	 * skin happened to be bound. */
	tex = &ofgl_flat_tex;
	if (ofgl_tex2d_enabled && ofgl_cur_tex > 0 && ofgl_cur_tex < OFGL_MAX_TEX)
	{
		ofgl_tex_t *ct = &ofgl_tex[ofgl_cur_tex];

		if (!ct->gtex_valid && ct->texels)
		{
			/* fallback lazy build (world textures are built eagerly at upload) */
			OFQ2_TexCreate(&ct->gtex, ct->texels, ct->w, ct->h,
				(uint32_t)((size_t)ct->w * (size_t)ct->h * 2));
			ct->gtex_valid = 1;
		}
		if (ct->gtex_valid)
		{
			tex = &ct->gtex;
		}
	}
	tw = (float)tex->width;
	th = (float)tex->height;
	ofgl_inv_tw = 1.0f / tw;
	ofgl_inv_th = 1.0f / th;

	OFQ2_TexBindDomain(tex);
	if (ofgl_obj_dirty)
	{
		/* CPU-projection constants for the vert-tri path. */
		ofgl_pc_xs = 0.5f * ofgl_vp_w * ofgl_proj[0];
		ofgl_pc_ys = 0.5f * ofgl_vp_h * ofgl_proj[5];
		ofgl_pc_xc = ofgl_vp_x + 0.5f * ofgl_vp_w - 0.5f;
		ofgl_pc_yc = ofgl_vp_y + 0.5f * ofgl_vp_h - 0.5f;
		ofgl_pc_near = (ofgl_proj[10] != 1.0f)
			? ofgl_proj[14] / (ofgl_proj[10] - 1.0f) : 4.0f;
		if (!(ofgl_pc_near > 0.1f))
		{
			ofgl_pc_near = 4.0f;
		}
		ofgl_build_clip_planes();
		ofgl_world_epoch++;
		ofgl_obj_dirty = 0;
	}
	ofgl_batch_inside = bounds && ofgl_draw_bounds_inside();
	ofgl_batch_world = world && ofgl_world_projection_enabled();
	/* Alpha is resolved from the actual triangle, including nonzero first /
	 * index offsets and independently fading particles within a single draw. */
	ofgl_batch_tex = tex;
	ofgl_batch_alpha = -1;
	if (!ofgl_blend_on)
	{
		ofgl_setup_tri_state(tex, 255);
		ofgl_batch_alpha = 255;
	}
	ofgl_prepare_attrs();
	ofgl_vmemo_new_batch();
	ofgl_gpu_slots_valid = 0;
	ofgl_geometry_state_ready = 0;
	/* ofgl_geometry currently offloads persistent BSP world surfaces only.
	 * Generic/alias indexed vertices must first share an exact projection
	 * convention with their CPU-clipped neighbours before enabling 0x56. */
	ofgl_indexed_geometry = 0;

	*out_tex = tex;
	*out_tw = tw;
	*out_th = th;
	return 1;
}

/* Keep the polygon scratch buffer off the common, unclipped triangle path. */
static void __attribute__((noinline))
ofgl_clipped_tri(const ofgl_cvert_t *a, const ofgl_cvert_t *b,
	const ofgl_cvert_t *c, float tw, float th)
{
	ofgl_cvert_t poly[OFGL_CLIP_MAX];
	poly[0] = *a; poly[1] = *b; poly[2] = *c;
	int np = ofgl_clip_poly(poly, 3);
	for (int k = 2; k < np; k++)
	{
		if (ofgl_front_visible(&poly[0], &poly[k - 1], &poly[k]))
		{
			ofgl_emit_ctri(&poly[0], &poly[k - 1], &poly[k], tw, th);
		}
	}
}

static void
ofgl_cached_tri(int i0, int i1, int i2, ofgl_cvert_t *a,
	ofgl_cvert_t *b, ofgl_cvert_t *c, float tw, float th)
{
	/* Wholly offscreen primitives can spill colour into the viewport on
	 * OS30. Reject them here as well as avoiding their attribute/setup work. */
	if ((a->viewcode & b->viewcode & c->viewcode) ||
		(a->clipcode & b->clipcode & c->clipcode))
	{
		return;
	}
	if (ofgl_indexed_geometry &&
		!(a->clipcode | b->clipcode | c->clipcode | a->viewcode | b->viewcode | c->viewcode) &&
		ofgl_geometry_tri(i0, i1, i2, a, b, c, tw, th)) return;
	if (!a->projected_valid) ofgl_project_cvert(a);
	if (!b->projected_valid) ofgl_project_cvert(b);
	if (!c->projected_valid) ofgl_project_cvert(c);
	int clipped = a->clipcode | b->clipcode | c->clipcode;
	/* Reject backfaces before fetching UVs, lightmaps and colours. Clipped
	 * triangles need their final screen positions before the winding test. */
	if (!clipped && !ofgl_front_visible(a, b, c))
	{
		return;
	}
	if (!a->attrs_valid) ofgl_fetch_attrs(i0, tw, th, a);
	if (!b->attrs_valid) ofgl_fetch_attrs(i1, tw, th, b);
	if (!c->attrs_valid) ofgl_fetch_attrs(i2, tw, th, c);
	if (!clipped)
	{
		ofgl_emit_ctri(a, b, c, tw, th);
	}
	else if (ofgl_draw_enabled(OFGL_DRAW_CLIPPED))
	{
		ofgl_clipped_tri(a, b, c, tw, th);
	}
}

/* A large batch may put two different indices in the same memo slot. Copy
 * before the next lookup in that case; the usual fan/strip uses cache entries
 * directly, without copying three complete vertices for every triangle. */
static void __attribute__((noinline))
ofgl_colliding_tri(int i0, int i1, int i2, float tw, float th)
{
	ofgl_cvert_t a = *ofgl_cvert(i0);
	ofgl_cvert_t b = *ofgl_cvert(i1);
	ofgl_cvert_t c = *ofgl_cvert(i2);
	ofgl_cached_tri(i0, i1, i2, &a, &b, &c, tw, th);
}

static void
ofgl_tri3d(int i0, int i1, int i2, float tw, float th)
{
	if (!((i0 ^ i1) & (OFGL_VMEMO_N - 1)) ||
		!((i1 ^ i2) & (OFGL_VMEMO_N - 1)) ||
		!((i0 ^ i2) & (OFGL_VMEMO_N - 1)))
	{
		ofgl_colliding_tri(i0, i1, i2, tw, th);
		return;
	}
	ofgl_cvert_t *a = ofgl_cvert(i0);
	ofgl_cvert_t *b = ofgl_cvert(i1);
	ofgl_cvert_t *c = ofgl_cvert(i2);
	ofgl_cached_tri(i0, i1, i2, a, b, c, tw, th);
}

static void __attribute__((noinline))
ofgl_alias_colliding_tri(int i0, int i1, int i2, int x0, int x1, int x2,
	float tw, float th)
{
	ofgl_cvert_t a = *ofgl_alias_cvert(i0, x0);
	ofgl_cvert_t b = *ofgl_alias_cvert(i1, x1);
	ofgl_cvert_t c = *ofgl_alias_cvert(i2, x2);
	ofgl_cached_tri(i0, i1, i2, &a, &b, &c, tw, th);
}

static void
ofgl_alias_tri(int i0, int i1, int i2, const byte *map, int stride,
	float tw, float th)
{
	int x0 = *(const int *)(map + (size_t)i0 * stride);
	int x1 = *(const int *)(map + (size_t)i1 * stride);
	int x2 = *(const int *)(map + (size_t)i2 * stride);
	if (!((x0 ^ x1) & (OFGL_VMEMO_N - 1)) ||
		!((x1 ^ x2) & (OFGL_VMEMO_N - 1)) ||
		!((x0 ^ x2) & (OFGL_VMEMO_N - 1)))
	{
		ofgl_alias_colliding_tri(i0, i1, i2, x0, x1, x2, tw, th);
		return;
	}
	ofgl_cvert_t *a = ofgl_alias_cvert(i0, x0);
	ofgl_cvert_t *b = ofgl_alias_cvert(i1, x1);
	ofgl_cvert_t *c = ofgl_alias_cvert(i2, x2);
	ofgl_cached_tri(i0, i1, i2, a, b, c, tw, th);
}

static void
ofgl_end3d(void)
{
	ofgl_gpu_pending = 1;
	/* Publish this batch so the GPU rasterises it while the CPU builds the
	 * next one; without a kick the ring only drains when it fills. */
	OFQ2_Kick();
}

/* A BSP polygon owns this cache for its whole model lifetime. The immutable
 * positions/UVs stay in the model hunk; only material-sized UVs and sampled
 * light colours need refreshing after a texture or lightmap change. */
#define OFGL_WORLD_VERTS 32
struct ofgl_world_poly_s
{
	const float *verts;
	float center[3], extent[3], max_st;
	int count, raw_ok, tex_w, tex_h, lm_block;
	uint32_t light_version;
	uint16_t light_index[OFGL_WORLD_VERTS];
	uint32_t words[];
};

size_t
OFGL_WorldPolyBytes(int count)
{
	return count >= 3 && count <= OFGL_WORLD_VERTS ?
		sizeof(ofgl_world_poly_t) + count * 5 * sizeof(uint32_t) : 0;
}

ofgl_world_poly_t *
OFGL_BuildWorldPoly(void *storage, const float *verts, int count)
{
	size_t bytes = OFGL_WorldPolyBytes(count);
	if (!storage || !verts || !bytes) return NULL;
	ofgl_world_poly_t *p = storage;
	memset(p, 0, bytes);
	p->verts = verts;
	p->count = count;
	p->raw_ok = 1;
	p->lm_block = -2;
	float lo[3] = {verts[0], verts[1], verts[2]};
	float hi[3] = {verts[0], verts[1], verts[2]};
	for (int i = 0; i < count; i++)
	{
		const float *v = verts + 7 * i;
		int16_t xyz[3] = {0, 0, 0};
		for (int j = 0; j < 3; j++)
		{
			if (v[j] < lo[j]) lo[j] = v[j];
			if (v[j] > hi[j]) hi[j] = v[j];
			/* Exact quarter-unit positions in a signed 16-bit word. Never
			 * quantize a BSP seam differently on its two adjacent surfaces. */
			float q = v[j] * 4.0f;
			if (!ofgl_finite(q) || q < -32768.0f || q > 32767.0f)
				p->raw_ok = 0;
			else
			{
				xyz[j] = (int16_t)lrintf(q);
				if ((float)xyz[j] != q) p->raw_ok = 0;
			}
		}
		p->words[i * 5] = i;
		p->words[i * 5 + 1] = (uint16_t)xyz[0] | ((uint32_t)(uint16_t)xyz[1] << 16);
		p->words[i * 5 + 2] = (uint16_t)xyz[2];
		int x = (int)(v[5] * OFGL_LM_DIM), y = (int)(v[6] * OFGL_LM_DIM);
		if (x < 0) x = 0; else if (x >= OFGL_LM_DIM) x = OFGL_LM_DIM - 1;
		if (y < 0) y = 0; else if (y >= OFGL_LM_DIM) y = OFGL_LM_DIM - 1;
		p->light_index[i] = y * OFGL_LM_DIM + x;
	}
	for (int j = 0; j < 3; j++)
	{
		p->center[j] = (lo[j] + hi[j]) * 0.5f;
		/* Inflate bounds to cover both float and GPU matrix rounding. */
		p->extent[j] = (hi[j] - lo[j]) * 0.5f + 0.125f + fabsf(p->center[j]) * 0.00001f;
	}
	return p;
}

int
OFGL_WorldPolysEnabled(void)
{
	return ofgl_world_cv && ofgl_world_cv->value != 0;
}

static ofgl_plane_t ofgl_world_planes[5];
static float ofgl_world_abs[5][3];
static unsigned ofgl_world_prepared;
static ofq2_object_state_t ofgl_world_object;
static int ofgl_world_raw_matrix;

static void
ofgl_world_prepare(void)
{
	if (ofgl_world_prepared == ofgl_world_epoch) return;
	ofgl_world_prepared = ofgl_world_epoch;
	const float *mv = OFGL_MV;
	for (int i = 0; i < 5; i++)
	{
		ofgl_plane_t p = ofgl_cplane[i];
		/* The four viewport planes are used for conservative surface culling.
		 * Full containment also prevents wholly offscreen fan triangles from
		 * reaching the hardware walker. Retain the CPU's one-pixel margin. */
		if (i > 0) p.c -= (i < 3 ? OFGL_GUARD_X : OFGL_GUARD_Y) - 1.0f;
		ofgl_plane_t *out = &ofgl_world_planes[i];
		out->a = p.a * mv[0] - p.b * mv[1] - p.c * mv[2];
		out->b = p.a * mv[4] - p.b * mv[5] - p.c * mv[6];
		out->c = p.a * mv[8] - p.b * mv[9] - p.c * mv[10];
		out->e = p.a * mv[12] - p.b * mv[13] - p.c * mv[14] + p.e;
		ofgl_world_abs[i][0] = fabsf(out->a);
		ofgl_world_abs[i][1] = fabsf(out->b);
		ofgl_world_abs[i][2] = fabsf(out->c);
	}

	/* The raw worker divides 2^38 by cw for depth, requiring cw = z*256
	 * for the same independent 2^30/z depth used by CPU triangles. Position
	 * words are world*4, so coefficients are camera*2^22, translations *256.
	 * Fold the fractional viewport scales and half-pixel centres into the
	 * matrix. A 1/32-pixel bias makes the worker's floor approximate the
	 * CPU's nearest Q12.4 rounding. Y's final subtraction rounds upwards. */
	memset(&ofgl_world_object, 0, sizeof(ofgl_world_object));
	ofgl_world_object.xscale = 128;
	ofgl_world_object.yscale = 2048;
	ofgl_world_object.xcenter = (int)floorf(ofgl_pc_xc);
	ofgl_world_object.ycenter = (int)lrintf(ofgl_pc_yc * 16.0f);
	ofgl_world_object.near_clip = (int)ceilf(ofgl_pc_near * 256.0f);
	ofgl_world_object.rows = 3;
	float xb = (ofgl_pc_xc - ofgl_world_object.xcenter + 0.03125f) / 128.0f;
	float yb = (ofgl_world_object.ycenter / 16.0f - ofgl_pc_yc + 0.03125f) / 128.0f;
	ofgl_world_raw_matrix = 1;
	for (int j = 0; j < 4; j++)
	{
		float scale = j == 3 ? 256.0f : 4194304.0f;
		float x = (mv[j * 4] * (ofgl_pc_xs / 128.0f) - mv[j * 4 + 2] * xb) * scale;
		float y = (mv[j * 4 + 1] * (ofgl_pc_ys / 128.0f) - mv[j * 4 + 2] * yb) * scale;
		float z = -mv[j * 4 + 2] * scale;
		if (!ofgl_finite(x) || !ofgl_finite(y) || !ofgl_finite(z) ||
			fabsf(x) > 2147483520.0f || fabsf(y) > 2147483520.0f || fabsf(z) > 2147483520.0f)
		{
			ofgl_world_raw_matrix = 0;
			break;
		}
		ofgl_world_object.matrix[0][j] = (int32_t)lrintf(x);
		ofgl_world_object.matrix[1][j] = (int32_t)lrintf(y);
		ofgl_world_object.matrix[2][j] = (int32_t)lrintf(z);
	}
}

/* The convention also covers generic buffered world draws: flowing,
 * translucent, unlightmapped and oversized BSP polygons can share an edge with
 * a raw GPU surface. Attributes and CPU-generated clip intersections stay live. */
static int
ofgl_world_projection_enabled(void)
{
	if (!ofgl_geometry_cv || !ofgl_geometry_cv->value ||
		!OFQ2_Supports(OFQ2_CAP_GEOMETRY) || ofgl_depth_scale != 1.0f) return 0;
	ofgl_world_prepare();
	return ofgl_world_raw_matrix;
}

static int
ofgl_geometry_tri(int i0, int i1, int i2, ofgl_cvert_t *a,
	ofgl_cvert_t *b, ofgl_cvert_t *c, float tw, float th)
{
	int indices[3] = {i0, i1, i2};
	ofgl_cvert_t *verts[3] = {a, b, c};
	/* Reject backfaces before uploading their attributes. With positive Z,
	 * this homogeneous determinant has the screen-space winding sign but
	 * needs no perspective divides or viewport projections. */
	if (ofgl_cull_on)
	{
		float bx = b->x * a->z - a->x * b->z;
		float by = b->y * a->z - a->y * b->z;
		float cx = c->x * a->z - a->x * c->z;
		float cy = c->y * a->z - a->y * c->z;
		float area = (bx * cy - cx * by) * (ofgl_pc_xs * ofgl_pc_ys);
		int positive = (ofgl_cull_face == GL_FRONT) == (ofgl_front_face == GL_CCW);
		if (positive ? area <= 0.0f : area >= 0.0f) return 1;
	}
	if (!((i0 ^ i1) & 31) || !((i1 ^ i2) & 31) || !((i0 ^ i2) & 31)) return 0;
	ofgl_world_prepare();
	uint32_t uploads[3 * 8];
	unsigned count = 0;
	float xb = (ofgl_pc_xc - ofgl_world_object.xcenter + 0.03125f) / 128.0f;
	float yb = (ofgl_world_object.ycenter / 16.0f - ofgl_pc_yc + 0.03125f) / 128.0f;
	for (int k = 0; k < 3; k++)
	{
		unsigned slot = indices[k] & 31;
		if ((ofgl_gpu_slots_valid & (1u << slot)) && ofgl_gpu_slots[slot] == indices[k]) continue;
		ofgl_cvert_t *v = verts[k];
		if (!v->attrs_valid) ofgl_fetch_attrs(indices[k], tw, th, v);
		/* zi=256/z is fixed in this cache. The separate depth remains exact,
		 * including the weapon depth-range hack. Limit all derived attributes
		 * to the same magnitude allowed by the CPU's per-triangle scaling. */
		float maxst = fmaxf(1.0f, fmaxf(fabsf(v->s), fabsf(v->t)));
		if (!ofgl_finite(maxst) || !ofgl_finite(v->z) ||
			v->z < 16.0f || v->z < maxst / 32.0f || v->z > 4096.0f || maxst > 32767.0f) return 0;
		float x = (v->x * (ofgl_pc_xs / 128.0f) + v->z * xb) * 256.0f;
		float y = (-v->y * (ofgl_pc_ys / 128.0f) + v->z * yb) * 256.0f;
		if (!ofgl_finite(x) || !ofgl_finite(y) ||
			fabsf(x) > 2147483520.0f || fabsf(y) > 2147483520.0f) return 0;
		float depth = (1.0f / v->z) * (1073741824.0f * ofgl_depth_scale);
		if (depth < 1.0f) depth = 1.0f;
		else if (depth > 2147483520.0f) depth = 2147483520.0f;
		uint32_t *w = uploads + count++ * 8;
		w[0] = slot;
		w[1] = (uint32_t)(int32_t)lrintf(x);
		w[2] = (uint32_t)(int32_t)lrintf(y);
		w[3] = (uint32_t)(int32_t)lrintf(v->z * 256.0f);
		w[4] = (uint32_t)(int32_t)lrintf(v->s * 65536.0f);
		w[5] = (uint32_t)(int32_t)lrintf(v->t * 65536.0f);
		w[6] = v->rgb;
		w[7] = (uint32_t)(int32_t)lrintf(depth);
	}
	unsigned cull = ofgl_cull_on ?
		((ofgl_cull_face == GL_FRONT) == (ofgl_front_face == GL_CCW) ? 1u : 2u) : 0u;
	uint32_t triangle = (i0 & 31) | ((i1 & 31) << 5) | ((i2 & 31) << 10) | (cull << 15);
	if (!ofgl_geometry_state_ready)
	{
		if (!OFQ2_SetObjectState(&ofgl_world_object)) return 0;
		ofgl_geometry_state_ready = 1;
	}
	if (!OFQ2_DrawClipIndexed(uploads, count, triangle)) return 0;
	for (int k = 0; k < 3; k++)
	{
		unsigned slot = indices[k] & 31;
		ofgl_gpu_slots_valid |= 1u << slot;
		ofgl_gpu_slots[slot] = indices[k];
	}
	return 1;
}

/* -1 = wholly outside, 0 = intersects a plane, 1 = wholly inside. */
static int
ofgl_box_bounds(const float center[3], const float extent[3], float *min_z, float *max_z)
{
	int inside = 1;
	for (int i = 0; i < 5; i++)
	{
		const ofgl_plane_t *p = &ofgl_world_planes[i];
		float d = p->a * center[0] + p->b * center[1] +
			p->c * center[2] + p->e;
		float r = ofgl_world_abs[i][0] * extent[0] +
			ofgl_world_abs[i][1] * extent[1] + ofgl_world_abs[i][2] * extent[2];
		if (d + r < 0.0f) return -1;
		if (d - r < 0.0f) inside = 0;
		if (i == 0)
		{
			*min_z = d - r + ofgl_pc_near;
			*max_z = d + r + ofgl_pc_near;
		}
	}
	return inside;
}

static int
ofgl_world_bounds(const ofgl_world_poly_t *poly, float *min_z, float *max_z)
{
	return ofgl_box_bounds(poly->center, poly->extent, min_z, max_z);
}

static int
ofgl_draw_bounds_inside(void)
{
	float min_z, max_z;
	ofgl_world_prepare();
	return ofgl_box_bounds(ofgl_draw_center, ofgl_draw_extent, &min_z, &max_z) == 1;
}

/* cw is the bounded positive 24.8 depth accepted by the raw worker. Start
 * with the hardware floating-point divider, then correct to the exact integer
 * quotient. This avoids the RV32 software 64-bit division without changing a
 * bit of the worker's depth or screen-coordinate convention. */
static uint32_t
ofgl_world_depth(uint32_t cw)
{
	const uint64_t numerator = UINT64_C(1) << 38;
	uint32_t q = (uint32_t)(0x1p38f / (float)cw);
	uint64_t product = (uint64_t)q * cw;
	while (product > numerator)
	{
		--q;
		product -= cw;
	}
	while (numerator - product >= cw)
	{
		++q;
		product += cw;
	}
	return q;
}

/* Original BSP vertices on a GPU/CPU shared edge need identical screen
 * coordinates and independent depth. Match the worker's fixed matrix and
 * reciprocal here, while retaining float camera coordinates for CPU clipping.
 * A fully contained raw polygon's shared edge cannot itself be clipped; newly
 * generated intersections remain on the established CPU clip/projection path.
 * Test each vertex independently: a fractional corner elsewhere in its surface
 * must not change the convention on an otherwise shared exact-quarter edge. */
static int
ofgl_world_project_original(const float xyz[3], ofgl_cvert_t *v)
{
	int32_t q[3], cam[3];
	if (!(v->z >= 16.0f && v->z <= 4096.0f)) return 0;
	for (int j = 0; j < 3; j++)
	{
		float f = xyz[j] * 4.0f;
		if (!ofgl_finite(f) || f < -32768.0f || f > 32767.0f) return 0;
		q[j] = (int32_t)f;
		if ((float)q[j] != f) return 0;
	}
	for (int row = 0; row < 3; row++)
	{
		int64_t sum = 0;
		for (int j = 0; j < 3; j++)
			sum += (int64_t)ofgl_world_object.matrix[row][j] * q[j];
		/* Unsigned addition reproduces RTL wrapping without C overflow. */
		cam[row] = (int32_t)((uint32_t)(sum >> 16) +
			(uint32_t)ofgl_world_object.matrix[row][3]);
	}
	if (cam[2] < 4096 || cam[2] > 1048576 ||
		cam[2] < ofgl_world_object.near_clip) return 0;
	uint32_t depth = ofgl_world_depth((uint32_t)cam[2]);
	uint32_t zi = depth >> 6;
	int32_t rx = (int32_t)(((int64_t)cam[0] * zi) >> 16);
	int32_t ry = (int32_t)(((int64_t)cam[1] * zi) >> 16);
	int64_t px = (int64_t)ofgl_world_object.xcenter * 16 +
		(((int64_t)ofgl_world_object.xscale * rx) >> 12);
	int64_t py = (int64_t)ofgl_world_object.ycenter -
		(((int64_t)ofgl_world_object.yscale * ry) >> 16);
	/* Every GPU-eligible polygon is inside the viewport margin. Outside this
	 * representable CPU guard, preserve the established CPU clipping path. */
	if (px < -32752 || px > 32752 || py < -32752 || py > 32752) return 0;
	v->px = (int16_t)px;
	v->py = (int16_t)py;
	v->sx = (float)px * (1.0f / 16.0f);
	v->sy = (float)py * (1.0f / 16.0f);
	v->iw = 1.0f / v->z;
	v->depth = (int32_t)depth;
	v->projected_valid = 1;
	return 1;
}

static void
ofgl_world_material(ofgl_world_poly_t *p, float tw, float th)
{
	if (p->tex_w != (int)tw || p->tex_h != (int)th)
	{
		p->tex_w = (int)tw; p->tex_h = (int)th;
		float bs = p->verts[3], bt = p->verts[4];
		for (int i = 1; i < p->count; i++)
		{
			if (p->verts[i * 7 + 3] < bs) bs = p->verts[i * 7 + 3];
			if (p->verts[i * 7 + 4] < bt) bt = p->verts[i * 7 + 4];
		}
		bs = floorf(bs) * tw; bt = floorf(bt) * th;
		p->max_st = 1.0f;
		for (int i = 0; i < p->count; i++)
		{
			float s = p->verts[i * 7 + 3] * tw - bs;
			float t = p->verts[i * 7 + 4] * th - bt;
			if (!ofgl_finite(s) || !ofgl_finite(t) || s < 0 || t < 0 || s > 32767 || t > 32767)
			{
				p->raw_ok = 0;
				continue;
			}
			p->words[i * 5 + 3] = (uint32_t)(int32_t)lrintf(s * 65536.0f);
			p->words[i * 5 + 4] = (uint32_t)(int32_t)lrintf(t * 65536.0f);
			if (s > p->max_st) p->max_st = s;
			if (t > p->max_st) p->max_st = t;
		}
	}
	int block = ofgl_lm_active ? ofgl_lm_index(ofgl_tex_bound[1]) : -1;
	uint16_t colour = 0xffff;
	if (block < 0 && !(ofgl_tex2d_enabled && ofgl_texenv_mode(0) == GL_REPLACE))
	{
		colour = ofgl_rgba_to_565(((uint32_t)(ofgl_cur_color[0] * 255.0f) & 255) |
			(((uint32_t)(ofgl_cur_color[1] * 255.0f) & 255) << 8) |
			(((uint32_t)(ofgl_cur_color[2] * 255.0f) & 255) << 16));
	}
	uint32_t version = block >= 0 ? ofgl_lm_version[block] : colour;
	if (p->lm_block != block || p->light_version != version)
	{
		p->lm_block = block; p->light_version = version;
		for (int i = 0; i < p->count; i++)
		{
			uint16_t rgb = block >= 0 ? ofgl_lm_cur[p->light_index[i]] : colour;
			p->words[i * 5 + 2] = (p->words[i * 5 + 2] & 65535u) | ((uint32_t)rgb << 16);
		}
	}
}

/* The worker's fixed reciprocal must retain enough UV precision before its
 * common attribute normalization and plane setup. Large texture spans or
 * steep depth ratios keep the adaptive CPU attribute path. */
static int
ofgl_world_precision(float max_st, float min_z, float max_z)
{
	float span = max_st + 1.0f;
	return span * span * max_z <= 262144.0f * min_z;
}

void
OFGL_DrawWorldPolys(ofgl_world_poly_t *const *polys, int count)
{
	const ofq2_texture_t *tex;
	float tw, th;
	if (count < 1 || !ofgl_draw_enabled(OFGL_DRAW_INDEXED)) return;
	OFGL_SetWorldDraw(1);
	if (!ofgl_begin3d(&tex, &tw, &th)) return;
	ofgl_world_prepare();
	ofgl_indexed_geometry = 0;
	int geometry = ofgl_batch_world && !ofgl_blend_on;
	unsigned cull = ofgl_cull_on ?
		((ofgl_cull_face == GL_FRONT) == (ofgl_front_face == GL_CCW) ? 1u : 2u) : 0u;
	ofgl_array_t saved = ofgl_va_vertex;
	for (int n = 0; n < count; n++)
	{
		ofgl_world_poly_t *p = polys[n];
		float min_z = 0.0f, max_z = 0.0f;
		int inside = ofgl_world_bounds(p, &min_z, &max_z);
		if (inside < 0) continue;
		ofgl_world_material(p, tw, th);
		/* The raw path has fixed zi=256/z. Bound s*zi and t*zi below the
		 * derive saturation threshold, and keep all near/viewport-crossing
		 * surfaces on the CPU clipper. */
		if (geometry && inside && p->raw_ok && min_z >= 16.0f && max_z <= 4096.0f &&
			min_z >= p->max_st / 32.0f &&
			ofgl_world_precision(p->max_st, min_z, max_z) &&
			(ofgl_geometry_state_ready || OFQ2_SetObjectState(&ofgl_world_object)) &&
			OFQ2_DrawGeometryFan(p->words, p->count, cull))
		{
			/* Matrix state survives both fan and CPU triangle commands.
			 * All surfaces in this batch share it; compare/arm only once. */
			ofgl_geometry_state_ready = 1;
			continue;
		}

		ofgl_va_vertex.ptr = (const uint8_t *)p->verts;
		ofgl_va_vertex.size = 3; ofgl_va_vertex.stride = 7 * sizeof(float);
		ofgl_cvert_t verts[OFGL_WORLD_VERTS];
		for (int i = 0; i < p->count; i++)
		{
			const float *v = ofgl_fetch_position(i, &verts[i]);
			if (ofgl_batch_world) ofgl_world_project_original(v, &verts[i]);
			/* The inflated surface bounds already prove every vertex lies
			 * inside all clip planes. Reuse that result on CPU-only cores. */
			if (inside) verts[i].clipcode = verts[i].viewcode = 0;
			else ofgl_classify_cvert(&verts[i]);
			verts[i].s = p->verts[i * 7 + 3] * tw;
			verts[i].t = p->verts[i * 7 + 4] * th;
			verts[i].rgb = p->words[i * 5 + 2] >> 16;
			verts[i].a = (ofgl_lm_active ||
				(ofgl_tex2d_enabled && ofgl_texenv_mode(0) == GL_REPLACE)) ? 255 :
				(uint8_t)(ofgl_cur_color[3] * 255.0f + 0.5f);
			verts[i].attrs_valid = 1;
		}
		for (int i = 1; i + 1 < p->count; i++)
			ofgl_cached_tri(0, i, i + 1, &verts[0], &verts[i], &verts[i + 1], tw, th);
	}
	ofgl_va_vertex = saved;
	ofgl_end3d();
}

/* Stream 3D triangles (vtx size 3). idx==NULL => sequential (glDrawArrays). */
static void
ofgl_draw_tris_3d(const GLushort *idx, int count)
{
	const ofq2_texture_t *tex;
	float tw, th;
	int g;

	if (count < 3 || !ofgl_draw_enabled(OFGL_DRAW_INDEXED) ||
		!ofgl_begin3d(&tex, &tw, &th))
	{
		return;
	}
	for (g = 0; g + 3 <= count; g += 3)
	{
		ofgl_tri3d(idx ? idx[g + 0] : g + 0, idx ? idx[g + 1] : g + 1,
			idx ? idx[g + 2] : g + 2, tw, th);
	}
	ofgl_end3d();
}

/* Optional geometry-engine requests keep their original staged source ABI.
 * The setting cannot change between buffer construction and immediate apply. */
int
OFGL_AliasPositionsEnabled(void)
{
	/* Aliases retain CPU projection until indexed hardware and CPU fallback
	 * share one convention; raw world offload does not require seam copies. */
	return 1;
}

void
OFGL_DrawAliasElements(const int *source_indices, int index_stride,
	const float *positions, const unsigned short *indices, int count)
{
	const ofq2_texture_t *tex;
	float tw, th;
	if (!source_indices || !positions || !indices || count < 3 ||
		!ofgl_draw_enabled(OFGL_DRAW_INDEXED)) return;
	ofgl_array_t saved = ofgl_va_vertex;
	ofgl_va_vertex.ptr = (const byte *)positions;
	ofgl_va_vertex.size = 3;
	ofgl_va_vertex.stride = 4 * sizeof(float);
	if (ofgl_begin3d(&tex, &tw, &th))
	{
		/* This loop has no GPU state changes for opaque aliases. Clipping
		 * can expand a triangle but retains its surface state. Diagnostics may
		 * print through the console, so keep them outside the exclusive scope. */
		int submission_batch = !ofgl_blend_on &&
			(!ofgl_bisect_cv || !ofgl_bisect_cv->value);
		if (submission_batch) OFQ2_BeginOpaqueTriBatch();
		for (int g = 0; g + 3 <= count; g += 3)
			ofgl_alias_tri(indices[g], indices[g + 1], indices[g + 2],
				(const byte *)source_indices, index_stride, tw, th);
		if (submission_batch) OFQ2_EndOpaqueTriBatch();
		ofgl_end3d();
	}
	ofgl_va_vertex = saved;
}

/* Non-indexed 3D draw (glDrawArrays with a 3-component vertex array): sky box
 * faces, particles, beams and the null-model box all arrive this way. The old
 * code required size == 2 and silently dropped every one of them. */
static void
ofgl_draw_arrays_3d(GLenum mode, int first, int count)
{
	const ofq2_texture_t *tex;
	float tw, th;
	int i;

	if (count < 3 || !ofgl_draw_enabled(OFGL_DRAW_ARRAYS) ||
		!ofgl_begin3d(&tex, &tw, &th))
	{
		return;
	}

	switch (mode)
	{
		case GL_TRIANGLES:
			for (i = 0; i + 3 <= count; i += 3)
			{
				ofgl_tri3d(first + i, first + i + 1, first + i + 2, tw, th);
			}
			break;
		case GL_TRIANGLE_FAN:
			for (i = 1; i + 1 < count; i++)
			{
				ofgl_tri3d(first, first + i, first + i + 1, tw, th);
			}
			break;
		case GL_TRIANGLE_STRIP:
			for (i = 0; i + 2 < count; i++)
			{
				/* swap every other triangle so the winding stays consistent
				 * for the backface test */
				if (i & 1)
				{
					ofgl_tri3d(first + i + 1, first + i, first + i + 2, tw, th);
				}
				else
				{
					ofgl_tri3d(first + i, first + i + 1, first + i + 2, tw, th);
				}
			}
			break;
		default:
			break;		/* GL_POINTS / GL_LINES: no rasteriser for these */
	}
	ofgl_end3d();
}

/* ------------------------------------------------------------------------ *
 *  mini-GL: value-returning queries gl1's init relies on
 * ------------------------------------------------------------------------ */

static const GLubyte *
ofgl_GetString(GLenum name)
{
	switch (name)
	{
		case GL_VENDOR:		return (const GLubyte *)"ThinkElastic";
		case GL_RENDERER:	return (const GLubyte *)"OFQ2 GPU (openfpgaOS)";
		case GL_VERSION:	return (const GLubyte *)"1.1";
		/* Advertise NPOT so gl1 uploads at NATIVE size and leaves the
		 * power-of-two resample to ofgl_TexImage2D, which rounds to the
		 * NEAREST power of two. gl1's own soft path always rounds UP, which
		 * costs memory on every odd-sized skin for no visual gain at 320x240. */
		case GL_EXTENSIONS:	return (const GLubyte *)"GL_OES_texture_npot";
		default:		return (const GLubyte *)"";
	}
}

static GLenum
ofgl_GetError(void)
{
	return GL_NO_ERROR;
}

static void
ofgl_GetFloatv(GLenum pname, GLfloat *params)
{
	int i;

	if (!params)
	{
		return;
	}

	switch (pname)
	{
#ifdef GL_MODELVIEW_MATRIX
		case GL_MODELVIEW_MATRIX:
			/* gl1 reads this into r_world_matrix for entity culling. */
			memcpy(params, OFGL_MV, 16 * sizeof(GLfloat));
			break;
#endif
#ifdef GL_PROJECTION_MATRIX
		case GL_PROJECTION_MATRIX:
			memcpy(params, ofgl_proj, 16 * sizeof(GLfloat));
			break;
#endif
#ifdef GL_TEXTURE_MATRIX
		case GL_TEXTURE_MATRIX:
			for (i = 0; i < 16; i++)
			{
				params[i] = (i % 5 == 0) ? 1.0f : 0.0f;
			}
			break;
#endif
		default:
			params[0] = 0.0f;
			break;
	}
}

static void
ofgl_GetIntegerv(GLenum pname, GLint *params)
{
	if (!params)
	{
		return;
	}

	switch (pname)
	{
		case GL_MAX_TEXTURE_SIZE:
			params[0] = 2048;
			break;
#ifdef GL_MAX_TEXTURE_UNITS
		case GL_MAX_TEXTURE_UNITS:
			params[0] = OFGL_NTMU;	/* unit 1 is tracked, unit 0 is drawn */
			break;
#endif
		default:
			params[0] = 0;
			break;
	}
}

/* ------------------------------------------------------------------------ *
 *  mini-GL: state
 * ------------------------------------------------------------------------ */

static void
ofgl_Enable(GLenum cap)
{
	switch (cap)
	{
		case GL_TEXTURE_2D:	ofgl_tex2d_on[ofgl_active_tmu] = 1; break;
		case GL_BLEND:		ofgl_blend_on = 1; break;
		case GL_CULL_FACE:	ofgl_cull_on = 1; break;
		case GL_DEPTH_TEST:	ofgl_depth_test = 1; break;
		default: break;
	}
}

static void
ofgl_Disable(GLenum cap)
{
	switch (cap)
	{
		case GL_TEXTURE_2D:	ofgl_tex2d_on[ofgl_active_tmu] = 0; break;
		case GL_BLEND:		ofgl_blend_on = 0; break;
		case GL_CULL_FACE:	ofgl_cull_on = 0; break;
		case GL_DEPTH_TEST:	ofgl_depth_test = 0; break;
		default: break;
	}
}

static void
ofgl_TexEnvi(GLenum target, GLenum pname, GLint param)
{
	if (target == GL_TEXTURE_ENV && pname == GL_TEXTURE_ENV_MODE)
	{
		ofgl_texenv[ofgl_active_tmu] = param;
	}
	/* GL_RGB_SCALE (overbright) has no equivalent in the RGB565 fragment
	 * path -- the texel is already the final colour -- so it is ignored. */
}

static void
ofgl_DepthMask(GLboolean flag)
{
	ofgl_depth_mask = flag ? 1 : 0;
}

static void
ofgl_CullFace(GLenum mode)
{
	ofgl_cull_face = mode;
}

static void
ofgl_FrontFace(GLenum mode)
{
	ofgl_front_face = mode;
}

static void
ofgl_Color4f(GLfloat r, GLfloat g, GLfloat b, GLfloat a)
{
	ofgl_cur_color[0] = r;
	ofgl_cur_color[1] = g;
	ofgl_cur_color[2] = b;
	ofgl_cur_color[3] = a;
}

/* R_DrawBeam sets its colour through the ubyte entry point; without it the
 * beam took whatever colour the previous batch left in ofgl_cur_color. */
static void
ofgl_Color4ub(GLubyte r, GLubyte g, GLubyte b, GLubyte a)
{
	ofgl_cur_color[0] = r * (1.0f / 255.0f);
	ofgl_cur_color[1] = g * (1.0f / 255.0f);
	ofgl_cur_color[2] = b * (1.0f / 255.0f);
	ofgl_cur_color[3] = a * (1.0f / 255.0f);
}

static void
ofgl_ClearColor(GLclampf r, GLclampf g, GLclampf b, GLclampf a)
{
	(void)a;
	ofgl_clear565 = ofgl_rgba_to_565(
		((uint32_t)(r * 255.0f) & 0xff) |
		(((uint32_t)(g * 255.0f) & 0xff) << 8) |
		(((uint32_t)(b * 255.0f) & 0xff) << 16));
}

static void
ofgl_Clear(GLbitfield mask)
{
	/* gl1 calls glClear at the start of every 3D frame (R_Clear). Use it as the
	 * frame-start hook: bind the GPU to this frame's draw buffer, re-arm the
	 * sticky object state, and force a matrix rebuild. */
	if (ofgl_gpu_ok)
	{
		OFQ2_BindFramebuffer(ofgl_fb(), ofgl_fb_stride);
		ofgl_obj_dirty = 1;
		/* Do NOT clear ofgl_gpu_pending here: RI_EndFrame queues the next
		 * buffer's colour fill on the GPU, so work really can still be in
		 * flight at frame start, and the flag is what makes the first CPU 2D
		 * blit drain before it writes the same pixels. */
	}

	if ((mask & GL_DEPTH_BUFFER_BIT) && ofgl_zbuf)
	{
		if (ofgl_gpu_ok)
		{
			/* Clear the packed 16-bit depth buffer. Zero means
			 * "infinitely far" for the 1/z depth convention, which is what the
			 * TEST_WRITE mode wants. */
			OFQ2_ClearRect(ofgl_zbuf, ofgl_z_major, ofgl_fb_width * OFGL_Z_BYTES,
				ofgl_fb_height, 0);
			ofgl_gpu_pending = 1;
		}
		else
		{
			memset(ofgl_zbuf, 0,
				(size_t)ofgl_fb_width * (size_t)ofgl_fb_height * OFGL_Z_BYTES);
		}
	}

	if (mask & GL_COLOR_BUFFER_BIT)
	{
		ofgl_clear_fb565();
	}
}

static void
ofgl_EnableClientState(GLenum array)
{
	switch (array)
	{
		case GL_VERTEX_ARRAY:		ofgl_va_vertex.enabled = 1; break;
		case GL_TEXTURE_COORD_ARRAY:
			ofgl_va_texcoord_tmu[ofgl_client_tmu].enabled = 1; break;
		case GL_COLOR_ARRAY:		ofgl_va_color.enabled = 1; break;
		default: break;
	}
}

static void
ofgl_DisableClientState(GLenum array)
{
	switch (array)
	{
		case GL_VERTEX_ARRAY:		ofgl_va_vertex.enabled = 0; break;
		case GL_TEXTURE_COORD_ARRAY:
			ofgl_va_texcoord_tmu[ofgl_client_tmu].enabled = 0; break;
		case GL_COLOR_ARRAY:		ofgl_va_color.enabled = 0; break;
		default: break;
	}
}

static void
ofgl_VertexPointer(GLint size, GLenum type, GLsizei stride, const void *ptr)
{
	(void)type;
	ofgl_va_vertex.ptr = (const uint8_t *)ptr;
	ofgl_va_vertex.size = size;
	ofgl_va_vertex.stride = stride;
}

static void
ofgl_TexCoordPointer(GLint size, GLenum type, GLsizei stride, const void *ptr)
{
	(void)type;
	ofgl_va_texcoord_tmu[ofgl_client_tmu].ptr = (const uint8_t *)ptr;
	ofgl_va_texcoord_tmu[ofgl_client_tmu].size = size;
	ofgl_va_texcoord_tmu[ofgl_client_tmu].stride = stride;
}

static void
ofgl_ColorPointer(GLint size, GLenum type, GLsizei stride, const void *ptr)
{
	(void)type;
	ofgl_va_color.ptr = (const uint8_t *)ptr;
	ofgl_va_color.size = size;
	ofgl_va_color.stride = stride;
}

/* ------------------------------------------------------------------------ *
 *  mini-GL: textures
 * ------------------------------------------------------------------------ */

static void
ofgl_BindTexture(GLenum target, GLuint texture)
{
	(void)target;
	ofgl_tex_bound[ofgl_active_tmu] = (int)texture;
}

static void
ofgl_ActiveTexture(GLenum unit)
{
	int u = (int)unit - GL_TEXTURE0;

	ofgl_active_tmu = (u > 0 && u < OFGL_NTMU) ? u : 0;
}

static void
ofgl_ClientActiveTexture(GLenum unit)
{
	int u = (int)unit - GL_TEXTURE0;

	ofgl_client_tmu = (u > 0 && u < OFGL_NTMU) ? u : 0;
}

static void
ofgl_GenTextures(GLsizei n, GLuint *textures)
{
	GLsizei i;

	if (!textures)
	{
		return;
	}
	for (i = 0; i < n; i++)
	{
		textures[i] = ofgl_gen_next++;
	}
}

static void
ofgl_release_tex(ofgl_tex_t *tex)
{
	/* SDRAM textures can still be sampled by commands already in the ring. */
	if (ofgl_gpu_pending)
	{
		OFQ2_Finish();
		ofgl_gpu_pending = 0;
	}
	free(tex->texels);
	ofgl_3d_tex_bytes -= tex->heap_bytes;
	memset(tex, 0, sizeof(*tex));
}

static void
ofgl_DeleteTextures(GLsizei n, const GLuint *textures)
{
	GLsizei i;

	if (!textures)
	{
		return;
	}
	for (i = 0; i < n; i++)
	{
		GLuint t = textures[i];

		if (t > 0 && t < OFGL_MAX_TEX)
		{
			ofgl_release_tex(&ofgl_tex[t]);
		}
	}
}

/* One RGBA8888 source texel -> RGB565 + transparent key. */
static inline uint16_t
ofgl_texel565(uint32_t c)
{
	uint16_t px;

	if (((c >> 24) & 0xff) < 0x80)
	{
		return OFGL_TEX_TRANSPARENT;
	}
	px = ofgl_rgba_to_565(c);
	return (px == OFGL_TEX_TRANSPARENT) ? OFGL_TEX_BLACK : px;
}

/* Nearest power of two (geometric mean split), clamped to [1, 2048]. The GPU
 * wraps texture coordinates with a plain bitmask -- `s & (w-1)` -- so a
 * non-power-of-two width drops address bits instead of wrapping and the image
 * collapses into a garbled band. Every texture the GPU samples must therefore
 * be power-of-two; we resample here rather than let gl1's own soft path do it,
 * because gl1 always rounds UP (more memory) and we want the nearest size. */
static int
ofgl_pow2_near(int v)
{
	int lo = 1;

	if (v < 1)
	{
		return 1;
	}
	while (lo * 2 <= v && lo < 2048)
	{
		lo *= 2;
	}
	if (lo >= 2048 || lo == v)
	{
		return lo;
	}
	/* round to whichever neighbour is nearer in log space: v*v vs lo*(2*lo) */
	return (v * v >= lo * lo * 2) ? lo * 2 : lo;
}

/* Box-filter (or point-sample, when magnifying) an RGBA8888 image into an
 * RGB565 buffer of arbitrary destination dims. Load-time only. The per-row and
 * per-column source spans are computed with one divide each, not one per
 * texel, so a full map's worth of odd-sized skins stays cheap. */
static void
ofgl_resample_565(uint16_t *dst, int dw, int dh,
	const uint32_t *src, int sw, int sh)
{
	static uint16_t xlo[2049], xhi[2049];
	int x, y;

	for (x = 0; x < dw; x++)
	{
		int a = (x * sw) / dw;
		int b = ((x + 1) * sw) / dw;

		if (b <= a)
		{
			b = a + 1;
		}
		if (b > sw)
		{
			b = sw;
		}
		xlo[x] = (uint16_t)a;
		xhi[x] = (uint16_t)b;
	}

	for (y = 0; y < dh; y++)
	{
		int y0 = (y * sh) / dh;
		int y1 = ((y + 1) * sh) / dh;
		uint16_t *drow = dst + (size_t)y * dw;

		if (y1 <= y0)
		{
			y1 = y0 + 1;
		}
		if (y1 > sh)
		{
			y1 = sh;
		}

		for (x = 0; x < dw; x++)
		{
			unsigned sa = 0, sr = 0, sg = 0, sb = 0, n = 0;
			int sx, sy;

			for (sy = y0; sy < y1; sy++)
			{
				const uint32_t *p = src + (size_t)sy * sw;

				for (sx = xlo[x]; sx < xhi[x]; sx++)
				{
					uint32_t c = p[sx];

					sr += c & 0xff;
					sg += (c >> 8) & 0xff;
					sb += (c >> 16) & 0xff;
					sa += (c >> 24) & 0xff;
					n++;
				}
			}
			if (!n)
			{
				drow[x] = OFGL_TEX_TRANSPARENT;
				continue;
			}
			drow[x] = ofgl_texel565(((sr / n) & 0xff) |
				(((sg / n) & 0xff) << 8) | (((sb / n) & 0xff) << 16) |
				(((sa / n) & 0xff) << 24));
		}
	}
}

static void
ofgl_TexImage2D(GLenum target, GLint level, GLint internalformat,
	GLsizei width, GLsizei height, GLint border, GLenum format,
	GLenum type, const void *pixels)
{
	ofgl_tex_t *t;
	uint16_t *buf;
	size_t n, i;
	/* Uploads target the ACTIVE unit's binding, not unit 0: gl1's lightmap
	 * uploads run with TMU1 selected. */
	int up_tex = ofgl_tex_bound[ofgl_active_tmu];

	(void)internalformat;
	(void)border;
	(void)format;
	(void)type;

	if (target != GL_TEXTURE_2D || level != 0)
	{
		return;		/* ignore mip levels: 2D pics have none */
	}
	if (ofgl_tex_skip(up_tex))
	{
		/* lightmap page: kept CPU-side for per-vertex sampling, never
		 * handed to the GPU as a texture */
		ofgl_lm_store(ofgl_lm_index(up_tex), 0, 0, width, height,
			(const uint32_t *)pixels);
		return;
	}
	if (up_tex <= 0 || up_tex >= OFGL_MAX_TEX ||
		width <= 0 || height <= 0 || ofgl_tex_skip(up_tex) ||
		(ofgl_store_texture == 0 && up_tex != OFGL_TEXNUM_SCRAPS) ||
		(ofgl_tex_is3d(ofgl_store_texture) && !ofgl_gpu_ok))
	{
		/* lightmaps + non-stored types; and 3D textures only when a GPU path is
		 * live -- otherwise they just waste the heap (OOM). */
		return;
	}

	t = &ofgl_tex[up_tex];
	ofgl_release_tex(t);

	/* Sizing. Textures the GPU samples MUST be power-of-two on both axes (the
	 * sampler wraps with a bitmask), so 3D textures are resampled to the
	 * nearest power of two. When the core has no dedicated fast texture memory
	 * they also live in SDRAM = the app heap, where the full set (~5 MB at
	 * 565) re-triggers the map-load OOM -- so shrink another two octaves there.
	 * With fast texture memory present they upload at native resolution and
	 * cost the heap nothing. 2D pics keep their exact dims: the CPU HUD blit
	 * samples them directly and resampling would only blur the font. */
	{
		int is3d = ofgl_tex_is3d(ofgl_store_texture) &&
			up_tex != OFGL_TEXNUM_SCRAPS;
		int gpu3d = is3d && ofgl_gpu_ok;
		int sw = width, sh = height;
		const uint32_t *src = (const uint32_t *)pixels;

		if (gpu3d)
		{
			sw = ofgl_pow2_near(width);
			sh = ofgl_pow2_near(height);

			if (!OFQ2_HasFastTexMem())
			{
				/* SDRAM-only: 3D textures come out of the tight app heap, which
				 * the historical sky-OOM state proved has ~zero headroom at
				 * half-res. Quarter each axis (1/16 the bytes). */
				int shrink = 4;

				while (shrink > 1 && (sw < shrink * 2 || sh < shrink * 2))
				{
					shrink >>= 1;
				}
				sw /= shrink;
				sh /= shrink;
			}
			if (sw < 1) sw = 1;
			if (sh < 1) sh = 1;
		}

		/* Heap budget: render flat white rather than risk a fatal map-load OOM. */
		if (gpu3d && !OFQ2_HasFastTexMem() &&
			ofgl_3d_tex_bytes + (uint32_t)((size_t)sw * sh * 2) > OFGL_3D_TEX_BUDGET)
		{
			t->texels = NULL;
			t->w = t->h = 0;
			t->gtex_valid = 0;
			t->heap_bytes = 0;
			return;
		}

		n = (size_t)sw * (size_t)sh;
		buf = (uint16_t *)malloc(n * 2);
		if (!buf)
		{
			t->w = t->h = 0;
			t->heap_bytes = 0;
			return;
		}

		if (!pixels)
		{
			for (i = 0; i < n; i++)
			{
				buf[i] = OFGL_TEX_TRANSPARENT;
			}
		}
		else if (sw == width && sh == height)
		{
			for (i = 0; i < n; i++)
			{
				buf[i] = ofgl_texel565(src[i]);
			}
		}
		else
		{
			ofgl_resample_565(buf, sw, sh, src, width, height);
		}

		t->texels = buf;
		t->w = (uint16_t)sw;
		t->h = (uint16_t)sh;
		t->gtex_valid = 0;
	}

	/* 3D textures (world/skin/sky/sprite): build the GPU handle now; free the
	 * heap copy if it landed in fast texture memory (the GPU samples it there). */
	if (ofgl_tex_is3d(ofgl_store_texture) &&
		up_tex != OFGL_TEXNUM_SCRAPS && ofgl_gpu_ok)
	{
		OFQ2_TexCreate(&t->gtex, t->texels, t->w, t->h, (uint32_t)(n * 2));
		t->gtex_valid = 1;
		if (t->gtex.flags & 0x1u)	/* _OF_TEX_FAST: lives in CRAM, not the heap */
		{
			free(t->texels);
			t->texels = NULL;
			t->heap_bytes = 0;
		}
		else if (ofgl_3d_tex_bytes + (uint32_t)(n * 2) > OFGL_3D_TEX_BUDGET)
		{
			/* Fast texture memory filled mid-load and this one spilled to
			 * SDRAM past the heap budget. Drop it (renders flat white) rather
			 * than push the map load into an OOM. */
			free(t->texels);
			t->texels = NULL;
			t->w = t->h = 0;
			t->gtex_valid = 0;
			t->heap_bytes = 0;
		}
		else
		{
			t->heap_bytes = (uint32_t)(n * 2);
			ofgl_3d_tex_bytes += t->heap_bytes;
		}
	}
}

static void
ofgl_TexSubImage2D(GLenum target, GLint level, GLint xoffset, GLint yoffset,
	GLsizei width, GLsizei height, GLenum format, GLenum type,
	const void *pixels)
{
	ofgl_tex_t *t;
	const uint32_t *src;
	int row;
	int up_tex = ofgl_tex_bound[ofgl_active_tmu];

	(void)format;
	(void)type;

	if (target != GL_TEXTURE_2D || level != 0 || !pixels)
	{
		return;
	}
	if (ofgl_tex_skip(up_tex))
	{
		/* gl1's per-frame dynamic-light updates land here */
		ofgl_lm_store(ofgl_lm_index(up_tex), xoffset, yoffset, width, height,
			(const uint32_t *)pixels);
		return;
	}
	if (up_tex <= 0 || up_tex >= OFGL_MAX_TEX)
	{
		return;
	}

	t = &ofgl_tex[up_tex];
	/* Sub-uploads address the ORIGINAL texel grid. A 3D texture that was
	 * resampled to power-of-two no longer has that grid, and the only callers
	 * (lightmap blocks) are in the skipped texnum range anyway -- so refuse
	 * rather than scribble at the wrong offsets. */
	if (!t->texels || t->gtex_valid)
	{
		return;
	}

	src = (const uint32_t *)pixels;

	for (row = 0; row < height; row++)
	{
		int dy = yoffset + row;
		int col;

		if (dy < 0 || dy >= t->h)
		{
			continue;
		}
		for (col = 0; col < width; col++)
		{
			int dx = xoffset + col;

			if (dx < 0 || dx >= t->w)
			{
				continue;
			}
			t->texels[(size_t)dy * t->w + dx] =
				ofgl_texel565(src[(size_t)row * width + col]);
		}
	}
}

/* ------------------------------------------------------------------------ *
 *  mini-GL: draw
 * ------------------------------------------------------------------------ */

static void
ofgl_DrawArrays(GLenum mode, GLint first, GLsizei count)
{
	float x0, y0, s0, t0, x2, y2, s2, t2, tmp;

	if (!ofgl_va_vertex.enabled || !ofgl_va_vertex.ptr)
	{
		return;
	}
	if (ofgl_va_vertex.size == 3)
	{
		ofgl_draw_arrays_3d(mode, first, count);	/* sky, particles, beams */
		return;
	}
	if (count < 4 || ofgl_va_vertex.size != 2)
	{
		return;
	}

	/* Every 2D primitive is a 4-vertex quad (GL_TRIANGLE_FAN): v0 = ul,
	 * v2 = dr. */
	ofgl_vertex(first + 0, &x0, &y0, &s0, &t0);
	ofgl_vertex(first + 2, &x2, &y2, &s2, &t2);

	if (x2 < x0) { tmp = x0; x0 = x2; x2 = tmp; tmp = s0; s0 = s2; s2 = tmp; }
	if (y2 < y0) { tmp = y0; y0 = y2; y2 = tmp; tmp = t0; t0 = t2; t2 = tmp; }

	ofgl_blit_rect(x0, y0, x2, y2, s0, t0, s2, t2);
}

static void
ofgl_DrawElements(GLenum mode, GLsizei count, GLenum type, const void *indices)
{
	const GLushort *idx = (const GLushort *)indices;
	int g;

	(void)mode;
	if (!ofgl_va_vertex.enabled || !ofgl_va_vertex.ptr ||
		type != GL_UNSIGNED_SHORT || !idx)
	{
		return;
	}
	if (ofgl_va_vertex.size == 3)
	{
		ofgl_draw_tris_3d(idx, count);		/* 3D world (Stage 4) */
		return;
	}
	if (ofgl_va_vertex.size != 2)
	{
		return;
	}

	/* The 2D buffer emits quads as two triangles: idx[g..g+5] =
	 * v, v+1, v+2, v, v+2, v+3 with v0 = ul and v2 = dr. */
	for (g = 0; g + 6 <= count; g += 6)
	{
		float x0, y0, s0, t0, x2, y2, s2, t2, tmp;

		ofgl_vertex(idx[g + 0], &x0, &y0, &s0, &t0);
		ofgl_vertex(idx[g + 2], &x2, &y2, &s2, &t2);

		if (x2 < x0) { tmp = x0; x0 = x2; x2 = tmp; tmp = s0; s0 = s2; s2 = tmp; }
		if (y2 < y0) { tmp = y0; y0 = y2; y2 = tmp; tmp = t0; t0 = t2; t2 = tmp; }

		ofgl_blit_rect(x0, y0, x2, y2, s0, t0, s2, t2);
	}
}

/* ------------------------------------------------------------------------ *
 *  the GLAD loader seam -- gl1 resolves every gl* through here
 * ------------------------------------------------------------------------ */

static void
ofgl_noop(void)
{
}

void *
RI_GetProcAddress(const char *proc)
{
	if (!proc)
	{
		return NULL;
	}

	/* Quake2 probes all three spellings before choosing GL_POINTS. We render
	 * its textured-triangle particle fallback; claiming point support silently
	 * dropped every particle because DrawArrays has no point rasterizer. */
	if (!strncmp(proc, "glPointParameter", 16))
	{
		return NULL;
	}

	/* queries */
	if (!strcmp(proc, "glGetString"))		return (void *)ofgl_GetString;
	if (!strcmp(proc, "glGetError"))		return (void *)ofgl_GetError;
	if (!strcmp(proc, "glGetFloatv"))		return (void *)ofgl_GetFloatv;
	if (!strcmp(proc, "glGetIntegerv"))		return (void *)ofgl_GetIntegerv;

	/* state */
	if (!strcmp(proc, "glEnable"))			return (void *)ofgl_Enable;
	if (!strcmp(proc, "glDisable"))			return (void *)ofgl_Disable;
	if (!strcmp(proc, "glDepthMask"))		return (void *)ofgl_DepthMask;
	if (!strcmp(proc, "glTexEnvi"))			return (void *)ofgl_TexEnvi;
	if (!strcmp(proc, "glCullFace"))		return (void *)ofgl_CullFace;
	if (!strcmp(proc, "glFrontFace"))		return (void *)ofgl_FrontFace;
	if (!strcmp(proc, "glColor4f"))			return (void *)ofgl_Color4f;
	if (!strcmp(proc, "glColor4ub"))		return (void *)ofgl_Color4ub;
	if (!strcmp(proc, "glClearColor"))		return (void *)ofgl_ClearColor;
	if (!strcmp(proc, "glClear"))			return (void *)ofgl_Clear;
	if (!strcmp(proc, "glEnableClientState"))	return (void *)ofgl_EnableClientState;
	if (!strcmp(proc, "glDisableClientState"))	return (void *)ofgl_DisableClientState;
	if (!strcmp(proc, "glVertexPointer"))		return (void *)ofgl_VertexPointer;
	if (!strcmp(proc, "glTexCoordPointer"))		return (void *)ofgl_TexCoordPointer;
	if (!strcmp(proc, "glColorPointer"))		return (void *)ofgl_ColorPointer;

	/* 3D matrix stack + viewport (Stage 4) */
	if (!strcmp(proc, "glMatrixMode"))		return (void *)ofgl_MatrixMode;
	if (!strcmp(proc, "glLoadIdentity"))		return (void *)ofgl_LoadIdentity;
	if (!strcmp(proc, "glLoadMatrixf"))		return (void *)ofgl_LoadMatrixf;
	if (!strcmp(proc, "glMultMatrixf"))		return (void *)ofgl_MultMatrixf;
	if (!strcmp(proc, "glPushMatrix"))		return (void *)ofgl_PushMatrix;
	if (!strcmp(proc, "glPopMatrix"))		return (void *)ofgl_PopMatrix;
	/* GLES1 spellings FIRST -- these are the ones GLAD actually asks for on
	 * this build; the desktop names are kept so the seam works either way. */
	if (!strcmp(proc, "glFrustumf"))		return (void *)ofgl_Frustumf;
	if (!strcmp(proc, "glOrthof"))			return (void *)ofgl_Orthof;
	if (!strcmp(proc, "glDepthRangef"))		return (void *)ofgl_DepthRangef;
	if (!strcmp(proc, "glFrustum"))			return (void *)ofgl_Frustum;
	if (!strcmp(proc, "glOrtho"))			return (void *)ofgl_Ortho;
	if (!strcmp(proc, "glRotatef"))			return (void *)ofgl_Rotatef;
	if (!strcmp(proc, "glTranslatef"))		return (void *)ofgl_Translatef;
	if (!strcmp(proc, "glScalef"))			return (void *)ofgl_Scalef;
	if (!strcmp(proc, "glViewport"))		return (void *)ofgl_Viewport;

	/* textures */
	if (!strcmp(proc, "glBindTexture"))		return (void *)ofgl_BindTexture;
	/* gl1 resolves these to decide gl_config.multitexture; returning real
	 * per-unit setters (not the generic no-op) is what keeps TMU1's lightmap
	 * binds from clobbering TMU0's diffuse texture. */
	if (!strcmp(proc, "glActiveTexture") ||
		!strcmp(proc, "glActiveTextureARB"))	return (void *)ofgl_ActiveTexture;
	if (!strcmp(proc, "glClientActiveTexture") ||
		!strcmp(proc, "glClientActiveTextureARB"))
							return (void *)ofgl_ClientActiveTexture;
	if (!strcmp(proc, "glGenTextures"))		return (void *)ofgl_GenTextures;
	if (!strcmp(proc, "glDeleteTextures"))		return (void *)ofgl_DeleteTextures;
	if (!strcmp(proc, "glTexImage2D"))		return (void *)ofgl_TexImage2D;
	if (!strcmp(proc, "glTexSubImage2D"))		return (void *)ofgl_TexSubImage2D;

	/* draw */
	if (!strcmp(proc, "glDrawArrays"))		return (void *)ofgl_DrawArrays;
	if (!strcmp(proc, "glDrawElements"))		return (void *)ofgl_DrawElements;

	/* everything else: a safe no-op. NEVER return NULL -- gladLoadGLES1Loader
	 * sanity-checks the loader and a NULL glad_gl* would crash on first call. */
	return (void *)ofgl_noop;
}

/* ------------------------------------------------------------------------ *
 *  RI_* window/context glue (replaces gl1_sdl.c's SDL_GL implementation)
 * ------------------------------------------------------------------------ */

int
RI_PrepareForWindow(void)
{
	/* No GL window attributes to request -- the of_video framebuffer is
	 * already up (created by GLimp_InitGraphics). Return 0 = no extra flags. */
	return 0;
}

int
RI_InitContext(void *win)
{
	of_video_mode_t want, mode;

	ofgl_fb_clear.fb = NULL;
	(void)win;	/* no SDL_GL context; we bind our own gl* table */

	/* Request the actual scanout format, as the SM64 port does. Verify it
	 * before writing any pixels: an indexed surface cannot hold our RGB565
	 * output, even when its resolution is correct. */
	of_video_init();
	memset(&want, 0, sizeof(want));
	want.width = 320;
	want.height = 240;
	want.color_mode = OF_VIDEO_MODE_RGB565;
	if (of_video_check_mode(&want, &mode) != 0 || of_video_set_mode(&mode) != 0)
	{
		of_video_set_display_mode(OF_DISPLAY_TERMINAL);
		Com_Printf("of_gl1: failed to set %dx%d RGB565 video mode\n",
			want.width, want.height);
		return false;
	}

	of_video_get_mode(&mode);
	if (mode.color_mode != OF_VIDEO_MODE_RGB565)
	{
		/* Older runtimes applied only the resolution through set_mode. */
		of_video_set_color_mode(OF_VIDEO_MODE_RGB565);
		of_video_get_mode(&mode);
	}
	if (mode.color_mode != OF_VIDEO_MODE_RGB565 ||
		mode.width != want.width || mode.height != want.height ||
		mode.stride < mode.width * 2 || (mode.stride & 1) ||
		mode.stride > OF_VIDEO_MAX_STRIDE)
	{
		of_video_set_display_mode(OF_DISPLAY_TERMINAL);
		Com_Printf("of_gl1: invalid video mode: %dx%d stride=%d color=%d\n",
			mode.width, mode.height, mode.stride, mode.color_mode);
		return false;
	}
	ofgl_fb_width = mode.width;
	ofgl_fb_height = mode.height;
	ofgl_fb_stride = mode.stride;
	Com_Printf("of_gl1: RGB565 %dx%d stride=%d\n",
		ofgl_fb_width, ofgl_fb_height, ofgl_fb_stride);

	/* Bind gl1's gl* pointers to our mini-GL. No SDL_GL context exists. */
	if (!gladLoadGLES1Loader((GLADloadproc)RI_GetProcAddress))
	{
		of_video_set_display_mode(OF_DISPLAY_TERMINAL);
		Com_Printf("of_gl1: gladLoadGLES1Loader failed\n");
		return false;
	}

	/* Settle the kernel/display swap state with kernel-driven flips,
	 * clearing every back buffer through the UNCACHED alias (the display
	 * scans DRAM directly), before any drawing. Matches the soft renderer's
	 * SWimp_CreateRender; CMD_FLIP was found unreliable without this. */
	{
		int b;

		for (b = 0; b < 3; b++)
		{
			ofgl_refresh_fb();
			uint8_t *fb = ofgl_fb();

			if (!fb)
			{
				of_video_set_display_mode(OF_DISPLAY_TERMINAL);
				Com_Printf("of_gl1: video surface unavailable\n");
				return false;
			}
			memset(fb, 0, (size_t)ofgl_fb_stride * (size_t)ofgl_fb_height);
			of_video_flip();
			of_video_wait_flip();
		}
		ofgl_refresh_fb();
	}

	/* Initialize CPU matrices and the GPU's truecolor screen-space triangle
	 * path. The depth buffer uses the uncached alias for GPU/CPU visibility. */
	/* bisect switches; see the OFGL_DRAW_* notes */
	ofgl_draw_cv = Cvar_Get("ofgl_draw", "15", 0);
	ofgl_bisect_cv = Cvar_Get("ofgl_bisect", "0", 0);
	ofgl_world_cv = Cvar_Get("ofgl_world", "1", 0);
	ofgl_geometry_cv = Cvar_Get("ofgl_geometry", "1", 0);

	ofgl_mat4_identity(ofgl_proj);
	ofgl_mat4_identity(ofgl_mv_stack[0]);
	ofgl_mv_sp = 0;
	ofgl_gpu_ok = 0;

	if (OFQ2_Init(ofgl_fb_stride))
	{
		const struct of_capabilities *c = of_get_caps();

		Com_Printf("of_gl1: GPU caps: VERT_TRI=%d VCOLOR=%d VERT_TRI_RGB=%d "
			"XFORM_RGB=%d CLIP_LOAD=%d COMBINE=%d FASTTEX=%d GEOMETRY=%d WORKER=%d\n",
			OFQ2_Supports(OFQ2_CAP_VERT_TRI), OFQ2_Supports(OFQ2_CAP_VCOLOR),
			OFQ2_Supports(OFQ2_CAP_VERT_TRI_RGB),
			OFQ2_Supports(OFQ2_CAP_XFORM_RGB),
			OFQ2_Supports(OFQ2_CAP_CLIP_LOAD), OFQ2_Supports(OFQ2_CAP_COMBINE),
			OFQ2_HasFastTexMem(), OFQ2_Supports(OFQ2_CAP_GEOMETRY),
			OFQ2_Supports(OFQ2_CAP_GEOMETRY_WORKER));
		Com_Printf("of_gl1: raw GPU hw_features = 0x%08x\n",
			c ? c->hw_features : 0u);

		if (OFQ2_Supports(OFQ2_CAP_VERT_TRI_RGB))
		{
			size_t zbytes = (size_t)ofgl_fb_width * (size_t)ofgl_fb_height * OFGL_Z_BYTES;
			uint8_t *zraw = (uint8_t *)malloc(zbytes);

			if (zraw)
			{
				ofgl_zraw = zraw;
				OFQ2_CacheClean(zraw, zbytes);
				ofgl_zbuf = (uint8_t *)of_uncached(zraw);
				ofgl_z_base = (uint32_t)(uintptr_t)ofgl_zbuf;
				ofgl_z_major = ofgl_fb_width * OFGL_Z_BYTES;
				ofgl_gpu_ok = 1;
			}
		}
	}

	if (!ofgl_gpu_ok)
	{
		of_video_set_display_mode(OF_DISPLAY_TERMINAL);
		Com_Printf("of_gl1: 3D initialization failed: RGB565 GPU or depth buffer unavailable\n");
		OFQ2_Shutdown();
		return false;
	}
	Com_Printf("of_gl1: framebuffer %dx%d RGB565 truecolor; 2D HUD + GPU-raster 3D\n",
		ofgl_fb_width, ofgl_fb_height);
	return true;
}

void
RI_EndFrame(void)
{
	/* GL1 batches the last font/HUD texture until presentation. Flush it onto
	 * this draw buffer before the flip, just as the SDL backend does. */
	extern void R_ApplyGLBuffer(void);
	R_ApplyGLBuffer();

	/* Present this frame, then clear the new draw buffer (RGB565) so the next
	 * frame starts clean (2D-only frames never call glClear). */

	ofgl_bisect_tick();

	/* Drain any 3D the HUD pass didn't already drain (e.g. a frame with no 2D):
	 * the kernel flip must not present a half-rasterised buffer. */
	if (ofgl_gpu_pending)
	{
		OFQ2_Finish();
		ofgl_gpu_pending = 0;
	}

	of_video_flip();
	ofgl_refresh_fb();
	/* of_video_surface() now names the next draw buffer; queue its clear on the
	 * GPU and publish it immediately, so the fill overlaps the client frame
	 * instead of costing the CPU a full-screen uncached memset. */
	ofgl_clear_fb565();
	if (ofgl_gpu_pending)
	{
		/* Advisory kicks retain short batches; this clear must start now. */
		OFQ2_KickNow();
	}
}

void
RI_ShutdownContext(void)
{
	/* The renderer is recreated by vid_restart. Drain before freeing any
	 * memory referenced by the GPU, including SDRAM textures and depth. */
	OFQ2_Shutdown();
	ofgl_gpu_pending = 0;
	ofgl_gpu_ok = 0;
	OFGL_SetDrawBounds(NULL, NULL);
	OFGL_SetWorldDraw(0);
	ofgl_batch_world = 0;
	ofgl_framebuffer = NULL;
	ofgl_fb_clear.fb = NULL;
	for (int i = 0; i < OFGL_MAX_TEX; i++)
	{
		ofgl_release_tex(&ofgl_tex[i]);
	}
	for (int i = 0; i < OFGL_LM_BLOCKS; i++)
	{
		free(ofgl_lm[i]);
		ofgl_lm[i] = NULL;
	}
	ofgl_lm_bytes = 0;
	ofgl_lm_dropped = 0;
	ofgl_lm_cur = NULL;
	ofgl_lm_active = 0;
	free(ofgl_zraw);
	ofgl_zraw = ofgl_zbuf = NULL;
	ofgl_z_base = 0;
	ofgl_z_major = 0;
	ofgl_flat_ready = 0;
	ofgl_gen_next = 1;
	ofgl_proj_sp = ofgl_mv_sp = 0;
	ofgl_proj_over = ofgl_mv_over = 0;
	ofgl_mat_mode = GL_MODELVIEW;
	ofgl_obj_dirty = 1;
	ofgl_active_tmu = ofgl_client_tmu = 0;
	memset(ofgl_tex_bound, 0, sizeof(ofgl_tex_bound));
	memset(&ofgl_va_vertex, 0, sizeof(ofgl_va_vertex));
	memset(ofgl_va_texcoord_tmu, 0, sizeof(ofgl_va_texcoord_tmu));
	memset(&ofgl_va_color, 0, sizeof(ofgl_va_color));
	ofgl_tex2d_on[0] = 1;
	ofgl_tex2d_on[1] = 0;
	ofgl_texenv[0] = ofgl_texenv[1] = GL_REPLACE;
	ofgl_depth_test = ofgl_depth_mask = 1;
	ofgl_blend_on = ofgl_cull_on = 0;
	ofgl_depth_scale = 1.0f;
	ofgl_cull_face = GL_FRONT;
	ofgl_front_face = GL_CCW;
	ofgl_Color4f(1, 1, 1, 1);
}

void
RI_SetVsync(void)
{
}

void
RI_UpdateGamma(void)
{
}

qboolean
RI_IsVSyncActive(void)
{
	return false;
}

void
RI_GetDrawableSize(int *width, int *height)
{
	if (width)
	{
		*width = ofgl_fb_width;
	}
	if (height)
	{
		*height = ofgl_fb_height;
	}
}

int
RI_GetSDLVersion(void)
{
	return 0;
}
