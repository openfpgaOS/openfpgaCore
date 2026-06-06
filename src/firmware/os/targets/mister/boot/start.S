//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Startup Code
 * Sets up stack, trap handler, TLS, and jumps to OS main.
 * Handles exceptions including ecall (syscall interface).
 */

.section .text.start
.global _start

_start:
    /* Cold boot only.  We do NOT share this entry with the trap
     * handler — doing so forces us to `csrr t0, mcause` before we can
     * tell cold-boot-vs-trap apart, and that clobbers the interrupted
     * code's t0.  On return via mret the restored t0 is the mcause
     * value, which quietly corrupts any function that was holding a
     * valid pointer in t0 when the trap fired (most visibly
     * swmixer_tick, whose inner loop does `mv a3, t0` and then loads
     * from a3 — so a3 becomes 0x80000007 after any timer IRQ). */

    /* Set up stack pointer */
    lui sp, %hi(_stack_top)
    addi sp, sp, %lo(_stack_top)

    /* Enable FPU: set mstatus.FS = 11 (Dirty) -- bits [14:13] */
    li t0, (3 << 13)
    csrs mstatus, t0

    /* Set mtvec to the dedicated trap entry so t0 is preserved on
     * trap delivery (nothing between mtvec and the save at
     * _trap_entry touches any GPR). */
    la t0, _trap_entry
    csrw mtvec, t0

    /* Keep interrupts globally disabled until the kernel has zeroed BSS,
     * cleared callback pointers, and initialized the HAL.  Enabling MEIE
     * here lets a stale pending external interrupt run through irq_handler
     * while .sbss still contains previous SDRAM contents, which can turn a
     * stale callback word into a wild jump. */
    csrw mie, zero
    csrci mstatus, 0x8

    /* Initialize thread pointer (tp) for musl TLS.
     * musl's __pthread_self() returns (tp - sizeof(struct __pthread)).
     * struct __pthread is ~120 bytes on riscv32. Point tp past the
     * struct so errno writes land in our TLS BSS, not in BRAM code. */
    la tp, __tls_base
    addi tp, tp, 200    /* skip past struct __pthread */

    /* Clear BSS section */
    lui a0, %hi(_bss_start)
    addi a0, a0, %lo(_bss_start)
    lui a1, %hi(_bss_end)
    addi a1, a1, %lo(_bss_end)
clear_bss:
    bge a0, a1, done_bss
    sw zero, 0(a0)
    addi a0, a0, 4
    j clear_bss
done_bss:

    /* Jump to bootloader main (loads OS binary, then jumps to os_main) */
    jal ra, main

    /* If main returns, loop forever */
halt:
    j halt


/* ========================================
 * Trap entry - handles exceptions and ecall (syscalls)
 *
 * mcause values:
 *   4  = Load address misaligned
 *   6  = Store address misaligned
 *   11 = Environment call from M-mode (ecall = syscall)
 *
 * Trap frame layout (272 bytes, 8-byte aligned):
 *   x0-x31  (32 words) at sp+0   .. sp+124
 *   mepc    (1 word)   at sp+128
 *   mcause  (1 word)   at sp+132
 *   mtval   (1 word)   at sp+136
 *   f0-f31  (32 words) at sp+140 .. sp+264
 *
 * The register frame stays in BRAM so restore remains deterministic.
 * C handlers that can recurse deeply switch to the OS runtime stack
 * while keeping the BRAM frame pointer in s2.
 * ======================================== */
