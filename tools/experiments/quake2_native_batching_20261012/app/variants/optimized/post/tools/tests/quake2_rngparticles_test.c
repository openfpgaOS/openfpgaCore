/* Verify the actual particle caller consumes exactly its allocated particles'
 * nine outputs, preserves the deployed FP operation order, and leaves global
 * generator state identical across exhausted pools and reseeds. */
#define main rng_sequence_test_main
#include "quake2_rng_test.c"
#undef main
#include <stddef.h>
#include "../../src/quake2/src/client/cl_particles.c"
client_state_t cl;

static void exact_float(float a, float b)
{
    assert(!memcmp(&a, &b, sizeof a));
}

int main(void)
{
    unsigned produced = 0;
    for (unsigned pass = 0; pass < 2000; ++pass)
    {
        if (pass % 251 == 0) { randk_seed(); ref_randk_seed(); }
        unsigned capacity = pass % 17;
        int count = (int)(pass * 13 % 31) - 3;
        unsigned expected = count < 0 ? 0 : (unsigned)count;
        if (expected > capacity) expected = capacity;
        cl_numparticles = capacity;
        memset(particles, 0, sizeof(particles));
        active_particles = free_particles = NULL;
        CL_ClearParticles();
        cl.time = (int)pass * 17;
        vec3_t org = {pass * .13f - 140, pass * -.21f + 50, pass * .017f};
        vec3_t dir = {pass * .011f - 10, 13 - pass * .013f, pass * -.005f};
        CL_ParticleEffect(org, dir, 100, count);
        for (unsigned i = 0; i < expected; ++i)
        {
            int r[9];
            for (unsigned n = 0; n < 9; ++n) r[n] = ref_randk();
            cparticle_t *p = particles + i;
            exact_float(p->time, (float)cl.time);
            exact_float(p->color, (float)(100 + (r[0] & 7)));
            float d = r[1] & 31;
            for (unsigned n = 0; n < 3; ++n)
            {
                float position = __builtin_fmaf(d, dir[n], (float)((r[2+2*n] & 7)-4));
                exact_float(p->org[n], org[n] + position);
                float velocity = __builtin_fmaf((float)(r[3+2*n] & 32767), 2.0f/32767.0f, -1.0f);
                exact_float(p->vel[n], velocity * 20);
            }
            exact_float(p->accel[0], 0);
            exact_float(p->accel[1], 0);
            exact_float(p->accel[2], -PARTICLE_GRAVITY + .2f);
            exact_float(p->alpha, 1);
            exact_float(p->alphavel, -1.0 / (0.5 + ((r[8] & 32767) * (1.0 / 32767)) * 0.3));
        }
        assert(active_particles == (expected ? particles + expected - 1 : NULL));
        assert(free_particles == (expected < capacity ? particles + expected : NULL));
        cparticle_t *link = active_particles;
        for (unsigned n = expected; n; --n)
        {
            assert(link == particles + n - 1);
            link = link->next;
        }
        assert(link == NULL);
        state_exact();
        produced += expected;
    }
    printf("PASS: %u exact batched particles; 2000 full/partial/exhausted pools, zero/negative counts, reseeds and complete PRNG state equality\n", produced);
    return 0;
}
