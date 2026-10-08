#!/usr/bin/env python3
"""Bounded literal assembled guest service proof; no CPU/hardware simulation."""
import hashlib,json,pathlib,random,struct
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';O=P/'native-renderer-services-v2';C=P/'native-renderer-capture-v1/run-reference/snapshot'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):p.write_text(json.dumps(d,indent=2,sort_keys=True)+'\n')
def sign(x,n):return x-(1<<n) if x>>(n-1) else x

def object_image():
 b=(O/'native_services.o').read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b);assert h[2]==243
 ss=[struct.unpack_from('<10I',b,h[6]+i*h[11]) for i in range(h[12])];st=ss[h[13]];names=b[st[4]:st[4]+st[5]]
 name=lambda tab,off:tab[off:tab.index(0,off)].decode()
 sections={name(names,s[0]):(i,s) for i,s in enumerate(ss)}
 fixed={'.native_services':0x103f0000,'.text.native_cache_flush_range':0x10320000,'.text.native_video':0x10320400,'.data.native_video':0x10321000}
 base={sections[n][0]:v for n,v in fixed.items()};mem={}
 for ix,addr in base.items():
  s=ss[ix]
  for off in range(0,s[5],4):mem[addr+off]=struct.unpack_from('<I',b,s[4]+off)[0]
 _,sy=sections['.symtab'];syms=[struct.unpack_from('<IIIBBH',b,off) for off in range(sy[4],sy[4]+sy[5],sy[9])]
 reloc=[]
 for _,s in sections.values():
  if s[1]!=4:continue
  assert s[7] in base
  for off in range(s[4],s[4]+s[5],s[9]):
   place,info,addend=struct.unpack_from('<IIi',b,off);typ=info&255;sym=syms[info>>8];target=base[sym[5]]+sym[1]+addend;pc=base[s[7]]+place;old=mem[pc]
   if typ==17:
    disp=target-pc;assert -(1<<20)<=disp<(1<<20) and not disp&1;u=disp&0x1fffff;assert old&127==0x6f
    mem[pc]=(old&0xfff)|((u>>20)<<31)|(((u>>1)&0x3ff)<<21)|(((u>>11)&1)<<20)|(((u>>12)&255)<<12)
   elif typ==26:assert old&127==0x37;mem[pc]=(old&0xfff)|((target+0x800)&0xfffff000)
   elif typ==27:assert old&127==0x13;mem[pc]=(old&0xfffff)|((target&0xfff)<<20)
   else:raise AssertionError(('unsupported relocation',typ))
   reloc.append(dict(pc=pc,target=target,type=typ))
 assert len([p for p in mem if 0x103f0000<=p<0x103f1000])==106
 return mem,reloc

def run(image,index,a0,a1,roles=(1,0xffffffff,1,0),fence_values=(361,)):
 mem=dict(image);mem.update({0x10321000+4*i:v&0xffffffff for i,v in enumerate(roles)})
 regs=[(i*0x01010101)&0xffffffff for i in range(32)];regs[0]=0;regs[1]=0xfffffffc;regs[10]=a0;regs[11]=a1;before=regs[:];pc=0x103f0000+4*index;cbo=[];fences=reads=0;stores=[]
 for step in range(2600000):
  if pc==0xfffffffc:
   assert all(regs[i]==before[i] for i in [1,2,3,4,8,9,11,18,19,20,21,22,23,24,25,26,27]);return dict(status='return',a0=regs[10],cbo=cbo,fences=fences,reads=reads,stores=stores,roles=[mem[0x10321000+4*i] for i in range(4)])
  w=mem[pc];op=w&127;rd=(w>>7)&31;f3=(w>>12)&7;rs1=(w>>15)&31;rs2=(w>>20)&31;nextpc=pc+4;v=None
  if op==0x37:v=w&0xfffff000
  elif op==0x13:
   imm=sign(w>>20,12);assert f3 in [0,1,7]
   v=regs[rs1]+imm if f3==0 else regs[rs1]<<(imm&31) if f3==1 else regs[rs1]&imm
  elif op==0x33:assert f3==0 and w>>25 in [0,32];v=regs[rs1]+(-regs[rs2] if w>>25==32 else regs[rs2])
  elif op==3:
   assert f3==2;addr=(regs[rs1]+sign(w>>20,12))&0xffffffff
   if addr==0x4a000018:v=fence_values[min(reads,len(fence_values)-1)];reads+=1
   else:assert 0x10321000<=addr<0x10321010;v=mem[addr]
  elif op==0x23:
   assert f3==2;imm=((w>>25)<<5)|((w>>7)&31);addr=(regs[rs1]+sign(imm,12))&0xffffffff;assert addr in [0x10321008,0x1032100c];mem[addr]=regs[rs2];stores.append((addr,regs[rs2]))
  elif op==0x63:
   imm=((w>>31)<<12)|(((w>>7)&1)<<11)|(((w>>25)&63)<<5)|(((w>>8)&15)<<1)
   take={0:regs[rs1]==regs[rs2],1:regs[rs1]!=regs[rs2],4:sign(regs[rs1],32)<sign(regs[rs2],32),5:sign(regs[rs1],32)>=sign(regs[rs2],32),6:regs[rs1]<regs[rs2],7:regs[rs1]>=regs[rs2]}[f3]
   if take:nextpc=pc+sign(imm,13)
  elif op==0x6f:
   imm=((w>>31)<<20)|(((w>>12)&255)<<12)|(((w>>20)&1)<<11)|(((w>>21)&1023)<<1);v=pc+4;nextpc=pc+sign(imm,21)
  elif op==0x67:assert f3==0;v=pc+4;nextpc=(regs[rs1]+sign(w>>20,12))&~1
  elif op==0x0f:
   if w==0x0ff0000f:fences+=1
   else:assert w==0x0022a00f;cbo.append(regs[5])
  elif op==0x73:assert w==0x00100073;return dict(status='fault',pc=pc,diagnostic=regs[7],cbo=cbo,fences=fences,reads=reads,stores=stores)
  else:raise AssertionError(hex(w))
  if v is not None and rd:regs[rd]=v&0xffffffff
  regs[0]=0;pc=nextpc&0xffffffff
 raise AssertionError('literal replay did not terminate')

