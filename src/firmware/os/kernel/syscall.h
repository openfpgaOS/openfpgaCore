/*
 * openfpgaOS Syscall Interface
 * Linux-compatible syscall numbers + openfpgaOS HAL extensions
 */

#ifndef OFOS_SYSCALL_H
#define OFOS_SYSCALL_H

#include <stdint.h>
#include "../../api/of_syscall_numbers.h"

/* ======================================================================
 * Linux-compatible syscall numbers (RISC-V ABI)
 * Only the subset actually used by musl is implemented.
 * ====================================================================== */

#define SYS_getcwd          17
#define SYS_dup             23
#define SYS_dup3            24
#define SYS_fcntl           25
#define SYS_ioctl           29
#define SYS_mkdirat         34
#define SYS_unlinkat        35
#define SYS_renameat2       276
#define SYS_ftruncate       46
#define SYS_faccessat       48
#define SYS_openat          56
#define SYS_close           57
#define SYS_lseek           62
#define SYS_read            63
#define SYS_write           64
#define SYS_writev          66
#define SYS_readv           65
#define SYS_pread64         67
#define SYS_pwrite64        68
#define SYS_fstatat         79      /* legacy — riscv32 musl uses SYS_statx instead */
#define SYS_fstat           80      /* legacy — riscv32 musl uses SYS_statx instead */
#define SYS_exit            93
#define SYS_exit_group      94
#define SYS_set_tid_address 96
#define SYS_tkill           130
#define SYS_clock_gettime   113     /* legacy alias */
#define SYS_clock_getres    114     /* legacy alias */
#define SYS_clock_nanosleep 115     /* legacy alias */
#define SYS_statx           291     /* riscv32 fstat replacement */
#define SYS_clock_gettime64 403     /* riscv32 — musl sends this, not 113 */
#define SYS_clock_getres64  406     /* riscv32 — musl sends this, not 114 */
#define SYS_futex           422     /* musl FILE locking */
#define SYS_rt_sigaction    134
#define SYS_rt_sigprocmask  135
#define SYS_getpid          172
#define SYS_gettid          178
#define SYS_brk             214
#define SYS_munmap          215
#define SYS_mmap2           222
#define SYS_mprotect        226
#define SYS_madvise         233
#define SYS_riscv_flush_icache 259

/* ======================================================================
 * Syscall dispatch (called from trap handler)
 * ====================================================================== */

long syscall_dispatch(long n, long a0, long a1, long a2,
                      long a3, long a4, long a5);

void syscall_init(uintptr_t heap_start);

/* Register a file slot mapping (slot_id → filename).
 * Called by the loader after parsing the instance JSON. */
void file_slot_register(uint32_t slot_id, const char *filename);

#endif /* OFOS_SYSCALL_H */
