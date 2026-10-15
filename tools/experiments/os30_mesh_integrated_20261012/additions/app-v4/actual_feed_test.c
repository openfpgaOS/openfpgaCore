
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
static uint32_t _gpu_cmd_words, _gpu_unflushed_sync, _gpu_wrptr;
static uint32_t _gpu_ring_mask = 16383u;
static unsigned _gpu_cpu_ring, flushes, rd_reads, checks;
static uint32_t hardware_read_pointer;
#define OF_GPU_KICK_MIN_WORDS 512u
static uint32_t read_pointer(void) { ++rd_reads; return hardware_read_pointer; }
#define GPU_RING_RDPTR read_pointer()
static void _gpu_flush_cmd_stream(void) {
    ++flushes; _gpu_cmd_words = 0; _gpu_unflushed_sync = 0;
}
static inline void of_gpu_kick_now(void) {
    _gpu_flush_cmd_stream();
}
static inline void of_gpu_kick(void) {
    if (_gpu_cmd_words >= OF_GPU_KICK_MIN_WORDS || _gpu_unflushed_sync)
        _gpu_flush_cmd_stream();
}
static void feed(void) {
    if(!_gpu_cpu_ring)of_gpu_kick_now();
    else if(_gpu_cmd_words>=OF_GPU_KICK_MIN_WORDS||_gpu_unflushed_sync)
        of_gpu_kick();
    else {
        const uint32_t published=(_gpu_wrptr-(_gpu_cmd_words<<2))&_gpu_ring_mask;
        const uint32_t queued=(published-GPU_RING_RDPTR)&_gpu_ring_mask;
        if(queued<512u)of_gpu_kick_now();
    }

}

static void check(int condition) { ++checks; assert(condition); }
int main(void) {
    const unsigned occupancies[] = {0, 1, 126, 127, 128, 129, 2047, 4095};
    const unsigned staged[] = {5, 31, 511, 512, 1024};
    unsigned cases = 0, wrapped = 0;
    for (unsigned published = 0; published < 4096; ++published)
      for (unsigned oi = 0; oi < sizeof(occupancies)/sizeof(occupancies[0]); ++oi)
       for (unsigned si = 0; si < sizeof(staged)/sizeof(staged[0]); ++si)
        for (unsigned cpu = 0; cpu < 2; ++cpu)
         for (unsigned sync = 0; sync < 2; ++sync) {
            unsigned occupancy = occupancies[oi], words = staged[si];
            unsigned rd = (published + 4096 - occupancy) % 4096;
            unsigned wr = (published + words) % 4096;
            _gpu_cmd_words = words; _gpu_unflushed_sync = sync;
            _gpu_cpu_ring = cpu; _gpu_wrptr = wr * 4;
            hardware_read_pointer = rd * 4;
            flushes = rd_reads = 0;
            feed(); ++cases; wrapped += published + words >= 4096;
            unsigned should_flush = !cpu || sync || words >= 512 || occupancy < 128;
            check(flushes == should_flush);
            check(rd_reads == (unsigned)(cpu && !sync && words < 512));
            check(_gpu_cmd_words == (should_flush ? 0 : words));
         }
    printf("{\"pass\":true,\"cases\":%u,\"checks\":%u,\"wrap_cases\":%u}\n",
           cases, checks, wrapped);
    return 0;
}
