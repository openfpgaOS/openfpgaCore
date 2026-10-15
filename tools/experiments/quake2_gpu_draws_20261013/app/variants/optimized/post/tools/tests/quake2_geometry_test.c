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
    for (unsigned status = 0; status < 16; status++)
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
            (dependencies == 7 && (status & 3) == 3));
        assert(!!OFQ2_Supports(OFQ2_CAP_NATIVE_MESH) ==
            (dependencies == 7 && (status & 7) == 7));
        assert(!!OFQ2_Supports(OFQ2_CAP_PARTICLES) ==
            (dependencies == 7 && (status & 11) == 11));
        OFQ2_Shutdown();
        assert(!OFQ2_Supports(OFQ2_CAP_GEOMETRY_WORKER));
    }
}

static void test_particle_packets(void)
{
    setup(4096, 4);
    ofq2_caps = OFQ2_CAP_PARTICLES;
    uint32_t context[16], records[65 * 5];
    for (unsigned i = 0; i < 16; ++i) context[i] = 0x17482738u + i;
    for (unsigned i = 0; i < 65 * 5; ++i) records[i] = 0x9182a937u + 83u * i;
    unsigned sizes[] = {1, 31, 32, 33, 63, 64};
    for (unsigned i = 0; i < sizeof(sizes)/sizeof(sizes[0]); ++i) {
        unsigned n = sizes[i];
        reset_ring(); ofq2_tristate_valid = ofq2_objstate_valid = true;
        ofq2_tristate_dirty = false;
        unsigned before = finish_count;
        assert(OFQ2_DrawParticles(context, records, n));
        assert(reservations == 1 && commits == 1 && committed_words == 18 + 5*n);
        assert(ring_words[0] == 0x61000010u && !memcmp(ring_words+1, context, sizeof(context)));
        assert(ring_words[17] == (0x62000000u | (5*n)));
        assert(!memcmp(ring_words+18, records, n*5*sizeof(uint32_t)));
        assert(ring_words[18+5*n] == 0xa5a5a5a5u);
        assert(ofq2_tristate_dirty && !ofq2_objstate_valid && ofq2_pending);
        assert(finish_count == before);
    }
    reset_ring(); ofq2_tristate_valid = true; ofq2_tristate_dirty = false;
    assert(!OFQ2_DrawParticles(NULL, records, 1));
    assert(!OFQ2_DrawParticles(context, NULL, 1));
    assert(!OFQ2_DrawParticles(context, records, 0));
    assert(!OFQ2_DrawParticles(context, records, 65));
    ofq2_tristate_dirty = true; assert(!OFQ2_DrawParticles(context, records, 1));
    ofq2_tristate_dirty = false; ofq2_tristate_valid = false;
    assert(!OFQ2_DrawParticles(context, records, 1));
    ofq2_tristate_valid = true; ofq2_caps = OFQ2_CAP_NATIVE_MESH;
    assert(!OFQ2_DrawParticles(context, records, 1));
    assert(!reservations && !commits && !ofq2_pending);
}

