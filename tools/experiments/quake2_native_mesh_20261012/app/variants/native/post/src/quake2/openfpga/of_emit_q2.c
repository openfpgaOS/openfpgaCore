/*
 * Quake II openfpgaOS GPU command owner.
 */

#include "../src/client/refresh/soft/header/local.h"
#include "of.h"
#include "of_cache.h"
#include "of_caps.h"
#include "of_gpu_profile_q2.h"
#include "of_gpu.h"
#include "of_texture.h"
#include "of_video.h"

#include "of_emit_q2.h"

#include <string.h>

#define OFQ2_CLEAN_ONCE_SLOTS 512
#define OFQ2_CLEAN_ONCE_HASH_SIZE 1024
#define OFQ2_CLEAN_ONCE_HASH_MASK (OFQ2_CLEAN_ONCE_HASH_SIZE - 1u)

typedef struct ofq2_clean_once_s {
	uintptr_t addr;
	uint32_t size;
} ofq2_clean_once_t;

static uint32_t ofq2_caps;
static qboolean ofq2_ready;
static qboolean ofq2_pending;
static uint8_t *ofq2_bound_fb;
static int ofq2_bound_stride;
static ofq2_clean_once_t ofq2_clean_once_slots[OFQ2_CLEAN_ONCE_SLOTS];
static uint16_t ofq2_clean_once_hash[OFQ2_CLEAN_ONCE_HASH_SIZE];
static uint32_t ofq2_clean_once_next;

static uint32_t
ofq2_clean_hash(uintptr_t addr, uint32_t size)
{
	uint32_t x = (uint32_t)(addr >> 6) ^ (uint32_t)(addr >> 17) ^
		(size * 2654435761u);
	x ^= x >> 16;
	x *= 0x7feb352du;
	x ^= x >> 15;
	return x & OFQ2_CLEAN_ONCE_HASH_MASK;
}

static int
ofq2_ranges_overlap(uintptr_t a, uint32_t asz, uintptr_t b, uint32_t bsz)
{
	uintptr_t ae = a + asz;
	uintptr_t be = b + bsz;
	return a < be && b < ae;
}

static void
ofq2_clean_once_unindex(uint32_t slot)
{
	if (slot >= OFQ2_CLEAN_ONCE_SLOTS)
	{
		return;
	}

	ofq2_clean_once_t *entry = &ofq2_clean_once_slots[slot];
	if (entry->addr == 0 || entry->size == 0)
	{
		return;
	}

	uint32_t h = ofq2_clean_hash(entry->addr, entry->size);
	if (ofq2_clean_once_hash[h] == slot + 1u)
	{
		ofq2_clean_once_hash[h] = 0;
	}
}

/* Sticky 0x4A surface-state shadow, shared by BOTH consumers — the
 * alias vert-tri path (0x4B, attr_q29 = 0) and the world records-only
 * param-tri path (0x4D, attr_q29 = 1).  Hardware contract: any 0x48
 * (>= 11 words), 0x49, or 0x4C header clears the GPU's tri_state_valid
 * (even on variants that drain the draw itself), so EVERY emission of
 * those commands in this file must set ofq2_tristate_dirty; 0x4B/0x4D
 * do not invalidate.  All 0x4A arming routes through OFQ2_SetTriState,
 * which re-emits only when the descriptor changed or the state was
 * dirtied — a missed dirty mark = stale sticky state = the GPU retires
 * 0x4B/0x4D as invisible no-op triangles. */
static ofq2_tri_state_t ofq2_tristate_cur;
static qboolean ofq2_tristate_valid;
static qboolean ofq2_tristate_dirty = true;

/* 0x50 object-state shadow: the matrix + projection consts are frame-constant
 * for the world (only q29_shift varies per surface), so SetObjectState skips
 * re-emitting the 26-word command when nothing changed.  Invalidated on init
 * and once per frame (OFQ2_ObjectStateReset) — within a frame the GPU 0x50
 * register persists, but a reset/frame boundary can clear it. */
static ofq2_object_state_t ofq2_objstate_cur;
static qboolean ofq2_objstate_valid;

/* Fast-texture manager generation: OFQ2_TexReset bumps it so image_t handles
 * built in an older generation are detected stale and lazily re-created. */
static uint32_t ofq2_tex_generation = 1;
/* Textures grow upwards; immutable native assets grow downwards. Shrinking
 * the texture manager's allocation ceiling keeps both owners disjoint. Its
 * physical end is retained so map changes can reclaim only native storage. */
static uint32_t ofq2_native_end;
static uint32_t ofq2_native_generation = 1;

static void
ofq2_native_epoch(void)
{
	if (++ofq2_native_generation == 0) ofq2_native_generation = 1;
}

/* ofq2_texture_t (of_emit_q2.h) is cast to of_texture_t (of_texture.h); the
 * layouts must stay identical. Field order/types match by construction; guard
 * the size so a future drift fails the build rather than corrupting handles. */
typedef char ofq2_texture_layout_check[
	(sizeof(ofq2_texture_t) == sizeof(of_texture_t)) ? 1 : -1];

/* The texture route survives GPU soft reset and shutdown. Normalize it only
 * with an idle GPU and a reported fast-memory tier; SDRAM-only cores need no
 * route-register access. This does not recreate or migrate texture handles. */
static void
ofq2_texture_route_sdram(void)
{
	if (of_texture_has_fast_mem())
	{
		_of_gpu_route_fast_tex(0);
		_of_tex_bound = 0;
	}
}

