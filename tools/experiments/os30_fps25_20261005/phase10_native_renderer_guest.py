#!/usr/bin/env python3
"""Build a private architectural restore/return shim for one native render call."""
import argparse,json,shutil,struct,subprocess
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';OUT=P/'native-renderer-window-v1/guest-v2'
SNAP=P/'native-renderer-capture-v1/run-reference/snapshot'
SERV=P/'native-renderer-services-v2'
APP=P/'renderer-pgo-v1/use/app.elf'

ASM=r'''
.option norvc
.option norelax
.section .text.start,"ax",@progbits
.global native_restore
native_restore:
  csrw mie, zero
  li t0, 0x6000
  csrs mstatus, t0
  la t0, native_trap
  csrw mtvec, t0
  la t6, native_saved_state
  lw t0, 260(t6)
  csrw fcsr, t0
  lw t0, 280(t6)
  csrw mscratch, t0
  lw t0, 268(t6)
  csrw mie, t0
  lw t0, 264(t6)
  andi t1, t0, 8
  slli t1, t1, 4
  li t2, -6281 /* clear MIE/MPIE/MPP: ~(8+128+6144) */
  and t0, t0, t2
  li t2, 0x1800
  or t0, t0, t2
  or t0, t0, t1
  csrw mstatus, t0
  lw t0, 256(t6)
  csrw mepc, t0
@RESTORE_FLOAT@
@RESTORE_INTEGER@
  mret

.balign 4
native_trap:
  csrr t0, mcause
  li t1, 3
  bne t0, t1, native_fault
  csrr t0, mepc
  li t1, @RETURN@
  bne t0, t1, native_fault
  la t0, native_phase
  lw t1, 0(t0)
  bnez t1, native_fault
  li t1, 1
  sw t1, 0(t0)
  la ra, native_finished
  li t0, @END@
  csrw mepc, t0
  mret

.global native_finished
native_finished:
  fence iorw, iorw
  li t0, 0x4a000014
1:
  lw t1, 0(t0)
  andi t1, t1, 1
  bnez t1, 1b
  fence iorw, iorw
.global native_complete
native_complete:
  la a0, native_done_message
  call native_puts
2:
  j 2b

.global native_fault
native_fault:
  csrr s0, mcause
  csrr s1, mepc
  csrr s2, mtval
  la a0, native_fail_message
  call native_puts
  mv a0, s0
  call native_hex
  mv a0, s1
  call native_hex
  mv a0, s2
  call native_hex
  la a0, native_newline
  call native_puts
3:
  j 3b

native_puts:
  li t3, 0x4f000000
4:
  lbu t4, 0(a0)
  beqz t4, 6f
5:
  lw t5, 0(t3)
  andi t5, t5, 2
  beqz t5, 5b
  sw t4, 4(t3)
  addi a0, a0, 1
  j 4b
6:
  ret
native_hex:
  li t0, 8
  la t1, native_digits
  li t3, 0x4f000000
7:
  srli t2, a0, 28
  add t2, t1, t2
  lbu t4, 0(t2)
8:
  lw t5, 0(t3)
  andi t5, t5, 2
  beqz t5, 8b
  sw t4, 4(t3)
  slli a0, a0, 4
  addi t0, t0, -1
  bnez t0, 7b
9:
  lw t5, 0(t3)
  andi t5, t5, 2
  beqz t5, 9b
  li t4, 32
  sw t4, 4(t3)
  ret

.section .rodata,"a",@progbits
native_done_message: .asciz "NATIVE_RENDER_DONE\n"
native_fail_message: .asciz "NATIVE_RENDER_FAIL "
native_newline: .asciz "\n"
native_digits: .ascii "0123456789abcdef"
.balign 64
native_saved_state: .incbin "cpu-state.bin"
.section .data,"aw",@progbits
.balign 4
native_phase: .word 0
'''
LINK='''ENTRY(native_restore)
PHDRS { native PT_LOAD FLAGS(7); services PT_LOAD FLAGS(5); }
SECTIONS {
 . = 0x10320000;
 .text : { *(.text.start) *(.text*) } :native
 .rodata : { *(.rodata*) } :native
 .data : { *(.data*) *(.sdata*) } :native
 .bss : { *(.bss*) *(.sbss*) *(COMMON) } :native
 ASSERT(. < 0x10340000, "Native overlay overlaps service table")
 . = 0x103f0000;
 .native_services : { *(.native_services) } :services
 ASSERT(SIZEOF(.native_services)==424, "Expected106 service vector slots")
}
'''

def segments(path):
    b=path.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b)
    return [(q[2],q[5],b[q[1]:q[1]+q[4]])for i in range(h[10])
            if (q:=struct.unpack_from('<8I',b,h[5]+i*h[9]))[0]==1 and q[5]]

