/*
 * Optimized memory functions for RISC-V (rv32)
 * Pinned in BRAM (.fasttext) for 0-wait-state access.
 * Word-aligned bulk transfers using lw/sw (4 bytes per instruction).
 */

.section .fasttext, "ax"

/* ================================================================
 * memcpy(dest, src, n) — returns dest
 * ================================================================ */
.global memcpy
memcpy:
    mv      t0, a0
    beqz    a2, .Lcpy_done

    /* Check alignment */
    or      t1, a0, a1
    andi    t1, t1, 3
    bnez    t1, .Lcpy_byte

    /* 4-word unrolled copy */
.Lcpy_16:
    li      t1, 16
    bltu    a2, t1, .Lcpy_4
    lw      t2, 0(a1)
    lw      t3, 4(a1)
    lw      t4, 8(a1)
    lw      t5, 12(a1)
    sw      t2, 0(a0)
    sw      t3, 4(a0)
    sw      t4, 8(a0)
    sw      t5, 12(a0)
    addi    a0, a0, 16
    addi    a1, a1, 16
    addi    a2, a2, -16
    j       .Lcpy_16

.Lcpy_4:
    li      t1, 4
    bltu    a2, t1, .Lcpy_byte
    lw      t2, 0(a1)
    sw      t2, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    j       .Lcpy_4

    /* Byte fallback */
.Lcpy_byte:
    beqz    a2, .Lcpy_done
    lbu     t1, 0(a1)
    sb      t1, 0(a0)
    addi    a0, a0, 1
    addi    a1, a1, 1
    addi    a2, a2, -1
    j       .Lcpy_byte

.Lcpy_done:
    mv      a0, t0
    ret

/* ================================================================
 * memset(dest, c, n) — returns dest
 * ================================================================ */
.global memset
memset:
    mv      t0, a0
    beqz    a2, .Lset_done

    /* Broadcast byte to word */
    andi    a1, a1, 0xFF
    slli    t1, a1, 8
    or      a1, a1, t1
    slli    t1, a1, 16
    or      a1, a1, t1

    /* Check alignment */
    andi    t1, a0, 3
    bnez    t1, .Lset_byte

    /* 4-word unrolled set */
.Lset_16:
    li      t1, 16
    bltu    a2, t1, .Lset_4
    sw      a1, 0(a0)
    sw      a1, 4(a0)
    sw      a1, 8(a0)
    sw      a1, 12(a0)
    addi    a0, a0, 16
    addi    a2, a2, -16
    j       .Lset_16

.Lset_4:
    li      t1, 4
    bltu    a2, t1, .Lset_byte
    sw      a1, 0(a0)
    addi    a0, a0, 4
    addi    a2, a2, -4
    j       .Lset_4

    /* Byte fallback */
.Lset_byte:
    beqz    a2, .Lset_done
    sb      a1, 0(a0)
    addi    a0, a0, 1
    addi    a2, a2, -1
    j       .Lset_byte

.Lset_done:
    mv      a0, t0
    ret

/* ================================================================
 * memmove(dest, src, n) — returns dest (handles overlap)
 * ================================================================ */
.global memmove
memmove:
    mv      t0, a0
    beqz    a2, .Lmov_done

    /* If dest <= src, forward copy (same as memcpy) */
    bleu    a0, a1, .Lmov_fwd

    /* Backward copy: start from end */
    add     a0, a0, a2
    add     a1, a1, a2

.Lmov_bwd:
    beqz    a2, .Lmov_done
    addi    a0, a0, -1
    addi    a1, a1, -1
    lbu     t1, 0(a1)
    sb      t1, 0(a0)
    addi    a2, a2, -1
    j       .Lmov_bwd

    /* Forward copy — reuse memcpy logic */
.Lmov_fwd:
    or      t1, a0, a1
    andi    t1, t1, 3
    bnez    t1, .Lmov_byte

.Lmov_16:
    li      t1, 16
    bltu    a2, t1, .Lmov_4
    lw      t2, 0(a1)
    lw      t3, 4(a1)
    lw      t4, 8(a1)
    lw      t5, 12(a1)
    sw      t2, 0(a0)
    sw      t3, 4(a0)
    sw      t4, 8(a0)
    sw      t5, 12(a0)
    addi    a0, a0, 16
    addi    a1, a1, 16
    addi    a2, a2, -16
    j       .Lmov_16

.Lmov_4:
    li      t1, 4
    bltu    a2, t1, .Lmov_byte
    lw      t2, 0(a1)
    sw      t2, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    j       .Lmov_4

.Lmov_byte:
    beqz    a2, .Lmov_done
    lbu     t1, 0(a1)
    sb      t1, 0(a0)
    addi    a0, a0, 1
    addi    a1, a1, 1
    addi    a2, a2, -1
    j       .Lmov_byte

.Lmov_done:
    mv      a0, t0
    ret