int
OFQ2_Init(int fb_stride)
{
	const struct of_capabilities *caps = of_get_caps();
	uint32_t hw;

	if (!caps || caps->gpu_base == 0)
	{
		ofq2_ready = false;
		return 0;
	}

	hw = caps->hw_features;
	if ((hw & OF_HW_GPU_SPAN) == 0)
	{
		ofq2_ready = false;
		return 0;
	}

	of_gpu_init();

	/* of_gpu_init() soft-resets the GPU, clearing the hardware
	 * tri_state_valid latch; mirror that on the port side so the first
	 * SetTriState after any (re)init re-arms the HW unconditionally —
	 * otherwise a vid_restart that re-acquires identical fb/z/skin
	 * addresses could short-circuit the re-emit and silently drop the
	 * first alias model. */
	ofq2_tristate_valid = false;
	ofq2_tristate_dirty = true;
	memset(&ofq2_tristate_cur, 0, sizeof(ofq2_tristate_cur));
	ofq2_objstate_valid = false;
	memset(&ofq2_objstate_cur, 0, sizeof(ofq2_objstate_cur));

	ofq2_caps = OFQ2_CAP_SPAN;
	if (hw & OF_HW_GPU_PERSP)
	{
		ofq2_caps |= OFQ2_CAP_PERSP;
	}
	if (hw & OF_HW_GPU_ALPHA)
	{
		ofq2_caps |= OFQ2_CAP_ALPHA;
	}
	if (hw & OF_HW_GPU_VCOLOR)
	{
		ofq2_caps |= OFQ2_CAP_VCOLOR;
	}
	/* Screen-space truecolor triangle (0x4E) only needs the raw vert-tri plane
	 * derivation + per-vertex color -- NOT the param-tri edge walker. */
	if ((hw & OF_HW_GPU_VERT_TRI) && (hw & OF_HW_GPU_VCOLOR))
	{
		ofq2_caps |= OFQ2_CAP_VERT_TRI_RGB;
	}
	/* GPU-queued CMD_FLIP needs the kernel's acquire/buffer-addr
	 * services (same derivation as the Quake reference). */
	if (OF_SVC && OF_SVC->magic == OF_SVC_MAGIC && OF_SVC->count >= 14 &&
		OF_SVC->video_acquire_next && OF_SVC->video_buffer_addr)
	{
		ofq2_caps |= OFQ2_CAP_FLIP;
	}
	if (hw & OF_HW_GPU_PARAM_SPAN_LIST)
	{
		ofq2_caps |= OFQ2_CAP_PARAM_SPAN_LIST;
	}
	/* The dependent caps are meaningless without the span-list
	 * command itself — compose like the reference does. */
	if ((hw & (OF_HW_GPU_PARAM_SPAN_LIST | OF_HW_GPU_PARAM_SPAN_Z)) ==
		(OF_HW_GPU_PARAM_SPAN_LIST | OF_HW_GPU_PARAM_SPAN_Z))
	{
		ofq2_caps |= OFQ2_CAP_PARAM_SPAN_Z;
	}
	if ((hw & (OF_HW_GPU_PARAM_SPAN_LIST | OF_HW_GPU_PARAM_SPAN_ZTEST)) ==
		(OF_HW_GPU_PARAM_SPAN_LIST | OF_HW_GPU_PARAM_SPAN_ZTEST))
	{
		ofq2_caps |= OFQ2_CAP_PARAM_SPAN_ZTEST;
	}
	if ((hw & (OF_HW_GPU_PARAM_SPAN_LIST | OF_HW_GPU_PARAM_SPAN_Q29_SCALE)) ==
		(OF_HW_GPU_PARAM_SPAN_LIST | OF_HW_GPU_PARAM_SPAN_Q29_SCALE))
	{
		ofq2_caps |= OFQ2_CAP_Q29_SCALE;
	}
	/* the triangle walker emits ATTR_PERSP commands through the param
	 * header path — both dependencies must exist or the RTL header
	 * gate silently drops every triangle */
	if ((hw & (OF_HW_GPU_PARAM_TRI | OF_HW_GPU_PERSP |
			OF_HW_GPU_PARAM_SPAN_LIST)) ==
		(OF_HW_GPU_PARAM_TRI | OF_HW_GPU_PERSP |
			OF_HW_GPU_PARAM_SPAN_LIST))
	{
		ofq2_caps |= OFQ2_CAP_PARAM_TRI;
	}
	/* Vertex-triangle (0x4A sticky + 0x4B HW plane derivation) needs ONLY
	 * OF_HW_GPU_VERT_TRI — of_gpu.h gates the emitter on exactly that bit.
	 * It must NOT be chained off OFQ2_CAP_PARAM_TRI: the lean 3D bitstream
	 * (Pocket os30) ships the vert-tri derivation while deliberately cutting
	 * the 0x49 param-tri edge walker (bit 19 clear), so the old chain
	 * silently zeroed vert-tri, the transform front-end and the truecolor
	 * transform cluster on the one core that implements all three. */
	if (hw & OF_HW_GPU_VERT_TRI)
	{
		ofq2_caps |= OFQ2_CAP_VERT_TRI;
	}
	/* transform front-end (0x50 object state + 0x51 xform tri): its own
	 * feature bit, coupled to vert-tri in the RTL decode */
	if ((ofq2_caps & OFQ2_CAP_VERT_TRI) && (hw & OF_HW_GPU_XFORM_RGB))
	{
		ofq2_caps |= OFQ2_CAP_XFORM_TRI;
	}
	/* truecolor transform cluster (0x52 xform_tri_rgb): the RTL gates it on
	 * the xform-rgb feature plus the truecolor pair (vert-tri + vcolor). */
	if ((ofq2_caps & OFQ2_CAP_XFORM_TRI) && (ofq2_caps & OFQ2_CAP_VCOLOR))
	{
		ofq2_caps |= OFQ2_CAP_XFORM_RGB;
	}
	/* GPU vertex cache fed with CPU clip-space verts (0x56 + 0x54).  Its own
	 * bit, independent of the matrix MAC cluster — a build can ship the cache
	 * load without the transform front-end and vice versa. */
	if ((ofq2_caps & OFQ2_CAP_VERT_TRI) && (hw & OF_HW_GPU_CLIP_LOAD))
	{
		ofq2_caps |= OFQ2_CAP_CLIP_LOAD;
	}
	uint32_t geometry_status = GPU_STATUS;
	if ((ofq2_caps & OFQ2_CAP_CLIP_LOAD) &&
		(ofq2_caps & OFQ2_CAP_VERT_TRI_RGB) && (geometry_status & (1u << 10)))
	{
		ofq2_caps |= OFQ2_CAP_GEOMETRY;
		/* Status bit 11 distinguishes the independent worker from the
		 * inline geometry decoder, whose raw packet limit is three vertices. */
		if (geometry_status & (1u << 11)) ofq2_caps |= OFQ2_CAP_GEOMETRY_WORKER;
		if ((geometry_status & (3u << 11)) == (3u << 11))
			ofq2_caps |= OFQ2_CAP_NATIVE_MESH;
	}
	/* full texel*C+D colour combiner (22-word 0x4E + control bit 30) */
	if ((ofq2_caps & OFQ2_CAP_VCOLOR) && (hw & OF_HW_GPU_COMBINE))
	{
		ofq2_caps |= OFQ2_CAP_COMBINE;
	}
	/* records-only param triangle (0x4A sticky + 0x4D): the same edge
	 * walker and plane datapath as 0x49, so it needs every PARAM_TRI
	 * dependency plus its own decode arm */
	if ((hw & OF_HW_GPU_PARAM_TRI_RECS) &&
		(ofq2_caps & OFQ2_CAP_PARAM_TRI))
	{
		ofq2_caps |= OFQ2_CAP_PARAM_TRI_RECS;
	}

	memset(ofq2_clean_once_slots, 0, sizeof(ofq2_clean_once_slots));
	memset(ofq2_clean_once_hash, 0, sizeof(ofq2_clean_once_hash));
	ofq2_clean_once_next = 0;
	ofq2_bound_fb = NULL;
	ofq2_bound_stride = fb_stride;
	ofq2_pending = false;

	/* fast-texture manager: read tex_fast_size now that the GPU is up
	 * (no-op / SDRAM-only when the target has no fast texture tier). */
	of_texture_init();
	ofq2_native_end = _of_tex_fast_size;
	ofq2_native_epoch();
	ofq2_texture_route_sdram();
	ofq2_tex_generation = 1;

	ofq2_ready = true;

	return 1;
}

/* ---- Fast-texture wrappers (the of_texture manager lives in this TU). ---- */

void
OFQ2_TexInit(void)
{
	if (!ofq2_ready)
		return;
	/* Reinitialization invalidates all handles: finish their draws first. */
	OFQ2_Finish();
	of_texture_init();
	ofq2_native_end = _of_tex_fast_size;
	ofq2_native_epoch();
	ofq2_texture_route_sdram();
	ofq2_tex_generation = 1;
}

