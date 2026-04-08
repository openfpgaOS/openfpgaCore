/*
 * openfpgaOS Syscall Interface
 *
 * Two namespaces share the same ecall instruction:
 *
 *   1. Linux-compatible syscall numbers (used by musl/POSIX). EIDs in
 *      this range follow the Linux ABI: a7 = number, return in a0.
 *
 *   2. openfpgaOS SBI vendor extensions (EID >= OF_EID_BASE_VALUE,
 *      defined in api/of_syscall_numbers.h). These follow the RISC-V
 *      SBI calling convention: a7 = EID, a6 = FID, returns
 *      sbiret { error, value } in {a0, a1}.
 *
 * The kernel detects the EID range and routes accordingly.
 */

#ifndef OFOS_SYSCALL_H
#define OFOS_SYSCALL_H

#include <stdint.h>
#include "../../api/of_syscall_numbers.h"
#include "../../api/of_syscall.h"

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
#define SYS_clock_gettime64    403  /* riscv32 — musl sends this, not 113 */
#define SYS_clock_getres64     406  /* riscv32 — musl sends this, not 114 */
#define SYS_clock_nanosleep_time64 407 /* riscv32 — musl sends this, not 115 */
#define SYS_futex           422     /* musl FILE locking */
#define SYS_setitimer       103
#define SYS_rt_sigaction    134
#define SYS_rt_sigprocmask  135
#define SYS_getpid          172
#define SYS_gettid          178
#define SYS_brk             214
#define SYS_munmap          215
#define SYS_mmap2           222
#define SYS_mprotect        226
#define SYS_madvise         233
#define SYS_getdents64  61
#define SYS_riscv_flush_icache 259

/* ======================================================================
 * Syscall dispatch (called from trap handler)
 *
 * Argument order matches the assembly trap handler in start.S, which
 * loads them straight from the saved trap frame into a0..a7 with no
 * shuffling. The C ABI returns small structs in a0/a1, which the trap
 * handler then writes back to the user's saved a0/a1 slots.
 * ====================================================================== */

struct of_sbiret syscall_dispatch(long a0, long a1, long a2, long a3,
                                  long a4, long a5, long fid, long eid);

void syscall_init(uintptr_t heap_start);

/* Register a file slot mapping (slot_id → filename).
 * Called by the loader after parsing the instance JSON. */
void file_slot_register(uint32_t slot_id, const char *filename);

#endif /* OFOS_SYSCALL_H */
