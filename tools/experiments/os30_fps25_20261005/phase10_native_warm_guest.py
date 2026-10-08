#!/usr/bin/env python3
"""Private continuous native guest: original caller, guest services, host entry stop."""
import argparse
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path
from phase10_experiment import CAMP, ROOT, module, sha, write

P=CAMP/'phase10'
OUT=P/'native-renderer-window-v1/guest-warm-v3'
CAP=P/'native-warm-capture-v4'
SNAP=CAP/'run-reference/snapshot'
SERV=P/'native-renderer-services-v3'
APP=P/'renderer-pgo-delivery-v7/payload/Assets/sm64/common/sm64.elf'
old=module('guest_restore_base',ROOT/'tools/experiments/os30_fps25_20261005/phase10_native_renderer_guest.py')


def assembly():
    s=old.ASM
    begin=s.index('native_trap:\n');end=s.index('.global native_finished',begin)
    save='\n'.join(f'  sw x{i}, {4*i}(sp)' for i in [1]+list(range(3,32)))
    restore='\n'.join(f'  lw x{i}, {4*i}(sp)' for i in [1]+list(range(3,32)))
    trap=f'''native_trap:
  addi sp, sp, -144
{save}
  csrr t0, mcause
  li t1, 11
  bne t0, t1, native_fault
  lw a0, 68(sp) /* a7: Linux syscall number or vendor EID */
  lw a1, 64(sp) /* a6: vendor function id */
  addi a2, sp, 40 /* saved a0..a7 */
  call native_vendor_dispatch
  sw a0, 40(sp)
  sw a1, 44(sp)
  csrr t0, mepc
  addi t0, t0, 4
  csrw mepc, t0
{restore}
  addi sp, sp, 144
  mret

'''
    # No return gate and no forced per-call drain. The host observes entry2/3.
    s=s[:begin]+trap+s[end:]
    begin=s.index('.global native_finished');end=s.index('.global native_fault',begin)
    s=s[:begin]+s[end:]
    s=s.replace('native_trap:\n','.global native_trap\nnative_trap:\n',1)
    return s


