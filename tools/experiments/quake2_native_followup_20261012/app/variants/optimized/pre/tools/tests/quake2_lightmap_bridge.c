/* Use the actual backend copy aliasing and RGB565 conversion in the
 * lightmap history regression; keep the unused mini-GL sections discarded. */
#include <assert.h>
#include "../../src/quake2/openfpga/of_gl1.c"

void TestLightmapUpload(int texnum, int x, int y, int w, int h,
    const void *pixels, uint16_t pages[][128 * 128])
{
    int block = ofgl_lm_index(texnum);
    assert(block >= 1 && block <= 3);
    ofgl_lm[block] = pages[block-1];
    ofgl_lm_store(block,x,y,w,h,pixels);
}
