/*
 * openfpgaOS LZW Compression Utility
 * Compatible with Build engine LZW format
 */

#ifndef OFOS_LZW_H
#define OFOS_LZW_H

#include <stdint.h>

/* Compress data using Build engine LZW format.
 * Returns compressed size in bytes. */
int32_t of_lzw_compress(const uint8_t *in, int32_t in_len, uint8_t *out);

/* Uncompress data from Build engine LZW format.
 * Returns uncompressed size in bytes. */
int32_t of_lzw_uncompress(const uint8_t *in, int32_t comp_len, uint8_t *out);

#endif /* OFOS_LZW_H */
