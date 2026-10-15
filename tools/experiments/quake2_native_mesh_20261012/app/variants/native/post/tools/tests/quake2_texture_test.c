/* Real non-OF_PC texture manager and extracted Quake2 lifecycle functions.
 * Hardware leaves are recording mocks; no cached/uncached coherency emulation. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define OF_GPU_H
#define OF_CACHE_H
#include "of_caps.h"
#include "of_emit_q2.h"

static struct of_capabilities caps;
const struct of_capabilities *_of_caps_ptr = &caps;
static uint32_t GPU_PALOOKUP_BASE, GPU_STATUS;
typedef struct { uint32_t addr; uint16_t width, height; } of_gpu_texture_t;
static unsigned char fast_bytes[4096];
static char events[1024];
static unsigned event_count, route, finish_count, upload_count, clean_count;
static unsigned pending, expected_live_top;
static void event(char c) { assert(event_count + 1 < sizeof(events)); events[event_count++] = c; events[event_count] = 0; }
static void clear_events(void) { event_count = 0; events[0] = 0; }
static void of_gpu_finish(void);
static void _of_gpu_route_fast_tex(int enable)
{
    assert(!pending);
    route = enable != 0;
    event(enable ? 'T' : 'S');
}
static void _of_gpu_fast_tex_upload(uint32_t off, const uint32_t *src, uint32_t words)
{
    assert(off % 4 == 0 && off + words * 4 <= sizeof(fast_bytes));
    /* Explicit word reads preserve the actual leaf's source access size. */
    for (uint32_t i = 0; i < words; ++i) {
        uint32_t word = src[i];
        memcpy(fast_bytes + off + i * 4, &word, 4);
    }
    ++upload_count;
    event('U');
}
static void of_gpu_bind_texture(const of_gpu_texture_t *tex) { (void)tex; event('B'); }
static void of_cache_flush_range(void *src, uint32_t bytes) { (void)src; (void)bytes; }
#include "of_texture.h"

static uint32_t ofq2_caps, ofq2_tex_generation;
static uint32_t ofq2_native_end, ofq2_native_generation = 1;
static bool ofq2_ready, ofq2_pending;
static uint8_t *ofq2_bound_fb;
static int ofq2_bound_stride;
static ofq2_tri_state_t ofq2_tristate_cur;
static bool ofq2_tristate_valid, ofq2_tristate_dirty;
static ofq2_object_state_t ofq2_objstate_cur;
static bool ofq2_objstate_valid;
static uint32_t ofq2_clean_once_slots[4], ofq2_clean_once_hash[4], ofq2_clean_once_next;
#define OF_SVC_MAGIC 0x4f535643u
static struct { unsigned magic, count; void *video_acquire_next, *video_buffer_addr; } services;
#define OF_SVC (&services)

static void of_gpu_init(void) { pending = 0; event('I'); }
static void of_gpu_finish(void)
{
    if (pending) assert(_of_tex_fast_top == expected_live_top);
    pending = 0;
    ++finish_count;
    event('F');
}
static void of_gpu_shutdown(void) { assert(!pending); event('D'); }
void OFQ2_Finish(void) { of_gpu_finish(); ofq2_pending = false; }
void OFQ2_CacheClean(const void *src, uint32_t bytes)
{
    assert(src && bytes);
    ++clean_count;
    event('C');
}
#include "extracted_texture_functions.inc"

static void setup(uint32_t bytes, uint32_t version)
{
    memset(&caps, 0, sizeof(caps));
    caps.version = version;
    caps.tex_fast_size = bytes;
    caps.gpu_base = 0x40000000u;
    caps.hw_features = OF_HW_GPU_SPAN;
    route = 1; /* hardware soft reset deliberately leaves the route latched */
    pending = 0;
    clean_count = finish_count = upload_count = 0;
    clear_events();
    memset(fast_bytes, 0xa5, sizeof(fast_bytes));
    assert(OFQ2_Init(640));
    assert(ofq2_ready && ofq2_bound_stride == 640);
    assert(strcmp(events, version >= 3 && bytes ? "IS" : "I") == 0);
    if (version >= 3 && bytes) assert(route == 0 && _of_tex_bound == 0);
    clear_events();
}

