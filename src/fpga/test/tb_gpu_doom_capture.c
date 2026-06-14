/*
 * tb_gpu_doom_capture.c  (ADDITIVE, host-only, NOT built by default)
 *
 * Captures the REAL GPU command-wire records that Doom's param-wall path
 * emits for an angled textured wall, swept across view angles / distances.
 *
 * It uses the REAL code, not a reimplementation:
 *   - the real Doom finetangent / finesine / finecosine tables (tables.c)
 *   - the real Doom fixed-point (m_fixed.h FixedMul/FixedDiv)
 *   - the real R_InitTextureMapping / R_ScaleFromGlobalAngle projection
 *     math (copied verbatim from r_main.c)
 *   - the real wall plane fit + Q29 encode + per-column emit functions
 *     (R_GPU_WallSegBegin / R_GPU_WallTierBegin / R_GPU_WallTierColumn /
 *      gpu_param_encode_q29 / gpu_wall_texcol_at — copied verbatim from
 *      r_gpu.c, guarded by OF_DOOM_CAPTURE so the originals are untouched)
 *   - the real SDK of_gpu.h wire-packing (compiled in NON-OF_PC mode with a
 *     capture hook behind OF_GPU_CAPTURE that decodes every emitted record)
 *
 * Output: a text dump (stdout) + a binary record stream (capture.bin) the
 * Verilator replay harness consumes.  Build:
 *   cc -O2 -DOF_GPU_CAPTURE -DOF_DOOM_CAPTURE -I<doomsdk>/include \
 *      tb_gpu_doom_capture.c <doom>/tables.c -lm -o capture
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <limits.h>

/* ---- real Doom fixed-point + table contract ---- */
typedef int        fixed_t;
typedef uint32_t   angle_t;
typedef uint8_t    byte;
typedef int        boolean;
#define true 1
#define false 0
#define FRACBITS 16
#define FRACUNIT (1 << FRACBITS)
#define FINEANGLES      8192
#define FINEMASK        (FINEANGLES - 1)
#define ANGLETOFINESHIFT 19
#define ANG90  0x40000000u
#define ANG180 0x80000000u
#define FIELDOFVIEW 2048
#define SCREENWIDTH  320
#define SCREENHEIGHT 200

extern const fixed_t finesine[5 * FINEANGLES / 4];
extern const fixed_t finetangent[FINEANGLES / 2];
#define finecosine (&finesine[FINEANGLES/4])

static inline fixed_t FixedMul(fixed_t a, fixed_t b) {
    return (fixed_t)(((int64_t)a * (int64_t)b) >> FRACBITS);
}
static inline fixed_t FixedDiv(fixed_t a, fixed_t b) {
    if ((abs(a) >> 14) >= abs(b))
        return (a ^ b) < 0 ? INT_MIN : INT_MAX;
    return (fixed_t)(((int64_t)a << FRACBITS) / b);
}
static inline fixed_t R_PositiveScaleDiv(fixed_t num, int den) {
    uint32_t whole, rem, result, uden, bit;
    uden = (uint32_t)den;
    whole = (uint32_t)num / uden;
    if (whole >= 64u) return 64 * FRACUNIT;
    rem = (uint32_t)num - whole * uden;
    result = whole << FRACBITS;
    for (bit = 1u << (FRACBITS - 1); bit != 0; bit >>= 1) {
        rem <<= 1;
        if (rem >= uden) { result |= bit; rem -= uden; }
    }
    return (fixed_t)result;
}

/* ---- Doom renderer globals the emit functions read ---- */
static int viewwidth, viewheight, centerx, centery, detailshift;
static fixed_t centerxfrac, centeryfrac, projection;
static int viewwindowx, viewwindowy;
static angle_t xtoviewangle[SCREENWIDTH + 1];
static int     viewangletox[FINEANGLES / 2];
static angle_t viewangle, rw_normalangle, rw_distance;

/* light table row map (real Doom shape: row 0..NUMCOLORMAPS-1 quantised) */
#define LIGHTSCALESHIFT 12
#define MAXLIGHTSCALE   48
#define NUMCOLORMAPS    32
static byte scalelightrow_storage[MAXLIGHTSCALE];
static byte *walllightrows = scalelightrow_storage;

/* GPU side mirrors */
#define GPU_WALL_TIERS 2
#define GPU_WALL_BANDS 2
#define GPU_WALL_BAND_RECORDS 128
#define OF_FASTTEXT
#define OF_PALOOKUP_SLOTS 16

static uint32_t gpu_fb_row_addr[SCREENHEIGHT];
static int  gpu_use_wall_param = 1, gpu_present = 1, gpu_frame_active = 1;
static int  gpu_write_prepared = 1;
static void *I_VideoBuffer = (void *)1;   /* non-NULL */
static void gpu_prepare_for_gpu_write(void) { gpu_write_prepared = 1; }

/* perf hook used inside R_GPU_WallTierColumn */
static unsigned long perf_columns, perf_pixels;
#define R_Perf_CountGpuColumn(n) do { perf_columns++; perf_pixels += (n); } while (0)

/* ---- real SDK of_gpu.h (NON-PC) with a capture hook ---- */
/* of_caps shim so of_gpu_init() can resolve a base.  of_gpu.h includes
 * of_caps.h / of_cache.h under !OF_PC; we provide a tiny local stand-in so
 * the wire-packing compiles and runs on the host. */
