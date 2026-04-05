/*
 * openfpgaOS libc Jump Table
 *
 * Populates a struct of function pointers from musl libc at a fixed
 * address. Apps call standard C functions through this table, so they
 * don't need to statically link musl themselves.
 *
 * The table lives at 0x7C00 in BRAM — fast access, no D-cache pollution.
 * Must not overlap app BRAM (0x2000–0x7C00) or trap stack (0x7E00–0x8000).
 */

#include "libc_table.h"
#include "../../api/of_libc.h"

/* musl headers for function declarations */
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <ctype.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>


void libc_table_init(void) {
    struct of_libc_table *t = (struct of_libc_table *)OF_LIBC_ADDR;

    t->magic    = OF_LIBC_MAGIC;
    t->version  = OF_LIBC_VERSION;
    t->count    = OF_LIBC_COUNT;
    t->_reserved = 0;

    /* memory — use dlmalloc instead of musl's malloc */
    extern void *dlmalloc(size_t);
    extern void  dlfree(void *);
    extern void *dlrealloc(void *, size_t);
    extern void *dlcalloc(size_t, size_t);
    t->malloc   = (void *(*)(size_t))dlmalloc;
    t->free     = (void (*)(void *))dlfree;
    t->realloc  = (void *(*)(void *, size_t))dlrealloc;
    t->calloc   = (void *(*)(size_t, size_t))dlcalloc;

    /* string */
    t->memcpy   = memcpy;
    t->memset   = memset;
    t->memmove  = memmove;
    t->memcmp   = memcmp;
    t->strlen   = strlen;
    t->strcmp    = strcmp;
    t->strncmp  = strncmp;
    t->strcpy   = strcpy;
    t->strncpy  = strncpy;
    t->strstr   = strstr;
    t->strchr   = strchr;
    t->strrchr  = strrchr;

    /* stdio */
    t->snprintf  = snprintf;
    t->vsnprintf = vsnprintf;
    t->printf    = printf;
    t->fprintf   = (int (*)(void *, const char *, ...))fprintf;
    t->stdout_ptr = stdout;
    t->stderr_ptr = stderr;

    /* stdlib */
    t->abs      = abs;
    t->labs     = labs;
    t->atoi     = atoi;
    t->strtol   = strtol;
    t->strtoul  = strtoul;
    t->qsort    = qsort;
    t->bsearch  = bsearch;
    t->rand     = rand;
    t->srand    = srand;

    /* math */
    t->sinf     = sinf;
    t->cosf     = cosf;
    t->tanf     = tanf;
    t->asinf    = asinf;
    t->acosf    = acosf;
    t->atan2f   = atan2f;
    t->sqrtf    = sqrtf;
    t->fmodf    = fmodf;
    t->floorf   = floorf;
    t->ceilf    = ceilf;
    t->roundf   = roundf;
    t->fabsf    = fabsf;
    t->fmaxf    = fmaxf;
    t->fminf    = fminf;
    t->powf     = powf;
    t->logf     = logf;
    t->log2f    = log2f;
    t->expf     = expf;
    t->sin      = sin;
    t->cos      = cos;
    t->sqrt     = sqrt;

    /* -- string extended -- */
    t->strcat   = strcat;
    t->strncat  = strncat;
    t->strdup   = strdup;
    t->memchr   = memchr;
    t->strtok   = strtok;
    t->strspn   = strspn;
    t->strcspn  = strcspn;

    /* -- stdio extended -- */
    t->sprintf  = sprintf;
    t->vsprintf = vsprintf;
    t->sscanf   = sscanf;
    t->fopen    = (void *(*)(const char *, const char *))fopen;
    t->fclose   = (int (*)(void *))fclose;
    t->fread    = (size_t (*)(void *, size_t, size_t, void *))fread;
    t->fwrite   = (size_t (*)(const void *, size_t, size_t, void *))fwrite;
    t->fseek    = (int (*)(void *, long, int))fseek;
    t->ftell    = (long (*)(void *))ftell;
    t->fgets    = (char *(*)(char *, int, void *))fgets;
    t->fputs    = (int (*)(const char *, void *))fputs;

    /* -- stdlib extended -- */
    t->atof     = atof;
    t->strtod   = strtod;
    t->strtoll  = strtoll;
    t->strtoull = strtoull;

    /* -- ctype -- */
    t->toupper  = toupper;
    t->tolower  = tolower;
    t->isalpha  = isalpha;
    t->isdigit  = isdigit;
    t->isalnum  = isalnum;
    t->isspace  = isspace;
    t->isupper  = isupper;
    t->islower  = islower;
    t->isprint  = isprint;

    /* -- POSIX I/O -- */
    t->open     = open;
    t->close    = close;
    t->read     = (int (*)(int, void *, unsigned int))read;
    t->write    = (int (*)(int, const void *, unsigned int))write;
    t->lseek    = (long long (*)(int, long long, int))lseek;
    t->fstat    = (int (*)(int, void *))fstat;
    t->stat     = (int (*)(const char *, void *))stat;

    /* Disable stdio buffering so printf() output appears immediately.
     * Without this, musl's stdout is line-buffered and holds output
     * until a newline or the buffer fills (~1KB). */
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

}
