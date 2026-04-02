/*
 * Optimized memory functions for RISC-V (rv32)
 * Pinned in BRAM (.fasttext) for 0-wait-state access.
 *
 * VexiiRiscv pipeline notes:
 *   - D-cache: 128KB, 2-way, 64B lines, write-back
 *   - Store buffer: 2 slots — LLSS interleaving prevents stalls
 *   - Zicbom: cbo.clean/flush/inval
 *   - Zicboz: cbo.zero (allocate zeroed line, no SDRAM read)
 *
 * cbo.zero eliminates write-allocate reads from SDRAM:
 *   memset: zero the line then store fill value (no SDRAM read)
 *   memcpy: zero the dest line then load src + store (dest reads eliminated)
 */

/* cbo.inval (reg): invalidate cache line, drop dirty data. */
.macro CBO_INVAL reg
    .if \reg == 10   /* a0 */
    .word 0x0005200F
    .elseif \reg == 6 /* t1 */
    .word 0x0003200F
    .endif
.endm

/* cbo.zero (reg): allocate cache line filled with zeros, no SDRAM read. */
.macro CBO_ZERO reg
    .if \reg == 10   /* a0 */
    .word 0x0045200F
    .elseif \reg == 11 /* a1 */
    .word 0x0045A00F
    .elseif \reg == 15 /* a5 */
    .word 0x0047A00F
    .endif
.endm

.section .fasttext, "ax"

/* ================================================================
 * memcpy(dest, src, n) — returns dest
 *
 * For 64B-aligned dest with n >= 64:
 *   cbo.zero on dest line (avoids write-allocate SDRAM read),
 *   then load 64B from src and store to dest.
 *   Source still needs cache fills but dest reads eliminated —
 *   cuts SDRAM traffic roughly in half for large copies.
 * ================================================================ */
.global memcpy
memcpy:
    mv      t0, a0
    beqz    a2, .Lcpy_done

    /* Check word alignment */
    or      t1, a0, a1
    andi    t1, t1, 3
    bnez    t1, .Lcpy_byte

    /* Need >= 64 bytes for cbo.zero path */
    li      t1, 64
    bltu    a2, t1, .Lcpy_32_enter

    /* cbo.zero only safe on cached regions (SDRAM 0x10-0x13, PSRAM 0x30-0x37) */
    srli    t1, a0, 26
    li      t2, 4             /* 0x10-0x13 */
    beq     t1, t2, .Lcpy_align_check
    srli    t1, a0, 27
    li      t2, 6             /* 0x30-0x37 */
    bne     t1, t2, .Lcpy_32_enter

.Lcpy_align_check:
    /* Handle unaligned head: copy words until dest is 64B-aligned */
    andi    t1, a0, 63
    beqz    t1, .Lcpy_64_zero

    /* Head: word-copy until 64B-aligned */
    li      t2, 64
    sub     t1, t2, t1         /* bytes to alignment */
    /* Don't overshoot */
    bgeu    t1, a2, .Lcpy_32_enter
.Lcpy_head:
    li      t2, 4
    bltu    t1, t2, .Lcpy_64_check
    lw      t3, 0(a1)
    sw      t3, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    addi    t1, t1, -4
    j       .Lcpy_head

.Lcpy_64_check:
    li      t1, 64
    bltu    a2, t1, .Lcpy_32_enter

    /* 64-byte copy with cbo.zero on dest.
     * cbo.zero allocates dest cache line as zeros (no SDRAM read).
     * Then we overwrite with src data. Eliminates dest read-allocate. */
.Lcpy_64_zero:
    li      t1, 64
.Lcpy_64_loop:
    bltu    a2, t1, .Lcpy_32_enter
    CBO_ZERO 10    /* cbo.zero (a0) — dest line allocated clean, no read */
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
    bgeu    a2, t1, .Lcpy_64_loop

    /* 32-byte interleaved copy (tail) */
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

.Lcpy_4:
    li      t1, 4
    bltu    a2, t1, .Lcpy_byte
    lw      t2, 0(a1)
    sw      t2, 0(a0)
    addi    a0, a0, 4
    addi    a1, a1, 4
    addi    a2, a2, -4
    bgeu    a2, t1, .Lcpy_4

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
 *
 * For 64B-aligned dest with n >= 64:
 *   c==0: cbo.zero (allocate zeroed line, no SDRAM read)
 *   c!=0: cbo.zero then store fill value (eliminates read-allocate)
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

    /* Need >= 64 bytes for cbo.zero path */
    li      t1, 64
    bltu    a2, t1, .Lset_sw_enter

    /* Handle unaligned head: store words until 64B-aligned */
    andi    t1, a0, 63
    beqz    t1, .Lset_64_check
    li      t2, 64
    sub     t1, t2, t1
    bgeu    t1, a2, .Lset_sw_enter
.Lset_head:
    li      t2, 4
    bltu    t1, t2, .Lset_64_check
    sw      a1, 0(a0)
    addi    a0, a0, 4
    addi    a2, a2, -4
    addi    t1, t1, -4
    j       .Lset_head

.Lset_64_check:
    li      t1, 64
    bltu    a2, t1, .Lset_sw_enter

    /* Check if cached region (SDRAM 0x10-0x13 or PSRAM 0x30-0x37).
     * cbo.zero is only safe on cacheable addresses. */
    srli    t1, a0, 26
    li      t2, 4             /* 0x10-0x13 */
    beq     t1, t2, .Lset_64_go
    srli    t1, a0, 27
    li      t2, 6             /* 0x30-0x37 */
    bne     t1, t2, .Lset_sw_enter

.Lset_64_go:
    /* Branch: zero fill (c==0) vs non-zero fill */
    bnez    a1, .Lset_64_nonzero

    /* Zero fill: cbo.zero only — no stores needed */
    li      t1, 64
.Lset_zero_64:
    CBO_ZERO 10
    addi    a0, a0, 64
    addi    a2, a2, -64
    bgeu    a2, t1, .Lset_zero_64
    beqz    a2, .Lset_done
    j       .Lset_4

    /* Non-zero fill: cbo.zero then store fill value.
     * cbo.zero allocates the line clean (no SDRAM read), then we
     * overwrite with the fill value. Saves the read-allocate. */
.Lset_64_nonzero:
    li      t1, 64
.Lset_nz_64:
    CBO_ZERO 10             /* allocate line as zeros, no SDRAM read */
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
    bgeu    a2, t1, .Lset_nz_64
    beqz    a2, .Lset_done
    j       .Lset_4

.Lset_sw_enter:
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
 *
 * Forward path uses cbo.zero optimization (same as memcpy).
 * Backward path cannot use cbo.zero (would destroy unread src data
 * if src/dst overlap).
 * ================================================================ */
.global memmove
memmove:
    mv      t0, a0
    beqz    a2, .Lmov_done

    /* If dest <= src, forward copy with cbo.zero (same as memcpy) */
    bleu    a0, a1, memcpy

    /* Backward copy: start from end — byte-by-byte (safe for overlap) */
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

.Lmov_done:
    mv      a0, t0
    ret
