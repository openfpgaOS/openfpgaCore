.section .text
.global _start

# ---- Handlers at fixed addresses ----
# Address 0x000: _start (jump to main)
# Address 0x100: ecall handler
# Address 0x200: trap handler (misaligned emulation)

_start:
    j main

    .balign 256
ecall_handler:
    csrr t0, mcause
    li t1, 11
    bne t0, t1, trap_handler
    slli a0, a7, 1
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret

    .balign 256
trap_handler:
    csrr t0, mcause
    li t1, 4
    beq t0, t1, misaligned_load
    # Unknown trap
    lui a4, 0x4F000
    li a3, 'T'; sw a3, 4(a4)
    csrr a0, mcause; andi a3, a0, 15; addi a3, a3, '0'
    li t6, ':'; blt a3, t6, 90f; addi a3, a3, 7
90: sw a3, 4(a4)
    li a3, '\n'; sw a3, 4(a4)
    lui a5, 0x10; addi a5, a5, -256; li a3, 2; sw a3, 0(a5); j .

misaligned_load:
    csrr t3, mtval
    lbu t4, 0(t3)
    lbu t5, 1(t3); slli t5, t5, 8; or t4, t4, t5
    lbu t5, 2(t3); slli t5, t5, 16; or t4, t4, t5
    lbu t5, 3(t3); slli t5, t5, 24; or t4, t4, t5
    # Extract rd from instruction at mepc
    csrr t0, mepc
    lw t1, 0(t0)
    srli t2, t1, 7; andi t2, t2, 0x1F
    li t6, 7;  beq t2, t6, 91f  # t2=x7
    li t6, 10; beq t2, t6, 92f  # a0=x10
    j trap_handler
91: mv t2, t4; j 93f
92: mv a0, t4
93: csrr t0, mepc; addi t0, t0, 4; csrw mepc, t0; mret

    .balign 256
main:
    csrw mie, zero
    csrci mstatus, 8
    li t0, 0x200             # trap_handler address
    csrw mtvec, t0
    lui sp, 0x14000
    addi sp, sp, -128
    li t0, (3 << 13); csrs mstatus, t0
    fence
    .word 0x0000100F
    lui a4, 0x4F000

    # Test 1: cbo.clean
    li a3, '1'; sw a3, 4(a4)
    lui a5, 0x10800
    li t0, 0xDEADBEEF; sw t0, 0(a5); sw t0, 4(a5)
    fence
    .insn i 0x0F, 2, x0, a5, 0x001
    fence
    lui a6, 0x50800
    lw t2, 0(a6); bne t2, t0, 1f
    lw t2, 4(a6); bne t2, t0, 1f
    li a3, 'P'; sw a3, 4(a4); j 2f
1:  li a3, 'F'; sw a3, 4(a4)
2:

    # Test 2: cbo.flush writes + invalidates
    li a3, '2'; sw a3, 4(a4)
    lui a5, 0x10801
    li t0, 0xCAFEBABE; sw t0, 0(a5)
    fence
    .insn i 0x0F, 2, x0, a5, 0x002
    fence
    lui a6, 0x50801; lw t2, 0(a6)
    bne t2, t0, 3f
    lw t2, 0(a5)
    bne t2, t0, 3f
    li a3, 'P'; sw a3, 4(a4); j 4f
3:  li a3, 'F'; sw a3, 4(a4)
4:

    # Test 3: D-cache eviction integrity (64 lines, verify after flush)
    li a3, '3'; sw a3, 4(a4)
    lui a5, 0x10900
    li t0, 0x90000000; li s0, 64
5:  sw t0, 0(a5); sw t0, 60(a5)
    addi t0, t0, 1; addi a5, a5, 64; addi s0, s0, -1
    bnez s0, 5b
    fence
    lui a5, 0x13FF8; lui a6, 0x14000
6:  lbu t2, 0(a5); addi a5, a5, 64; bne a5, a6, 6b
    fence
    lui a6, 0x50900; lw t2, 0(a6)
    li t1, 0x90000000; bne t2, t1, 7f
    lui a6, 0x50900; li t3, 4032; add a6, a6, t3
    lw t2, 0(a6); li t1, 0x9000003F; bne t2, t1, 7f
    li a3, 'P'; sw a3, 4(a4); j 8f
7:  li a3, 'F'; sw a3, 4(a4)
8:

    # Test 4: misaligned load emulation
    li a3, '4'; sw a3, 4(a4)
    lui a5, 0x10A00
    li t0, 0x44332211; sw t0, 0(a5)
    li t0, 0x88776655; sw t0, 4(a5)
    fence
    lw t2, 1(a5)
    li t1, 0x55443322
    bne t2, t1, 9f
    li a3, 'P'; sw a3, 4(a4); j 10f
9:  li a3, 'F'; sw a3, 4(a4)
10:

    # Test 5: ecall round-trip
    li a3, '5'; sw a3, 4(a4)
    li t0, 0x100; csrw mtvec, t0  # point to ecall_handler
    li a7, 42; li a0, 0
    ecall
    li t1, 84; bne a0, t1, 11f
    li a3, 'P'; sw a3, 4(a4); j 12f
11: li a3, 'F'; sw a3, 4(a4)
12: li t0, 0x200; csrw mtvec, t0  # restore trap_handler

    # Test 6: I-cache coherency (write code, fence.i, execute)
    li a3, '6'; sw a3, 4(a4)
    lui a5, 0x10B00
    li t0, 0x06300513; sw t0, 0(a5)  # addi a0, zero, 99
    li t0, 0x00008067; sw t0, 4(a5)  # ret
    fence; .word 0x0000100F
    lui t0, 0x10B00; jalr ra, t0, 0
    li t1, 99; bne a0, t1, 13f
    li a3, 'P'; sw a3, 4(a4); j 14f
13: li a3, 'F'; sw a3, 4(a4)
14:

    # Test 7: Multi-target sweep
    li a3, '7'; sw a3, 4(a4)
    li a5, 0x100; li t0, 0xBEEF0001
    sw t0, 0(a5); lw t2, 0(a5); bne t2, t0, 15f
    lui a5, 0x10C00; li t0, 0xBEEF0002
    sw t0, 0(a5); fence; lw t2, 0(a5); bne t2, t0, 15f
    lui a5, 0x40000; lw t2, 0(a5); andi t2, t2, 1; beqz t2, 15f
    li a3, 'P'; sw a3, 4(a4); j 16f
15: li a3, 'F'; sw a3, 4(a4)
16:

    li a3, '\n'; sw a3, 4(a4)
    lui a5, 0x10; addi a5, a5, -256
    li a3, 1; sw a3, 0(a5)
    j .
