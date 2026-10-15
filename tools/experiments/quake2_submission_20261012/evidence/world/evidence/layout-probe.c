typedef unsigned uint32_t; typedef unsigned short uint16_t;
#define OFGL_WORLD_VERTS 32
typedef struct ofgl_world_poly_s ofgl_world_poly_t;
struct ofgl_world_poly_s
{
	const float *verts;
	float center[3], extent[3], max_st;
	int count, raw_ok, tex_w, tex_h, lm_block;
	uint32_t light_version;
	uint16_t light_index[OFGL_WORLD_VERTS];
    unsigned packet_cull;
    /* Keep the upload header directly adjacent to the existing vertex words.
     * The cached fan commands follow the vertices in the same hunk allocation. */
    uint32_t upload_header;
	uint32_t words[];
};
_Static_assert(sizeof(void*)==4,"RV32");
_Static_assert(sizeof(ofgl_world_poly_t)==128,"world128");
_Static_assert(__builtin_offsetof(ofgl_world_poly_t,words)==__builtin_offsetof(ofgl_world_poly_t,upload_header)+4,"adjacent packet");