def build():
    assert not OUT.exists();OUT.mkdir()
    s=json.loads((SNAP/'state.json').read_text())
    words=struct.unpack('<77I',(SNAP/'cpu-state.bin').read_bytes())
    assert words[64]==s['gfx_run'] and words[1]==s['return_pc'] and not words[75]
    assert words[67]==words[68]==0 # no selected IRQ source; traps are private guest environment
    assert words[74]==0 # qsim-only CSR7ff has no decoder in selected Vexii; do not emit it
    shutil.copy2(SNAP/'cpu-state.bin',OUT/'cpu-state.bin')
    asm=ASM.replace('@RESTORE_FLOAT@','\n'.join(f'  flw f{i}, {128+4*i}(t6)'for i in range(32)))
    asm=asm.replace('@RESTORE_INTEGER@','\n'.join(f'  lw x{i}, {4*i}(t6)'for i in list(range(1,31))+[31]))
    asm=asm.replace('@RETURN@',hex(s['return_pc'])).replace('@END@',hex(s['gfx_end_frame']))
    (OUT/'restore.S').write_text(asm);(OUT/'link.ld').write_text(LINK)
    shutil.copy2(SERV/'native_services.S',OUT/'native_services.S')
    cmd=['riscv64-elf-gcc','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-nostdlib','-nostartfiles',
         '-Wl,-T,link.ld','-Wl,--no-relax','restore.S','native_services.S','-o','overlay.elf']
    with(OUT/'build.log').open('x')as f:subprocess.run(cmd,cwd=OUT,stdout=f,stderr=subprocess.STDOUT,check=True)
    with(OUT/'overlay.asm').open('x')as f:subprocess.run(['riscv64-elf-objdump','-d',str(OUT/'overlay.elf')],stdout=f,check=True)
    symbols={row.split()[2]:int(row.split()[0],16)for row in subprocess.check_output(['riscv64-elf-nm','-n',str(OUT/'overlay.elf')],text=True).splitlines()if len(row.split())==3}
    ss=[]
    for i,(address,size,data)in enumerate(segments(OUT/'overlay.elf')):
        assert (0x10320000<=address and address+size<=0x10340000)or(address==0x103f0000 and size==424)
        name=f'overlay-{i}.bin';(OUT/name).write_bytes(data+bytes(size-len(data)));ss.append(dict(address=address,size=size,path=name,sha256=sha(OUT/name)))
    # One compressed caller instruction becomes a return gate; renderer text is untouched.
    original=(SNAP/'sdram.bin').read_bytes();off=s['return_pc']-s['sdram_base'];prior=original[off:off+2]
    assert prior==bytes.fromhex('0d45') # li a0,3 in display_and_vsync after tail-called renderer
    (OUT/'return-shim.bin').write_bytes(bytes.fromhex('0290'))
    ss.append(dict(address=s['return_pc'],size=2,path='return-shim.bin',sha256=sha(OUT/'return-shim.bin'),before_hex=prior.hex(),scope='One caller boundary instruction, outside gfx_run/gfx_end_frame'))
    write(OUT/'overlay.json',dict(segments=ss,symbols=symbols,entry=symbols['native_restore'],
        note='Apply all segments before CPU release only. Never flatten sparse overlay across captured service table/data.',
        csr_environment='All integer/FP registers and FCSR restored; mtvec is private fail-loud trap handler; mepc/mstatus MPP/MPIE are the normal MRET entry mechanism. Captured mie=mip=0. No interrupts injected. Qsim-only CSR7ff is required zero and not written, because selected Vexii has no such CSR.'))
    config=dict(entry_pc=symbols['native_restore'],gfx_entry_pc=s['gfx_run'],gfx_end_pc=s['gfx_end_frame'],
        completion_pc=symbols['native_complete'],fault_pc=symbols['native_fault'],return_pc=s['return_pc'],
        framebuffer_address=0x10000000,framebuffer_bytes=153600,depth_address=s['sdram_base']+s['depth_address'],depth_bytes=153600,
        ring_word_pointer=s['ring_byte_pointer']//4,last_fence=s['fence'],expected_fence=s['fence']+1,
        display_index=s['display_index'],pending_index=s['pending_index'],draw_index=s['draw_index'],
        width=s['width'],height=s['height'],stride=s['stride'],color_mode=s['color_mode'])
    (OUT/'run-config.txt').write_text(''.join(f'{k}={v}\n'for k,v in config.items()))
    write(OUT/'prepared.json',dict(command=cmd,elf_sha256=sha(APP),snapshot_manifest_sha256=sha(P/'native-renderer-capture-v1/manifest.json'),
        services_component_manifest_sha256=sha(SERV/'component-manifest.json'),symbols=symbols,
        files={f.name:sha(f)for f in OUT.iterdir()if f.is_file()},helper_sha256=sha(Path(__file__)),native_execution_pending=True))
    print(json.dumps(config,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['build']);a=p.parse_args();globals()[a.action]()