void
OFQ2_TexReset(void)
{
	if (!ofq2_ready)
		return;
	/* Drain before new uploads reuse offsets referenced by queued draws. */
	OFQ2_Finish();
	_of_tex_fast_size = ofq2_native_end;
	ofq2_native_epoch();
	of_texture_reset();
	ofq2_tex_generation++;
}

uint32_t
OFQ2_TexGeneration(void)
{
	return ofq2_tex_generation;
}

void
OFQ2_TexCreate(ofq2_texture_t *t, const void *pixels,
	uint16_t w, uint16_t h, uint32_t nbytes)
{
	if (!t || !pixels)
		return;
	/* The GPU reads the source in place on SDRAM-only targets, and the
	 * fast upload reads it too — commit it from the CPU D-cache first. */
	if (ofq2_ready)
		OFQ2_CacheClean(pixels, nbytes);
	of_texture_create((of_texture_t *)t, pixels, w, h, nbytes);
}

void
OFQ2_TexSetColormap(const void *cm, uint32_t nbytes)
{
	if (!ofq2_ready || !cm)
		return;
	OFQ2_Finish();
	/* of_texture_set_colormap points the GPU at `cm` in place for the SDRAM
	 * domain (and copies it into fast memory if present); commit the CPU
	 * writes so the GPU's AXI fetch sees them. */
	OFQ2_CacheClean(cm, nbytes);
	of_texture_set_colormap(cm, nbytes);
	GPU_TEX_FLUSH = 1;
}

void
OFQ2_TexBind(const ofq2_texture_t *t)
{
	if (!ofq2_ready || !t)
		return;
	of_texture_bind((const of_texture_t *)t);
}

void
OFQ2_TexBindDomain(const ofq2_texture_t *t)
{
	int want_fast;

	if (!ofq2_ready || !t || !_of_tex_fast_size)
		return;	/* SDRAM-only target: one domain, nothing to route */
	want_fast = (t->flags & _OF_TEX_FAST) ? 1 : 0;
	if (want_fast == _of_tex_bound)
		return;	/* already in this tier — the common per-surface no-op */
	of_gpu_finish();			/* drain before the fetch-domain switch */
	_of_gpu_route_fast_tex(want_fast);
	GPU_PALOOKUP_BASE = (want_fast && _of_tex_cmap_has_fast)
			  ? _of_tex_cmap_fast : _of_tex_cmap_sdram;
	_of_tex_bound = want_fast;
}

void
OFQ2_TexDomainSDRAM(void)
{
	if (!ofq2_ready || !_of_tex_fast_size || _of_tex_bound == 0)
		return;
	of_gpu_finish();
	_of_gpu_route_fast_tex(0);
	GPU_PALOOKUP_BASE = _of_tex_cmap_sdram;
	_of_tex_bound = 0;
}

void
OFQ2_Shutdown(void)
{
	if (ofq2_ready)
	{
		OFQ2_Finish();
		ofq2_texture_route_sdram();
		of_gpu_shutdown();
	}

	ofq2_ready = false;
	ofq2_caps = 0;
	ofq2_bound_fb = NULL;
	ofq2_bound_stride = 0;
	ofq2_pending = false;
}

int
OFQ2_Supports(uint32_t cap)
{
	return ofq2_ready && ((ofq2_caps & cap) == cap);
}

int
OFQ2_HasFastTexMem(void)
{
	return of_texture_has_fast_mem();
}

void
OFQ2_BindFramebuffer(uint8_t *fb, int stride)
{
	if (!ofq2_ready || !fb)
	{
		return;
	}

	if (ofq2_bound_fb == fb && ofq2_bound_stride == stride)
	{
		return;
	}

	of_gpu_set_framebuffer((uint32_t)(uintptr_t)fb, (uint16_t)stride);
	ofq2_bound_fb = fb;
	ofq2_bound_stride = stride;
}

void
OFQ2_Kick(void)
{
	if (ofq2_ready)
	{
		of_gpu_kick();
	}
}

/* Publish a frame-boundary batch without waiting for its GPU completion. */
void
OFQ2_KickNow(void)
{
	if (ofq2_ready)
	{
		of_gpu_kick_now();
	}
}

/* perf forensics: microseconds spent waiting for the GPU, and the
 * volume of submitted span work — read+reset by the renderer's perf
 * heartbeat each window */
uint32_t ofq2_stat_sync_us;
uint32_t ofq2_stat_lists;
uint32_t ofq2_stat_records;

/* which call site is draining: 0 = CPU drawers (alias fallback,
 * particles, translucency), 1 = 2D prepare, 2 = turb/water z barrier,
 * 3 = presentation/fb-validate */
uint8_t ofq2_sync_tag;
uint32_t ofq2_stat_sync_site[4];
uint32_t ofq2_stat_2d;	/* 2D GPU draws this window (chars + pics) */
uint32_t ofq2_stat_verttri;	/* vert-tri (0x4B) draws emitted this window */
uint32_t ofq2_stat_trirecs;	/* records-only tri (0x4D) draws this window */
uint32_t ofq2_stat_tristate;	/* sticky-state (0x4A) re-arms this window:
				 * trirecs/tristate is the header-dedup hit
				 * rate vs the 0x49 baseline (17 vs 38 words
				 * per triangle, +17 per re-arm) */
uint32_t ofq2_stat_2d_us;	/* us building 2D GPU blits this window */

/* Ring-backpressure counts, not elapsed time. MMIO polling latency varies;
 * use the optional episode timers to measure time spent waiting. */
void
OFQ2_RingStats(uint32_t *waits, uint32_t *spins,
	uint32_t *dma_waits, uint32_t *dma_spins)
{
	*waits = _gpu_dbg_ring_waits;
	*spins = _gpu_dbg_ring_spin_iters;
	*dma_waits = _gpu_dbg_dma_waits;
	*dma_spins = _gpu_dbg_dma_spin_iters;
	_gpu_dbg_ring_waits = 0;
	_gpu_dbg_ring_spin_iters = 0;
	_gpu_dbg_dma_waits = 0;
	_gpu_dbg_dma_spin_iters = 0;
}

void
OFQ2_Finish(void)
{
	if (ofq2_ready && ofq2_pending)
	{
		uint32_t t0 = of_time_us();
		uint32_t dt;

		of_gpu_finish();
		dt = of_time_us() - t0;
		ofq2_stat_sync_us += dt;
		ofq2_stat_sync_site[ofq2_sync_tag & 3] += dt;
		ofq2_pending = false;
	}
}

void
OFQ2_PrepareForCPU(void)
{
	OFQ2_Finish();
}

uint32_t
OFQ2_FlipTo(int idx)
{
	if (!ofq2_ready)
	{
		return 0;
	}

	return of_gpu_flip_to(idx);
}

void
OFQ2_NotifyFlipRetired(void)
{
	/* of_video_acquire_next() proved the queued CMD_FLIP retired,
	 * which orders after every prior raster command in the ring. */
	ofq2_pending = false;
}

void
OFQ2_TextureCacheFlush(void)
{
	if (ofq2_ready)
	{
		GPU_TEX_FLUSH = 1;
	}
}