struct of_capabilities {
    uint32_t gpu_base, sdram_base, sdram_size, sdram_uncached_base;
};
static uint8_t host_sdram[64u * 1024u * 1024u];
static struct of_capabilities host_caps;
static const struct of_capabilities *of_get_caps(void) { return &host_caps; }
static int of_has_feature(int b) { (void)b; return 1; }
#define OF_HW_GPU_SPAN_GROUP            1
#define OF_HW_GPU_COLUMN_LIST           2
#define OF_HW_GPU_PARAM_TRI            3
#define OF_HW_GPU_PARAM_TRI_RECS       4
#define OF_HW_GPU_VERT_TRI             5
#define OF_HW_GPU_PARAM_SPAN_Q29_SCALE 6
#define OF_CAPS_H
#define OF_CACHE_H
static inline void of_cache_flush_range(void *a, uint32_t n) { (void)a;(void)n; }

/* The capture hook: decode every emitted GPU_CMD_DRAW_PARAM_SPAN_LIST. */
typedef struct {
    uint8_t  cmd;
    uint32_t fb_base; int32_t fb_major_step, fb_minor_step;
    uint32_t tex_addr; uint16_t tex_width, tex_w_mask, tex_h_mask;
    uint8_t  flags, colormap_id, attr_mode, span_axis, z_mode, q29_attr_shift;
    int32_t  attr_origin[3], attr_du[3], attr_dv[3];
    int32_t  light_origin, light_du, light_dv;
    uint32_t record_count;
    uint16_t u[GPU_WALL_BAND_RECORDS], v[GPU_WALL_BAND_RECORDS], cnt[GPU_WALL_BAND_RECORDS];
} cap_cmd_t;

static cap_cmd_t cap_cmds[4096];
static int       cap_n;

/* Hook signature matched in of_gpu.h under OF_GPU_CAPTURE. */
struct of_gpu_param_span_list_t_fwd;
struct of_gpu_param_span_record_t_fwd;
static void of_gpu_capture_param_span(const void *p_v, const void *records_v,
                                      uint32_t record_count);

#define OF_GPU_CAPTURE_HOOK(p, records, n) of_gpu_capture_param_span((p),(records),(n))

#include "of_gpu.h"

static void of_gpu_capture_param_span(const void *p_v, const void *records_v,
                                      uint32_t record_count) {
    const of_gpu_param_span_list_t  *p = (const of_gpu_param_span_list_t *)p_v;
    const of_gpu_param_span_record_t *r = (const of_gpu_param_span_record_t *)records_v;
    cap_cmd_t *c;
    uint32_t i;
    if (cap_n >= (int)(sizeof cap_cmds / sizeof cap_cmds[0])) return;
    c = &cap_cmds[cap_n++];
    c->cmd = GPU_CMD_DRAW_PARAM_SPAN_LIST;
    c->fb_base = p->fb_base;
    c->fb_major_step = p->fb_major_step;
    c->fb_minor_step = p->fb_minor_step;
    c->tex_addr = p->tex_addr;
    c->tex_width = p->tex_width;
    c->tex_w_mask = p->tex_w_mask;
    c->tex_h_mask = p->tex_h_mask;
    c->flags = p->flags;
    c->colormap_id = p->colormap_id;
    c->attr_mode = p->attr_mode;
    c->span_axis = p->span_axis;
    c->z_mode = p->z_mode;
    c->q29_attr_shift = p->q29_attr_shift;
    for (i = 0; i < 3; i++) {
        c->attr_origin[i] = p->attr_origin[i];
        c->attr_du[i] = p->attr_du[i];
        c->attr_dv[i] = p->attr_dv[i];
    }
    c->light_origin = p->light_origin;
    c->light_du = p->light_du;
    c->light_dv = p->light_dv;
    if (record_count > GPU_WALL_BAND_RECORDS) record_count = GPU_WALL_BAND_RECORDS;
    c->record_count = record_count;
    for (i = 0; i < record_count; i++) {
        c->u[i] = r[i].u; c->v[i] = r[i].v; c->cnt[i] = r[i].count;
    }
}

/* ---- real wall plane fit + Q29 encode + emit (verbatim from r_gpu.c) ---- */
/* The originals are file-static in r_gpu.c; we re-host the identical code so
 * the capture exercises the exact float math and the exact record packing. */

static float gpu_wall_zi_org, gpu_wall_zi_du;
static float gpu_wall_szi_org, gpu_wall_szi_du;
static float gpu_wall_texcol1;
static int   gpu_wall_seg_valid;
static int   gpu_wall_record_count;

typedef struct {
    of_gpu_param_span_list_t params;
    struct { byte light; } bands_meta[GPU_WALL_BANDS];
    struct { of_gpu_param_span_record_t records[GPU_WALL_BAND_RECORDS]; int count; int light; } bands[GPU_WALL_BANDS];
    int rr, active;
} gpu_wall_tier_t;
static gpu_wall_tier_t gpu_wall_tiers[GPU_WALL_TIERS];

