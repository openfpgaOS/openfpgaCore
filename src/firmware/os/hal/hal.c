/*
 * openfpgaOS HAL initialization
 */

#include "hal.h"

void of_init(void) {
    of_cache_init();
    of_timer_init();
    of_video_init();
    of_term_init();
    of_input_init();
    of_audio_init();
    of_file_init();
    of_save_init();
    of_analogizer_init();
    of_link_init();
}
