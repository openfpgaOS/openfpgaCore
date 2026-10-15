/* Experimental native billboard ABI and independent CPU rendering reference.
 * All integer arithmetic here defines the wire-format convention. It does not
 * claim bit identity with the legacy floating-point GL path. */
#ifndef OF_PARTICLES_H
#define OF_PARTICLES_H
#include <stdint.h>
#include <limits.h>
#define OFQ2_PARTICLE_CONTEXT_WORDS 16u
#define OFQ2_PARTICLE_WORDS 5u
#define OFQ2_PARTICLE_BATCH_MAX 64u
#define OFQ2_PARTICLE_ZI (1u << 24)

/* Signed round-to-nearest, ties-to-even, without signed left shifts. */
static inline int64_t ofq2_particle_rne(int64_t value, unsigned shift)
{
    int64_t divisor = INT64_C(1) << shift;
    int64_t q = value / divisor, r = value % divisor;
    uint64_t magnitude = r < 0 ? (uint64_t)-r : (uint64_t)r;
    if (magnitude > (uint64_t)(divisor / 2) ||
        (magnitude == (uint64_t)(divisor / 2) && (q & 1)))
        q += value < 0 ? -1 : 1;
    return q;
}
static inline int64_t ofq2_particle_floor(int64_t value, unsigned shift)
{
    int64_t divisor = INT64_C(1) << shift;
    int64_t q = value / divisor;
    return q - (value < 0 && value % divisor != 0);
}
static inline int32_t ofq2_particle_sat16(int64_t value)
{
    return value < INT16_MIN ? INT16_MIN : value > INT16_MAX ? INT16_MAX : (int32_t)value;
}

/* Context: up XYZ, right XYZ, viewport center XY, viewport scales XY, near,
 * U0,U1,V0,V1, cull. All signed Q16 except cull. Up/right Z must be zero.
 * Record: camera XYZ (positive Y up), size Q16, RGB565|alpha<<16.
 * Return -1 for unsupported/malformed input, 0 for an invisible primitive,
 * 1 with the exact 17-word 0x4E reference payload. clip may be NULL, otherwise
 * it is the surface state's [x0,y0,x1,y1] in integer pixels. */
static inline int ofq2_particle_reference(const uint32_t context[16],
    const uint32_t record[5], const int clip[4], uint32_t words[17])
{
    if (context[2] || context[5] || context[15] > 2 ||
        (int32_t)context[8] <= 0 || (int32_t)context[9] <= 0 ||
        (int32_t)context[10] <= 0 || (int32_t)record[3] <= 0 ||
        (record[4] & 0xff000000u)) return -1;
    if (!(record[4] & 0xff0000u)) return 0;
    int32_t z = (int32_t)record[2];
    if (z < (int32_t)context[10] || z < 16 * 65536 || z > 4096 * 65536) return -1;
    for (unsigned i = 11; i <= 14; ++i)
        if ((int32_t)context[i] < 0 || context[i] >= 128u * 65536u) return -1;
    uint32_t depth = (uint32_t)((UINT64_C(1) << 46) / (uint32_t)z);
    int32_t x[3], y[3];
    for (unsigned vertex = 0; vertex < 3; ++vertex) {
        int64_t cx = (int32_t)record[0], cy = (int32_t)record[1];
        if (vertex) {
            unsigned basis = (vertex - 1) * 3;
            cx += ofq2_particle_rne((int64_t)(int32_t)context[basis] * (int32_t)record[3], 16);
            cy += ofq2_particle_rne((int64_t)(int32_t)context[basis + 1] * (int32_t)record[3], 16);
        }
        if (cx < INT32_MIN || cx > INT32_MAX || cy < INT32_MIN || cy > INT32_MAX) return -1;
        int64_t rx = ofq2_particle_floor(cx * depth, 26);
        int64_t ry = ofq2_particle_floor(cy * depth, 26);
        if (rx < INT32_MIN || rx > INT32_MAX || ry < INT32_MIN || ry > INT32_MAX) return -1;
        int64_t px = rx * (int32_t)context[8] + (int64_t)(int32_t)context[6] * 1048576;
        int64_t py = (int64_t)(int32_t)context[7] * 1048576 - ry * (int32_t)context[9];
        x[vertex] = ofq2_particle_sat16(ofq2_particle_rne(px, 32));
        y[vertex] = ofq2_particle_sat16(ofq2_particle_rne(py, 32));
        words[vertex] = (uint32_t)(uint16_t)x[vertex] | (uint32_t)(uint16_t)y[vertex] << 16;
        words[3 + vertex] = context[vertex == 1 ? 12 : 11];
        words[6 + vertex] = context[vertex == 2 ? 14 : 13];
        words[9 + vertex] = OFQ2_PARTICLE_ZI;
        words[14 + vertex] = depth;
    }
    if (clip && ((x[0] < clip[0] * 16 && x[1] < clip[0] * 16 && x[2] < clip[0] * 16) ||
        (x[0] >= clip[2] * 16 && x[1] >= clip[2] * 16 && x[2] >= clip[2] * 16) ||
        (y[0] < clip[1] * 16 && y[1] < clip[1] * 16 && y[2] < clip[1] * 16) ||
        (y[0] >= clip[3] * 16 && y[1] >= clip[3] * 16 && y[2] >= clip[3] * 16))) return 0;
    int64_t determinant = (int64_t)(x[1] - x[0]) * (y[2] - y[0]) -
        (int64_t)(x[2] - x[0]) * (y[1] - y[0]);
    if (context[15] && (!determinant ||
        (context[15] == 1 && determinant < 0) || (context[15] == 2 && determinant > 0))) return 0;
    words[12] = (record[4] & 65535u) * 65537u;
    words[13] = record[4] & 65535u;
    return 1;
}
#endif
