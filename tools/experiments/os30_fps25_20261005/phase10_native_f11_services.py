#!/usr/bin/env python3
"""Build and check the bounded guest-owned services; no native CPU simulation."""
import csv,hashlib,json,pathlib,re,subprocess,struct,os,shutil,difflib
R=pathlib.Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'native-f11-services-v1'; C=P/'native-f11-warm-capture-v1/run-reference/snapshot'
SUPPORTED=[12,13,14,15,19,20,24,26,34,35,36,56,68]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
def run(cmd,log):
 r=subprocess.run(cmd,cwd=O,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=dict(os.environ,ASAN_OPTIONS="detect_leaks=0"))
 (O/log).write_bytes(r.stdout); assert r.returncode==0,(cmd,r.returncode,r.stdout.decode(errors='replace'));return r.stdout.decode()
def voice(v):return '{'+','.join(str(v[k])+('ull' if k in ['position','last_us','fraction'] else '')for k in ['position','last_us','fraction','count','rate','loop_start','loop_end','active'])+'}'
def prepare():
 a=json.loads((C/'service-state.json').read_text());b=json.loads((C/'service-state-final.json').read_text());rows=list(csv.DictReader((C/'services.csv').open()))
 assert a['flips']==360 and b['flips']==362 and a['SM64_AUDIO'] and a['sound_enabled']==1
 assert len(rows)==229 and set(int(x['index'])for x in rows if x['kind']=='0')==set(SUPPORTED)
 assert all(int(x['flip_token'])==int(x['frame'])+1 for x in rows)
 assert a['mmap_used']==170 and not any(x['free']for x in a['mmap_slots'])
 assert [(i,x)for i,x in enumerate(b['mmap_slots'])if x!=a['mmap_slots'][i]]==[(170,{'base':0x1336e000,'len':16384,'free':0})]
 state='/* Generated only from the frozen initial service state. */\n'
 state+='NativeVoice native_voices[32] = {\n'+',\n'.join(voice(v)for v in a['voices'])+'\n};\n'
 state+=f'uint32_t native_ended={a["ended"]}, native_mmap_bottom={a["mmap_bottom"]}u, native_mmap_used={a["mmap_used"]};\n'
 state+=f'int32_t native_display={a["display"]}, native_ready={a["ready"]}, native_draw={a["draw"]}, native_swap_kicked={a["swap_kicked"]};\n'
 state+=f'#define NATIVE_CAPTURE_CUR_BRK {a["cur_brk"]}u\n'
 (O/'native_state.h').write_text(state)
 trace='typedef struct { uint32_t kind,index,fid,token,a[8],r0,r1,out_address,out_size; } Trace;\nstatic const Trace trace[] = {\n'
 for x in rows:
  assert x['out_hex']=='' or x['out_hex']=='00'*int(x['out_size'])
  trace+='{'+','.join(x[k]+'u'for k in ['kind','index','fid','flip_token'])+',{'+','.join(x[f'a{i}']+'u'for i in range(8))+'},'+','.join(x[k]+'u'for k in ['r0','r1','out_address','out_size'])+'},\n'
 trace+='};\nstatic const NativeVoice final_voices[32]={\n'+',\n'.join(voice(v)for v in b['voices'])+'\n};\n'
 (O/'trace.h').write_text(trace)
 put(O/'capture-binding.json',dict(capture_manifest_sha256=sha(P/'native-f11-warm-capture-v1/manifest.json'),records=len(rows),initial_mmap_used=a['mmap_used'],final_mmap_used=b['mmap_used'],only_mmap_slot_changed=170,source_sha256={str(p.relative_to(R)):sha(p) for p in [C/'service-state.json',C/'service-state-final.json',C/'services.csv']}))
def wrapper_check(elf):
 raw=elf.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',raw);ss=[struct.unpack_from('<10I',raw,h[6]+i*h[11])for i in range(h[12])];st=ss[h[13]];names=raw[st[4]:st[4]+st[5]]
 name=lambda b,i:b[i:b.index(0,i)].decode()
 sections={name(names,s[0]):s for s in ss};sy=sections['.symtab'];syms={}
 strtab=ss[sy[6]];strings=raw[strtab[4]:strtab[4]+strtab[5]]
 for p in range(sy[4],sy[4]+sy[5],sy[9]):
  n,v,sz,info,other,ix=struct.unpack_from('<IIIBBH',raw,p)
  if n:syms[name(strings,n)]=v
 mem={}
 for s in ss:
  if s[2]&2 and s[1]!=8:
   for off in range(s[5]):mem[s[3]+off]=raw[s[4]+off]
 def word(pc):return sum(mem[pc+i]<<(8*i)for i in range(4))
 def signed(v,n):return v-(1<<n)if v>>(n-1)else v
 tested=0
 for idx in range(106):
  pc=0x103f0000+idx*4
  if idx not in SUPPORTED:assert word(pc)==0x00100073;continue
  for pattern in [0x01010101,0x9e3779b9,0xffffffff]:
   regs=[(i*pattern)&0xffffffff for i in range(32)];regs[0]=0;regs[1]=0xfffffffc;regs[2]=0x13ff0000;orig=regs[:];stack={};calls=0
   for step in range(100):
    if pc==0xfffffffc:break
    if pc==syms['native_service_dispatch']:
     assert regs[10]==idx and regs[11]==orig[2]-48
     assert [stack[regs[11]+4*i]for i in range(8)]==orig[10:18]
     ret=regs[1]
     for i in [1,5,6,7,10,11,12,13,14,15,16,17,28,29,30,31]:regs[i]=0xdeaddead
     regs[10]=0x12345678;regs[11]=0x89abcdef;pc=ret;calls+=1;continue
    w=word(pc);op=w&127;rd=w>>7&31;f3=w>>12&7;rs1=w>>15&31;rs2=w>>20&31;nxt=pc+4;v=None
    if op==0x13:assert f3==0;v=regs[rs1]+signed(w>>20,12)
    elif op==0x17:v=pc+(w&0xfffff000)
    elif op==0x6f:
     imm=((w>>31)<<20)|(((w>>12)&255)<<12)|(((w>>20)&1)<<11)|(((w>>21)&1023)<<1);v=pc+4;nxt=pc+signed(imm,21)
    elif op==0x67:v=pc+4;nxt=(regs[rs1]+signed(w>>20,12))&~1
    elif op==0x23:
     assert f3==2;imm=((w>>25)<<5)|((w>>7)&31);stack[(regs[rs1]+signed(imm,12))&0xffffffff]=regs[rs2]
    elif op==3:assert f3==2;v=stack[(regs[rs1]+signed(w>>20,12))&0xffffffff]
    else:raise AssertionError(hex(w))
    if rd and v is not None:regs[rd]=v&0xffffffff
    pc=nxt&0xffffffff
   assert pc==0xfffffffc and calls==1
   assert all(regs[i]==orig[i]for i in range(32)if i not in [10,11])
   assert regs[10:12]==[0x12345678,0x89abcdef];tested+=1;pc=0x103f0000+idx*4
 assert sections['.native_services'][5]==106*4
 return dict(assembled_wrappers=tested,unsupported_ebreak_vectors=106-len(SUPPORTED),service_vector_bytes=424,code_data_end=syms['__component_end'])