static void gpu_param_encode_q29(of_gpu_param_span_list_t *p,
                                 const float f_org[3], const float f_du[3],
                                 const float f_dv[3]) {
    const float fmu = (float)viewwidth;
    const float fmv = (float)viewheight;
    float fmax = 0.0f;
    float scale;
    int sh;
    for (int i = 0; i < 3; i++) {
        float o = f_org[i];
        float du_span = f_du[i] * fmu;
        float dv_span = f_dv[i] * fmv;
        float c;
        c = fabsf(o);                     if (c > fmax) fmax = c;
        c = fabsf(f_du[i]);               if (c > fmax) fmax = c;
        c = fabsf(f_dv[i]);               if (c > fmax) fmax = c;
        c = fabsf(o + du_span);           if (c > fmax) fmax = c;
        c = fabsf(o + dv_span);           if (c > fmax) fmax = c;
        c = fabsf(o + du_span + dv_span); if (c > fmax) fmax = c;
    }
    {
        union { float f; uint32_t u; } mb, sb;
        mb.f = fmax;
        sh = (int)((mb.u >> 23) & 0xFFu) - 126 - 1;
        if (sh < 0) sh = 0; else if (sh > 31) sh = 31;
        sb.u = (uint32_t)(127 + 29 - sh) << 23;
        scale = sb.f;
    }
    p->q29_attr_shift = (uint8_t)sh;
    for (int i = 0; i < 3; i++) {
        p->attr_origin[i] = (int32_t)(f_org[i] * scale);
        p->attr_du[i] = (int32_t)(f_du[i] * scale);
        p->attr_dv[i] = (int32_t)(f_dv[i] * scale);
    }
}

static float gpu_wall_texcol_at(int x, fixed_t offset, fixed_t distance,
                                angle_t centerangle) {
    const float inv16 = 1.0f / 65536.0f;
    unsigned int a = (unsigned int)((centerangle + xtoviewangle[x])
                                    >> ANGLETOFINESHIFT);
    return (float)offset * inv16
         - ((float)finetangent[a] * inv16) * ((float)distance * inv16);
}

static boolean R_GPU_WallSegBegin(int x1, int x2, fixed_t scale1,
                                  fixed_t scalestep, fixed_t distance,
                                  fixed_t offset, angle_t centerangle) {
    const float inv16 = 1.0f / 65536.0f;
    float proj, zi1, texcol2;
    gpu_wall_seg_valid = 0;
    gpu_wall_tiers[0].active = 0;
    gpu_wall_tiers[1].active = 0;
    if (!gpu_use_wall_param || !gpu_present || !gpu_frame_active ||
        I_VideoBuffer == NULL) return false;
    if (x2 < x1 || scale1 <= 0) return false;
    if (!gpu_write_prepared) gpu_prepare_for_gpu_write();
    proj = (float)centerxfrac * inv16;
    gpu_wall_zi_du = ((float)scalestep * inv16) / proj;
    zi1 = ((float)scale1 * inv16) / proj;
    gpu_wall_zi_org = zi1 - (float)x1 * gpu_wall_zi_du;
    gpu_wall_texcol1 = gpu_wall_texcol_at(x1, offset, distance, centerangle);
    if (x2 > x1) {
        float zi2 = zi1 + (float)(x2 - x1) * gpu_wall_zi_du;
        texcol2 = gpu_wall_texcol_at(x2, offset, distance, centerangle);
        gpu_wall_szi_du = (texcol2 * zi2 - gpu_wall_texcol1 * zi1)
                        / (float)(x2 - x1);
    } else {
        gpu_wall_szi_du = 0.0f;
    }
    gpu_wall_szi_org = gpu_wall_texcol1 * zi1 - (float)x1 * gpu_wall_szi_du;
    gpu_wall_seg_valid = 1;
    return true;
}

static boolean R_GPU_WallTierBegin(int tier, const byte *tex2d, int tex_height,
                                   int widthmask, fixed_t texturemid) {
    const float inv16 = 1.0f / 65536.0f;
    float f_org[3], f_du[3], f_dv[3];
    float proj, tm_f, rebase;
    gpu_wall_tier_t *t;
    fixed_t tmr;
    int k;
    if (!gpu_wall_seg_valid || tex2d == NULL) return false;
    if ((unsigned int)tier >= GPU_WALL_TIERS) return false;
    if (tex_height <= 0 || tex_height > 0xFFFF ||
        widthmask < 0 || widthmask > 0xFFFF) return false;
    t = &gpu_wall_tiers[tier];
    proj = (float)centerxfrac * inv16;
    tmr = texturemid & ((128 << FRACBITS) - 1);
    tm_f = (float)tmr * inv16;
    k = (int)(gpu_wall_texcol1 / (float)(widthmask + 1));
    if (gpu_wall_texcol1 < (float)k * (float)(widthmask + 1)) k--;
    rebase = (float)k * (float)(widthmask + 1);
    f_du[0] = tm_f * gpu_wall_zi_du;
    f_dv[0] = 1.0f / proj;
    f_org[0] = tm_f * gpu_wall_zi_org - (float)centery * f_dv[0];
    f_du[1] = gpu_wall_szi_du - rebase * gpu_wall_zi_du;
    f_dv[1] = 0.0f;
    f_org[1] = gpu_wall_szi_org - rebase * gpu_wall_zi_org;
    f_du[2] = gpu_wall_zi_du;
    f_dv[2] = 0.0f;
    f_org[2] = gpu_wall_zi_org;
    memset(&t->params, 0, sizeof(t->params));
    t->params.fb_base = gpu_fb_row_addr[viewwindowy] + (uint32_t)viewwindowx;
    t->params.fb_major_step = 1;
    t->params.fb_minor_step = SCREENWIDTH;
    t->params.tex_addr = (uint32_t)(uintptr_t)tex2d;
    t->params.tex_width = (uint16_t)tex_height;
    t->params.tex_w_mask = 127;
    t->params.tex_h_mask = (uint16_t)widthmask;
    t->params.flags = OF_GPU_SPAN_COLORMAP | OF_GPU_SPAN_PERSP;
    t->params.colormap_id = 0;
    t->params.attr_mode = OF_GPU_PARAM_ATTR_PERSP_Q29;
    t->params.span_axis = OF_GPU_PARAM_AXIS_Y;
    t->params.z_mode = OF_GPU_PARAM_Z_NONE;
    gpu_param_encode_q29(&t->params, f_org, f_du, f_dv);
    for (int b = 0; b < GPU_WALL_BANDS; b++) t->bands[b].light = -1;
    t->rr = 0;
    t->active = 1;
    return true;
}

