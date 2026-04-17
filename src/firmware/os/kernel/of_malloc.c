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

/* dlmalloc's default trim threshold is now harmless: the kernel heap
 * is a fixed BSS array, not a shared brk region.  Trim shrinks our
 * private kernel_brk back into the array, which is safe because no
 * other allocator touches it.  Leaving dlmalloc's default (MAX_SIZE_T,
 * i.e. trim disabled) because with a fixed-size region there's
 * nothing to return to the OS anyway. */

/* Use DL prefix to avoid conflict with musl's malloc */
#define USE_DL_PREFIX 1

#define EINVAL 22
#define ENOMEM 12

/* Dedicated kernel heap — fully separate from the app's brk.
 *
 * Previously __kernel_sbrk moved syscall.c's current_brk, which the
 * app's musl also grows via the sys_brk syscall.  syscall_init() is
 * called twice (boot, then app load) and reseeds current_brk each
 * time, but dlmalloc's internal malloc_state (top chunk, free-lists)
 * keeps its cached pointers from the first init, so after app load
 * dlmalloc would hand out memory that belongs to the app's heap
 * (state mismatch → corruption or NULL returns).  See issue.md
 * (LZW intermittent failure) for a concrete symptom.
 *
 * Giving dlmalloc its own heap region eliminates the sharing: the
 * app's current_brk is app-only, dlmalloc's kernel_brk is kernel-
 * only, and resetting one has no effect on the other.  The region
 * sits in CRAM0 .bss alongside the OS image (plenty of headroom
 * under the 1 MB OS limit).
 *
 * Sized for dlmalloc's current callers (only OF_EID_MEMORY syscalls;
 * the kernel's own musl overrides resolve to dlmalloc but the kernel
 * never actually calls malloc after the LZW static-buffer fix).  If
 * apps start relying on OF_MEMORY_FID_* heavily, bump KERNEL_HEAP_SIZE
 * — it's only constrained by the os.ld 1 MB CRAM0 budget. */
#define KERNEL_HEAP_SIZE (128u * 1024u)   /* 128 KB */

static uint8_t     kernel_heap[KERNEL_HEAP_SIZE] __attribute__((aligned(16)));
static uintptr_t   kernel_brk;            /* lazily initialised on first sbrk */

__attribute__((noinline))
static void *__kernel_sbrk(intptr_t increment) {
    if (kernel_brk == 0)
        kernel_brk = (uintptr_t)kernel_heap;
    uintptr_t cur = kernel_brk;
    if (increment == 0)
        return (void *)cur;
    uintptr_t new_brk = cur + increment;
    if (increment > 0 && new_brk < cur)
        return (void *)-1;   /* overflow */
    if (new_brk > (uintptr_t)kernel_heap + KERNEL_HEAP_SIZE)
        return (void *)-1;   /* region exhausted */
    if (new_brk < (uintptr_t)kernel_heap)
        return (void *)-1;   /* trim below start — dlmalloc bug */
    kernel_brk = new_brk;
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