void
OFQ2_CacheClean(const void *addr, uint32_t size)
{
	volatile const uint32_t *p;
	uintptr_t a, start, end;
	uint32_t sink = 0;

	if (!addr || size == 0)
	{
		return;
	}

	of_cache_flush_range((void *)addr, size);

	/* of_cache_flush_range's trailing fence orders the CPU but does
	 * NOT wait for d_axi writebacks to reach SDRAM (see the kernel
	 * HAL's own comment); the GPU may fetch these texels microseconds
	 * after submit. Re-read each line through the cached alias — the
	 * refill on d_axi drains the pending writebacks first (the
	 * reference port's readback fence). */
	start = (uintptr_t)addr & ~(uintptr_t)63u;
	end = ((uintptr_t)addr + size + 63u) & ~(uintptr_t)63u;
	for (a = start; a < end; a += 64u)
	{
		p = (volatile const uint32_t *)a;
		sink ^= *p;
	}
	(void)sink;
}

void
OFQ2_CacheCleanOnce(const void *addr, uint32_t size)
{
	uintptr_t a;
	uint32_t h, slot;

	if (!addr || size == 0)
	{
		return;
	}

	a = (uintptr_t)addr;
	h = ofq2_clean_hash(a, size);
	slot = ofq2_clean_once_hash[h];
	if (slot)
	{
		ofq2_clean_once_t *entry = &ofq2_clean_once_slots[slot - 1u];
		if (entry->addr == a && entry->size == size)
		{
			return;
		}

		/* (#18) Hash bucket is occupied by a DIFFERENT (addr,size) — a
		 * collision. Only then can our entry still be living in some other
		 * slot, so only then is the full table scan worthwhile. When the
		 * bucket is empty (slot==0) our entry cannot be in the table at all
		 * (every insert sets its bucket; eviction/forget clears it), so the
		 * ~512-wide scan is provably futile — skip it on the common
		 * first-clean-of-a-texture path. */
		for (uint32_t i = 0; i < OFQ2_CLEAN_ONCE_SLOTS; i++)
		{
			if (ofq2_clean_once_slots[i].addr == a &&
				ofq2_clean_once_slots[i].size == size)
			{
				ofq2_clean_once_hash[h] = (uint16_t)(i + 1u);
				return;
			}
		}
	}

	OFQ2_CacheClean(addr, size);

	ofq2_clean_once_unindex(ofq2_clean_once_next);
	ofq2_clean_once_slots[ofq2_clean_once_next].addr = a;
	ofq2_clean_once_slots[ofq2_clean_once_next].size = size;
	ofq2_clean_once_hash[h] = (uint16_t)(ofq2_clean_once_next + 1u);
	ofq2_clean_once_next =
		(ofq2_clean_once_next + 1u) % OFQ2_CLEAN_ONCE_SLOTS;
}

void
OFQ2_CacheCleanForget(const void *addr, uint32_t size)
{
	uintptr_t a;

	if (!addr || size == 0)
	{
		return;
	}

	a = (uintptr_t)addr;
	for (uint32_t i = 0; i < OFQ2_CLEAN_ONCE_SLOTS; i++)
	{
		ofq2_clean_once_t *entry = &ofq2_clean_once_slots[i];
		if (entry->addr && ofq2_ranges_overlap(a, size, entry->addr, entry->size))
		{
			ofq2_clean_once_unindex(i);
			entry->addr = 0;
			entry->size = 0;
		}
	}
}

void
OFQ2_PalookupUpload(int slot, const void *data, uint32_t size)
{
	if (!ofq2_ready || !data || slot < 0)
	{
		return;
	}

	/* The GPU reads palookup bytes through the texture cache; make the
	 * upload visible to in-flight and future fetches alike. */
	OFQ2_Finish();
	of_gpu_palookup_upload((uint8_t)slot, (const uint8_t *)data, size);
	GPU_TEX_FLUSH = 1;
}

/* last-N GPU submission log — dumped by the .text watchdog when code
 * memory corrupts, to identify the scribbling command */
#define OFQ2_CMDLOG_N 8
typedef struct {
	uint32_t fb_base, z_base;
	uint8_t z_mode, attr_mode, flags;
	uint16_t maxv, maxcount, nrec;
} ofq2_cmdlog_t;
static ofq2_cmdlog_t ofq2_cmdlog[OFQ2_CMDLOG_N];
static uint32_t ofq2_cmdlog_next;


void
OFQ2_CmdLogDump(void)
{
	uint32_t i;

	for (i = 0; i < OFQ2_CMDLOG_N; i++)
	{
		const ofq2_cmdlog_t *e =
			&ofq2_cmdlog[(ofq2_cmdlog_next + i) % OFQ2_CMDLOG_N];

		Com_Printf("  cmd[-%u] fb=%08x z=%08x zm=%u am=%u fl=%02x maxv=%u maxc=%u n=%u\n",
			OFQ2_CMDLOG_N - i, e->fb_base, e->z_base, e->z_mode,
			e->attr_mode, e->flags, e->maxv, e->maxcount, e->nrec);
	}
}

int
OFQ2_DrawParamSpanList(const ofq2_param_span_list_t *params,
		const ofq2_param_span_record_t *records, uint32_t record_count)
{
	ofq2_cmdlog_t *e;
	uint32_t i;

	if (!OFQ2_Supports(OFQ2_CAP_PARAM_SPAN_LIST) ||
		!params || !records || record_count == 0)
	{
		return 0;
	}

	e = &ofq2_cmdlog[ofq2_cmdlog_next % OFQ2_CMDLOG_N];
	ofq2_cmdlog_next++;
	e->fb_base = params->fb_base;
	e->z_base = params->z_base;
	e->z_mode = params->z_mode;
	e->attr_mode = params->attr_mode;
	e->flags = params->flags;
	e->maxv = 0;
	e->maxcount = 0;
	e->nrec = (uint16_t)record_count;
	for (i = 0; i < record_count; i++)
	{
		if (records[i].v > e->maxv)
			e->maxv = records[i].v;
		if (records[i].count > e->maxcount)
			e->maxcount = records[i].count;
	}

	ofq2_stat_lists++;
	ofq2_stat_records += record_count;

	of_gpu_draw_param_span_list((const of_gpu_param_span_list_t *)params,
		(const of_gpu_param_span_record_t *)records, record_count);
	ofq2_tristate_dirty = true;
	ofq2_pending = true;
	return (int)record_count;
}