static void gpu_flush_wall_band(gpu_wall_tier_t *tier, int b) {
    int n = tier->bands[b].count;
    if (n <= 0) return;
    tier->params.light_origin = (int32_t)tier->bands[b].light << 16;
    of_gpu_draw_param_span_list(&tier->params, tier->bands[b].records,
                                (uint32_t)n);
    /* Capture-only: recycle the staging cursor so we never trip the SDK's
     * lazy flush (which would drive absent GPU MMIO).  The wire bytes were
     * already snapshotted by the capture hook. */
    _gpu_cmd_words = 0;
    _gpu_wrptr     = 0;
    gpu_wall_record_count -= n;
    tier->bands[b].count = 0;
}

static boolean R_GPU_WallTierColumn(int tier, int x, int yl, int yh,
                                    fixed_t scale) {
    of_gpu_param_span_record_t *r;
    gpu_wall_tier_t *t = &gpu_wall_tiers[tier];
    int bandidx;
    unsigned int lindex;
    int light;
    int count;
    if (!t->active) return false;
    if ((unsigned int)x >= (unsigned int)viewwidth || yl < 0 || yh >= viewheight)
        return false;
    lindex = (unsigned int)scale >> LIGHTSCALESHIFT;
    if (lindex >= MAXLIGHTSCALE) lindex = MAXLIGHTSCALE - 1;
    light = walllightrows[lindex];
    if (light < 0 || light > 63) return false;
    count = yh - yl + 1;
    if (count <= 0) return true;
    bandidx = 0;
    if (t->bands[0].light != light) {
        if (t->bands[1].light == light) bandidx = 1;
        else {
            bandidx = t->rr;
            t->rr ^= 1;
            gpu_flush_wall_band(t, bandidx);
            t->bands[bandidx].light = light;
        }
    }
    if (t->bands[bandidx].count >= GPU_WALL_BAND_RECORDS)
        gpu_flush_wall_band(t, bandidx);
    r = &t->bands[bandidx].records[t->bands[bandidx].count++];
    gpu_wall_record_count++;
    r->u = (uint16_t)x;
    r->v = (uint16_t)yl;
    r->count = (uint16_t)count;
    R_Perf_CountGpuColumn((unsigned int)count);
    return true;
}

static void R_GPU_WallTiersEnd(void) {
    for (int i = 0; i < GPU_WALL_TIERS; i++) {
        gpu_wall_tier_t *t = &gpu_wall_tiers[i];
        if (!t->active) continue;
        for (int b = 0; b < GPU_WALL_BANDS; b++) gpu_flush_wall_band(t, b);
        t->active = 0;
    }
    gpu_wall_seg_valid = 0;
}

/* ---- real projection setup (verbatim from r_main.c) ---- */
static void R_InitTextureMapping(void) {
    int i, x, t;
    fixed_t focallength;
    focallength = FixedDiv(centerxfrac,
                           finetangent[FINEANGLES / 4 + FIELDOFVIEW / 2]);
    for (i = 0; i < FINEANGLES / 2; i++) {
        if (finetangent[i] > FRACUNIT * 2) t = -1;
        else if (finetangent[i] < -FRACUNIT * 2) t = viewwidth + 1;
        else {
            t = FixedMul(finetangent[i], focallength);
            t = (centerxfrac - t + FRACUNIT - 1) >> FRACBITS;
            if (t < -1) t = -1;
            else if (t > viewwidth + 1) t = viewwidth + 1;
        }
        viewangletox[i] = t;
    }
    for (x = 0; x <= viewwidth; x++) {
        i = 0;
        while (viewangletox[i] > x) i++;
        xtoviewangle[x] = (i << ANGLETOFINESHIFT) - ANG90;
    }
    for (i = 0; i < FINEANGLES / 2; i++) {
        if (viewangletox[i] == -1) viewangletox[i] = 0;
        else if (viewangletox[i] == viewwidth + 1) viewangletox[i] = viewwidth;
    }
}