static void test_exact_sizes(void)
{
    for (unsigned n = 1; n <= 65; ++n) {
        setup(4096, 4);
        unsigned char *src = malloc(n); /* exact allocation: ASan checks tails */
        assert(src);
        for (unsigned i = 0; i < n; ++i) src[i] = (unsigned char)(i * 37u + n);
        ofq2_texture_t t;
        OFQ2_TexCreate(&t, src, 1, 1, n);
        assert(clean_count == 1 && events[0] == 'C');
        assert((t.flags & _OF_TEX_FAST) && t.addr == 0);
        assert(memcmp(fast_bytes, src, n) == 0);
        for (unsigned i = n; i < (n + 3u) / 4u * 4u; ++i) assert(fast_bytes[i] == 0);
        assert(fast_bytes[(n + 3u) / 4u * 4u] == 0xa5);
        assert(_of_tex_fast_top == ((n + 15u) & ~15u));
        assert(upload_count == (n / 4u != 0) + (n % 4u != 0));
        free(src);
    }
    puts("PASS: 65 exact source lengths, padded tails, alignment, one clean per create");
}

static void test_capacity_and_versions(void)
{
    unsigned char *src = malloc(45);
    memset(src, 0x35, 45);
    ofq2_texture_t a, b;
    setup(64, 4);
    OFQ2_TexCreate(&a, src, 1, 1, 45);
    assert(a.flags & _OF_TEX_FAST);
    assert(_of_tex_fast_top == 48 && of_texture_budget_free() == 16);
    unsigned uploads = upload_count;
    OFQ2_TexCreate(&b, src, 1, 1, 17);
    assert(!(b.flags & _OF_TEX_FAST) && upload_count == uploads);
    assert(_of_tex_fast_top == 48);
    assert(b.addr == (uint32_t)(uintptr_t)src);
    for (unsigned version = 2; version <= 4; ++version) {
        setup(version == 2 ? 4096 : 0, version);
        assert(!of_texture_has_fast_mem());
        OFQ2_TexCreate(&a, src, 1, 1, 45);
        assert(a.flags == 0 && upload_count == 0);
        clear_events();
        OFQ2_Shutdown();
        assert(strcmp(events, "FD") == 0); /* no fast-register write */
    }
    free(src);
    puts("PASS: bounded capacity fallback and caps v2/v3/v4 SDRAM-only lifecycle");
}

static void test_lifecycle_and_domains(void)
{
    unsigned char *src = malloc(17);
    memset(src, 0x91, 17);
    ofq2_texture_t a;
    setup(4096, 4);
    OFQ2_TexCreate(&a, src, 1, 1, 17);
    clear_events();
    OFQ2_TexBindDomain(&a);
    assert(strcmp(events, "FT") == 0 && route == 1);
    clear_events();
    OFQ2_TexBindDomain(&a);
    assert(event_count == 0); /* no per-draw fence for the common domain */
    expected_live_top = _of_tex_fast_top;
    pending = 1;
    unsigned generation = ofq2_tex_generation;
    OFQ2_TexReset();
    assert(strcmp(events, "F") == 0);
    assert(_of_tex_fast_top == 0 && ofq2_tex_generation == generation + 1);
    /* New offsets may now overwrite old bytes; old handles are invalid. */
    OFQ2_TexCreate(&a, src, 1, 1, 17);
    assert(a.addr == 0);
    clear_events();
    OFQ2_TexBindDomain(&a);
    assert(strcmp(events, "FT") == 0); /* reset invalidates route/cache shadow */
    clear_events();
    expected_live_top = _of_tex_fast_top;
    pending = 1;
    OFQ2_TexInit();
    assert(strcmp(events, "FS") == 0);
    assert(route == 0 && _of_tex_fast_top == 0);
    OFQ2_TexCreate(&a, src, 1, 1, 17);
    OFQ2_TexBindDomain(&a);
    clear_events();
    expected_live_top = _of_tex_fast_top;
    pending = 1;
    OFQ2_Shutdown();
    assert(strcmp(events, "FSD") == 0);
    assert(!ofq2_ready && route == 0);
    clear_events();
    OFQ2_TexInit(); OFQ2_TexReset(); OFQ2_Shutdown();
    assert(event_count == 0); /* idle wrappers do not touch uninitialized GPU */
    free(src);
    puts("PASS: startup stale route, reset/reinit drain-before-reuse, shutdown route, stable binds");
}

int main(void)
{
    test_exact_sizes();
    test_capacity_and_versions();
    test_lifecycle_and_domains();
    return 0;
}
