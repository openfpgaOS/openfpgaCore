/* Actual batch owner and packet writer against a capacity-bounded ring oracle. */
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
typedef int qboolean;
#define OFQ2_CAP_VERT_TRI_RGB 1u
#define OF_HW_GPU_VERT_TRI 2u
#define OF_HW_GPU_VCOLOR 4u
#define GPU_CMD_DRAW_VERT_TRI_RGB 0x4eu
#define OF_GPU_COMMAND_STREAM_BATCH_WORDS 4095u
static qboolean ofq2_tri_batch_active, ofq2_tristate_valid, ofq2_tristate_dirty, ofq2_pending;
static uint32_t *ofq2_tri_batch_start, *ofq2_tri_batch_cursor, *ofq2_tri_batch_end;
static uint32_t buffers[2][4096], stream[65536], expected[65536];
static uint32_t * _gpu_batch_buf;
static unsigned _gpu_cmd_words, free_words, reserved, submissions, commits, waits, flushes;
static unsigned output_words, expected_words, active_buffer, low_free_requests;
static unsigned supports = 1, features = 6;
static int OFQ2_Supports(unsigned cap) { return supports & cap; }
static int of_has_feature(unsigned cap) { return (features & cap) != 0; }
static unsigned _gpu_ring_free_known(void) { return free_words * 4; }
static void publish(void)
{
    assert(!ofq2_tri_batch_start); /* owner must commit first */
    assert(output_words + _gpu_cmd_words < 65536);
    memcpy(stream + output_words, _gpu_batch_buf, _gpu_cmd_words * 4);
    output_words += _gpu_cmd_words;
    _gpu_cmd_words = 0;
    active_buffer ^= 1;
    _gpu_batch_buf = buffers[active_buffer];
    ++flushes;
}
static void _gpu_stream_reserve_words(unsigned n)
{
    assert(n && n <= 144 && _gpu_batch_buf);
    ++submissions;
    /* No speculative wait for a group larger than the available capacity. */
    if (free_words < n) { assert(n == 18); ++low_free_requests; }
    if (_gpu_cmd_words + n > 4095) publish();
    if (free_words < n) { publish(); free_words = 4095; ++waits; }
    assert(_gpu_cmd_words + n <= 4095 && n <= free_words);
    reserved = n;
}
static uint32_t *_gpu_ring_claim(void) { assert(reserved); return _gpu_batch_buf + _gpu_cmd_words; }
static void _gpu_ring_commit(unsigned n)
{
    assert(n <= reserved && n <= free_words && _gpu_cmd_words + n <= 4095);
    assert(n % 18 == 0);
    assert(_gpu_batch_buf[4095] == 0xdeadbeef);
    _gpu_cmd_words += n; free_words -= n; reserved = 0; ++commits;
}
#include "opaque_functions.inc"
static void reset(unsigned free, unsigned staged)
{
    assert(!ofq2_tri_batch_active && !ofq2_tri_batch_start);
    memset(buffers, 0, sizeof buffers);
    buffers[0][4095] = buffers[1][4095] = 0xdeadbeef;
    active_buffer = 0; _gpu_batch_buf = buffers[0];
    free_words = free; _gpu_cmd_words = staged;
    output_words = expected_words = staged;
    memset(stream, 0, sizeof stream); memset(expected, 0, sizeof expected);
    // staged prefix belongs to earlier commands, so it is part of the oracle
    output_words = 0;
    reserved = submissions = commits = waits = flushes = low_free_requests = 0;
    ofq2_tristate_valid = 1; ofq2_tristate_dirty = ofq2_pending = 0;
    supports = 1; features = 6;
}
static void draw(unsigned number)
{
    uint32_t payload[17];
    expected[expected_words++] = 0x4e000011;
    for (unsigned i=0;i<17;i++) expected[expected_words++] = payload[i] = 0xab120000 ^ (number*67+i);
    assert(OFQ2_DrawVertTriRGBPacked(payload));
}
int main(void)
{
    unsigned cases = 0, checks = 0;
    const unsigned free_cases[] = {0,1,17,18,19,35,36,37,71,72,73,143,144,145,4095};
    const unsigned stage_cases[] = {0,1,18,3950,3951,3952,4059,4077,4078,4094,4095};
    for(unsigned f=0;f<sizeof free_cases/sizeof *free_cases;f++)
    for(unsigned s=0;s<sizeof stage_cases/sizeof *stage_cases;s++)
    for(unsigned n=0;n<=37;n++)
    {
        reset(free_cases[f],stage_cases[s]);
        OFQ2_BeginOpaqueTriBatch();
        for(unsigned i=0;i<n;i++) draw(i);
        OFQ2_EndOpaqueTriBatch();
        assert(!ofq2_tri_batch_active && !ofq2_tri_batch_start);
        publish();
        assert(output_words==expected_words && !memcmp(stream,expected,expected_words*4));
        assert(ofq2_pending == (n!=0));
        ++cases; checks += expected_words;
    }
    reset(4095,0);
    OFQ2_BeginOpaqueTriBatch();
    for(unsigned i=0;i<65;i++) draw(i);
    OFQ2_EndOpaqueTriBatch();
    assert(submissions==9 && commits==9 && !waits);
    publish(); assert(output_words==expected_words && !memcmp(stream,expected,expected_words*4));
    // Non-batched calls retain immediate per-command bookkeeping.
    reset(4095,0); for(unsigned i=0;i<17;i++) draw(i);
    assert(submissions==17 && commits==17);
    publish(); assert(output_words==expected_words && !memcmp(stream,expected,expected_words*4));
    // Every capability/state refusal must leave an empty scope inert.
    for(unsigned refusal=0;refusal<5;refusal++)
    {
        reset(4095,0); OFQ2_BeginOpaqueTriBatch();
        if(refusal==0) supports=0;
        if(refusal==1) ofq2_tristate_valid=0;
        if(refusal==2) ofq2_tristate_dirty=1;
        if(refusal==3) features=2;
        if(refusal==4) features=4;
        uint32_t payload[17] = {0};
        int rc = OFQ2_DrawVertTriRGBPacked(payload);
        assert(rc == (refusal>=3)); // preserve existing raw-feature fall-through behavior
        OFQ2_EndOpaqueTriBatch();
        assert(!submissions && !commits && !_gpu_cmd_words && !ofq2_tri_batch_active);
    }
    // Scope boundaries do not change stream order, and partial chunks return
    // every unused slot rather than publishing garbage or leaving a hole.
    reset(4095,0);
    for(unsigned scope=0;scope<80;scope++)
    {
        OFQ2_BeginOpaqueTriBatch();
        for(unsigned i=0;i<scope%13;i++) draw(scope*32+i);
        OFQ2_EndOpaqueTriBatch();
        draw(scope*32+31);
    }
    publish(); assert(output_words==expected_words && !memcmp(stream,expected,expected_words*4));
    printf("PASS: %u bounded capacity/partial/empty scopes; %u word checks; 65 triangles use 9 reserve/commit pairs; refusal and mixed-scope ordering\n",cases,checks);
}
