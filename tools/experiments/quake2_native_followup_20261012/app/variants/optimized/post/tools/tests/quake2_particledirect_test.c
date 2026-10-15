#define main renderer_existing_main
#include "quake2_renderer_test.c"
#undef main

typedef struct { float origin[3]; int colour; float alpha; } particle_input_t;
static uint32_t random_state = 713821u;
static uint32_t random_value(void)
{
    random_state = random_state * 1664525u + 1013904223u;
    return random_state;
}
int main(void)
{
    assert(RI_InitContext(NULL));
    uint32_t palette[256]; uint8_t gamma[256];
    for (unsigned i = 0; i < 256; ++i) {
        palette[i] = random_value(); gamma[i] = (i * 199u) & 255u;
    }
    for (unsigned iteration = 0; iteration < 10000; ++iteration) {
        particle_input_t particles[4];
        float vtx[12][3], uv[12][2]; uint8_t rgba[12][4];
        float up[3] = {0,1.5f,0}, right[3] = {1.5f,0,0};
        float eye[3] = {0,0,0}, forward[3] = {0,0,-1};
        for (unsigned i = 0; i < 4; ++i) {
            particle_input_t *p = particles + i;
            for (unsigned k = 0; k < 2; ++k)
                p->origin[k] = ((int)(random_value() % 100001u) - 50000) * .001f;
            p->origin[2] = -(random_value() % 100001u) * .001f;
            p->colour = random_value() & 255u;
            p->alpha = iteration % 9 == 0 ? 0 : iteration % 7 == 0 ? 1 :
                (random_value() & 255u) * (1.0f / 255.0f);
            float scale = -p->origin[2];
            scale = scale < 20 ? 1 : 1 + scale * .004f;
            uint32_t c = palette[p->colour];
            for (unsigned j = 0; j < 3; ++j) {
                for (unsigned k = 0; k < 3; ++k)
                    vtx[i*3+j][k] = p->origin[k] + (j == 0 ? 0 :
                        (j == 1 ? up[k] : right[k]) * scale);
                uv[i*3+j][0] = j == 1 ? 1.0625f : .0625f;
                uv[i*3+j][1] = j == 2 ? 1.0625f : .0625f;
                rgba[i*3+j][0] = gamma[c & 255u];
                rgba[i*3+j][1] = gamma[(c >> 8) & 255u];
                rgba[i*3+j][2] = gamma[(c >> 16) & 255u];
                rgba[i*3+j][3] = p->alpha * 255;
            }
        }
        setup_world_arrays(vtx[0]);
        ofgl_tex2d_on[1] = 0;
        ofgl_TexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE);
        ofgl_Enable(GL_BLEND); ofgl_DepthMask(GL_FALSE);
        ofgl_Rotatef((iteration % 31u) * .13f, 0, 1, 0);
        ofgl_EnableClientState(GL_COLOR_ARRAY);
        ofgl_VertexPointer(3, GL_FLOAT, 0, vtx);
        ofgl_TexCoordPointer(2, GL_FLOAT, 0, uv);
        ofgl_ColorPointer(4, GL_UNSIGNED_BYTE, 0, rgba);
        reset_geometry_recording();
        ofgl_DrawArrays(GL_TRIANGLES, 0, 12);
        int expected_count = draws;
        cpu_triangle_t expected[32]; unsigned expected_alpha[32];
        for (int i = 0; i < draws; ++i) {
            expected[i] = recorded_cpu_triangle(i); expected_alpha[i] = alpha_seen[i];
        }
        reset_geometry_recording();
        assert(OFGL_DrawParticleBillboards(particles[0].origin, &particles[0].colour,
            &particles[0].alpha, sizeof(*particles), 4, up, right, eye, forward, palette, gamma));
        assert(draws == expected_count);
        for (int i = 0; i < draws; ++i) {
            check_cpu_triangle(i, expected+i);
            assert(alpha_seen[i] == expected_alpha[i]);
        }
    }
    RI_ShutdownContext();
    puts("PASS: 40000 direct particle billboards exactly match staged geometry, UVs, RGB, alpha, clipping and ordering");
    return 0;
}
