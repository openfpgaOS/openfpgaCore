/* Actual capability discovery and geometry emitters with a bounded ring mock. */
#define main texture_original_main
#include "quake2_texture_test.c"
#undef main

static uint32_t ring_words[1024];
static unsigned reserved_words, committed_words, reservations, commits;
static void _gpu_stream_reserve_words(unsigned words)
{
    assert(!reserved_words && words <= 1024);
    reserved_words = words;
    ++reservations;
}
static uint32_t *_gpu_ring_claim(void) { assert(reserved_words); return ring_words; }
static void _gpu_ring_commit(unsigned words)
{
    assert(words == reserved_words);
    committed_words = words;
    reserved_words = 0;
    ++commits;
}
#include "extracted_geometry_functions.inc"

static void reset_ring(void)
{
    memset(ring_words, 0xa5, sizeof(ring_words));
    reservations = commits = reserved_words = committed_words = 0;
    ofq2_pending = false;
}

static void test_capabilities(void)
{
    for (unsigned dependencies = 0; dependencies < 8; dependencies++)
    for (unsigned status = 0; status < 4; status++)
    {
        setup(0, 4);
        caps.hw_features = OF_HW_GPU_SPAN |
            ((dependencies & 1) ? OF_HW_GPU_VERT_TRI : 0) |
            ((dependencies & 2) ? OF_HW_GPU_VCOLOR : 0) |
            ((dependencies & 4) ? OF_HW_GPU_CLIP_LOAD : 0);
        GPU_STATUS = status << 10;
        assert(OFQ2_Init(640));
        assert(!!OFQ2_Supports(OFQ2_CAP_GEOMETRY) ==
            (dependencies == 7 && !!(status & 1)));
        assert(!!OFQ2_Supports(OFQ2_CAP_GEOMETRY_WORKER) ==
            (dependencies == 7 && status == 3));
        OFQ2_Shutdown();
        assert(!OFQ2_Supports(OFQ2_CAP_GEOMETRY_WORKER));
    }
}

static void test_fan_packets(void)
{
    uint32_t vertices[32 * 5];
    for (unsigned i = 0; i < 32; i++) {
        vertices[i * 5] = i;
        for (unsigned j = 1; j < 5; j++) vertices[i * 5 + j] = 0x81230000 + i * 37 + j;
    }
    ofq2_ready = true;
    ofq2_tristate_valid = ofq2_objstate_valid = true;
    ofq2_tristate_dirty = false;
    for (unsigned worker = 0; worker < 2; worker++)
    for (unsigned count = 3; count <= 32; count++)
    for (unsigned cull = 0; cull <= 2; cull++)
    {
        ofq2_caps = OFQ2_CAP_GEOMETRY | (worker ? OFQ2_CAP_GEOMETRY_WORKER : 0);
        reset_ring();
        assert(OFQ2_DrawGeometryFan(vertices, count, cull));
        assert(reservations == 1 && commits == 1 && ofq2_pending);
        assert(committed_words == (worker ? 1 : (count + 2) / 3) + 5 * count + 2 * (count - 2));
        unsigned pos = 0, uploaded = 0;
        while (uploaded < count) {
            uint32_t header = ring_words[pos++];
            assert((header >> 24) == 0x59);
            unsigned words = header & 0xffffffu;
            assert(words && words % 5 == 0 && words <= (worker ? 160 : 15));
            assert(uploaded + words / 5 <= count);
            assert(!memcmp(ring_words + pos, vertices + uploaded * 5, words * sizeof(uint32_t)));
            pos += words; uploaded += words / 5;
        }
        for (unsigned i = 1; i + 1 < count; i++) {
            assert(ring_words[pos++] == 0x5b000001);
            assert(ring_words[pos++] == ((i << 5) | ((i + 1) << 10) | (cull << 15)));
        }
        assert(pos == committed_words && ring_words[pos] == 0xa5a5a5a5);
    }
    reset_ring();
    assert(!OFQ2_DrawGeometryFan(NULL, 3, 0));
    assert(!OFQ2_DrawGeometryFan(vertices, 2, 0));
    assert(!OFQ2_DrawGeometryFan(vertices, 33, 0));
    assert(!OFQ2_DrawGeometryFan(vertices, 3, 3));
    ofq2_tristate_dirty = true; assert(!OFQ2_DrawGeometryFan(vertices, 3, 0));
    ofq2_tristate_dirty = false; ofq2_objstate_valid = false;
    assert(!OFQ2_DrawGeometryFan(vertices, 3, 0));
    ofq2_objstate_valid = true; ofq2_caps = OFQ2_CAP_GEOMETRY_WORKER;
    assert(!OFQ2_DrawGeometryFan(vertices, 3, 0));
    assert(!reservations && !commits && !ofq2_pending);
}

static void test_clip_packets_unchanged(void)
{
    uint32_t vertices[24];
    for (unsigned i = 0; i < 24; i++) vertices[i] = 0x091f0000 + i;
    ofq2_ready = ofq2_tristate_valid = ofq2_objstate_valid = true;
    ofq2_tristate_dirty = false;
    ofq2_caps = OFQ2_CAP_GEOMETRY | OFQ2_CAP_GEOMETRY_WORKER;
    for (unsigned count = 0; count <= 3; count++) {
        reset_ring();
        assert(OFQ2_DrawClipIndexed(count ? vertices : NULL, count, 0x1337));
        unsigned pos = 0;
        for (unsigned i = 0; i < count; i++) {
            assert(ring_words[pos++] == 0x56000008);
            assert(!memcmp(ring_words + pos, vertices + i * 8, 8 * sizeof(uint32_t)));
            pos += 8;
        }
        assert(ring_words[pos++] == 0x5b000001 && ring_words[pos++] == 0x1337);
        assert(pos == committed_words && ring_words[pos] == 0xa5a5a5a5);
    }
}

int main(void)
{
    test_capabilities();
    test_fan_packets();
    test_clip_packets_unchanged();
    puts("PASS: real geometry capability dependencies; worker 3..32-vertex packet batching; inline three-vertex limit; fan ordering/culling/payloads; invalid state; unchanged clip uploads");
    return 0;
}
