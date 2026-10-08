.section .text.invoke,"ax"
.global invoke_load
.type invoke_load,@function
invoke_load:
 addi sp,sp,-16
 sw ra,12(sp)
 sw gp,8(sp)
 li gp,0x10fdac58
 li t0,0x104cf140
 jalr ra,t0,0
 lw gp,8(sp)
 lw ra,12(sp)
 addi sp,sp,16
 ret
.size invoke_load,.-invoke_load

.global invoke_copy
.type invoke_copy,@function
invoke_copy:
 addi sp,sp,-16
 sw ra,12(sp)
 sw gp,8(sp)
 li gp,0x10fdac58
 li t0,0x104cf0c0
 jalr ra,t0,0
 lw gp,8(sp)
 lw ra,12(sp)
 addi sp,sp,16
 ret
.size invoke_copy,.-invoke_copy
