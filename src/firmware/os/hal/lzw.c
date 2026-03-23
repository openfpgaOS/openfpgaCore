/*
 * openfpgaOS LZW Compression
 * Ported from PocketDukeNukem/Engine/src/filesystem.c (Build engine format).
 * Uses safe byte-level reads for RISC-V alignment.
 * Work buffers are allocated internally via malloc/free.
 */

#include "lzw.h"
#include <string.h>

/* Use dlmalloc directly — kernel HAL code runs inside the syscall handler,
 * so calling musl's malloc would issue ecall (re-entering the trap) AND
 * conflict with dlmalloc over the shared brk heap. */
extern void *dlmalloc(unsigned int);
extern void  dlfree(void *);
#define malloc dlmalloc
#define free   dlfree

#define LZWSIZE 16384

/* Safe unaligned reads/writes (byte-level, no alignment issues on RISC-V) */
static inline int32_t read_le32(const void *p) {
    const uint8_t *b = (const uint8_t *)p;
    return b[0] | (b[1]<<8) | (b[2]<<16) | (b[3]<<24);
}
static inline void write_le32(void *p, int32_t v) {
    uint8_t *b = (uint8_t *)p;
    b[0] = v; b[1] = v>>8; b[2] = v>>16; b[3] = v>>24;
}
static inline void or_le32(void *p, int32_t v) {
    write_le32(p, read_le32(p) | v);
}
static inline int16_t read_le16(const void *p) {
    const uint8_t *b = (const uint8_t *)p;
    return (int16_t)(b[0] | (b[1]<<8));
}
static inline void write_le16(void *p, int16_t v) {
    uint8_t *b = (uint8_t *)p;
    b[0] = (uint8_t)v; b[1] = (uint8_t)(v>>8);
}

int32_t of_lzw_compress(const uint8_t *in, int32_t in_len, uint8_t *out)
{
    if (!in || !out || in_len <= 0) return -1;

    int32_t i, addr, newaddr, addrcnt, zx;
    int32_t bytecnt1, bitcnt, numbits, oneupnumbits;

    /* Allocate work buffers */
    int bufsz = LZWSIZE + (LZWSIZE >> 4);
    uint8_t *lzwbuf1 = (uint8_t *)malloc(bufsz);
    short *lzwbuf2 = (short *)malloc(bufsz * 2);
    short *lzwbuf3 = (short *)malloc(bufsz * 2);
    if (!lzwbuf1 || !lzwbuf2 || !lzwbuf3) {
        free(lzwbuf1); free(lzwbuf2); free(lzwbuf3);
        return -1;
    }

    for(i=255;i>=0;i--) { lzwbuf1[i] = (uint8_t)i; lzwbuf3[i] = (short)((i+1)&255); }
    /* Clear lzwbuf2 to all 0xFFFF (-1 as short) */
    memset(lzwbuf2, 0xFF, 256 * sizeof(short));
    /* Clear output buffer */
    memset(out, 0, ((in_len+15)+3) & ~3);

    addrcnt = 256; bytecnt1 = 0; bitcnt = (4<<3);
    numbits = 8; oneupnumbits = (1<<8);
    do
    {
        addr = in[bytecnt1];
        do
        {
            bytecnt1++;
            if (bytecnt1 == in_len) break;
            if (lzwbuf2[addr] < 0) {lzwbuf2[addr] = (short)addrcnt; break;}
            newaddr = lzwbuf2[addr];
            while (lzwbuf1[newaddr] != in[bytecnt1])
            {
                zx = lzwbuf3[newaddr];
                if (zx < 0) {lzwbuf3[newaddr] = (short)addrcnt; break;}
                newaddr = zx;
            }
            if (lzwbuf3[newaddr] == addrcnt) break;
            addr = newaddr;
        } while (addr >= 0);
        lzwbuf1[addrcnt] = in[bytecnt1];
        lzwbuf2[addrcnt] = -1;
        lzwbuf3[addrcnt] = -1;

        or_le32(&out[bitcnt>>3], addr<<(bitcnt&7));
        bitcnt += numbits;
        if ((addr&((oneupnumbits>>1)-1)) > ((addrcnt-1)&((oneupnumbits>>1)-1)))
            bitcnt--;

        addrcnt++;
        if (addrcnt > oneupnumbits) { numbits++; oneupnumbits <<= 1; }
    } while ((bytecnt1 < in_len) && (bitcnt < (in_len<<3)));

    or_le32(&out[bitcnt>>3], addr<<(bitcnt&7));
    bitcnt += numbits;
    if ((addr&((oneupnumbits>>1)-1)) > ((addrcnt-1)&((oneupnumbits>>1)-1)))
        bitcnt--;

    int32_t result;
    write_le16(&out[0], (int16_t)in_len);
    if (((bitcnt+7)>>3) < in_len)
    {
        write_le16(&out[2], (int16_t)addrcnt);
        result = (bitcnt+7)>>3;
    }
    else
    {
        write_le16(&out[2], 0);
        for(i=0;i<in_len;i++) out[i+4] = in[i];
        result = in_len+4;
    }

    free(lzwbuf1); free(lzwbuf2); free(lzwbuf3);
    return result;
}

