#!/usr/bin/env python3
"""Literal assembled RV32 bridge checks; not a native CPU integration claim."""
import hashlib,json,pathlib,random,struct
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';O=P/'native-renderer-services-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):p.write_text(json.dumps(d,indent=2,sort_keys=True)+'\n')
def sign(x,n):return x-(1<<n) if x>>(n-1) else x
def object_image():
 b=(O/'native_services.o').read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b);assert h[2]==243
 ss=[struct.unpack_from('<10I',b,h[6]+i*h[11]) for i in range(h[12])];st=ss[h[13]];names=b[st[4]:st[4]+st[5]]
 name=lambda tab,off:tab[off:tab.index(0,off)].decode()
 sections={name(names,s[0]):(i,s) for i,s in enumerate(ss)};base={sections['.native_services'][0]:0x103f0000,sections['.text.native_cache_flush_range'][0]:0x10320000};mem={}
 for ix,addr in base.items():
  s=ss[ix]
  for off in range(0,s[5],4):mem[addr+off]=struct.unpack_from('<I',b,s[4]+off)[0]
 si,sy=sections['.symtab'];syms=[struct.unpack_from('<IIIBBH',b,off) for off in range(sy[4],sy[4]+sy[5],sy[9])]
 reloc=[]
 for name_,(_,s) in sections.items():
  if s[1]!=4:continue
  assert s[7] in base
  for off in range(s[4],s[4]+s[5],s[9]):
   place,info,addend=struct.unpack_from('<IIi',b,off);assert info&255==17
   sym=syms[info>>8];target=base[sym[5]]+sym[1]+addend;pc=base[s[7]]+place;disp=target-pc;assert -(1<<20)<=disp<(1<<20) and not disp&1
   u=disp&0x1fffff;old=mem[pc];assert old&0x7f==0x6f
   mem[pc]=(old&0xfff)|((u>>20)<<31)|(((u>>1)&0x3ff)<<21)|(((u>>11)&1)<<20)|(((u>>12)&255)<<12)
   reloc.append(dict(pc=pc,target=target))
 assert len([p for p in mem if 0x103f0000<=p<0x103f1000])==106
 return mem,reloc

def run(mem,a,n):
 regs=[(i*0x01010101)&0xffffffff for i in range(32)];regs[0]=0;regs[1]=0xfffffffc;regs[10]=a;regs[11]=n;before=regs[:];pc=0x103f0110;cbo=[];fences=0
 for step in range(100000):
  if pc==0xfffffffc:
   assert all(regs[i]==before[i] for i in [1,2,3,4,8,9,10,11,18,19,20,21,22,23,24,25,26,27]);return 'return',cbo,fences
  w=mem[pc];op=w&127;rd=(w>>7)&31;f3=(w>>12)&7;rs1=(w>>15)&31;rs2=(w>>20)&31;nextpc=pc+4;v=None
  if op==0x37:v=w&0xfffff000
  elif op==0x13:
   imm=sign(w>>20,12);assert f3 in [0,7];v=regs[rs1]+imm if f3==0 else regs[rs1]&imm
  elif op==0x33:assert f3==0 and w>>25==0;v=regs[rs1]+regs[rs2]
  elif op==0x63:
   imm=((w>>31)<<12)|(((w>>7)&1)<<11)|(((w>>25)&63)<<5)|(((w>>8)&15)<<1);assert f3 in [0,6,7]
   take=regs[rs1]==regs[rs2] if f3==0 else regs[rs1]<regs[rs2] if f3==6 else regs[rs1]>=regs[rs2]
   if take:nextpc=pc+sign(imm,13)
  elif op==0x6f:
   imm=((w>>31)<<20)|(((w>>12)&255)<<12)|(((w>>20)&1)<<11)|(((w>>21)&1023)<<1);v=pc+4;nextpc=pc+sign(imm,21)
  elif op==0x67:assert f3==0;v=pc+4;nextpc=(regs[rs1]+sign(w>>20,12))&~1
  elif op==0x0f:
   if w==0x0ff0000f:fences+=1
   else:assert w==0x0022a00f;cbo.append(regs[5])
  elif op==0x73:assert w==0x00100073 and regs[7]==68;return 'fault',cbo,fences
  else:raise AssertionError(hex(w))
  if v is not None and rd:regs[rd]=v&0xffffffff
  regs[0]=0;pc=nextpc&0xffffffff
 raise AssertionError('literal replay did not terminate')

def main():
 assert not(O/'component-result.json').exists();mem,reloc=object_image()
 for i in range(106):
  w=mem[0x103f0000+4*i];assert (w&127==0x6f) if i==68 else w==0x00100073
 rng=random.Random(68064);cases=[(0x11000000+off,n)for off in range(64)for n in range(257)]
 cases += [(a,n)for a in [0,0xfffffff,0x10000000,0x10000001,0x13ffff80,0x13ffffbf,0x13ffffc0,0x13ffffff,0x14000000,0xffffffff]for n in [0,1,2,63,64,65,127,128,129,0xffffffff]]
 cases += [(0x10000000+rng.randrange(0x4000000),rng.randrange(2049))for _ in range(4096)]
 passed=failed=ops=0
 for a,n in cases:
  status,cbo,fences=run(mem,a,n);valid=n==0 or (a>=0x10000000 and a+n<=0x14000000 and a+n<=0xffffffff)
  assert status==('return' if valid else 'fault'),(a,n,status)
  expected=list(range(a&~63,a+n,64))if valid and n else[];assert cbo==expected;assert fences==(2 if valid and n else 0)
  passed+=valid;failed+=not valid;ops+=len(cbo)
 result=dict(passed=True,literal_assembled_rv32_instruction_replay=True,native_cpu_replay=False,supported_service_index=68,service_slots=106,unsupported_slots_ebreak=105,valid_cases=passed,invalid_range_cases=failed,total_cases=len(cases),cbo_addresses_checked=ops,source_preserves_callee_saved_and_arguments=True,real_cbo_opcode='0x0022a00f',fence_opcode='0x0ff0000f',relocations=reloc,limits=['This validates assembled instruction semantics and slot layout, not CPU/bus/cache hardware execution.','Video services remain unsupported until captured ownership contract is reviewed.','Valid cacheable ranges only; invalid inputs intentionally fail instead of original HAL clamping.','No extra writeback drain is claimed.'])
 put(O/'component-result.json',result)
 inputs=[O/'native_services.S',O/'native_services.o',O/'native_services.asm',O/'CONTRACT.txt',O/'component-result.json',pathlib.Path(__file__),R/'src/firmware/os/hal/cache.c',P/'renderer-pgo-rebuild-v7/SM64/src/sdk/include/of_services.h',P/'renderer-pgo-coupled-v1/qsim/env.c',P/'renderer-pgo-coupled-v1/qsim/env.h']
 put(O/'component-manifest.json',dict(passed=True,native_integration_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p)for p in inputs}))
 print(json.dumps(result,indent=2));print('manifest',sha(O/'component-manifest.json'))
if __name__=='__main__':main()