def main():
 assert not O.exists()
 O.mkdir()
 parent=P/'native-renderer-services-v3'
 for name in ['native_services.S','native_services.c','host_check.c']:
  old=(parent/name).read_text()
  new=old.replace('0x10fe1604', '0x10fda714')
  assert (old==new)==(name=='native_services.S')
  (O/name).write_text(new)
 put(O/'source-binding.json',dict(parent_manifest_sha256=sha(parent/'component-manifest.json'),token_address=0x10fda714,
     scope='F11 normal on selected v7 core; unchanged service formulas and supported forms; new captured state.',
     parent_sha256={n:sha(parent/n)for n in ['native_services.S','native_services.c','host_check.c']}))
 assert not(O/'component-manifest.json').exists()
 prepare()
 run(['gcc','-std=c11','-O2','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer','host_check.c','-o','host_check'],'host-build.log')
 host=run([str(O/'host_check')],'host-result.log')
 flags=['-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-msmall-data-limit=0','-mno-relax','-O2','-ffreestanding','-fno-builtin','-fno-stack-protector','-Wall','-Wextra','-Werror','-fstack-usage']
 for src,obj in [('native_services.c','services-c.o'),('native_services.S','services-s.o')]:run(['riscv64-elf-gcc',*flags,'-c',src,'-o',obj],obj+'.log')
 (O/'component.ld').write_text('SECTIONS { . = 0x10320000; .text : { *(.text*) } .rodata : { *(.rodata*) } .data : { *(.data*) *(.sdata*) } .bss : { *(.bss*) *(.sbss*) *(COMMON) } __component_end = .; ASSERT(. < 0x10340000, "overlay overflow") . = 0x103f0000; .native_services : { *(.native_services) } }\n')
 run(['riscv64-elf-gcc',*flags,'-nostdlib','-Wl,--no-relax,-T,component.ld','services-c.o','services-s.o','-lgcc','-o','component.elf'],'link.log')
 asm=run(['riscv64-elf-objdump','-d','component.elf'],'component.asm')
 assert not re.search(r'\t(?:f(?:add|sub|mul|div|sqrt|madd|msub|nmadd|nmsub|cvt|sgnj|class|min|max|eq|lt|le|mv|lw|sw|ld|sd)\S*)\s',asm)
 assert run(['riscv64-elf-nm','-u','component.elf'],'undefined.log').strip()==''
 wr=wrapper_check(O/'component.elf');assert wr['code_data_end']<0x10340000
 result=dict(passed=True,host_actual_c_trace_records=229,asan_ubsan_passed=True,final_voice_records_exact=32,final_ended_exact=True,final_mmap_state_exact=True,wrapper_proof=wr,actual_native_integration_pending=True,no_guest_fp_instructions=True,limits=['Bounded tokens361..363 only; not a general timer or OS.','Functional voice lifetime only, no PCM or interrupt cost claim.','Host MMIO scripts qualify video logic but actual native role selection may differ with hardware timing.','Actual CPU execution/cache coherence remain the integration owner\'s pending proof.','One captured anonymous allocation only; inherited170 slots are immutable and only new slot170 is summarized.'])
 put(O/'component-result.json',result)
 files=[p for p in O.iterdir()if p.is_file() and p.name!='component-manifest.json']+[pathlib.Path(__file__),C/'services.csv',C/'service-state.json',C/'service-state-final.json',P/'native-f11-warm-capture-v1/manifest.json',P/'native-f11-warm-capture-v1/qsim/env.c']
 assert files[-1].exists()
 put(O/'component-manifest.json',dict(passed=True,deployment_ready=False,native_integration_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files}))
 print(host, json.dumps(result,indent=2),'manifest',sha(O/'component-manifest.json'))
if __name__=='__main__':main()
