.section .text.start,"ax",@progbits
.global _start
_start:
    li sp, 0x20000
    li t0, 0x6000
    csrs mstatus, t0
    call main
    li t0, 0x20000
    li t1, 2
    sw t1, 0(t0)
1:  j 1b
