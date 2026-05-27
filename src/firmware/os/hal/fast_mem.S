/*
 * Optimized memory functions for RISC-V (rv32)
 * Pinned in BRAM (.fasttext) for 0-wait-state access.
 *
 * VexiiRiscv pipeline notes:
 *   - D-cache: 128KB, 2-way, 64B lines, write-back
 *   - Store buffer: 4 slots — LLSS interleaving prevents stalls
 *   - Zicbom: cbo.clean/flush/inval
 *
 * Strategy:
 *   memset:          plain 16-word unrolled stores (store buffer
 *                    handles write-allocate pipelining)
 *   memcpy:          cbo.inval on dest (drop dirty, no writeback),
 *                    then load src + store dest (write-allocate is
 *                    cheap when we're writing the full line)
 *   memmove fwd:     same as memcpy
 *   memmove bwd:     word-aligned backward copy
 */

/* cbo.inval macro placeholder.  The current assembly path keeps this as a
 * no-op because the generic copy still performs well and stays portable
 * across CPU cache configurations. */
.macro CBO_INVAL reg
.endm

.section .fasttext, "ax"

/* ================================================================
 * memcpy(dest, src, n) — returns dest
 *
 * For 64B-aligned dest in cached region with n >= 64:
 *   cbo.inval on dest line (drop dirty, no writeback cost),
 *   then load 64B from src and store to dest.
 *   Saves dirty eviction writeback (~60 cycles per line).
 * ================================================================ */
.global memcpy
memcpy:
    mv      t0, a0
    beqz    a2, .Lcpy_done

    /* Check word alignment of both src and dest */
    or      t1, a0, a1
    andi    t1, t1, 3
    bnez    t1, .Lcpy_byte

    /* Need >= 64 bytes for 64B path */
    li      t1, 64
    bltu    a2, t1, .Lcpy_32_enter

    /* Fast full-line path for cached SDRAM and PSRAM windows.  CBO is
     * currently a no-op macro, but keep the region gate conservative. */
    srli    t1, a0, 26
    li      t2, 4
    beq     t1, t2, .Lcpy_align
    srli    t1, a0, 27
    li      t2, 6
    bne     t1, t2, .Lcpy_32_enter

.Lcpy_align:
    /* Word-copy head until dest is 64B-aligned */
    andi    t1, a0, 63
    beqz    t1, .Lcpy_64

    li      t2, 64
    sub     t1, t2, t1         /* bytes to next 64B boundary */
    bgeu    t1, a2, .Lcpy_32_enter
.Lcpy_head4:
    lw      t3, 0(a1)
    sw      t3, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    addi    t1, t1, -4
    bnez    t1, .Lcpy_head4

    /* 64-byte copy: cbo.inval drops dest line (no dirty writeback),
     * then we write the full line from src. */
.Lcpy_64:
    li      t1, 64
.Lcpy_64_loop:
    bltu    a2, t1, .Lcpy_32_enter
    CBO_INVAL 10
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
    bgeu    a2, t1, .Lcpy_64_loop

    /* 32-byte unrolled tail */
.Lcpy_32_enter:
    li      t1, 32
    bltu    a2, t1, .Lcpy_4
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

    /* Word tail */
.Lcpy_4:
    li      t1, 4
    bltu    a2, t1, .Lcpy_byte
.Lcpy_4_loop:
    lw      t2, 0(a1)
    sw      t2, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    bgeu    a2, t1, .Lcpy_4_loop

    /* Byte fallback */
.Lcpy_byte:
    beqz    a2, .Lcpy_done
.Lcpy_byte_loop:
    lbu     t1, 0(a1)
    sb      t1, 0(a0)
    addi    a0, a0, 1
    addi    a1, a1, 1
    addi    a2, a2, -1
    bnez    a2, .Lcpy_byte_loop

.Lcpy_done:
    mv      a0, t0
    ret

/* ================================================================
 * memset(dest, c, n) — returns dest
 *
 * Plain 16-word unrolled stores. The store buffer handles
 * write-allocate pipelining, so this is the fastest approach we have.
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

    /* Check word alignment */
    andi    t1, a0, 3
    bnez    t1, .Lset_byte

    li      t1, 64
    bltu    a2, t1, .Lset_32
.Lset_64:
    sw      a1,  0(a0)
    sw      a1,  4(a0)
    sw      a1,  8(a0)
    sw      a1, 12(a0)
    sw      a1, 16(a0)
    sw      a1, 20(a0)
    sw      a1, 24(a0)
    sw      a1, 28(a0)
    sw      a1, 32(a0)
    sw      a1, 36(a0)
    sw      a1, 40(a0)
    sw      a1, 44(a0)
    sw      a1, 48(a0)
    sw      a1, 52(a0)
    sw      a1, 56(a0)
    sw      a1, 60(a0)
    addi    a0, a0, 64
    addi    a2, a2, -64
    bgeu    a2, t1, .Lset_64

.Lset_32:
    li      t1, 32
    bltu    a2, t1, .Lset_4
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

.Lset_4:
    li      t1, 4
    bltu    a2, t1, .Lset_byte
.Lset_4_loop:
    sw      a1, 0(a0)
    addi    a0, a0, 4
    addi    a2, a2, -4
    bgeu    a2, t1, .Lset_4_loop

.Lset_byte:
    beqz    a2, .Lset_done
.Lset_byte_loop:
    sb      a1, 0(a0)
    addi    a0, a0, 1
    addi    a2, a2, -1
    bnez    a2, .Lset_byte_loop

.Lset_done:
    mv      a0, t0
    ret

/* ================================================================
 * memmove(dest, src, n) — returns dest (handles overlap)
 *
 * Forward: reuses memcpy (cbo.inval + LLSS).
 * Backward: word-aligned reverse copy when possible.
 * ================================================================ */
.global memmove
memmove:
    mv      t0, a0
    beqz    a2, .Lmov_done

    /* Forward (dest <= src): use memcpy */
    bleu    a0, a1, memcpy

    /* Backward copy: start from end */
    add     a0, a0, a2
    add     a1, a1, a2

    /* Check word alignment for fast backward path */
    or      t1, a0, a1
    andi    t1, t1, 3
    bnez    t1, .Lmov_bwd_byte

    /* Word-aligned backward: 4 words at a time */
    li      t1, 16
    bltu    a2, t1, .Lmov_bwd_4
.Lmov_bwd_16:
    lw      t2, -4(a1)
    lw      t3, -8(a1)
    sw      t2, -4(a0)
    sw      t3, -8(a0)
    lw      t4, -12(a1)
    lw      t5, -16(a1)
    sw      t4, -12(a0)
    sw      t5, -16(a0)
    addi    a0, a0, -16
    addi    a1, a1, -16
    addi    a2, a2, -16
    bgeu    a2, t1, .Lmov_bwd_16

.Lmov_bwd_4:
    li      t1, 4
    bltu    a2, t1, .Lmov_bwd_byte
.Lmov_bwd_4_loop:
    lw      t2, -4(a1)
    sw      t2, -4(a0)
    addi    a0, a0, -4
    addi    a1, a1, -4
    addi    a2, a2, -4
    bgeu    a2, t1, .Lmov_bwd_4_loop

.Lmov_bwd_byte:
    beqz    a2, .Lmov_done
.Lmov_bwd_byte_loop:
    addi    a0, a0, -1
    addi    a1, a1, -1
    lbu     t1, 0(a1)
    sb      t1, 0(a0)
    addi    a2, a2, -1
    bnez    a2, .Lmov_bwd_byte_loop

.Lmov_done:
    mv      a0, t0
    ret