int
OFQ2_DrawParamTri(const ofq2_param_span_list_t *params,
		const int16_t vx[3], const int16_t vy[3],
		int16_t clip_x0, int16_t clip_x1,
		int16_t clip_y0, int16_t clip_y1)
{
	of_gpu_tri_vert_t v[3];
	ofq2_cmdlog_t *e;
	int i;

	if (!OFQ2_Supports(OFQ2_CAP_PARAM_SPAN_LIST | OFQ2_CAP_PARAM_TRI) ||
		!params || !vx || !vy)
	{
		return 0;
	}

	e = &ofq2_cmdlog[ofq2_cmdlog_next % OFQ2_CMDLOG_N];
	ofq2_cmdlog_next++;
	e->fb_base = params->fb_base;
	e->z_base = params->z_base;
	e->z_mode = params->z_mode;
	e->attr_mode = params->attr_mode;
	e->flags = params->flags;
	e->maxv = (uint16_t)clip_y1;
	e->maxcount = (uint16_t)(clip_x1 - clip_x0);
	e->nrec = 0xFFFF;	/* marks a HW-walked triangle in the log */

	for (i = 0; i < 3; i++)
	{
		v[i].x = vx[i];
		v[i].y = vy[i];
	}

	ofq2_stat_lists++;

	/* Records-only lowering (0x4A sticky surface state + 17-word 0x4D
	 * per triangle instead of one 38-word 0x49): take it whenever the
	 * cap is present and the attr mode is PERSP-family — the sticky
	 * control word can only express PERSP/PERSP_Q29.  The per-triangle
	 * q29 shift rides in the 0x4D payload, so a varying shift does NOT
	 * force a re-arm; only the MODE bit (attr_q29) is sticky.  Even a
	 * full surface switch (0x4A + 0x4D = 34 words) beats one 0x49.
	 * Degenerate clips fall through so the 0x49 emitter's refusal keeps
	 * today's behavior. */
	if ((ofq2_caps & OFQ2_CAP_PARAM_TRI_RECS) &&
		(params->attr_mode == OFQ2_PARAM_ATTR_PERSP ||
			params->attr_mode == OFQ2_PARAM_ATTR_PERSP_Q29) &&
		clip_x0 < clip_x1 && clip_y0 < clip_y1)
	{
		ofq2_tri_state_t st;

		memset(&st, 0, sizeof(st));
		st.fb_base = params->fb_base;
		st.fb_major_step = params->fb_major_step;
		st.fb_minor_step = params->fb_minor_step;
		st.tex_addr = params->tex_addr;
		st.tex_width = params->tex_width;
		st.tex_w_mask = params->tex_w_mask;
		st.tex_h_mask = params->tex_h_mask;
		st.flags = params->flags;
		st.colormap_id = params->colormap_id;
		st.z_mode = params->z_mode;
		st.clamp_min[0] = params->clamp_min[0];
		st.clamp_max[0] = params->clamp_max[0];
		st.clamp_min[1] = params->clamp_min[1];
		st.clamp_max[1] = params->clamp_max[1];
		st.z_base = params->z_base;
		st.z_major_step = params->z_major_step;
		st.z_minor_step = params->z_minor_step;
		st.clip_x0 = clip_x0;
		st.clip_x1 = clip_x1;
		st.clip_y0 = clip_y0;
		st.clip_y1 = clip_y1;
		st.attr_q29 =
			(params->attr_mode == OFQ2_PARAM_ATTR_PERSP_Q29);

		/* shadow-aware: emits the 0x4A only when a surface-relevant
		 * field differs or another command dirtied the sticky state.
		 * Gate the 0x4D on SetTriState having ARMED THIS descriptor
		 * (returns false on a z_mode/cap refusal), so a refusal can't
		 * reuse a stale-but-clean sticky from a different surface. */
		if (OFQ2_SetTriState(&st))
		{
			e->nrec = 0xFFFD;	/* marks a records-only (0x4D) tri */
			ofq2_stat_trirecs++;
			of_gpu_draw_param_tri_recs(
				(const of_gpu_param_span_list_t *)params, v);
			ofq2_pending = true;
			return 1;
		}
		/* SetTriState refused — fall through to the full 0x49 */
	}

	of_gpu_draw_param_tri((const of_gpu_param_span_list_t *)params, v,
		clip_x0, clip_x1, clip_y0, clip_y1);
	ofq2_tristate_dirty = true;
	ofq2_pending = true;
	return 1;
}

int
OFQ2_SetTriState(const ofq2_tri_state_t *st)
{
	of_gpu_tri_state_t hw;

	/* 0x4A serves every sticky-state consumer — 0x4B (VERT_TRI), 0x4D
	 * (PARAM_TRI_RECS), and the truecolor 0x4E (VERT_TRI_RGB). Accept it
	 * when any of those is present (mirrors of_gpu_set_tri_state, which
	 * gates on the raw OF_HW_GPU_VERT_TRI). */
	if (!ofq2_ready || !st ||
		!(ofq2_caps & (OFQ2_CAP_VERT_TRI | OFQ2_CAP_PARAM_TRI_RECS |
			OFQ2_CAP_VERT_TRI_RGB)))
	{
		return false;
	}

	/* mirror the SDK emitter's z_mode refusal BEFORE touching the
	 * shadow: otherwise the shadow would claim an armed state the SDK
	 * never put on the wire and the next 0x4B/0x4D would no-op */
	if (st->z_mode != OFQ2_PARAM_Z_NONE &&
		st->z_mode != OFQ2_PARAM_Z_WRITE_ZI &&
		st->z_mode != OFQ2_PARAM_Z_TEST_ZI &&
		st->z_mode != OFQ2_PARAM_Z_TEST_WRITE)
	{
		return false;
	}

	/* idempotent: skip the GPU re-emit when nothing changed and no
	 * other command dirtied the sticky state. Field-wise compare (not
	 * memcmp) so the struct's interior padding can't force a spurious
	 * per-triangle re-emit regardless of caller zero-init discipline. */
	if (ofq2_tristate_valid && !ofq2_tristate_dirty &&
		ofq2_tristate_cur.attr_q29 == st->attr_q29 &&
		ofq2_tristate_cur.fb_base == st->fb_base &&
		ofq2_tristate_cur.fb_major_step == st->fb_major_step &&
		ofq2_tristate_cur.fb_minor_step == st->fb_minor_step &&
		ofq2_tristate_cur.tex_addr == st->tex_addr &&
		ofq2_tristate_cur.tex_width == st->tex_width &&
		ofq2_tristate_cur.tex_w_mask == st->tex_w_mask &&
		ofq2_tristate_cur.tex_h_mask == st->tex_h_mask &&
		ofq2_tristate_cur.flags == st->flags &&
		ofq2_tristate_cur.colormap_id == st->colormap_id &&
		ofq2_tristate_cur.z_mode == st->z_mode &&
		ofq2_tristate_cur.clamp_min[0] == st->clamp_min[0] &&
		ofq2_tristate_cur.clamp_min[1] == st->clamp_min[1] &&
		ofq2_tristate_cur.clamp_max[0] == st->clamp_max[0] &&
		ofq2_tristate_cur.clamp_max[1] == st->clamp_max[1] &&
		ofq2_tristate_cur.z_base == st->z_base &&
		ofq2_tristate_cur.z_major_step == st->z_major_step &&
		ofq2_tristate_cur.z_minor_step == st->z_minor_step &&
		ofq2_tristate_cur.clip_x0 == st->clip_x0 &&
		ofq2_tristate_cur.clip_x1 == st->clip_x1 &&
		ofq2_tristate_cur.clip_y0 == st->clip_y0 &&
		ofq2_tristate_cur.clip_y1 == st->clip_y1 &&
		ofq2_tristate_cur.mirror_s == st->mirror_s &&
		ofq2_tristate_cur.mirror_t == st->mirror_t &&
		ofq2_tristate_cur.const_alpha == st->const_alpha &&
		ofq2_tristate_cur.cd_combine == st->cd_combine &&
		ofq2_tristate_cur.subpix_y == st->subpix_y)
	{
		return true;
	}

	/* Zero FIRST, then assign.  of_gpu_tri_state_t grows over time (the
	 * truecolor extras — mirror_s/_t, const_alpha, cd_combine, subpix_y —
	 * arrived after this mirror was written) and every one of its bytes is
	 * live: they become control-word bits 28..31 and payload word 16.  A
	 * field-by-field build on an uninitialised stack struct therefore ships
	 * whatever the stack held as a GPU mode.  memset keeps that class of bug
	 * from ever recurring when the SDK struct grows again. */
	memset(&hw, 0, sizeof(hw));
	hw.fb_base = st->fb_base;
	hw.fb_major_step = st->fb_major_step;
	hw.fb_minor_step = st->fb_minor_step;
	hw.tex_addr = st->tex_addr;
	hw.tex_width = st->tex_width;
	hw.tex_w_mask = st->tex_w_mask;
	hw.tex_h_mask = st->tex_h_mask;
	hw.flags = st->flags;
	hw.colormap_id = st->colormap_id;
	hw.z_mode = st->z_mode;
	hw.clamp_min[0] = st->clamp_min[0];
	hw.clamp_min[1] = st->clamp_min[1];
	hw.clamp_max[0] = st->clamp_max[0];
	hw.clamp_max[1] = st->clamp_max[1];
	hw.z_base = st->z_base;
	hw.z_major_step = st->z_major_step;
	hw.z_minor_step = st->z_minor_step;
	hw.clip_x0 = st->clip_x0;
	hw.clip_x1 = st->clip_x1;
	hw.clip_y0 = st->clip_y0;
	hw.clip_y1 = st->clip_y1;
	hw.attr_q29 = st->attr_q29;
	hw.mirror_s = st->mirror_s;
	hw.mirror_t = st->mirror_t;
	hw.const_alpha = st->const_alpha;
	hw.cd_combine = st->cd_combine;
	hw.subpix_y = st->subpix_y;

	of_gpu_set_tri_state(&hw);

	ofq2_stat_tristate++;
	ofq2_tristate_cur = *st;
	ofq2_tristate_valid = true;
	ofq2_tristate_dirty = false;
	ofq2_pending = true;
	return true;
}