_trap_entry:
    /* Drain the CPU store buffer before any ISR-side work.  Prevents
     * a pre-trap pending SDRAM store from racing with the new stores
     * an ISR body is about to issue — observed as random single-word
     * corruption of Doom-owned memory when the timer ISR writes any
     * SDRAM-resident global under concurrent GPU/bridge bus traffic. */
    fence

    /* Save current sp to mscratch */
    csrw mscratch, sp

    /* Switch to trap stack in BRAM */
    lui sp, %hi(_stack_top)
    addi sp, sp, %lo(_stack_top)

    /* Allocate trap frame: 272 bytes */
    addi sp, sp, -272

    /* Save x1-x31.
     *
     * NOTE: x5 (= t0) MUST be saved before we clobber t0 with `csrr t0,
     * mscratch` to read the interrupted sp.  Otherwise the saved value
     * at frame[5] is the outer sp (not the outer t0), and on mret the
     * interrupted code returns with t0 = sp — quietly corrupting every
     * function that was mid-flight when the trap fired.  (Spent many
     * hours chasing phantom "load access fault at 0x8000" in
     * swmixer_tick because its t0-based accum_l iterator was
     * restored wrong after each timer IRQ.) */
    sw x1,   4(sp)
    sw x5,  20(sp)      /* save t0 BEFORE we overwrite it below */
    csrr t0, mscratch
    sw t0,   8(sp)      /* x2 (original sp) */
    sw x3,  12(sp)
    sw x4,  16(sp)
    sw x6,  24(sp)
    sw x7,  28(sp)
    sw x8,  32(sp)
    sw x9,  36(sp)
    sw x10, 40(sp)
    sw x11, 44(sp)
    sw x12, 48(sp)
    sw x13, 52(sp)
    sw x14, 56(sp)
    sw x15, 60(sp)
    sw x16, 64(sp)
    sw x17, 68(sp)
    sw x18, 72(sp)
    sw x19, 76(sp)
    sw x20, 80(sp)
    sw x21, 84(sp)
    sw x22, 88(sp)
    sw x23, 92(sp)
    sw x24, 96(sp)
    sw x25, 100(sp)
    sw x26, 104(sp)
    sw x27, 108(sp)
    sw x28, 112(sp)
    sw x29, 116(sp)
    sw x30, 120(sp)
    sw x31, 124(sp)

    /* Save CSRs */
    csrr t0, mepc
    sw t0, 128(sp)
    csrr t0, mcause
    sw t0, 132(sp)
    csrr t0, mtval
    sw t0, 136(sp)

    sw zero, 0(sp)

    /* Save f0-f31 */
    fsw f0,  140(sp)
    fsw f1,  144(sp)
    fsw f2,  148(sp)
    fsw f3,  152(sp)
    fsw f4,  156(sp)
    fsw f5,  160(sp)
    fsw f6,  164(sp)
    fsw f7,  168(sp)
    fsw f8,  172(sp)
    fsw f9,  176(sp)
    fsw f10, 180(sp)
    fsw f11, 184(sp)
    fsw f12, 188(sp)
    fsw f13, 192(sp)
    fsw f14, 196(sp)
    fsw f15, 200(sp)
    fsw f16, 204(sp)
    fsw f17, 208(sp)
    fsw f18, 212(sp)
    fsw f19, 216(sp)
    fsw f20, 220(sp)
    fsw f21, 224(sp)
    fsw f22, 228(sp)
    fsw f23, 232(sp)
    fsw f24, 236(sp)
    fsw f25, 240(sp)
    fsw f26, 244(sp)
    fsw f27, 248(sp)
    fsw f28, 252(sp)
    fsw f29, 256(sp)
    fsw f30, 260(sp)
    fsw f31, 264(sp)

    /* Check mcause: is this an ecall (syscall)? */
    lw t0, 132(sp)      /* mcause */
    bltz t0, _irq_handler      /* bit 31 set = interrupt */
    li t1, 11           /* Environment call from M-mode */
    beq t0, t1, _ecall_handler

    /* Not an ecall - try misaligned access emulation.
     * Use the large OS runtime stack for the C helper; the trap frame
     * itself remains in BRAM. */
    mv s2, sp
    lui sp, %hi(_runtime_stack_top)
    addi sp, sp, %lo(_runtime_stack_top)
    mv a0, s2
    la t0, handle_misaligned
    jalr ra, t0
    mv sp, s2

    beqz a0, _fatal_trap

    /* Handled - restore and return */
    j _trap_restore

_irq_handler:
    /* Machine external interrupt (mcause = 0x8000000B)
     * No need to advance mepc — mret returns to interrupted insn */
    mv a0, sp
    la t0, irq_handler
    jalr ra, t0
    j _trap_restore

_ecall_handler:
    /* SBI-style syscall dispatch (see api/of_syscall.h for the full ABI).
     *
     *   User registers on entry:
     *     a7 = EID (extension id) -- subsystem, or Linux syscall number
     *     a6 = FID (function id)  -- ignored for Linux-compat EIDs
     *     a0..a5 = up to six call arguments
     *
     *   Return:
     *     - SBI vendor calls: a0 = sbiret.error, a1 = sbiret.value
     *     - Linux compat calls: a0 = return value, a1 PRESERVED unchanged
     *
     * Why a1 must be preserved on the Linux path: musl's syscall_arch.h
     * declares only `a0` as the ecall output, so the compiler is free
     * to assume a1 (and every other a-reg) survives the syscall. If we
     * clobber a1, any musl code with a live value in a1 across a syscall
     * sees that value silently corrupted -- which manifests as immediate
     * hangs in any non-trivial musl-built code.
     *
     * The C dispatcher takes 8 arguments which fit exactly in a0..a7.
     * No shuffling needed: copy them straight from the trap frame.
     *
     *   struct of_sbiret syscall_dispatch(
     *       long arg0, long arg1, long arg2, long arg3,
     *       long arg4, long arg5, long fid,  long eid);
     *           a0       a1       a2       a3
     *           a4       a5       a6       a7
     *
     * We stash the user's original a1 and the EID in callee-saved
     * registers (s0, s1) BEFORE calling the C dispatcher. The C ABI
     * guarantees s0/s1 are preserved across the call, so we get them
     * back intact and can decide which value to write to a1.
     */
    lw a0, 40(sp)       /* original a0 (x10) */
    lw a1, 44(sp)       /* original a1 (x11) */
    lw a2, 48(sp)       /* original a2 (x12) */
    lw a3, 52(sp)       /* original a3 (x13) */
    lw a4, 56(sp)       /* original a4 (x14) */
    lw a5, 60(sp)       /* original a5 (x15) */
    lw a6, 64(sp)       /* original a6 (x16) = FID */
    lw a7, 68(sp)       /* original a7 (x17) = EID */

    /* Stash original a1 and EID in callee-saved regs for use after the
     * C call. The user's saved s0/s1 in the trap frame are untouched --
     * the restore step at _trap_restore reloads them from the frame. */
    mv s0, a1           /* preserve user a1 */
    mv s1, a7           /* preserve EID for the post-call branch */

    /* Run syscall bodies on the OS runtime stack. File/path syscalls can
     * exceed the small BRAM trap-frame reserve. */
    mv s2, sp
    lui sp, %hi(_runtime_stack_top)
    addi sp, sp, %lo(_runtime_stack_top)
    la t0, syscall_dispatch
    jalr ra, t0
    mv sp, s2
    /* sbiret returned in a0/a1: a0 = error/value, a1 = SBI value */

    /* Always write the primary return value to user a0. */
    sw a0, 40(sp)

    /* Decide what goes in user a1:
     *   - SBI vendor call (EID >= OF_EID_BASE_VALUE = 0xC0DE0000):
     *       write the C function's a1 (sbiret.value)
     *   - Linux compat call (EID below the vendor range):
     *       restore the user's original a1 unchanged
     *
     * 0xC0DE0000 has the high bit set, so an unsigned compare suffices:
     * any EID below the vendor range fails the bgeu and falls through
     * to the "restore original" branch. */
    li t0, 0xC0DE0000
    bgeu s1, t0, 1f
    /* Linux path -- restore original a1 from s0 */
    sw s0, 44(sp)
    j 2f
