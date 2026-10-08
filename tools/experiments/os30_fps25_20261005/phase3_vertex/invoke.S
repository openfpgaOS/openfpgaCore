.section .text.invoke_vertex,"ax"
.global invoke_vertex
.type invoke_vertex,@function
invoke_vertex:
  addi sp, sp, -16
  sw ra, 12(sp)
  sw gp, 8(sp)
  sw s0, 4(sp)
  mv s0, sp
  mv sp, a3
  li gp, 0x10fdbc70
  li t0, 0x104d0180
  jalr ra, t0, 0
  mv sp, s0
  lw s0, 4(sp)
  lw gp, 8(sp)
  lw ra, 12(sp)
  addi sp, sp, 16
  ret
.size invoke_vertex,.-invoke_vertex
.section .corpus,"a"
.balign 4
.global corpus_start,corpus_end
corpus_start:
.incbin "vertex-state.bin"
corpus_end:
