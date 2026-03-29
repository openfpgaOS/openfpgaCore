# Regression tests for bugs found during session
# Run as: obj_dir_system/Vtb_system /tmp/test_regressions.bin 5000000

.section .text
.global _start
_start:
    csrw mie, zero
    csrci mstatus, 8
    auipc t0, 0
    addi t0, t0, 0          # trap handler placeholder
    csrw mtvec, t0
    lui sp, 0x14000
    addi sp, sp, -128
    fence
    .word 0x0000100F
    lui a4, 0x4F000          # UART base

    # ============================================================
    # Test 1: BRAM address wrap (bug #2)
    # Writes to 0x20000000 (VRAM) must NOT corrupt BRAM at addr 0.
    # Before fix: 0x20000000 & BRAM_MASK = 0x0 → overwrote firmware
    # ============================================================
    li a3, '1'; sw a3, 4(a4)

    # Save BRAM word 0 (should be our own code)
    lw s0, 0(zero)           # read BRAM[0] via uncached (IO bus)

    # Write to VRAM address (goes through IO bus → LOCAL → BRAM model)
    lui a5, 0x20000
    li t0, 0xDEADDEAD
    sw t0, 0(a5)             # VRAM write — must be silently dropped
    fence

    # Read BRAM word 0 again — must match original
    lw s1, 0(zero)
    bne s0, s1, 1f
    li a3, 'P'; sw a3, 4(a4)
    j 2f
1:  li a3, 'F'; sw a3, 4(a4)
2:

    # ============================================================
    # Test 2: IO during D-cache traffic (bug #3)
    # UART write (IO bus) must complete while D-cache is active.
    # Before fix: D-cache fills starved IO bus indefinitely.
    # ============================================================
    li a3, '2'; sw a3, 4(a4)

    # Generate D-cache traffic: write 4KB to SDRAM (64 cache lines)
    lui a5, 0x10800
    lui a6, 0x10801          # end = +4096
    li t0, 0x55555555
3:  sw t0, 0(a5)
    addi a5, a5, 4
    bne a5, a6, 3b

    # Now do an IO write (UART) — must not hang
    li a3, 'P'; sw a3, 4(a4)
    # If we get here, IO wasn't starved

    # ============================================================
    # Test 3: SDRAM write then read-back (validates burst write path)
    # Write 16 words to SDRAM, flush, read back via uncached alias.
    # Tests the cpu_system → axi_sdram_slave write path (bug #1).
    # ============================================================
    li a3, '3'; sw a3, 4(a4)

    # Write 16 words (one cache line) to 0x10900000
    lui a5, 0x10900
    li t0, 0xCAFE0000
    sw t0, 0(a5)
    addi t0, t0, 1
    sw t0, 4(a5)
    addi t0, t0, 1
    sw t0, 8(a5)
    addi t0, t0, 1
    sw t0, 12(a5)
    addi t0, t0, 1
    sw t0, 16(a5)
    addi t0, t0, 1
    sw t0, 20(a5)
    addi t0, t0, 1
    sw t0, 24(a5)
    addi t0, t0, 1
    sw t0, 28(a5)
    addi t0, t0, 1
    sw t0, 32(a5)
    addi t0, t0, 1
    sw t0, 36(a5)
    addi t0, t0, 1
    sw t0, 40(a5)
    addi t0, t0, 1
    sw t0, 44(a5)
    addi t0, t0, 1
    sw t0, 48(a5)
    addi t0, t0, 1
    sw t0, 52(a5)
    addi t0, t0, 1
    sw t0, 56(a5)
    addi t0, t0, 1
    sw t0, 60(a5)
    fence

    # Flush to SDRAM
    .insn i 0x0F, 2, x0, a5, 0x002   # cbo.flush
    fence

    # Read back via uncached alias (0x50900000)
    lui a6, 0x50900
    li t0, 0xCAFE0000
    li t3, 0                 # error count
    lw t2, 0(a6);  bne t2, t0, 4f; j 5f; 4: addi t3, t3, 1; 5:
    addi t0, t0, 1
    lw t2, 4(a6);  bne t2, t0, 6f; j 7f; 6: addi t3, t3, 1; 7:
    addi t0, t0, 1
    lw t2, 8(a6);  bne t2, t0, 8f; j 9f; 8: addi t3, t3, 1; 9:
    addi t0, t0, 1
    lw t2, 60(a6)
    li t1, 0xCAFE000F
    bne t2, t1, 10f; j 11f; 10: addi t3, t3, 1; 11:

    beqz t3, 12f
    li a3, 'F'; sw a3, 4(a4); j 13f
