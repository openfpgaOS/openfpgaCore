#define main renderer_existing_main
#include "quake2_renderer_test.c"
#undef main
#include <limits.h>

static void check_position(int index)
{
    ofgl_cvert_t expected = {0}, actual;
    const float *xyz = ofgl_fetch_position(index, &expected);
    ofgl_world_project_original(xyz, &expected);
    ofgl_classify_cvert(&expected);
    ofgl_world_position(index, &actual);
    assert(actual.x == expected.x && actual.y == expected.y && actual.z == expected.z);
    assert(actual.clipcode == expected.clipcode && actual.viewcode == expected.viewcode);
    assert(actual.attrs_valid == 0 && actual.projected_valid == expected.projected_valid);
    if (actual.projected_valid == 1) {
        assert(actual.px == expected.px && actual.py == expected.py);
        assert(actual.depth == expected.depth && actual.iw == expected.iw);
        assert(actual.sx == expected.sx && actual.sy == expected.sy);
    }
    /* The caller can modify its attributes without contaminating the cache. */
    actual.s = 123; actual.t = -456; actual.rgb = 12345; actual.attrs_valid = 1;
    ofgl_world_position(index, &actual);
    assert(actual.attrs_valid == 0);
}

static void begin_world(void)
{
    const ofq2_texture_t *tex;
    float tw, th;
    OFGL_SetWorldDraw(1);
    assert(ofgl_begin3d(&tex, &tw, &th));
    assert(ofgl_batch_world);
}

int main(void)
{
    assert(RI_InitContext(NULL));
    float v[4][7] = {{-4,-4,-32,0,0,0,0}, {4,-4,-32,1,0,0,0},
        {4,4,-32,1,1,0,0}, {-4,4,-32,0,1,0,0}};
    gpu_geometry = 1;
    setup_world_arrays(v[0]);
    begin_world();
    for (int i = 0; i < 4; ++i) { check_position(i); check_position(i); }
    ofgl_end3d();

    /* Reusing the array pointer with new coordinates cannot hit stale data. */
    v[0][0] = 7.25f; v[0][2] = -45;
    begin_world(); check_position(0); ofgl_end3d();

    /* Force a complete tag wrap with ancient epoch-one entries. All of them
     * must be discarded before epoch one is reused by the new transform. */
    for (unsigned i = 0; i < 128; ++i) {
        ofgl_world_positions[i].epoch = 1;
        ofgl_world_positions[i].position.x = 1000000;
    }
    ofgl_world_prepared = 1;
    ofgl_world_epoch = UINT_MAX;
    ofgl_Translatef(1, 2, 3);
    begin_world();
    assert(ofgl_world_epoch == 1 && ofgl_world_prepared == 1);
    for (unsigned i = 0; i < 128; ++i) assert(ofgl_world_positions[i].epoch == 0);
    for (int i = 0; i < 4; ++i) check_position(i);
    ofgl_end3d();

    /* Both viewport and matrix changes invalidate all projected coordinates. */
    ofgl_Viewport(17, 23, 240, 180);
    ofgl_Rotatef(7, 0, 1, 0);
    begin_world();
    assert(ofgl_world_epoch == 2);
    for (int i = 0; i < 4; ++i) check_position(i);
    ofgl_end3d();
    RI_ShutdownContext();
    puts("PASS: world positions preserve fresh attributes and invalidate on XYZ, transform, viewport and epoch wrap");
    return 0;
}
