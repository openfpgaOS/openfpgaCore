/* Persistent world surfaces for the openfpga mini-GL renderer. */
#ifndef OPENFPGA_OF_WORLD_H
#define OPENFPGA_OF_WORLD_H

#include <stddef.h>

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

#endif
