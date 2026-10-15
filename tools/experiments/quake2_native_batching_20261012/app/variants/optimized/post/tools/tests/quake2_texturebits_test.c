/* Sparse world texture enumeration must preserve the original image order,
 * chains and consumption rules across registration changes and slot reuse. */
#include "../../src/quake2/src/client/refresh/gl1/gl1_surf.c"
#include <assert.h>
#include <stdlib.h>

image_t gltextures[MAX_GLTEXTURES];
int numgltextures;
static unsigned rng = 983171;
static unsigned Random(void) { rng = rng * 1664525u + 1013904223u; return rng; }

int main(void)
{
    enum {FRAMES = 4000, INSERTS = 16};
    msurface_t *pool = calloc(FRAMES * INSERTS, sizeof(*pool));
    msurface_t *heads[MAX_GLTEXTURES] = {0};
    assert(pool);
    for (int frame = 0; frame < FRAMES; ++frame)
    {
        /* Include 0/1/31/32/33/full counts and arbitrary word boundaries. */
        static const int sizes[] = {0, 1, 31, 32, 33, 63, 64, 65, 1023, 1024};
        numgltextures = frame % 3 ? (int)(Random() % 1025) : sizes[frame % 10];
        for (int k = 0; k < 24; ++k)
        {
            unsigned i = Random() % MAX_GLTEXTURES;
            if (Random() & 1)
            {
                /* Free or reuse a slot. A stale sparse bit is harmless. */
                memset(&gltextures[i], 0, sizeof(gltextures[i]));
                heads[i] = NULL;
            }
            gltextures[i].registration_sequence = (Random() % 3 != 0);
        }
        for (int k = 0; k < INSERTS && numgltextures; ++k)
        {
            unsigned i = Random() % (unsigned)numgltextures;
            msurface_t *s = pool + frame * INSERTS + k;
            R_WorldTextureChainAdd(&gltextures[i], s);
            assert(s->texturechain == heads[i]);
            heads[i] = s;
        }
        /* The first multitexture pass must leave chains intact. Classic mode
         * consumes in its sole pass; multitexture consumes in its second. */
        int passes = 1 + (frame & 1);
        for (int pass = 0; pass < passes; ++pass)
        {
            int reference = 0;
            for (int i = R_WorldTextureChainNext(-1); i < numgltextures;
                 i = R_WorldTextureChainNext(i))
            {
                if (!gltextures[i].registration_sequence || !gltextures[i].texturechain)
                    continue;
                while (reference < numgltextures &&
                       (!gltextures[reference].registration_sequence || !heads[reference]))
                    ++reference;
                assert(reference == i);
                assert(heads[i] == gltextures[i].texturechain);
                ++reference;
                if (pass == passes - 1)
                {
                    R_WorldTextureChainClear(&gltextures[i], i);
                    heads[i] = NULL;
                }
            }
            while (reference < numgltextures &&
                   (!gltextures[reference].registration_sequence || !heads[reference]))
                ++reference;
            assert(reference == numgltextures);
        }
        for (int i = 0; i < MAX_GLTEXTURES; ++i)
            assert(gltextures[i].texturechain == heads[i]);
    }
    free(pool);
    puts("PASS: 4000 sparse texture-chain histories; image order, chains, registration and slot reuse exact");
    return 0;
}
