/*
 * openfpgaOS Audio Codec
 * VOC and WAV parsers with safe byte-level reads for RISC-V alignment.
 *
 * VOC parser ported from PocketDukeNukem/pocket_audio.c
 */

#include "codec.h"

/* Safe unaligned reads (byte-level, no alignment issues on RISC-V) */
static uint16_t rd16(const uint8_t *p) { return p[0] | (p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return p[0]|(p[1]<<8)|(p[2]<<16)|(p[3]<<24); }

int of_codec_parse_voc(const uint8_t *data, uint32_t size, of_codec_result_t *out)
{
    if (!data || !out || size < 0x1A)
        return -1;

    out->pcm = 0;
    out->pcm_len = 0;
    out->sample_rate = 11025;
    out->bits_per_sample = 8;
    out->channels = 1;

    /* Check "Creative Voice File\x1A" magic */
    if (data[0] != 'C' || data[1] != 'r')
        return -1;

    uint16_t data_offset = rd16(data + 0x14);
    const uint8_t *p = data + data_offset;
    const uint8_t *end = data + size;

    while (p + 4 <= end) {
        uint8_t block_type = *p;
        if (block_type == 0) break;  /* terminator */

        uint32_t block_len = p[1] | (p[2] << 8) | (p[3] << 16);
        p += 4;

        if (p + block_len > end) break;  /* truncated block */

        if (block_type == 1 && block_len >= 2) {
            /* Sound data block (original VOC format) */
            uint8_t time_constant = p[0];
            uint32_t sample_rate = 1000000 / (256 - (uint32_t)time_constant);
            out->sample_rate = sample_rate;
            out->pcm_len = block_len - 2;
            out->pcm = p + 2;
            return 0;
        }

        if (block_type == 9 && block_len >= 12) {
            /* Extended sound data block (VOC v1.20+) */
            out->sample_rate = rd32(p);
            out->bits_per_sample = p[4];
            out->channels = p[5];
            out->pcm_len = block_len - 12;
            out->pcm = p + 12;
            return 0;
        }

        p += block_len;
    }

    return -1;
}

int of_codec_parse_wav(const uint8_t *data, uint32_t size, of_codec_result_t *out)
{
    if (!data || !out || size < 44)
        return -1;

    out->pcm = 0;
    out->pcm_len = 0;
    out->sample_rate = 0;
    out->bits_per_sample = 0;
    out->channels = 0;

    /* Check "RIFF" magic */
    if (data[0] != 'R' || data[1] != 'I' || data[2] != 'F' || data[3] != 'F')
        return -1;

    /* Check "WAVE" format */
    if (data[8] != 'W' || data[9] != 'A' || data[10] != 'V' || data[11] != 'E')
        return -1;

    /* Walk chunks to find "fmt " and "data" */
    const uint8_t *p = data + 12;
    const uint8_t *end = data + size;
    int found_fmt = 0;

    while (p + 8 <= end) {
        uint32_t chunk_id = rd32(p);
        uint32_t chunk_size = rd32(p + 4);
        const uint8_t *chunk_data = p + 8;

        if (chunk_data + chunk_size > end)
            break;

        /* "fmt " = 0x20746D66 */
        if (chunk_id == 0x20746D66 && chunk_size >= 16) {
            uint16_t audio_format = rd16(chunk_data);
            if (audio_format != 1)  /* PCM only */
                return -1;
            out->channels = (uint8_t)rd16(chunk_data + 2);
            out->sample_rate = rd32(chunk_data + 4);
            out->bits_per_sample = (uint8_t)rd16(chunk_data + 14);
            found_fmt = 1;
        }

        /* "data" = 0x61746164 */
        if (chunk_id == 0x61746164) {
            if (!found_fmt)
                return -1;
            out->pcm = chunk_data;
            out->pcm_len = chunk_size;
            return 0;
        }

        p = chunk_data + chunk_size;
        /* Chunks are word-aligned */
        if (chunk_size & 1) p++;
    }

    return -1;
}