int32_t of_lzw_uncompress(const uint8_t *in, int32_t comp_len, uint8_t *out)
{
    if (!in || !out || comp_len < 4) return -1;

    int32_t strtot, currstr, numbits, oneupnumbits;
    int32_t i, dat, leng, bitcnt, outbytecnt;

    (void)comp_len;

    strtot = (int32_t)read_le16(&in[2]);
    if (strtot == 0)
    {
        /* Uncompressed: copy raw data after 4-byte header */
        int32_t uncompleng = (int32_t)read_le16(&in[0]);
        memcpy(out, in + 4, uncompleng);
        return uncompleng;
    }

    /* Allocate work buffers */
    int bufsz = LZWSIZE + (LZWSIZE >> 4);
    uint8_t *lzwbuf1 = (uint8_t *)malloc(bufsz);
    short *lzwbuf2 = (short *)malloc(bufsz * 2);
    short *lzwbuf3 = (short *)malloc(bufsz * 2);
    if (!lzwbuf1 || !lzwbuf2 || !lzwbuf3) {
        free(lzwbuf1); free(lzwbuf2); free(lzwbuf3);
        return -1;
    }

    for(i=255;i>=0;i--) { lzwbuf2[i] = (short)i; lzwbuf3[i] = (short)i; }
    currstr = 256; bitcnt = (4<<3); outbytecnt = 0;
    numbits = 8; oneupnumbits = (1<<8);
    do
    {
        dat = ((read_le32(&in[bitcnt>>3])>>(bitcnt&7)) & (oneupnumbits-1));
        bitcnt += numbits;
        if ((dat&((oneupnumbits>>1)-1)) > ((currstr-1)&((oneupnumbits>>1)-1)))
        { dat &= ((oneupnumbits>>1)-1); bitcnt--; }

        lzwbuf3[currstr] = (short)dat;

        for(leng=0;dat>=256;leng++,dat=lzwbuf3[dat])
            lzwbuf1[leng] = (uint8_t)lzwbuf2[dat];

        out[outbytecnt++] = (uint8_t)dat;
        for(i=leng-1;i>=0;i--) out[outbytecnt++] = lzwbuf1[i];

        lzwbuf2[currstr-1] = (short)dat; lzwbuf2[currstr] = (short)dat;
        currstr++;
        if (currstr > oneupnumbits) { numbits++; oneupnumbits <<= 1; }
    } while (currstr < strtot);

    int32_t result = (int32_t)read_le16(&in[0]); /* uncompleng */

    free(lzwbuf1); free(lzwbuf2); free(lzwbuf3);
    return result;
}