static fixed_t R_ScaleFromGlobalAngle(angle_t visangle) {
    fixed_t scale, num;
    angle_t anglea, angleb;
    int sinea, sineb, den;
    anglea = ANG90 + (visangle - viewangle);
    angleb = ANG90 + (visangle - rw_normalangle);
    sinea = finesine[anglea >> ANGLETOFINESHIFT];
    sineb = finesine[angleb >> ANGLETOFINESHIFT];
    num = FixedMul(projection, sineb) << detailshift;
    den = FixedMul(rw_distance, sinea);
    if (den > num >> FRACBITS) {
        scale = R_PositiveScaleDiv(num, den);
        if (scale > 64 * FRACUNIT) scale = 64 * FRACUNIT;
        else if (scale < 256) scale = 256;
    } else {
        scale = 64 * FRACUNIT;
    }
    return scale;
}

/* ---- view-size init matching R_ExecuteSetViewSize ---- */
static void setup_view(int setblocks) {
    int scaledviewwidth;
    if (setblocks == 11) { scaledviewwidth = SCREENWIDTH; viewheight = SCREENHEIGHT; }
    else { scaledviewwidth = setblocks * 32; viewheight = (setblocks * 168 / 10) & ~7; }
    detailshift = 0;
    viewwidth = scaledviewwidth >> detailshift;
    centery = viewheight / 2;
    centerx = viewwidth / 2;
    centerxfrac = centerx << FRACBITS;
    centeryfrac = centery << FRACBITS;
    projection = centerxfrac;
    viewwindowx = (SCREENWIDTH - scaledviewwidth) / 2;
    viewwindowy = (scaledviewwidth == SCREENWIDTH) ? 0 : (SCREENHEIGHT - 32 - viewheight) / 2;
    for (int y = 0; y < SCREENHEIGHT; y++)
        gpu_fb_row_addr[y] = (uint32_t)(y * SCREENWIDTH);
    /* real-shape scalelightrow: brighter (lower row) as scale grows. */
    for (int j = 0; j < MAXLIGHTSCALE; j++) {
        int lvl = j * 16 / MAXLIGHTSCALE;     /* 0..15 spread */
        if (lvl < 0) lvl = 0; if (lvl > NUMCOLORMAPS - 1) lvl = NUMCOLORMAPS - 1;
        scalelightrow_storage[j] = (byte)lvl;
    }
    R_InitTextureMapping();
}

/* Build one angled textured wall covering [x1,x2] and emit it through the
 * REAL wall path.  Geometry chosen so the wall is oblique to the view. */
static byte dummy_tex[64 * 64];

static long total_gaps, total_dups, total_accepted, total_emitted;
/* cumulative param-range stats across ALL walls (capture buffer is recycled
 * per wall so it never overflows; stats are accumulated here). */
static int  g_sh_min = 99, g_sh_max = -1, g_cid_min = 99, g_cid_max = -1;
static int  g_z_nonzero;             /* any q29 zinv (attr2 origin/du/dv)==0? */
static long g_cnt_max, g_rec_max, g_total_records;
static int  g_count_pow2_leak;       /* colormap nibble changed across 2^N count */

/* accumulate range stats from one captured command */
static void accumulate_cmd_stats(const cap_cmd_t *cc) {
    if (cc->q29_attr_shift < g_sh_min) g_sh_min = cc->q29_attr_shift;
    if (cc->q29_attr_shift > g_sh_max) g_sh_max = cc->q29_attr_shift;
    if (cc->colormap_id < g_cid_min) g_cid_min = cc->colormap_id;
    if (cc->colormap_id > g_cid_max) g_cid_max = cc->colormap_id;
    if ((long)cc->record_count > g_rec_max) g_rec_max = cc->record_count;
    g_total_records += cc->record_count;
    /* attr2 (zi) plane all-zero would flatten perspective -> degenerate */
    if (cc->attr_origin[2] == 0 && cc->attr_du[2] == 0 && cc->attr_dv[2] == 0)
        g_z_nonzero++;
    for (uint32_t i = 0; i < cc->record_count; i++)
        if (cc->cnt[i] > g_cnt_max) g_cnt_max = cc->cnt[i];
}

