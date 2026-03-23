/*
 * dlmalloc configuration for openfpgaOS
 * Bare-metal RISC-V, no threads, no mmap, sbrk via ecall
 */

#ifndef DLMALLOC_CONFIG_H
#define DLMALLOC_CONFIG_H

/* No mmap — we only have sbrk (brk syscall) */
#define HAVE_MMAP 0
#define HAVE_MREMAP 0

/* No threads */
#define USE_LOCKS 0

/* No system headers available */
#define LACKS_UNISTD_H
#define LACKS_SYS_PARAM_H
#define LACKS_SYS_MMAN_H
#define LACKS_FCNTL_H
#define LACKS_SYS_TYPES_H
#define LACKS_ERRNO_H
#define LACKS_SCHED_H
#define LACKS_TIME_H
#define LACKS_STRINGS_H

/* We provide our own sbrk via brk syscall */
#define HAVE_MORECORE 1
#define MORECORE __of_dl_sbrk
#define MORECORE_CONTIGUOUS 1

/* Abort = infinite loop (no OS to return to) */
#define ABORT  do { __asm__ volatile("ebreak"); } while(0)
#define ABORT_ON_ASSERT_FAILURE 0

/* Don't try to set errno */
#define MALLOC_FAILURE_ACTION

/* Error codes for posix_memalign */
#define EINVAL 22
#define ENOMEM 12

/* No security overhead needed on embedded */
#define INSECURE 1

/* Small footprint */
#define DEFAULT_TRIM_THRESHOLD ((size_t)2U * (size_t)1024U * (size_t)1024U)

#include <stddef.h>
#include <string.h>

/* sbrk implementation using brk syscall */
static inline void *__of_dl_sbrk(intptr_t increment) {
    register long a7 __asm__("a7") = 214; /* SYS_brk */
    register long a0 __asm__("a0") = 0;

    /* Get current brk */
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7) : "memory");
    uintptr_t cur = (uintptr_t)a0;

    if (increment == 0)
        return (void *)cur;

    /* Set new brk */
    uintptr_t new_brk = cur + increment;
    a0 = (long)new_brk;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7) : "memory");

    if ((uintptr_t)a0 != new_brk)
        return (void *)-1;  /* MFAIL */

    return (void *)cur;
}

#endif /* DLMALLOC_CONFIG_H */