12: li a3, 'P'; sw a3, 4(a4)
13:

    # ============================================================
    # Test 4: cbo.inval + immediate stores (bug #5)
    # cbo.inval on a line, then immediately write to it.
    # Tests pipeline hazard between invalidation and stores.
    # ============================================================
    li a3, '4'; sw a3, 4(a4)

    # Pre-fill a cache line at 0x10A00000
    lui a5, 0x10A00
    li t0, 0xAAAAAAAA
    sw t0, 0(a5)
    sw t0, 4(a5)
    sw t0, 60(a5)
    fence

    # cbo.inval then immediately write (no fence between)
    .insn i 0x0F, 2, x0, a5, 0x000   # cbo.inval
    # Immediate stores — pipeline might stall here
    li t0, 0xBBBBBBBB
    sw t0, 0(a5)
    sw t0, 4(a5)
    sw t0, 8(a5)
    sw t0, 12(a5)
    sw t0, 16(a5)
    sw t0, 20(a5)
    sw t0, 24(a5)
    sw t0, 28(a5)
    sw t0, 32(a5)
    sw t0, 36(a5)
    sw t0, 40(a5)
    sw t0, 44(a5)
    sw t0, 48(a5)
    sw t0, 52(a5)
    sw t0, 56(a5)
    sw t0, 60(a5)
    fence

    # If we get here without hanging, the pipeline didn't stall
    # Verify data
    lw t2, 0(a5)
    li t1, 0xBBBBBBBB
    bne t2, t1, 14f
    lw t2, 60(a5)
    bne t2, t1, 14f
    li a3, 'P'; sw a3, 4(a4); j 15f
14: li a3, 'F'; sw a3, 4(a4)
15:

    # ============================================================
    # Test 5: Sustained cbo.inval + store loop (memcpy pattern)
    # 4KB of cbo.inval + 64B store, like the memcpy optimization.
    # This is the pattern that hangs on real hardware.
    # ============================================================
    li a3, '5'; sw a3, 4(a4)

    # Source: fill 4KB at 0x10B00000
    lui a5, 0x10B00
    lui a6, 0x10B01
    li t0, 0x12345678
16: sw t0, 0(a5)
    addi a5, a5, 4
    bne a5, a6, 16b
    fence

    # Dest: 0x10C00000
    lui s2, 0x10B00          # src
    lui s3, 0x10C00          # dst
    li s4, 4096              # remaining
    li t1, 64

17: bltu s4, t1, 18f
    # cbo.inval on dest line
    .insn i 0x0F, 2, x0, s3, 0x000
    # Copy 64 bytes
    lw t2,  0(s2); lw t3,  4(s2); sw t2,  0(s3); sw t3,  4(s3)
    lw t2,  8(s2); lw t3, 12(s2); sw t2,  8(s3); sw t3, 12(s3)
    lw t2, 16(s2); lw t3, 20(s2); sw t2, 16(s3); sw t3, 20(s3)
    lw t2, 24(s2); lw t3, 28(s2); sw t2, 24(s3); sw t3, 28(s3)
    lw t2, 32(s2); lw t3, 36(s2); sw t2, 32(s3); sw t3, 36(s3)
    lw t2, 40(s2); lw t3, 44(s2); sw t2, 40(s3); sw t3, 44(s3)
    lw t2, 48(s2); lw t3, 52(s2); sw t2, 48(s3); sw t3, 52(s3)
    lw t2, 56(s2); lw t3, 60(s2); sw t2, 56(s3); sw t3, 60(s3)
    addi s2, s2, 64
    addi s3, s3, 64
    addi s4, s4, -64
    j 17b
18: fence

    # Verify first and last words
    lui a5, 0x10C00
    lw t2, 0(a5)
    li t1, 0x12345678
    bne t2, t1, 19f
    addi t6, a5, 2047; lw t2, 2045(t6)         # last word
    bne t2, t1, 19f
    li a3, 'P'; sw a3, 4(a4); j 20f
19: li a3, 'F'; sw a3, 4(a4)
20:

    # ============================================================
    # Done
    # ============================================================
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
    csrr a0, mcause; andi a3, a0, 15; addi a3, a3, '0'
    li t6, ':'; blt a3, t6, 21f; addi a3, a3, 7
21: sw a3, 4(a4)
    li a3, '@'; sw a3, 4(a4)
    csrr a0, mepc
    li t5, 28
22: srl a3, a0, t5; andi a3, a3, 15; addi a3, a3, '0'
    li t6, ':'; blt a3, t6, 23f; addi a3, a3, 7
23: sw a3, 4(a4); addi t5, t5, -4; bgez t5, 22b
    li a3, '\n'; sw a3, 4(a4)
    lui a5, 0x10; addi a5, a5, -256; li a3, 2; sw a3, 0(a5); j .
