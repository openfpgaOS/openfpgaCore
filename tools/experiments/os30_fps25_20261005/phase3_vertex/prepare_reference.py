#!/usr/bin/env python3
import hashlib,json,shutil,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase3/vertex-cpu';OLD=ROOT/'docs/measurements/os30-simple-20261005/validation/cpu-kernel';HERE=Path(__file__).parent
ELF=ROOT/'docs/measurements/os30-fps25-20261005/phase2/exact-v1/diagnostic.elf'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def segments(path):
 b=path.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b);assert h[0][:5]==b'\x7fELF\x01'
 out=[]
 for i in range(h[10]):
  p=struct.unpack_from('<8I',b,h[5]+i*h[9])
  if p[0]==1:out.append((p[2],p[5],b[p[1]:p[1]+p[4]]))
 return out

def main():
 assert sha(ELF)=='a3feb3f8415929e73f380c37eb08ed8162d05ae83e3664dc60e30c73541b1260'
 proof=json.loads((OUT/'capture-proof-v2.json').read_text());assert proof['passed']
 fw=OUT/'firmware-reference';fw.mkdir(exist_ok=False)
 for n in ['start.S','firmware.mif']:shutil.copy2(OLD/'firmware'/n,fw/n)
 for n in ['kernel.c','link.ld','invoke.S']:shutil.copy2(HERE/('kernel_reference.c' if n=='kernel.c' else n),fw/n)
 (fw/'start.S').write_text((fw/'start.S').read_text().replace('  csrs mstatus, t0','  csrs mstatus, t0\n  csrw fcsr, zero'))
 shutil.copy2(OUT/'capture-f5-state-v2/vertex-state.bin',fw/'vertex-state.bin')
 command=json.loads((OLD/'manifest.json').read_text())['compiler_command'];command=[str(fw) if t==str(OLD/'firmware') else '-mtune=sifive-5-series' if t=='-mtune=sifive-7-series' else t for t in command]
 command.insert(command.index('kernel.c'),'invoke.S')
 # Repository read-only; compiler may write only this fixture's directory.
 at=command.index('-v');command[at+1]=command[at+1]+':ro';command[at:at]=['-v',str(fw)+':'+str(fw)]
 with (OUT/'firmware-reference-build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (OUT/'kernel-reference.asm').open('w') as log:subprocess.run(['llvm-objdump','-d','--mattr=+m,+f,+c',str(fw/'kernel.elf')],stdout=log,check=True)
 allsegs=[('F5',*s) for s in segments(ELF)]+[('fixture',*s) for s in segments(fw/'kernel.elf')]
 for i,(name,addr,size,data) in enumerate(allsegs):
  assert addr>=0x10320000 and addr+size<0x13a00000
  for name2,addr2,size2,data2 in allsegs[:i]:assert addr+size<=addr2 or addr2+size2<=addr,(name,name2)
 top=max(a+s for _,a,s,_ in allsegs);image=bytearray(top-0x10320000)
 for name,addr,size,data in allsegs:image[addr-0x10320000:addr-0x10320000+len(data)]=data
 (fw/'kernel.bin').write_bytes(image)
 assert image[0x104d0180-0x10320000:0x104d0be0-0x10320000]==ELF.read_bytes()[0x4000+0xd0180:0x4000+0xd0be0]
 record={'original_elf_sha256':sha(ELF),'capture_proof_sha256':sha(OUT/'capture-proof-v2.json'),'original_vertex_pc':0x104d0180,'original_vertex_size':2656,'original_fog_helper_pc':0x104ce600,'original_gp':0x10fdbc70,'original_function_instruction_bytes_retained':True,'whole_original_load_segment_retained':True,'segments':[{'name':n,'address':a,'memory_size':s,'file_size':len(b),'file_sha256':hashlib.sha256(b).hexdigest()}for n,a,s,b in allsegs],'compiler_command':command,'firmware_sha256':{p.name:sha(p)for p in fw.iterdir() if p.is_file()},'script_sha256':sha(Path(__file__)),'source_files_sha256':{str(p.relative_to(ROOT)):sha(p)for p in [HERE/'kernel_reference.c',HERE/'invoke.S',HERE/'link.ld']},'scope':'Executes original shipped F5 function/helper bytes at original addresses and GP, with each captured original SP and complete consumed mutable state/input restored. No renderer source recompile or relocation. Whole post-call state is logged word-for-word where it differs from qsim, and must repeat byte-exactly against its own actual-A reference under both cache preparations. This does not pass qsim numerical equivalence; the strict fixture remains failed evidence. FRM reset to RNE by fixture startup; accrued exception flags not part of comparison.','cache_cases':['restore-prepared: execute+verify once, restore original state, time call; full state restoration affects cache residency and does not reproduce entire game history.','cold-prepared: same restore then scan256KiB and fence.i before call.'],'timing_limits':'UART-marker retired/freeze/hazard counters and timer include common wrapper/timer overhead; restore/check outside interval. Only FDIV-freeze subset attributed explicitly by exact stage2 PC. Freeze/hazard counters overlap and must not be summed. Model comparison uses original full-game cache/predictor history, unlike fixture.'}
 (OUT/'firmware-reference-manifest.json').write_text(json.dumps(record,indent=2)+'\n');print('FIRMWARE READY',len(image),sha(fw/'kernel.bin'))
if __name__=='__main__':main()
