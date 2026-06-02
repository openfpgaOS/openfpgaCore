//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Audio Codec HAL
 * Parsers for VOC and WAV audio formats
 */

#ifndef OFOS_CODEC_H
#define OFOS_CODEC_H

#include <stdint.h>

typedef struct {
    const uint8_t *pcm;
    uint32_t pcm_len;
    uint32_t sample_rate;
    uint8_t  bits_per_sample;
    uint8_t  channels;
} of_codec_result_t;

/* Parse a Creative Voice File (VOC).
 * Returns 0 on success, -1 on failure. */
int of_codec_parse_voc(const uint8_t *data, uint32_t size, of_codec_result_t *out);

/* Parse a WAV/RIFF file.
 * Returns 0 on success, -1 on failure. */
int of_codec_parse_wav(const uint8_t *data, uint32_t size, of_codec_result_t *out);

#endif /* OFOS_CODEC_H */