static void emit_one_wall(angle_t va, angle_t normalangle, fixed_t dist,
                          fixed_t offset, int x1, int x2,
                          int yl_base, int yh_base, int yslope) {
    viewangle = va;
    rw_normalangle = normalangle;
    rw_distance = dist;

    fixed_t scale1 = R_ScaleFromGlobalAngle(viewangle + xtoviewangle[x1]);
    fixed_t scale2 = R_ScaleFromGlobalAngle(viewangle + xtoviewangle[x2]);
    fixed_t scalestep = (x2 > x1) ? (scale2 - scale1) / (x2 - x1) : 0;
    angle_t centerangle = ANG90 + viewangle - rw_normalangle;

    cap_n = 0;                 /* recycle capture buffer per wall */
    gpu_wall_record_count = 0;

    if (!R_GPU_WallSegBegin(x1, x2, scale1, scalestep, dist, offset, centerangle))
        return;
    if (!R_GPU_WallTierBegin(0, dummy_tex, 64, 63, 40 << FRACBITS))
        return;

    /* Per-column coverage tracking: 1 = accepted (GPU must emit a record). */
    static uint8_t col_accepted[SCREENWIDTH];
    memset(col_accepted, 0, sizeof col_accepted);

    fixed_t scale = scale1;
    for (int x = x1; x <= x2; x++) {
        /* Realistic silhouette: column half-height ~ perspective scale, so
         * near columns are tall and far columns shrink to a few pixels.
         * This is the shape R_RenderSegLoop produces from topfrac/bottomfrac. */
        int half = (int)(((int64_t)scale * (viewheight / 2)) / (24 << FRACBITS));
        if (half < 1) half = 1;
        if (half > viewheight / 2) half = viewheight / 2;
        int yl = centery - half;
        int yh = centery + half - 1;
        if (yl < 0) yl = 0;
        if (yh >= viewheight) yh = viewheight - 1;
        if (yl <= yh) {
            /* R_GPU_WallTierColumn returns true => GPU is responsible for the
             * column (no CPU fallback).  Any true column missing from the
             * emitted records IS a black bar on real hardware. */
            if (R_GPU_WallTierColumn(0, x, yl, yh, scale)) {
                col_accepted[x] = 1;
                total_accepted++;
            }
        }
        scale += scalestep;
    }
    R_GPU_WallTiersEnd();

    /* Coverage + dup check against the emitted record stream. */
    static uint8_t col_emitted[SCREENWIDTH];
    memset(col_emitted, 0, sizeof col_emitted);
    for (int c = 0; c < cap_n; c++) {
        cap_cmd_t *cc = &cap_cmds[c];
        accumulate_cmd_stats(cc);
        if (cc->span_axis != OF_GPU_PARAM_AXIS_Y) continue;
        for (uint32_t i = 0; i < cc->record_count; i++) {
            uint16_t u = cc->u[i];
            if (u < SCREENWIDTH) {
                if (col_emitted[u]) total_dups++;
                col_emitted[u]++;
                total_emitted++;
            }
        }
    }
    for (int x = x1; x <= x2; x++) {
        if (col_accepted[x] && !col_emitted[x]) {
            if (total_gaps < 12)
                printf("  GAP: col x=%d accepted by WallTierColumn but NOT "
                       "emitted (va=0x%08x normal=0x%08x dist=%d off=%d "
                       "scale1=%d)\n", x, va, normalangle, dist, offset, scale1);
            total_gaps++;
        }
    }
}

