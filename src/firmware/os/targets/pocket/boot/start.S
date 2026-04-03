/*
 * openfpgaOS Startup Code
 * Sets up stack, trap handler, TLS, and jumps to OS main.
 * Handles exceptions including ecall (syscall interface).
 */

.section .text.start
.global _start

_start:
    /* Check if this is a trap (exception re-entry) or cold boot.
     * On cold boot, mcause is 0. On exception, mcause is non-zero.
     * IMPORTANT: must check BEFORE touching sp, otherwise we clobber
     * the interrupted code's stack pointer on every trap/ecall. */
    csrr t0, mcause
    bnez t0, _trap_entry

    /* Cold boot only: set up stack pointer */
    lui sp, %hi(_stack_top)
    addi sp, sp, %lo(_stack_top)

    /* Enable FPU: set mstatus.FS = 11 (Dirty) -- bits [14:13] */
    li t0, (3 << 13)
    csrs mstatus, t0

    /* Set mtvec to point to our trap handler (address 0 = _start) */
    la t0, _start
    csrw mtvec, t0

    /* Enable machine interrupts: MTIE (bit 7) + MEIE (bit 11) in mie,
     * then set mstatus.MIE (bit 3) to enable global interrupt delivery */
    li t0, (1 << 7) | (1 << 11)
    csrw mie, t0
    li t0, (1 << 3)
    csrs mstatus, t0

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
 * ======================================== */
_trap_entry:
    /* Save current sp to mscratch */
    csrw mscratch, sp

    /* Switch to trap stack in BRAM */
    lui sp, %hi(_stack_top)
    addi sp, sp, %lo(_stack_top)

    /* Allocate trap frame: 272 bytes */
    addi sp, sp, -272

    /* Save x1-x31 */
    sw x1,   4(sp)
    csrr t0, mscratch
    sw t0,   8(sp)      /* x2 (original sp) */
    sw x3,  12(sp)
    sw x4,  16(sp)
    sw x5,  20(sp)
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

    /* Not an ecall - try misaligned access emulation */
    mv a0, sp
    la t0, handle_misaligned
    jalr ra, t0

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
    /* Syscall dispatch:
     * a7 (x17) = syscall number
     * a0-a5 (x10-x15) = arguments
     * Return value goes in a0 (x10) */
    lw a0, 40(sp)       /* original a0 (x10) */
    lw a1, 44(sp)       /* original a1 (x11) */
    lw a2, 48(sp)       /* original a2 (x12) */
    lw a3, 52(sp)       /* original a3 (x13) */
    lw a4, 56(sp)       /* original a4 (x14) */
    lw a5, 60(sp)       /* original a5 (x15) */
    lw a6, 68(sp)       /* original a7 (x17) = syscall number */

    /* Call: long syscall_dispatch(long n, long a0..a5) */
    /* Move a7 to first argument position, shift others */
    mv t0, a0
    mv a0, a6           /* n = a7 (syscall number) */
    mv a6, a5
    mv a5, a4
    mv a4, a3
    mv a3, a2
    mv a2, a1
    mv a1, t0           /* a0 = original a0 */
    /* a2..a6 already shifted */

    la t0, syscall_dispatch
    jalr ra, t0

    /* Store return value in trap frame's a0 (x10) position */
    sw a0, 40(sp)

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
    .space 512      /* Must fit struct __pthread (~120B) + TLS data */