int
OFQ2_DrawVertTri(const int16_t x[3], const int16_t y[3],
		const int32_t s[3], const int32_t t[3],
		const int32_t zi[3], const uint8_t light[3])
{
	ofq2_cmdlog_t *e;

	if (!OFQ2_Supports(OFQ2_CAP_VERT_TRI) || !ofq2_tristate_valid ||
		ofq2_tristate_dirty || ofq2_tristate_cur.attr_q29)
	{
		/* no current sticky state — caller must SetTriState first.
		 * A Q29-armed state (world 0x4D path) also refuses: the 0x4B
		 * derivation emits plain persp planes, which a Q29 sticky
		 * control would misscale. */
		return 0;
	}

	e = &ofq2_cmdlog[ofq2_cmdlog_next % OFQ2_CMDLOG_N];
	ofq2_cmdlog_next++;
	e->fb_base = ofq2_tristate_cur.fb_base;
	e->z_base = ofq2_tristate_cur.z_base;
	e->z_mode = ofq2_tristate_cur.z_mode;
	e->attr_mode = OFQ2_PARAM_ATTR_PERSP;
	e->flags = ofq2_tristate_cur.flags;
	e->maxv = 0;
	e->maxcount = 0;
	e->nrec = 0xFFFE;	/* marks a HW-derived vertex triangle */

	ofq2_stat_lists++;

	ofq2_stat_verttri++;
	of_gpu_draw_vert_tri(x, y, s, t, zi, light);
	ofq2_pending = true;
	return 1;
}

int
OFQ2_SetObjectState(const ofq2_object_state_t *st)
{
	of_gpu_object_state_t hw;
	int i, j;

	if ((!OFQ2_Supports(OFQ2_CAP_XFORM_TRI) &&
		 !OFQ2_Supports(OFQ2_CAP_CLIP_LOAD)) || !st)
		return 0;

	/* Shadow-skip: re-emit the 26-word 0x50 only when the object state
	 * actually changed (callers memset the struct, so the trailing pad byte
	 * compares clean).  Runs of same-distance surfaces share one shift =>
	 * one 0x50, fewer command-stream words. */
	if (ofq2_objstate_valid &&
		memcmp(&ofq2_objstate_cur, st, sizeof(*st)) == 0)
		return 1;
	ofq2_objstate_cur = *st;
	ofq2_objstate_valid = true;
	memset(&hw, 0, sizeof(hw));	/* future-proof: every SDK byte is live */

	for (i = 0; i < 5; i++)
		for (j = 0; j < 4; j++)
			hw.matrix[i][j] = st->matrix[i][j];
	hw.xcenter = st->xcenter;
	hw.ycenter = st->ycenter;
	hw.xscale = st->xscale;
	hw.yscale = st->yscale;
	hw.near_clip = st->near_clip;
	hw.rows = st->rows;
	hw.q29_en = st->q29_en;
	hw.q29_shift = st->q29_shift;

	/* 0x50 is sticky and does NOT clear the GPU tri_state_valid, so it must
	 * NOT dirty the 0x4A shadow — a following 0x51 still needs it armed. */
	of_gpu_set_object_state(&hw);
	ofq2_pending = true;
	return 1;
}

void
OFQ2_ObjectStateReset(void)
{
	/* Drop the 0x50 shadow so the next SetObjectState re-arms the HW.  Call
	 * once at the start of the world tri pass each frame. */
	ofq2_objstate_valid = false;
}

int
OFQ2_DrawGeometryFan(const uint32_t *vertices, unsigned count, unsigned cull)
{
	if (!OFQ2_Supports(OFQ2_CAP_GEOMETRY) || !ofq2_tristate_valid ||
		ofq2_tristate_dirty || !ofq2_objstate_valid || !vertices ||
		count < 3 || count > 32 || cull > 2)
		return 0;

	/* Publish a complete upload and its fan together. No per-vertex fence or
	 * per-triangle capacity check; the worker and rasterizer run in parallel. */
	/* Only the independent worker accepts all 32 vertices in one 0x59.
	 * Both implementations still require one index word per 0x5B packet. */
	int worker = OFQ2_Supports(OFQ2_CAP_GEOMETRY_WORKER);
	unsigned group_size = worker ? count : 3;
	unsigned groups = worker ? 1 : (count + 2) / 3;
	unsigned words = groups + 5 * count + 2 * (count - 2);
	_gpu_stream_reserve_words(words);
	uint32_t *w = _gpu_ring_claim();
	for (unsigned i = 0; i < count; i += group_size)
	{
		unsigned n = count - i;
		if (n > group_size) n = group_size;
		*w++ = 0x59000000u | (5 * n);
		memcpy(w, vertices + i * 5, n * 5 * sizeof(*vertices));
		w += n * 5;
	}
	for (unsigned i = 1; i + 1 < count; i++)
	{
		*w++ = 0x5b000001u;
		*w++ = (i << 5) | ((i + 1) << 10) | (cull << 15);
	}
	_gpu_ring_commit(words);
	ofq2_pending = true;
	return 1;
}

int
OFQ2_NativeReserve(uint32_t bytes, uint32_t *address)
{
#ifndef OF_PC
	if (!address || !bytes || bytes > 0x01000000u ||
		ofq2_native_end > 0x01000000u || !OFQ2_Supports(OFQ2_CAP_NATIVE_MESH)) return 0;
	uint32_t aligned = (bytes + 15u) & ~15u;
	if (aligned > of_texture_budget_free() ||
		aligned >= _of_tex_fast_size) return 0;
	/* The upload port is load-time/GPU-idle. Generation resets already wait
	 * for completion too, so old immutable asset copies cannot be overwritten
	 * while a queued native draw still reads them. This is never a draw fence. */
	OFQ2_Finish();
	_of_tex_fast_size -= aligned;
	*address = _of_tex_fast_size;
	return 1;
#else
	(void)bytes; (void)address;
	return 0;
#endif
}

