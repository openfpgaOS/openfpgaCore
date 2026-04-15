/*
 * openfpgaOS HAL initialization
 */

#include "hal.h"

void of_init(void) {
    uint32_t features = HW_FEATURES;

    of_cache_init();
    of_timer_init();
    of_video_init();
    of_term_init();
    of_input_init();
    /* of_disk_init must run before of_file_init: the latter's
     * bridge warmup DMA only fires when the bridge is the active
     * backend, and that's decided here. */
    of_disk_init();
    of_file_init();

    if (features & HW_FEAT_MIXER)
        of_audio_init();
    if (features & HW_FEAT_SAVE_SLOTS)
        of_save_init();
    if (features & HW_FEAT_ANALOGIZER)
        of_analogizer_init();
    if (features & HW_FEAT_LINK)
        of_link_init(LINK_MODE_SLAVE);
}
