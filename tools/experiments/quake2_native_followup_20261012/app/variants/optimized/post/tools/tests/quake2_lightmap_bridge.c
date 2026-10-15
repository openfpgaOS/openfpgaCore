/* Use the actual backend copy aliasing and RGB565 conversion in the
 * lightmap history regression; keep the unused mini-GL sections discarded. */
#include <assert.h>
#define OFGL_UpdateLightmapRect TestRendererUpdateLightmapRect
#define OFGL_UpdateLightmapSamples TestRendererUpdateLightmapSamples
#include "../../src/quake2/openfpga/of_gl1.c"
#undef OFGL_UpdateLightmapRect
#undef OFGL_UpdateLightmapSamples

void TestLightmapUpload(int texnum, int x, int y, int w, int h,
    const void *pixels, uint16_t pages[][128 * 128])
{
    int block = ofgl_lm_index(texnum);
    assert(block >= 1 && block <= 3);
    ofgl_lm[block] = pages[block-1];
    ofgl_lm_store(block,x,y,w,h,pixels);
}

void TestLightmapUploadStride(int texnum, int x, int y, int w, int h,
    const uint32_t *pixels, int stride, uint16_t pages[][128 * 128])
{
    int block = ofgl_lm_index(texnum);
    assert(block >= 1 && block <= 3);
    ofgl_lm[block] = pages[block-1];
    TestRendererUpdateLightmapRect(texnum,x,y,w,h,pixels,stride);
}

void TestLightmapUploadSamples(int texnum, const uint32_t *pixels,
    const uint16_t *indices, int count, uint16_t pages[][128 * 128])
{
    int block = ofgl_lm_index(texnum);
    assert(block >= 1 && block <= 3);
    ofgl_lm[block] = pages[block-1];
    TestRendererUpdateLightmapSamples(texnum,pixels,indices,count);
}