void
OFQ2_NativeUpload(uint32_t address, const uint32_t *words, uint32_t count)
{
	if (!words || (address & 3u) || address < _of_tex_fast_size ||
		address > ofq2_native_end || count > (ofq2_native_end - address) / 4u) return;
	_of_gpu_fast_tex_upload(address, words, count);
}

uint32_t
OFQ2_NativeGeneration(void)
{
	return ofq2_native_generation;
}

void
OFQ2_NativeReset(void)
{
	if (!OFQ2_Supports(OFQ2_CAP_NATIVE_MESH)) return;
	OFQ2_Finish();
	_of_tex_fast_size = ofq2_native_end;
	ofq2_native_epoch();
}

int
OFQ2_DrawNativeMesh(const uint32_t words[OFQ2_NATIVE_WORDS])
{
	if (!words || !OFQ2_Supports(OFQ2_CAP_NATIVE_MESH) ||
		!ofq2_tristate_valid || ofq2_tristate_dirty) return 0;
	_gpu_stream_reserve_words(1u + OFQ2_NATIVE_WORDS);
	uint32_t *out = _gpu_ring_claim();
	*out++ = 0x60000000u | OFQ2_NATIVE_WORDS;
	memcpy(out, words, OFQ2_NATIVE_WORDS * sizeof(*words));
	_gpu_ring_commit(1u + OFQ2_NATIVE_WORDS);
	/* Native jobs own their matrix/viewport. The next legacy object packet
	 * must be emitted even if its CPU shadow equals the earlier world state. */
	ofq2_objstate_valid = false;
	ofq2_pending = true;
	return 1;
}

int
OFQ2_DrawGeometryPackets(const ofq2_geometry_packet_t *packets, unsigned count)
{
    if (!OFQ2_Supports(OFQ2_CAP_GEOMETRY | OFQ2_CAP_GEOMETRY_WORKER) ||
        !ofq2_tristate_valid || ofq2_tristate_dirty || !ofq2_objstate_valid ||
        !packets || !count || count > OFQ2_GEOMETRY_BATCH_PACKETS)
        return 0;
    unsigned words = 0;
    for (unsigned i = 0; i < count; i++)
    {
        unsigned n = packets[i].count;
        if (!packets[i].words || n < 3 || n > 32) return 0;
        words += 7u * n - 3u;
    }
    if (words > OFQ2_GEOMETRY_BATCH_WORDS) return 0;
    _gpu_stream_reserve_words(words);
    uint32_t *w = _gpu_ring_claim();
    for (unsigned i = 0; i < count; i++)
    {
        unsigned n = 7u * packets[i].count - 3u;
        memcpy(w, packets[i].words, n * sizeof(*w));
        w += n;
    }
    _gpu_ring_commit(words);
    ofq2_pending = true;
    return 1;
}

int
OFQ2_DrawClipIndexed(const uint32_t *vertices, unsigned count, uint32_t triangle)
{
	if (!OFQ2_Supports(OFQ2_CAP_GEOMETRY) || !ofq2_tristate_valid ||
		ofq2_tristate_dirty || !ofq2_objstate_valid || count > 3 ||
		(count && !vertices)) return 0;
	unsigned words = count * 9 + 2;
	_gpu_stream_reserve_words(words);
	uint32_t *w = _gpu_ring_claim();
	for (unsigned i = 0; i < count; i++)
	{
		*w++ = 0x56000008u;
		const uint32_t *v = vertices + i * 8;
		w[0] = v[0]; w[1] = v[1]; w[2] = v[2]; w[3] = v[3];
		w[4] = v[4]; w[5] = v[5]; w[6] = v[6]; w[7] = v[7];
		w += 8;
	}
	*w++ = 0x5b000001u;
	*w++ = triangle;
	_gpu_ring_commit(words);
	ofq2_pending = true;
	return 1;
}

int
OFQ2_DrawXformTri(const int32_t vx[3], const int32_t vy[3],
		const int32_t vz[3], const int32_t s[3], const int32_t t[3],
		const uint8_t light[3])
{
	/* needs the 0x4A surface state armed (same contract as 0x4B); the 0x50
	 * matrix is sticky, assumed set by the caller for this surface.  Unlike
	 * 0x4B this DOES support Q29 (carried in the 0x50 object state). */
	if (!OFQ2_Supports(OFQ2_CAP_XFORM_TRI) || !ofq2_tristate_valid ||
		ofq2_tristate_dirty)
		return 0;

	of_gpu_draw_xform_tri(vx, vy, vz, s, t, light);
	ofq2_pending = true;
	return 1;
}

int
OFQ2_DrawXformTriRGB(const int32_t vx[3], const int32_t vy[3],
		const int32_t vz[3], const int32_t s[3], const int32_t t[3],
		const uint16_t rgb[3])
{
	/* truecolor sibling of OFQ2_DrawXformTri: per-vertex RGB565 instead of an
	 * 8-bit light row.  Same armed-state contract (0x4A surface + sticky 0x50
	 * matrix). */
	if (!OFQ2_Supports(OFQ2_CAP_XFORM_RGB) || !ofq2_tristate_valid ||
		ofq2_tristate_dirty)
		return 0;

	of_gpu_draw_xform_tri_rgb(vx, vy, vz, s, t, rgb);
	ofq2_pending = true;
	return 1;
}

/* Packed 0x4E payload: three XY, S, T and ZI words, two RGB565 words,
 * and three independent depth words. Keep reservation and publication here
 * so no renderer translation unit acquires its own SDK ring state. */
int
OFQ2_DrawVertTriRGBPacked(const uint32_t words[17])
{
	if (!OFQ2_Supports(OFQ2_CAP_VERT_TRI_RGB) || !ofq2_tristate_valid ||
		ofq2_tristate_dirty)
		return 0;

#ifndef OF_PC
	if (of_has_feature(OF_HW_GPU_VERT_TRI) && of_has_feature(OF_HW_GPU_VCOLOR))
#endif
	{
		_gpu_stream_reserve_words(18u);
		uint32_t *w = _gpu_ring_claim();
		w[0] = ((uint32_t)GPU_CMD_DRAW_VERT_TRI_RGB << 24) | 17u;
		/* Keep this fixed payload inline: the target libc copy call costs
		 * more than its seventeen aligned word loads/stores. */
		w[1] = words[0]; w[2] = words[1]; w[3] = words[2]; w[4] = words[3];
		w[5] = words[4]; w[6] = words[5]; w[7] = words[6]; w[8] = words[7];
		w[9] = words[8]; w[10] = words[9]; w[11] = words[10]; w[12] = words[11];
		w[13] = words[12]; w[14] = words[13]; w[15] = words[14]; w[16] = words[15];
		w[17] = words[16];
		_gpu_ring_commit(18u);
	}
	ofq2_pending = true;
	return 1;
}

