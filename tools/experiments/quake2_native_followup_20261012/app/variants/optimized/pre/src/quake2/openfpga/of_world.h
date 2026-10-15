/* Persistent world surfaces for the openfpga mini-GL renderer. */
#ifndef OPENFPGA_OF_WORLD_H
#define OPENFPGA_OF_WORLD_H

#include <stddef.h>
#include <stdint.h>

typedef struct ofgl_world_poly_s ofgl_world_poly_t;

/* Storage belongs to the model hunk and is released with the BSP. Oversized
 * polygons return zero and continue through the ordinary GL array path. */
size_t OFGL_WorldPolyBytes(int numverts);
ofgl_world_poly_t *OFGL_BuildWorldPoly(void *storage, const float *verts, int numverts);
int OFGL_WorldPolysEnabled(void);
void OFGL_DrawWorldPolys(ofgl_world_poly_t *const *polys, int count);

/* Mark one actual generic submission as BSP world geometry so raw GPU surfaces
 * and their CPU neighbours use identical original-vertex screen/depth values.
 * Begin3d consumes the hint even if drawing fails. Clear after submission too. */
void OFGL_SetWorldDraw(int enabled);

/* Conservative model-space bounds for the next explicit submission. Copies
 * the values; NULL,NULL disables the hint. Call only after buffer construction
 * (which can flush earlier draws), then disable immediately after drawing. */
void OFGL_SetDrawBounds(const float mins[3], const float maxs[3]);

/* Source indices identify tightly packed XYZW interpolation entries; UV and
 * color arrays keep their seam indices. Only standalone immediate alias draws
 * may borrow this storage. Raw world offload leaves this CPU alias path active. */
int OFGL_AliasPositionsEnabled(void);
void OFGL_DrawAliasElements(const int *source_indices, int index_stride,
    const float *positions, const unsigned short *indices, int count);

/* Native MD2 assets are immutable CPU model data until uploaded, then immutable
 * fast-memory copies owned by the texture allocator generation. A job embeds
 * all mutable draw state and colours in the command ring; it borrows no CPU
 * interpolation, GL-buffer or palette storage after the call returns. */
typedef struct ofgl_native_mesh_s {
    size_t bytes;
    uint32_t *blocks; /* nv|nt<<8, nv*(source-index,float S,float T), nt*indices */
    uint32_t block_words, block_count, source_vertices, frame_count;
    const uint8_t *frames;
    uint32_t frame_stride;
    uint8_t *bounds; /* per-frame minXYZ/maxXYZ of referenced native bytes */
    uint32_t *normal_masks; /* eight words per frame, referenced normal indices */
    float max_s, max_t;
    uint32_t gpu_generation, gpu_frames, gpu_blocks, gpu_end;
    unsigned gpu_width, gpu_height;
} ofgl_native_mesh_t;

typedef struct {
    ofgl_native_mesh_t *mesh;
    unsigned old_frame, frame;
    float move[3], old_scale[3], scale[3];
    float mins[3], maxs[3];
    uint32_t palette[128]; /* two RGB565 values, normal 2n in low half */
} ofgl_native_draw_t;

int OFGL_NativeAvailable(void);
void OFGL_NativeReset(void);
int OFGL_DrawNativeAlias(const ofgl_native_draw_t *draw);

#endif