static void test_native_packets(void)
{
    setup(4096, 4);
    ofq2_caps = OFQ2_CAP_NATIVE_MESH;
    ofq2_tristate_valid = ofq2_objstate_valid = true;
    ofq2_tristate_dirty = false;
    uint32_t addr, second, job[OFQ2_NATIVE_WORDS];
    unsigned before = finish_count;
    assert(OFQ2_NativeReserve(77, &addr) && addr == 4016 && _of_tex_fast_size == 4016 && _of_tex_fast_top == 0);
    assert(finish_count == before + 1);
    for (unsigned i = 0; i < OFQ2_NATIVE_WORDS; ++i) job[i] = 0x12345678u + 337u * i;
    OFQ2_NativeUpload(addr, job, 19);
    assert(!memcmp(fast_bytes + addr, job, 76));
    assert(OFQ2_NativeReserve(96, &second) && second == 3920 && _of_tex_fast_size == 3920);
    unsigned uploads = upload_count;
    OFQ2_NativeUpload(3916, job, 1); OFQ2_NativeUpload(4096, job, 1);
    assert(upload_count == uploads);
    before = finish_count;
    assert(!OFQ2_NativeReserve(4096, &addr));
    assert(!OFQ2_NativeReserve(0, &addr));
    assert(!OFQ2_NativeReserve(0xfffffff0u, &addr));
    assert(!OFQ2_NativeReserve(16, NULL));
    assert(_of_tex_fast_top == 0 && _of_tex_fast_size == 3920 && finish_count == before);
    for (unsigned n = 0; n < 17; ++n) {
        reset_ring(); ofq2_objstate_valid = true;
        assert(OFQ2_DrawNativeMesh(job));
        assert(reservations == 1 && commits == 1 && committed_words == 165);
        assert(ring_words[0] == 0x600000a4u && !memcmp(ring_words + 1, job, sizeof(job)));
        assert(!ofq2_objstate_valid && ofq2_pending);
        assert(finish_count == before); /* no per-model wait */
    }
    reset_ring(); assert(!OFQ2_DrawNativeMesh(NULL));
    ofq2_tristate_dirty = true; assert(!OFQ2_DrawNativeMesh(job));
    ofq2_tristate_dirty = false; ofq2_tristate_valid = false;
    assert(!OFQ2_DrawNativeMesh(job));
    ofq2_tristate_valid = true; ofq2_caps = OFQ2_CAP_GEOMETRY_WORKER;
    assert(!OFQ2_DrawNativeMesh(job)); assert(!OFQ2_NativeReserve(16, &addr));
    assert(!reservations && !commits);
    puts("PASS: native status dependency, atomic shared allocation, bounds, exact 164-word snapshot, legacy object invalidation and no per-model fence");
}

