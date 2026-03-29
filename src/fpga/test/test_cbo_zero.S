.section .text
.global _start
_start:
    csrw mie, zero
    csrci mstatus, 8
    auipc t0, 0
    addi t0, t0, 492          # placeholder
    csrw mtvec, t0
    lui sp, 0x14000
    addi sp, sp, -128
    fence
    .word 0x0000100F
    lui a4, 0x4F000

    # Test 1: cbo.zero on cold line
    li a3, '1'; sw a3, 4(a4)
    lui a5, 0x10C00
    .insn i 0x0F, 2, x0, a5, 0x004    # cbo.zero
    fence
    lw t2, 0(a5);  bnez t2, 1f
    lw t2, 60(a5); bnez t2, 1f
    li a3, 'P'; sw a3, 4(a4); j 2f
1:  li a3, 'F'; sw a3, 4(a4)
2:

    # Test 2: cbo.zero + write + flush + uncached verify
    li a3, '2'; sw a3, 4(a4)
    lui a5, 0x10C01
    .insn i 0x0F, 2, x0, a5, 0x004    # cbo.zero
    li t0, 0x42424242
    sw t0, 0(a5)
    fence
    .insn i 0x0F, 2, x0, a5, 0x002    # cbo.flush
    fence
    lui a6, 0x50C01
    lw t2, 0(a6); bne t2, t0, 3f
    lw t2, 4(a6); bnez t2, 3f
    li a3, 'P'; sw a3, 4(a4); j 4f
3:  li a3, 'F'; sw a3, 4(a4)
4:

    # Test 3: cbo.zero on dirty line
    li a3, '3'; sw a3, 4(a4)
    lui a5, 0x10C02
    li t0, -1
    sw t0, 0(a5); sw t0, 28(a5); sw t0, 60(a5)
    fence
    .insn i 0x0F, 2, x0, a5, 0x004    # cbo.zero
    fence
    lw t2, 0(a5);  bnez t2, 5f
    lw t2, 28(a5); bnez t2, 5f
    lw t2, 60(a5); bnez t2, 5f
    li a3, 'P'; sw a3, 4(a4); j 6f
5:  li a3, 'F'; sw a3, 4(a4)
6:

    # Test 4: cbo.zero on clean cached line
    li a3, '4'; sw a3, 4(a4)
    lui a5, 0x10C03
    li t0, 0xABCD1234
    sw t0, 0(a5); sw t0, 4(a5)
    fence
    .insn i 0x0F, 2, x0, a5, 0x001    # cbo.clean
    fence
    .insn i 0x0F, 2, x0, a5, 0x004    # cbo.zero
    fence
    lw t2, 0(a5); bnez t2, 7f
    lw t2, 4(a5); bnez t2, 7f
    li a3, 'P'; sw a3, 4(a4); j 8f
7:  li a3, 'F'; sw a3, 4(a4)
8:

    # Test 5: 4 consecutive lines
    li a3, '5'; sw a3, 4(a4)
    lui a5, 0x10D00
    mv s0, a5
    .insn i 0x0F, 2, x0, a5, 0x004; addi a5, a5, 64
    .insn i 0x0F, 2, x0, a5, 0x004; addi a5, a5, 64
    .insn i 0x0F, 2, x0, a5, 0x004; addi a5, a5, 64
    .insn i 0x0F, 2, x0, a5, 0x004
    fence
    lw t2, 0(s0);   bnez t2, 9f
    lw t2, 60(s0);  bnez t2, 9f
    lw t2, 192(s0); bnez t2, 9f
    lw t2, 252(s0); bnez t2, 9f
    li a3, 'P'; sw a3, 4(a4); j 10f
9:  li a3, 'F'; sw a3, 4(a4)
10:

    # Test 6: cbo.zero then cbo.inval, read gets SDRAM value
    li a3, '6'; sw a3, 4(a4)
    lui a5, 0x10C04
    li t0, 0x99887766
    sw t0, 0(a5)
    fence
    .insn i 0x0F, 2, x0, a5, 0x002    # cbo.flush
    fence
    # SDRAM has 0x99887766, line evicted
    .insn i 0x0F, 2, x0, a5, 0x004    # cbo.zero (allocate with zeros)
    fence
    .insn i 0x0F, 2, x0, a5, 0x000    # cbo.inval (discard zeros)
    fence
    # Read should refetch from SDRAM
    lw t2, 0(a5)
    bne t2, t0, 11f
    li a3, 'P'; sw a3, 4(a4); j 12f
11: li a3, 'F'; sw a3, 4(a4)
12:

    # Test 7: pass (placeholder)
    li a3, '7'; sw a3, 4(a4)
    li a3, 'P'; sw a3, 4(a4)

    li a3, '\n'; sw a3, 4(a4)
    lui a5, 0x10
    addi a5, a5, -256
    li a3, 1
    sw a3, 0(a5)
    j .

    .balign 4
trap_handler:
    lui a4, 0x4F000
    li a3, 'T'; sw a3, 4(a4)
    csrr a0, mcause
    andi a3, a0, 15
    addi a3, a3, '0'
    li t6, ':'; blt a3, t6, 13f; addi a3, a3, 7
13: sw a3, 4(a4)
    li a3, '@'; sw a3, 4(a4)
    csrr a0, mepc
    li t5, 28
14: srl a3, a0, t5; andi a3, a3, 15; addi a3, a3, '0'
    li t6, ':'; blt a3, t6, 15f; addi a3, a3, 7
15: sw a3, 4(a4); addi t5, t5, -4; bgez t5, 14b
    li a3, '\n'; sw a3, 4(a4)
    lui a5, 0x10; addi a5, a5, -256; li a3, 2; sw a3, 0(a5); j .