def main():
 assert not(O/'component-result.json').exists();mem,reloc=object_image();state=json.loads((C/'state.json').read_text());assert [state[k] for k in ['display_index','pending_index','draw_index','fence']]==[1,-1,1,361]
 for i in range(106):
  w=mem[0x103f0000+4*i];assert (w&127==0x6f) if i in [12,13,68] else w==0x00100073
  if i not in [12,13,68]:assert run(mem,i,0,0)['pc']==0x103f0000+4*i
 rng=random.Random(68064);cases=[(0x11000000+off,n)for off in range(64)for n in range(257)]
 cases += [(a,n)for a in [0,0xfffffff,0x10000000,0x10000001,0x13ffff80,0x13ffffbf,0x13ffffc0,0x13ffffff,0x14000000,0xffffffff]for n in [0,1,2,63,64,65,127,128,129,0xffffffff]]
 cases += [(0x10000000+rng.randrange(0x4000000),rng.randrange(2049))for _ in range(4096)]
 valid_count=bad_count=ops=0
 for a,n in cases:
  r=run(mem,68,a,n);valid=n==0 or (a>=0x10000000 and a+n<=0x14000000 and a+n<=0xffffffff)
  assert r['status']==('return' if valid else 'fault');expected=list(range(a&~63,a+n,64))if valid and n else[];assert r['cbo']==expected;assert r['fences']==(2 if valid and n else 0)
  if valid:assert r['a0']==a
  valid_count+=valid;bad_count+=not valid;ops+=len(expected)
 video_cases=0
 for display in range(3):
  for pending in [-1,0,1,2]:
   for draw in range(3):
    r=run(mem,12,1,361,roles=(display,pending,draw,0),fence_values=(360,0x80000169,361))
    expected=next(i for i in range(3) if i not in [display,pending]);assert r['status']=='return' and r['a0']==expected and r['roles']==[display,pending&0xffffffff,expected,1] and r['reads']==3
    r=run(mem,12,0xffffffff,361,roles=(display,pending,draw,0));assert r['status']=='return' and r['a0']==draw and r['reads']==0 and not r['stores'];video_cases+=2
 for idx in list(range(10))+[0xffffffff,0x80000000]:
  r=run(mem,13,idx,17232);assert r['status']=='return' and r['a0']==(0x10000000+idx*0x100000 if idx<3 else 0);video_cases+=1
 for just,token,roles in [(0,361,(1,-1,1,0)),(2,361,(1,-1,1,0)),(1,360,(1,-1,1,0)),(1,361,(1,-1,1,1))]:
  r=run(mem,12,just,token,roles=roles);assert r['status']=='fault' and r['diagnostic']==12 and not r['stores'];video_cases+=1
 r=run(mem,12,1,361,fence_values=(360,));assert r['status']=='fault' and r['diagnostic']==12 and r['reads']==500000 and not r['stores'];video_cases+=1
 r=run(mem,12,1,361);assert r['a0']==0 and r['reads']==1;video_cases+=1
 result=dict(passed=True,native_cpu_replay=False,literal_assembled_instruction_replay=True,service_slots=106,supported=[12,13,68],unsupported_fatal_slots=103,cache_valid_cases=valid_count,cache_invalid_cases=bad_count,cbo_addresses_checked=ops,video_cases=video_cases,timeout_exact_reads=500000,guest_only_owned_stores=True,expected_capture={'acquire_args':[1,361],'acquire_return':0,'buffer_return':0x10000000},relocations=reloc,limits=['One captured render call only; role state is not a general video driver.','Only one positive acquire; a repeated call or unexpected arguments fails.','Instruction replay is not native CPU, cache or bus qualification.','Cache range requires valid cacheable SDRAM; no writeback-drain guarantee.'])
 put(O/'component-result.json',result)
 files=[O/x for x in ['native_services.S','native_services.o','native_services.asm','CONTRACT.txt','component-result.json']]+[pathlib.Path(__file__),C/'state.json',C/'services.csv',P/'native-renderer-capture-v1/model/frozen/coupled.cpp',P/'native-renderer-services-v1/component-manifest.json',R/'src/firmware/os/targets/pocket/video.c',R/'src/firmware/os/hal/cache.c']
 put(O/'component-manifest.json',dict(passed=True,native_integration_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files}))
 print(json.dumps(result,indent=2));print('manifest',sha(O/'component-manifest.json'))
if __name__=='__main__':main()
