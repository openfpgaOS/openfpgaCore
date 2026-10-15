#ifndef ASYNC_MESH_REPAIR_H
#define ASYNC_MESH_REPAIR_H
#include <stdint.h>
/* This is a CPU-local interface, not an MMIO struct. All pointer targets remain
 * immutable until return. Raw rows are vertex-major: X,Y,Z,W,1/W,UV16,RGBA8,clip.
 * Original vertices use the frozen RV32 Vtx ABI:24B, XYZ float@0,UVs16@14,RGBA@18.
 * Commands are the original little-endian F3DEX2 TRI1/TRI2 word pairs. */
struct am_transform {
    uint32_t matrix[16], aspect, coefficient[3], ambient, diffuse, scales, lighting;
};
struct am_material {
    uint32_t flags_rgb, u_offset, v_offset, alpha;
    int32_t xcenter, ycenter, xscale, yscale; /* exact g_clip_* integers */
};
struct am_repair_job {
    uint32_t count, command_count, cull, raw_valid;
    const uint32_t (*raw)[8];
    const uint32_t (*commands)[2];
    const unsigned char *vertices;
    struct am_transform transform;
    struct am_material material;
};
struct am_repair_result {
    uint32_t input_triangles, clip_rejected, cull_rejected, clipped_triangles;
    uint32_t indexed_triangles, direct_triangles, emitted_words, recomputed_vertices;
};
/* emit returns1 only after this word was accepted. It may wait on the dedicated
 * repair insertion port, but must not enter renderer/ring code or recurse.
 * Return0 cancels after consumer reset; caller must NOT send ordinary END.
 * Emission contains only0x56/0x54/0x4E; prior ordered material/viewport is held.
 * Structural bad input is rejected before any word. Runtime cancellation may
 * leave a prefix and therefore requires a coupled consumer/output reset. */
typedef int (*am_repair_emit)(void *opaque, uint32_t word);
enum { AM_REPAIR_OK=0, AM_REPAIR_BAD_INPUT=1, AM_REPAIR_CANCELLED=2 };
int am_repair(const struct am_repair_job *, am_repair_emit, void *, struct am_repair_result *);
/* Same raw layout, for independent transform qualification and CPU recovery.
 * Caller provides32rows. FCSR is saved/restored; RNE arithmetic is explicit. */
int am_repair_transform(const struct am_repair_job *, uint32_t raw[32][8]);
#endif