int main(void) {
    memset(host_sdram, 0xAA, sizeof host_sdram);
    /* Capture mode bypasses of_gpu_init() (which drives real GPU MMIO / DMA).
     * We only need the SDK wire-packing path to run far enough to fire the
     * capture hook: a non-NULL staging buffer and a huge apparent ring free.
     * The packed words land in host_sdram and are never DMA'd. */
    _gpu_batch_buf_base = (uint32_t *)host_sdram;
    _gpu_batch_buf      = (uint32_t *)host_sdram;
    _gpu_batch_dma_base = 0;
    _gpu_batch_dma_addr = 0;
    _gpu_batch_index    = 0;
    _gpu_cmd_words      = 0;
    _gpu_wrptr          = 0;
    _gpu_known_rdptr    = OF_GPU_RING_SIZE - 4; /* always plenty of ring free */
    _gpu_fence_next     = 1;
    memset(dummy_tex, 0x40, sizeof dummy_tex);

    setup_view(11); /* full-screen 3D view */

    /* Sweep view angles (turning) and distances (walking) for an oblique
     * wall.  normalangle offset from view => oblique textured wall.  We retain
     * the single wall that produced the most records (the most stressful
     * replay) for the Verilator dump. */
    static cap_cmd_t replay_cmds[256];
    int replay_n = 0; long replay_records = 0;

    int sweeps = 0;
    for (uint32_t aoff = 0; aoff < FINEANGLES; aoff += 16) {
        angle_t va = (uint32_t)aoff << ANGLETOFINESHIFT;
        for (uint32_t noff = 64; noff <= 2000; noff += 233) {
            angle_t normalangle = va + (uint32_t)(noff << ANGLETOFINESHIFT);
            for (fixed_t dist = (8 << FRACBITS); dist <= (4096 << FRACBITS); dist <<= 1) {
                for (fixed_t off = 0; off <= (256 << FRACBITS); off += (37 << FRACBITS)) {
                    int x1 = 0, x2 = viewwidth - 1;
                    emit_one_wall(va, normalangle, dist, off, x1, x2,
                                  0, viewheight - 1, 0);
                    sweeps++;
                    /* Prefer the wall with the highest q29 shift (most extreme
                     * perspective compression) AND full coverage, since that
                     * is the param regime least covered by the existing tests
                     * and the regime "bars move with view" points at. */
                    int sh = 0; long recs = 0;
                    for (int c = 0; c < cap_n; c++) {
                        recs += cap_cmds[c].record_count;
                        if (cap_cmds[c].q29_attr_shift > sh)
                            sh = cap_cmds[c].q29_attr_shift;
                    }
                    static int best_sh = -1;
                    long score = (long)sh * 100000 + recs;
                    long best_score = (long)best_sh * 100000 + replay_records;
                    if (score > best_score && cap_n > 0 && cap_n <= 256) {
                        best_sh = sh; replay_records = recs; replay_n = cap_n;
                        memcpy(replay_cmds, cap_cmds, cap_n * sizeof(cap_cmd_t));
                    }
                }
            }
        }
    }

    /* MULTI-WALL FRAME: emit several real walls back-to-back into ONE captured
     * command stream (NO cap_n reset between them) to expose any inter-command
     * / band-eviction / state-carryover column drop a single wall can't show.
     * Each wall covers a horizontal slice [x1,x2] of the view, like adjacent
     * segs in a real frame; coverage is checked over the union. */
    static cap_cmd_t frame_cmds[1024];
    int frame_n = 0;
    {
        cap_n = 0;
        struct { angle_t va, na; fixed_t dist, off; int x1, x2; } walls[] = {
            { (uint32_t)512u << ANGLETOFINESHIFT, (uint32_t)(512u+900u) << ANGLETOFINESHIFT,  64 << FRACBITS,  0,                0,  79 },
            { (uint32_t)512u << ANGLETOFINESHIFT, (uint32_t)(512u+700u) << ANGLETOFINESHIFT, 256 << FRACBITS, 37 << FRACBITS,   80, 159 },
            { (uint32_t)512u << ANGLETOFINESHIFT, (uint32_t)(512u+1100u)<< ANGLETOFINESHIFT,1024 << FRACBITS, 74 << FRACBITS,  160, 239 },
            { (uint32_t)512u << ANGLETOFINESHIFT, (uint32_t)(512u+1500u)<< ANGLETOFINESHIFT,  16 << FRACBITS, 11 << FRACBITS,  240, 303 },
        };
        static uint8_t frame_accepted[SCREENWIDTH];
        memset(frame_accepted, 0, sizeof frame_accepted);
        for (unsigned wi = 0; wi < sizeof walls / sizeof walls[0]; wi++) {
            viewangle = walls[wi].va; rw_normalangle = walls[wi].na;
            rw_distance = walls[wi].dist;
            int x1 = walls[wi].x1, x2 = walls[wi].x2;
            fixed_t s1 = R_ScaleFromGlobalAngle(viewangle + xtoviewangle[x1]);
            fixed_t s2 = R_ScaleFromGlobalAngle(viewangle + xtoviewangle[x2]);
            fixed_t sstep = (x2 > x1) ? (s2 - s1) / (x2 - x1) : 0;
            angle_t ca = ANG90 + viewangle - rw_normalangle;
            gpu_wall_record_count = 0;
            if (!R_GPU_WallSegBegin(x1, x2, s1, sstep, walls[wi].dist, walls[wi].off, ca)) continue;
            if (!R_GPU_WallTierBegin(0, dummy_tex, 64, 63, 40 << FRACBITS)) continue;
            fixed_t scale = s1;
            for (int x = x1; x <= x2; x++) {
                int half = (int)(((int64_t)scale * (viewheight/2)) / (24 << FRACBITS));
                if (half < 1) half = 1; if (half > viewheight/2) half = viewheight/2;
                int yl = centery - half, yh = centery + half - 1;
                if (yl < 0) yl = 0; if (yh >= viewheight) yh = viewheight - 1;
                if (yl <= yh && R_GPU_WallTierColumn(0, x, yl, yh, scale))
                    frame_accepted[x] = 1;
                scale += sstep;
            }
            R_GPU_WallTiersEnd();
        }
        frame_n = cap_n;
        if (frame_n > 1024) frame_n = 1024;
        memcpy(frame_cmds, cap_cmds, frame_n * sizeof(cap_cmd_t));
        /* coverage over the frame */
        static uint8_t frame_emitted[SCREENWIDTH];
        memset(frame_emitted, 0, sizeof frame_emitted);
        for (int c = 0; c < frame_n; c++) {
            cap_cmd_t *cc = &frame_cmds[c];
            if (cc->span_axis != OF_GPU_PARAM_AXIS_Y) continue;
            for (uint32_t i = 0; i < cc->record_count; i++)
                if (cc->u[i] < SCREENWIDTH) frame_emitted[cc->u[i]]++;
        }
        int frame_gaps = 0;
        for (int x = 0; x < SCREENWIDTH; x++)
            if (frame_accepted[x] && !frame_emitted[x]) frame_gaps++;
        printf("MULTI-WALL FRAME: %d commands, frame coverage gaps=%d\n",
               frame_n, frame_gaps);
    }

    /* Dump the most-stressful wall to binary for the Verilator replay. */
    FILE *bin = fopen("doom_capture.bin", "wb");
    uint32_t magic = 0x44434150; /* 'DCAP' */
    fwrite(&magic, 4, 1, bin);
    fwrite(&replay_n, 4, 1, bin);
    for (int c = 0; c < replay_n; c++)
        fwrite(&replay_cmds[c], sizeof(cap_cmd_t), 1, bin);
    fclose(bin);

    /* Emit a C header the Verilator acceptance replay test includes. */
    FILE *h = fopen("doom_capture_replay.h", "w");
    fprintf(h, "/* AUTO-GENERATED by tb_gpu_doom_capture.c — real Doom wall\n"
               " * GPU command records for the most-stressful swept view.\n"
               " * fb_base/tex_addr are placeholders; the replay test remaps\n"
               " * them to its own SDRAM map and keeps every persp/q29/u/v/count\n"
               " * value exactly as Doom emitted. */\n");
    fprintf(h, "#ifndef DOOM_CAPTURE_REPLAY_H\n#define DOOM_CAPTURE_REPLAY_H\n");
    fprintf(h, "struct DoomCapCmd { int span_axis, attr_mode, q29_attr_shift, colormap_id;\n"
               "  int fb_major_step, fb_minor_step; int tex_width, tex_w_mask, tex_h_mask;\n"
               "  int flags, z_mode; int attr_origin[3], attr_du[3], attr_dv[3];\n"
               "  int light_origin, light_du, light_dv; int record_count;\n"
               "  unsigned short u[128], v[128], cnt[128]; };\n");
    fprintf(h, "static const int doom_cap_cmd_count = %d;\n", replay_n);
    fprintf(h, "static const struct DoomCapCmd doom_cap_cmds[] = {\n");
    for (int c = 0; c < replay_n; c++) {
        cap_cmd_t *cc = &replay_cmds[c];
        fprintf(h, " { %d,%d,%d,%d, %d,%d, %d,%d,%d, %d,%d,\n",
                cc->span_axis, cc->attr_mode, cc->q29_attr_shift, cc->colormap_id,
                cc->fb_major_step, cc->fb_minor_step,
                cc->tex_width, cc->tex_w_mask, cc->tex_h_mask, cc->flags, cc->z_mode);
        fprintf(h, "   {%d,%d,%d},{%d,%d,%d},{%d,%d,%d}, %d,%d,%d, %u,\n",
                cc->attr_origin[0], cc->attr_origin[1], cc->attr_origin[2],
                cc->attr_du[0], cc->attr_du[1], cc->attr_du[2],
                cc->attr_dv[0], cc->attr_dv[1], cc->attr_dv[2],
                cc->light_origin, cc->light_du, cc->light_dv, cc->record_count);
        fprintf(h, "   {");
        for (uint32_t i = 0; i < cc->record_count; i++) fprintf(h, "%u,", cc->u[i]);
        fprintf(h, "},\n   {");
        for (uint32_t i = 0; i < cc->record_count; i++) fprintf(h, "%u,", cc->v[i]);
        fprintf(h, "},\n   {");
        for (uint32_t i = 0; i < cc->record_count; i++) fprintf(h, "%u,", cc->cnt[i]);
        fprintf(h, "} },\n");
    }
    fprintf(h, "};\n");
    /* Multi-wall frame (back-to-back walls in one stream). */
    fprintf(h, "static const int doom_frame_cmd_count = %d;\n", frame_n);
    fprintf(h, "static const struct DoomCapCmd doom_frame_cmds[] = {\n");
    for (int c = 0; c < frame_n; c++) {
        cap_cmd_t *cc = &frame_cmds[c];
        fprintf(h, " { %d,%d,%d,%d, %d,%d, %d,%d,%d, %d,%d,\n",
                cc->span_axis, cc->attr_mode, cc->q29_attr_shift, cc->colormap_id,
                cc->fb_major_step, cc->fb_minor_step,
                cc->tex_width, cc->tex_w_mask, cc->tex_h_mask, cc->flags, cc->z_mode);
        fprintf(h, "   {%d,%d,%d},{%d,%d,%d},{%d,%d,%d}, %d,%d,%d, %u,\n",
                cc->attr_origin[0], cc->attr_origin[1], cc->attr_origin[2],
                cc->attr_du[0], cc->attr_du[1], cc->attr_du[2],
                cc->attr_dv[0], cc->attr_dv[1], cc->attr_dv[2],
                cc->light_origin, cc->light_du, cc->light_dv, cc->record_count);
        fprintf(h, "   {");
        for (uint32_t i = 0; i < cc->record_count; i++) fprintf(h, "%u,", cc->u[i]);
        fprintf(h, "},\n   {");
        for (uint32_t i = 0; i < cc->record_count; i++) fprintf(h, "%u,", cc->v[i]);
        fprintf(h, "},\n   {");
        for (uint32_t i = 0; i < cc->record_count; i++) fprintf(h, "%u,", cc->cnt[i]);
        fprintf(h, "} },\n");
    }
    fprintf(h, "};\n#endif\n");
    fclose(h);

    printf("=== Doom wall capture ===\n");
    printf("sweeps=%d  total_records=%ld\n", sweeps, g_total_records);
    printf("q29_attr_shift range: [%d..%d]\n", g_sh_min, g_sh_max);
    printf("colormap_id range:    [%d..%d]\n", g_cid_min, g_cid_max);
    printf("max records/cmd=%ld  max count=%ld\n", g_rec_max, g_cnt_max);
    printf("degenerate(zi-plane-all-zero) commands: %d\n", g_z_nonzero);
    printf("columns accepted=%ld emitted=%ld dups=%ld\n",
           total_accepted, total_emitted, total_dups);
    printf("COVERAGE GAPS (accepted-but-not-emitted columns): %ld\n", total_gaps);
    printf("replay wall: %d commands, %ld records\n", replay_n, replay_records);
    return 0;
}