def build():
    assert not OUT.exists()
    capture=json.loads((CAP/'result.json').read_text());assert capture['passed']
    proof=json.loads((SERV/'component-manifest.json').read_text());assert proof.get('passed',True)
    s=json.loads((SNAP/'state.json').read_text())
    words=struct.unpack('<77I',(SNAP/'cpu-state.bin').read_bytes())
    assert words[64]==s['gfx_run'] and words[1]==s['return_pc']
    assert words[67]==words[68]==words[74]==words[75]==0
    OUT.mkdir()
    shutil.copyfile(SNAP/'cpu-state.bin',OUT/'cpu-state.bin')
    asm=assembly().replace('@RESTORE_FLOAT@','\n'.join(f'  flw f{i}, {128+4*i}(t6)'for i in range(32)))
    asm=asm.replace('@RESTORE_INTEGER@','\n'.join(f'  lw x{i}, {4*i}(t6)'for i in list(range(1,31))+[31]))
    assert '@RETURN@' not in asm and '@END@' not in asm
    (OUT/'restore.S').write_text(asm)
    (OUT/'link.ld').write_text(old.LINK)
    for name in ['native_services.S','native_services.c','native_state.h']:
        shutil.copyfile(SERV/name,OUT/name)
    cmd=['riscv64-elf-gcc','-O2','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f',
         '-msmall-data-limit=0','-mno-relax','-fno-pic','-ffreestanding','-fno-builtin',
         '-fno-stack-protector','-nostdlib','-nostartfiles','-Wl,-T,link.ld','-Wl,--no-relax',
         'restore.S','native_services.S','native_services.c','-lgcc','-o','overlay.elf']
    with(OUT/'build.log').open('x')as f:
        subprocess.run(cmd,cwd=OUT,stdout=f,stderr=subprocess.STDOUT,check=True)
    text=subprocess.check_output(['riscv64-elf-objdump','-d',str(OUT/'overlay.elf')],text=True)
    (OUT/'overlay.asm').write_text(text)
    assert '(gp)' not in text, 'Guest globals must not depend on captured application gp'
    instructions=[]
    for line in text.splitlines():
        match=re.match(r'\s*([0-9a-f]+):\s+[0-9a-f]+\s+([^\s]+)',line)
        if match:instructions.append((int(match[1],16),match[2]))
    symbols={r.split()[2]:int(r.split()[0],16)for r in subprocess.check_output(['riscv64-elf-nm','-n',str(OUT/'overlay.elf')],text=True).splitlines()if len(r.split())==3}
    for pc,op in instructions:
        if op.startswith('f') and op not in ('fence','fence.i','frcsr','fscsr'):
            assert op=='flw' and symbols['native_restore']<=pc<symbols['native_trap'],(hex(pc),op)
    ss=[]
    for i,(address,size,data)in enumerate(old.segments(OUT/'overlay.elf')):
        assert (0x10320000<=address and address+size<=0x10340000)or(address==0x103f0000 and size==424)
        name=f'overlay-{i}.bin';(OUT/name).write_bytes(data+bytes(size-len(data)))
        ss.append(dict(address=address,size=size,path=name,sha256=sha(OUT/name)))
    assert len(ss)==2
    assert all(not (x['address']<=s['return_pc']<x['address']+x['size'])for x in ss)
    write(OUT/'overlay.json',dict(segments=ss,symbols=symbols,entry=symbols['native_restore'],
        original_application_instructions_unchanged=True,
        note='Sparse overlay before release only. No caller/renderer patch. Host stops at a selected actual gfx_run entry.',
        csr_environment='Private M-mode ECALL/fault handler; captured integer/FP/FCSR state restored, no IRQs. ECALLs preserve every register except specified a0/a1 returns. Unsupported services fault.'))
    # The upload stream identifies logical draw buffers; preserve raw addresses.
    commands=struct.unpack('<%dI'%((SNAP/'commands.bin').stat().st_size//4),(SNAP/'commands.bin').read_bytes())
    flips=[];i=0
    while i<len(commands):
        hdr=commands[i];n=hdr&0x1fff
        if hdr>>24==0x42:
            assert n==2;flips.append((commands[i+1],commands[i+2]))
        i+=1+n
    assert i==len(commands) and len(flips)==2
    assert [x[1]for x in flips]==[362,363]
    config=dict(entry_pc=symbols['native_restore'],gfx_entry_pc=s['gfx_run'],gfx_end_pc=s['gfx_end_frame'],
        completion_pc=0,finished_pc=0,fault_pc=symbols['native_fault'],return_pc=s['return_pc'],stop_entries=3,
        framebuffer_address=0x10000000,framebuffer_bytes=153600,depth_address=s['sdram_base']+s['depth_address'],depth_bytes=153600,
        boundary1_color_address=0x10000000+flips[0][0]*0x100000,
        boundary2_color_address=0x10000000+flips[1][0]*0x100000,
        ring_word_pointer=s['ring_byte_pointer']//4,last_fence=s['fence'],expected_fence=363,
        display_index=s['display_index'],pending_index=s['pending_index'],draw_index=s['draw_index'],
        width=s['width'],height=s['height'],stride=s['stride'],color_mode=s['color_mode'])
    (OUT/'run-config.txt').write_text(''.join(f'{k}={v}\n'for k,v in config.items()))
    (OUT/'goldens').mkdir()
    for name,src in [('entry-1.bin','cpu-state.bin'),('entry-2.bin','frame361-entry.bin'),('entry-3.bin','frame362-entry.bin')]:
        (OUT/'goldens'/name).write_bytes((SNAP/src).read_bytes()[:66*4])
    for frame in (360,361):shutil.copyfile(CAP/f'run-reference/frame_{frame:05}.bin',OUT/f'goldens/frame-{frame-359}.bin')
    shutil.copyfile(SNAP/'commands.bin',OUT/'goldens/commands.bin')
    shutil.copyfile(__file__,OUT/'recipe.py')
    write(OUT/'prepared.json',dict(command=cmd,elf_sha256=sha(APP),snapshot_manifest_sha256=sha(CAP/'manifest.json'),
        services_component_manifest_sha256=sha(SERV/'component-manifest.json'),symbols=symbols,
        no_application_instruction_overlay=True,no_new_host_live_memory_write=True,
        integer_only_services_linked=True,helper_sha256=sha(Path(__file__)),native_execution_pending=True,
        files={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()}))
    print(json.dumps(config,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['build']);args=parser.parse_args();globals()[args.action]()