1:  /* SBI vendor path -- write the returned sbiret.value */
    sw a1, 44(sp)
2:

    /* Advance mepc past the ecall instruction (4 bytes) */
    lw t0, 128(sp)
    addi t0, t0, 4
    sw t0, 128(sp)

    /* Fall through to restore */

_trap_restore:
    /* Restore mepc */
    lw t0, 128(sp)
    csrw mepc, t0

    /* Restore f0-f31 */
    flw f0,  140(sp)
    flw f1,  144(sp)
    flw f2,  148(sp)
    flw f3,  152(sp)
    flw f4,  156(sp)
    flw f5,  160(sp)
    flw f6,  164(sp)
    flw f7,  168(sp)
    flw f8,  172(sp)
    flw f9,  176(sp)
    flw f10, 180(sp)
    flw f11, 184(sp)
    flw f12, 188(sp)
    flw f13, 192(sp)
    flw f14, 196(sp)
    flw f15, 200(sp)
    flw f16, 204(sp)
    flw f17, 208(sp)
    flw f18, 212(sp)
    flw f19, 216(sp)
    flw f20, 220(sp)
    flw f21, 224(sp)
    flw f22, 228(sp)
    flw f23, 232(sp)
    flw f24, 236(sp)
    flw f25, 240(sp)
    flw f26, 244(sp)
    flw f27, 248(sp)
    flw f28, 252(sp)
    flw f29, 256(sp)
    flw f30, 260(sp)
    flw f31, 264(sp)

    /* Restore integer registers */
    lw x1,   4(sp)
    lw t0,   8(sp)
    csrw mscratch, t0   /* Save original sp to mscratch */
    lw x3,  12(sp)
    lw x4,  16(sp)
    lw x5,  20(sp)
    lw x6,  24(sp)
    lw x7,  28(sp)
    lw x8,  32(sp)
    lw x9,  36(sp)
    lw x10, 40(sp)
    lw x11, 44(sp)
    lw x12, 48(sp)
    lw x13, 52(sp)
    lw x14, 56(sp)
    lw x15, 60(sp)
    lw x16, 64(sp)
    lw x17, 68(sp)
    lw x18, 72(sp)
    lw x19, 76(sp)
    lw x20, 80(sp)
    lw x21, 84(sp)
    lw x22, 88(sp)
    lw x23, 92(sp)
    lw x24, 96(sp)
    lw x25, 100(sp)
    lw x26, 104(sp)
    lw x27, 108(sp)
    lw x28, 112(sp)
    lw x29, 116(sp)
    lw x30, 120(sp)
    lw x31, 124(sp)

    /* Restore original sp */
    csrr sp, mscratch

    /* Drain the store buffer before returning to interrupted code so
     * the ISR's stores hit memory in order before main-thread stores
     * resume.  Pairs with the fence at _trap_entry. */
    fence

    mret

_fatal_trap:
    mv a0, sp
    la t0, fatal_trap
    jalr ra, t0
    j _fatal_trap

/* ========================================
 * Switch to a new runtime stack and jump to entry point.
 * a0 = function pointer, a1 = new stack top
 * Does not return.
 * ======================================== */
.global switch_to_runtime_stack_and_call
switch_to_runtime_stack_and_call:
    mv sp, a1
    jr a0
1:
    j 1b

/* ========================================
 * TLS base (static allocation for single-threaded musl)
 * musl needs ~128 bytes of TLS for errno, locale, etc.
 * ======================================== */
.section .bss.boot
.global __tls_base
.align 4
__tls_base:
    .space 384      /* Must fit struct __pthread (~120B) + TLS data */