int
OFQ2_DrawVertTriRGB(const int16_t x[3], const int16_t y[3],
		const int32_t s[3], const int32_t t[3], const int32_t zi[3],
		const uint16_t rgb[3], const int32_t depth[3])
{
	/* CPU-projected screen-space truecolor triangle. Needs the 0x4A surface
	 * state armed. q29 override 0 (use the sticky tri-state attr mode). */
	if (!OFQ2_Supports(OFQ2_CAP_VERT_TRI_RGB) || !ofq2_tristate_valid ||
		ofq2_tristate_dirty)
		return 0;

	/* rgb_d=NULL keeps the 19-word legacy form (no decoupled RGB565 pair). */
	of_gpu_draw_vert_tri_rgb(x, y, s, t, zi, rgb, 0u, depth, NULL);
	ofq2_pending = true;
	return 1;
}

int
OFQ2_StretchBlit(uint8_t *dst, int dst_stride, int dst_w, int dst_h,
		const void *src, int src_pitch,
		int32_t s_origin, int32_t t_origin,
		int32_t sstep, int32_t tstep,
		int skip_transparent)
{
	static ofq2_param_span_record_t recs[OFQ2_PARAM_SPAN_MAX_RECORDS];
	ofq2_param_span_list_t params;
	int row = 0;
	uint32_t blit_t0 = of_time_us();

	if (!OFQ2_Supports(OFQ2_CAP_PARAM_SPAN_LIST) ||
		!dst || !src || dst_w <= 0 || dst_h <= 0 ||
		src_pitch <= 0 || src_pitch > 0xFFFF)
	{
		return 0;
	}

	/* No SET_FB rebind: span lists carry absolute fb addresses, so the
	 * bound-framebuffer state only matters for legacy scalar spans.
	 * Zeroed clamp_min/clamp_max disable attr clamping (matches the
	 * SDK's own legacy-span lowering). */
	memset(&params, 0, sizeof(params));
	params.fb_base = (uint32_t)(uintptr_t)dst;
	params.fb_major_step = dst_stride;
	params.fb_minor_step = 1;
	params.tex_addr = (uint32_t)(uintptr_t)src;
	params.tex_width = (uint16_t)src_pitch;
	params.flags = skip_transparent ? OFQ2_EMIT_SKIP_ZERO : 0;
	params.attr_mode = OFQ2_PARAM_ATTR_AFFINE;
	params.span_axis = OFQ2_PARAM_AXIS_X;
	params.attr_origin[0] = s_origin;
	params.attr_origin[1] = t_origin;
	params.attr_du[0] = sstep;
	params.attr_dv[1] = tstep;

	while (row < dst_h)
	{
		uint32_t n = 0;

		while (n < OFQ2_PARAM_SPAN_MAX_RECORDS && row + (int)n < dst_h)
		{
			recs[n].u = 0;
			recs[n].v = (uint16_t)(row + (int)n);
			recs[n].count = (uint16_t)dst_w;
			n++;
		}

		of_gpu_draw_param_span_list(
			(const of_gpu_param_span_list_t *)&params,
			(const of_gpu_param_span_record_t *)recs, n);
		row += (int)n;
	}

	ofq2_tristate_dirty = true;
	ofq2_pending = true;
	ofq2_stat_2d_us += of_time_us() - blit_t0;
	return dst_h;
}

int
OFQ2_Blit(uint8_t *dst, int dst_stride, int w, int h,
		const void *src, int src_pitch, int skip_transparent)
{
	return OFQ2_StretchBlit(dst, dst_stride, w, h, src, src_pitch,
		0, 0, 1 << 16, 1 << 16, skip_transparent);
}

void
OFQ2_ClearRect(uint8_t *dst, int stride, int w, int h, uint8_t color)
{
	if (!ofq2_ready || !dst || w <= 0 || h <= 0)
	{
		return;
	}

	/* clear_rect carries an absolute address — no SET_FB rebind. */
	of_gpu_clear_rect_strided((uint32_t)(uintptr_t)dst,
		(uint16_t)w, (uint16_t)h, (uint16_t)stride, color);
	ofq2_tristate_dirty = true;
	ofq2_pending = true;
}

int
OFQ2_HasPendingWork(void)
{
	return ofq2_pending;
}

#if OFQ2_GPU_PROFILE
/* Windows end after presentation. No additional GPU barrier is inserted.
 * The frozen overlay is drawn only after collection has stopped. */
void OFQ2_GPUProfileToggle(void)
{
    if (ofq2_profile_visible)
    {
        ofq2_profile_visible = 0;
        ofq2_profile_active = 0;
        ofq2_profile_warmup = 2;
    }
    else if (ofq2_profile_valid)
    {
        ofq2_profile_visible = 1;
        ofq2_profile_active = 0;
    }
}

const char *OFQ2_GPUProfileLine(unsigned line)
{
    return ofq2_profile_visible && line < 6 ? ofq2_profile_lines[line] : NULL;
}

static uint32_t ofq2_profile_ms10(uint32_t us)
{
    return (uint32_t)((uint64_t)us / ((uint64_t)ofq2_profile_frames * 100u));
}

void OFQ2_GPUProfileFrame(int gameplay)
{
    if (!gameplay)
    {
        ofq2_profile_reset();
        return;
    }
    if (ofq2_profile_visible) return;
    if (ofq2_profile_warmup) { ofq2_profile_warmup--; return; }
    uint32_t now = of_time_us();
    if (!ofq2_profile_active)
    {
        ofq2_profile_counters_clear();
        ofq2_profile_window = now;
        ofq2_profile_active = 1;
        return;
    }
    ofq2_profile_frames++;
    uint32_t elapsed = now - ofq2_profile_window;
    if (elapsed < 2000000u) return;
    if (elapsed <= 4000000u)
    {
        uint32_t fps10 = (uint32_t)((uint64_t)ofq2_profile_frames * 10000000u / elapsed);
        uint32_t frame = ofq2_profile_ms10(elapsed);
        uint32_t ring = ofq2_profile_ms10(ofq2_profile_wait[0]);
        uint32_t dma = ofq2_profile_ms10(ofq2_profile_wait[1]);
        uint32_t fence = ofq2_profile_ms10(ofq2_profile_wait[2]);
        uint32_t copy = ofq2_profile_ms10(ofq2_profile_copy_us);
        snprintf(ofq2_profile_lines[0], 40, "Q2 P1 GPU SNAPSHOT");
        snprintf(ofq2_profile_lines[1], 40, "FPS %u.%u FRAME %u.%u MS", fps10/10, fps10%10, frame/10, frame%10);
        snprintf(ofq2_profile_lines[2], 40, "RING %u.%u DMA %u.%u MS", ring/10, ring%10, dma/10, dma%10);
        snprintf(ofq2_profile_lines[3], 40, "FENC %u.%u COPY %u.%u MS", fence/10, fence%10, copy/10, copy%10);
        snprintf(ofq2_profile_lines[4], 40, "WORDS %u BATCH %u / FRAME", ofq2_profile_words/ofq2_profile_frames, ofq2_profile_batches/ofq2_profile_frames);
        snprintf(ofq2_profile_lines[5], 40, "MODE %s TEX FREE %uK", ofq2_profile_modes == 2 ? "CPU" : ofq2_profile_modes == 1 ? "DMA" : "MIX", of_texture_budget_free()/1024u);
        ofq2_profile_valid = 1;
    }
    /* Formatting is outside the next measurement window. */
    ofq2_profile_counters_clear();
    ofq2_profile_window = of_time_us();
}
#endif
