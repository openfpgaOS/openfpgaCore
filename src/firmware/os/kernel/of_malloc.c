/*
 * of_malloc.c — dlmalloc for the openfpgaOS kernel
 *
 * Provides dlmalloc_malloc/dlmalloc_free/dlmalloc_realloc/dlmalloc_calloc,
 * exposed to apps via syscalls. Uses sys_brk directly for heap expansion.
 *
 * Doug Lea's malloc, MIT-0 license.
 */

#include <stdint.h>
#include <stddef.h>
#include <string.h>

/* ======================================================================
 * dlmalloc configuration for kernel context
 * ====================================================================== */

#define HAVE_MMAP 0
#define HAVE_MREMAP 0
#define USE_LOCKS 0

#define LACKS_UNISTD_H
#define LACKS_SYS_PARAM_H
#define LACKS_SYS_MMAN_H
#define LACKS_FCNTL_H
#define LACKS_SYS_TYPES_H
#define LACKS_ERRNO_H
#define LACKS_SCHED_H
#define LACKS_TIME_H
#define LACKS_STRINGS_H

#define HAVE_MORECORE 1
#define MORECORE __kernel_sbrk
#define MORECORE_CONTIGUOUS 1

#define ABORT  do { for(;;){} } while(0)
#define ABORT_ON_ASSERT_FAILURE 0
#define MALLOC_FAILURE_ACTION

#define INSECURE 1

/* Force trimming even though HAVE_MMAP is 0.
 * Without this, dlmalloc sets trim threshold to MAX_SIZE_T and never
 * returns memory via sbrk(-N). Large alloc/free cycles (e.g. 48MB probe)
 * leave brk at the ceiling permanently, exhausting SDRAM. */
#define DEFAULT_TRIM_THRESHOLD ((size_t)256U * (size_t)1024U)

/* Use DL prefix to avoid conflict with musl's malloc */
#define USE_DL_PREFIX 1

#define EINVAL 22
#define ENOMEM 12

/* Kernel-side sbrk: calls sys_brk directly (no ecall needed) */
extern uintptr_t current_brk;

/* Heap cannot grow past the save region (0x13C00000).
 * Stack is above that at 0x13F80000. */
#define HEAP_LIMIT 0x13C00000

__attribute__((noinline))
static void *__kernel_sbrk(intptr_t increment) {
    volatile uintptr_t *brk = &current_brk;
    uintptr_t cur = *brk;
    if (increment == 0)
        return (void *)cur;
    uintptr_t new_brk = cur + increment;
    if (new_brk < cur && increment > 0)
        return (void *)-1;  /* overflow */
    if (new_brk > HEAP_LIMIT)
        return (void *)-1;  /* out of memory */
    *brk = new_brk;
    return (void *)cur;
}

#include "dlmalloc.c"

/* Override musl's malloc/free/realloc/calloc with dlmalloc.
 * musl's internal code (fopen, fprintf, etc.) calls malloc directly,
 * not through the jump table. Without these overrides, musl uses its
 * own malloc which conflicts with dlmalloc over sys_brk, corrupting
 * both heaps. */
void *malloc(size_t n)              { return dlmalloc(n); }
void  free(void *p)                 { dlfree(p); }
void *realloc(void *p, size_t n)    { return dlrealloc(p, n); }
void *calloc(size_t m, size_t n)    { return dlcalloc(m, n); }