static void test_native_map_lifecycle(void)
{
    setup(4096, 4); ofq2_caps = OFQ2_CAP_NATIVE_MESH;
    uint32_t a[16], b[32], geometry[128];
    for (unsigned i = 0; i < 16; ++i) a[i] = 0x10001u * i;
    for (unsigned i = 0; i < 32; ++i) b[i] = 0x330055u * i;
    memset(geometry, 0x93, sizeof(geometry));
    ofq2_texture_t ta, tb;
    OFQ2_TexCreate(&ta, a, 8, 4, sizeof(a));
    uint32_t addr; assert(OFQ2_NativeReserve(256, &addr) && addr == 3840);
    OFQ2_NativeUpload(addr, geometry, 64);
    OFQ2_TexCreate(&tb, b, 8, 8, sizeof(b));
    assert(ta.addr == 0 && tb.addr == 64 && _of_tex_fast_top == 192);
    unsigned texture_generation = ofq2_tex_generation, epoch = OFQ2_NativeGeneration();
    unsigned before = finish_count;
    expected_live_top = _of_tex_fast_top; pending = 1;
    OFQ2_NativeReset();
    assert(!pending && finish_count == before + 1 && OFQ2_NativeGeneration() != epoch);
    assert(ofq2_tex_generation == texture_generation && _of_tex_fast_top == 192 && _of_tex_fast_size == 4096);
    assert(!memcmp(fast_bytes + ta.addr, a, sizeof(a)) && !memcmp(fast_bytes + tb.addr, b, sizeof(b)));
    unsigned uploads = upload_count;
    OFQ2_NativeUpload(addr, geometry, 64); /* retired map's old allocation */
    assert(upload_count == uploads);
    assert(OFQ2_NativeReserve(512, &addr) && addr == 3584);
    OFQ2_NativeUpload(addr, geometry, 128);
    assert(!memcmp(fast_bytes + ta.addr, a, sizeof(a)) && !memcmp(fast_bytes + tb.addr, b, sizeof(b)));
    epoch = OFQ2_NativeGeneration(); OFQ2_TexReset();
    assert(_of_tex_fast_top == 0 && _of_tex_fast_size == 4096 && OFQ2_NativeGeneration() != epoch);
    assert(ofq2_tex_generation == texture_generation + 1);
    ofq2_native_generation = UINT32_MAX; OFQ2_NativeReset();
    assert(OFQ2_NativeGeneration() == 1);
    ofq2_caps = OFQ2_CAP_GEOMETRY_WORKER; before = finish_count; epoch = OFQ2_NativeGeneration();
    OFQ2_NativeReset(); assert(finish_count == before && OFQ2_NativeGeneration() == epoch);
    setup(16, 4); ofq2_caps = OFQ2_CAP_NATIVE_MESH;
    assert(!OFQ2_NativeReserve(16, &addr) && of_texture_has_fast_mem());
    setup(64, 4); ofq2_caps = OFQ2_CAP_NATIVE_MESH;
    assert(OFQ2_NativeReserve(48, &addr) && addr == 16 && of_texture_has_fast_mem());
    epoch = OFQ2_NativeGeneration(); OFQ2_TexInit();
    assert(_of_tex_fast_size == 64 && _of_tex_fast_top == 0 && OFQ2_NativeGeneration() != epoch);
    puts("PASS: top-down native/low texture isolation, fenced map recycling, native-only generation, retained texture bytes/handles, reset/init, wrap and nonzero fast-tier routing");
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

static void test_cached_packet_batches(void)
{
    uint32_t vertices[160], packet[221];
    for (unsigned i = 0; i < 160; i++) vertices[i] = 0x91230000u + 37u * i;
    ofq2_ready = ofq2_tristate_valid = ofq2_objstate_valid = true;
    ofq2_tristate_dirty = false;
    ofq2_caps = OFQ2_CAP_GEOMETRY | OFQ2_CAP_GEOMETRY_WORKER;
    for (unsigned n = 3; n <= 32; n++)
    for (unsigned cull = 0; cull <= 2; cull++) {
        reset_ring(); assert(OFQ2_DrawGeometryFan(vertices, n, cull));
        unsigned words = committed_words;
        memcpy(packet, ring_words, words * sizeof(uint32_t));
        ofq2_geometry_packet_t packets[32];
        unsigned count = 512 / words;
        for (unsigned i = 0; i < count; i++) packets[i] = (ofq2_geometry_packet_t){packet,n};
        reset_ring(); assert(OFQ2_DrawGeometryPackets(packets,count));
        assert(reservations == 1 && commits == 1 && ofq2_pending);
        assert(committed_words == count * words);
        for (unsigned i = 0; i < count; i++)
            assert(!memcmp(ring_words + i*words,packet,words*sizeof(uint32_t)));
        assert(ring_words[committed_words] == 0xa5a5a5a5);
    }
    uint32_t tri[18] = {0}, quad[25] = {0};
    ofq2_geometry_packet_t packets[32];
    for (unsigned i = 0; i < 23; i++) packets[i] = (ofq2_geometry_packet_t){i<14?quad:tri,i<14?4:3};
    reset_ring(); assert(OFQ2_DrawGeometryPackets(packets,23));
    assert(committed_words == 512 && reservations == 1 && commits == 1);
    packets[23] = (ofq2_geometry_packet_t){tri,3};
    reset_ring(); assert(!OFQ2_DrawGeometryPackets(packets,24));
    assert(!OFQ2_DrawGeometryPackets(NULL,1));
    assert(!OFQ2_DrawGeometryPackets(packets,0));
    assert(!OFQ2_DrawGeometryPackets(packets,33));
    packets[1].words = NULL; assert(!OFQ2_DrawGeometryPackets(packets,2));
    packets[1].words = tri; packets[1].count = 2; assert(!OFQ2_DrawGeometryPackets(packets,2));
    packets[1].count = 33; assert(!OFQ2_DrawGeometryPackets(packets,2));
    packets[1].count = 3;
    ofq2_caps = OFQ2_CAP_GEOMETRY; assert(!OFQ2_DrawGeometryPackets(packets,1));
    ofq2_caps = OFQ2_CAP_GEOMETRY_WORKER; assert(!OFQ2_DrawGeometryPackets(packets,1));
    ofq2_caps = OFQ2_CAP_GEOMETRY | OFQ2_CAP_GEOMETRY_WORKER;
    ofq2_tristate_dirty = true; assert(!OFQ2_DrawGeometryPackets(packets,1));
    ofq2_tristate_dirty = false; ofq2_objstate_valid = false; assert(!OFQ2_DrawGeometryPackets(packets,1));
    ofq2_objstate_valid = true; ofq2_tristate_valid = false; assert(!OFQ2_DrawGeometryPackets(packets,1));
    ofq2_tristate_valid = true; ofq2_ready = false; assert(!OFQ2_DrawGeometryPackets(packets,1));
    ofq2_ready = true;
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
    test_particle_packets();
    test_fan_packets();
    test_clip_packets_unchanged();
    test_cached_packet_batches();
    test_native_packets();
    test_native_map_lifecycle();
    puts("PASS: real geometry capability dependencies; worker 3..32-vertex packet batching; inline three-vertex limit; fan ordering/culling/payloads; invalid state; unchanged clip uploads");
    return 0;
}
