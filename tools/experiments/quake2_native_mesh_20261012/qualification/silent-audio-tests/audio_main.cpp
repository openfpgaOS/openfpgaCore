#include "Vaudio_selection.h"
#include "verilated.h"
#include <cassert>
#include <cstdint>
#include <cstdio>

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Vaudio_selection top;
    unsigned div = 0, cycles = 0, rises = 0, falls = 0;
    const unsigned lengths[] = {9, 4096, 11, 4096};
    for (unsigned phase = 0; phase < 4; ++phase) {
        unsigned phase_rises = 0, phase_falls = 0;
        for (unsigned n = 0; n < lengths[phase]; ++n, ++cycles) {
            bool reset_n = (phase & 1) != 0;
            top.reset_n_cpu_media = reset_n;
            top.clk_cpu = (cycles % 5) < 2;
            top.periph_audio_sample_wr = cycles & 1;
            top.mixer_sample_wr = (cycles >> 1) & 1;
            top.periph_audio_sample_data = cycles * 0x9182635du;
            top.mixer_sample_data = ~top.periph_audio_sample_data;
            top.clk_core_12288 = 0;
            top.eval();
            assert(top.audio_mclk == 0);
            unsigned before = div >> 7;
            div = reset_n ? ((div + 1) & 255) : 0;
            top.clk_core_12288 = 1;
            top.eval();
            assert(top.audio_mclk == 1);
#if EXPECT_MODE == 2
            assert(top.audio_fifo_level == 1023 && top.audio_fifo_full == 1);
            assert(top.audio_dac == 0);
            assert(top.audio_lrck == (div >> 7));
            if (reset_n && (div >> 7) != before) {
                if (div >> 7) ++phase_rises; else ++phase_falls;
            }
#else
            uint32_t data = EXPECT_MODE ? top.mixer_sample_data : top.periph_audio_sample_data;
            bool wr = EXPECT_MODE ? top.mixer_sample_wr : top.periph_audio_sample_wr;
            assert(top.audio_fifo_level == (data & 1023));
            assert(top.audio_fifo_full == wr);
            assert(top.audio_lrck == reset_n);
            assert(top.audio_dac == (data >> 31));
#endif
        }
#if EXPECT_MODE == 2
        if (phase & 1) assert(phase_rises == 16 && phase_falls == 16);
        rises += phase_rises;
        falls += phase_falls;
#endif
    }
    top.final();
    printf("PASS: audio mode %d, %u audio cycles, reset/warm reset; LRCK rises=%u falls=%u\n",
        EXPECT_MODE, cycles, rises, falls);
    return 0;
}
