/*
 * Optimized memory functions for RISC-V (rv32)
 * Pinned in BRAM (.fasttext) for 0-wait-state access.
 *
 * VexiiRiscv pipeline notes:
 *   - D-cache: 32KB, 2-way, 64B lines, write-back
 *   - Store buffer: 2 slots — LLSS interleaving prevents stalls
 *   - Zicbom: cbo.clean/flush/inval available (NOT cbo.zero)
 *   - In-cache: ~200 MB/s with LLSS pattern
 *   - Out-of-cache: ~30 MB/s (write-allocate + eviction overhead)
 *
 * cbo.inval encoding: .word 0x0005200F  (cbo.inval (a0))
 *   Invalidates cache line at a0. Does NOT prevent write-allocate
 *   on subsequent stores. Would need Zicboz (cbo.zero) for that.
 *   Used here on DESTINATION lines before overwriting: if the line
 *   was dirty from a previous write, cbo.inval drops it without
 *   write-back (saves eviction cost, not allocation cost).
 */

/* cbo.inval (reg): invalidate cache line. Encoding: funct12=0, rs1, 010, 00000, 0001111 */
.macro CBO_INVAL reg
    /* cbo.inval (\reg): 0000000_00000_rs1_010_00000_0001111 */
    .if \reg == 10   /* a0 */
    .word 0x0005200F
    .elseif \reg == 6 /* t1 */
    .word 0x0003200F
    .endif
.endm

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

    /* For large copies (> D-cache size), invalidate destination lines
     * before overwriting. This saves dirty eviction cost when the dest
     * line was previously written (avoids write-back of data we'll
     * overwrite anyway). Both dest AND src must be 64B-aligned for
     * the invalidation path. */
    li      t1, 64
    bltu    a2, t1, .Lcpy_32_enter

    /* Check 64-byte alignment of dest for cbo.inval path */
    andi    t1, a0, 63
    bnez    t1, .Lcpy_32_enter

    /* 64-byte cache-line copy with dest invalidation.
     * Saves eviction writeback cost for large copies. */
    li      t1, 64
.Lcpy_64_inval:
    bltu    a2, t1, .Lcpy_32_enter
    /* Invalidate dest cache line (drops dirty data, no write-back) */
    CBO_INVAL 10    /* cbo.inval (a0) */
    /* Copy 64 bytes (16 words) interleaved LLSS */
    lw      t2,  0(a1)
    lw      t3,  4(a1)
    sw      t2,  0(a0)
    sw      t3,  4(a0)
    lw      t4,  8(a1)
    lw      t5, 12(a1)
    sw      t4,  8(a0)
    sw      t5, 12(a0)
    lw      t2, 16(a1)
    lw      t3, 20(a1)
    sw      t2, 16(a0)
    sw      t3, 20(a0)
    lw      t4, 24(a1)
    lw      t5, 28(a1)
    sw      t4, 24(a0)
    sw      t5, 28(a0)
    lw      t2, 32(a1)
    lw      t3, 36(a1)
    sw      t2, 32(a0)
    sw      t3, 36(a0)
    lw      t4, 40(a1)
    lw      t5, 44(a1)
    sw      t4, 40(a0)
    sw      t5, 44(a0)
    lw      t2, 48(a1)
    lw      t3, 52(a1)
    sw      t2, 48(a0)
    sw      t3, 52(a0)
    lw      t4, 56(a1)
    lw      t5, 60(a1)
    sw      t4, 56(a0)
    sw      t5, 60(a0)
    addi    a0, a0, 64
    addi    a1, a1, 64
    addi    a2, a2, -64
    bgeu    a2, t1, .Lcpy_64_inval

    /* 32-byte interleaved copy (no invalidation) */
.Lcpy_32_enter:
    li      t1, 32
    bltu    a2, t1, .Lcpy_16
.Lcpy_32:
    lw      t2,  0(a1)
    lw      t3,  4(a1)
    sw      t2,  0(a0)
    sw      t3,  4(a0)
    lw      t4,  8(a1)
    lw      t5, 12(a1)
    sw      t4,  8(a0)
    sw      t5, 12(a0)
    lw      t2, 16(a1)
    lw      t3, 20(a1)
    sw      t2, 16(a0)
    sw      t3, 20(a0)
    lw      t4, 24(a1)
    lw      t5, 28(a1)
    sw      t4, 24(a0)
    sw      t5, 28(a0)
    addi    a0, a0, 32
    addi    a1, a1, 32
    addi    a2, a2, -32
    bgeu    a2, t1, .Lcpy_32

    /* 4-word tail (16 bytes) */
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

    /* Single-word tail */
.Lcpy_4:
    li      t1, 4
    bltu    a2, t1, .Lcpy_byte
    lw      t2, 0(a1)
    sw      t2, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    bgeu    a2, t1, .Lcpy_4

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

    /* 8-word unrolled set (32 bytes/iteration) */
    li      t1, 32
    bltu    a2, t1, .Lset_16
.Lset_32:
    sw      a1,  0(a0)
    sw      a1,  4(a0)
    sw      a1,  8(a0)
    sw      a1, 12(a0)
    sw      a1, 16(a0)
    sw      a1, 20(a0)
    sw      a1, 24(a0)
    sw      a1, 28(a0)
    addi    a0, a0, 32
    addi    a2, a2, -32
    bgeu    a2, t1, .Lset_32

.Lset_16:
    li      t1, 16
    bltu    a2, t1, .Lset_4
    sw      a1, 0(a0)
    sw      a1, 4(a0)
    sw      a1, 8(a0)
    sw      a1, 12(a0)
    addi    a0, a0, 16
    addi    a2, a2, -16

.Lset_4:
    li      t1, 4
    bltu    a2, t1, .Lset_byte
    sw      a1, 0(a0)
    addi    a0, a0, 4
    addi    a2, a2, -4
    bgeu    a2, t1, .Lset_4

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

.Lmov_32:
    li      t1, 32
    bltu    a2, t1, .Lmov_16
    lw      t2,  0(a1)
    lw      t3,  4(a1)
    sw      t2,  0(a0)
    sw      t3,  4(a0)
    lw      t4,  8(a1)
    lw      t5, 12(a1)
    sw      t4,  8(a0)
    sw      t5, 12(a0)
    lw      t2, 16(a1)
    lw      t3, 20(a1)
    sw      t2, 16(a0)
    sw      t3, 20(a0)
    lw      t4, 24(a1)
    lw      t5, 28(a1)
    sw      t4, 24(a0)
    sw      t5, 28(a0)
    addi    a0, a0, 32
    addi    a1, a1, 32
    addi    a2, a2, -32
    bgeu    a2, t1, .Lmov_32

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

.Lmov_4:
    li      t1, 4
    bltu    a2, t1, .Lmov_byte
    lw      t2, 0(a1)
    sw      t2, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    bgeu    a2, t1, .Lmov_4

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
