#!/usr/bin/env python3
"""Fresh actual-current-CPU arithmetic oracle, isolated from all prior evidence."""
import hashlib,json,random,struct,subprocess,sys
from pathlib import Path
T=Path(__file__).resolve().parent
P=T.parent.parent
C=P/'cached-cpu-v1/native-sdram-v2'
ROOT=P.parents[2]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
 out=T/sys.argv[1]
 assert out.parent==T and not out.exists()
 out.mkdir()
 old=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
 native=json.loads((old/'cpu-audit/cull-native/manifest.json').read_text())
 rows=[r['bits'] for r in native['records']]
 origin=['old-native-or-control']*len(rows)
 random_rows=[list(map(lambda x:int(x,16),line.split()))[:14] for line in (old/'float-cull/random-vectors.txt').read_text().splitlines()]
 rows.extend(random_rows[:2048]);origin.extend(['old-high-precision-oracle']*2048)
 rng=random.Random(0x54465031)
 def fbits(v):return struct.unpack('<I',struct.pack('<f',v))[0]
 for i in range(1024):
  v=[]
  for j in range(3):
   x=(rng.getrandbits(1)<<31)|(rng.randrange(1,255)<<23)|rng.getrandbits(23)
   y=(rng.getrandbits(1)<<31)|(rng.randrange(1,255)<<23)|rng.getrandbits(23)
   inv=(rng.randrange(1,255)<<23)|rng.getrandbits(23)
   v.extend([x,y,inv])
  if i%4==0:v[6:9]=v[0:3]
  if i%4==1:v[6:9]=v[3:6];v[6]^=1
  rows.append(v+[i%8,0,0,0,1+i%2]);origin.append('wide-exponent-cancellation')
 special=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x00800001,
          0x3f7fffff,0x3f800000,0x3f800001,0xbf800000,0x7f7fffff,0xff7fffff,
          0x7f800000,0xff800000,0x7fc00000,0x7f800001]
 for p in range(9):
  for value in special:
   for mode in [1,2]:
    v=[fbits(0.25),fbits(0.5),fbits(0.25),fbits(0.5),fbits(0.25),fbits(0.125),fbits(-0.5),fbits(0.5),fbits(0.5)]
    v[p]=value;rows.append(v+[0,0,0,0,mode]);origin.append('special-boundary')
 (out/'inputs.json').write_text(json.dumps({'rows':rows,'origins':origin},indent=2)+'\n')
 (out/'vectors.h').write_text('#define VECTOR_COUNT %du\nstatic const uint32_t vectors[VECTOR_COUNT][14]={\n'%len(rows)+',\n'.join('{'+','.join('0x%08xu'%x for x in row)+'}'for row in rows)+'\n};\n')
 old_start=(P/'cached-cpu-v1/cache-check/start.S').read_text()
 (out/'start.S').write_text(old_start.replace('call kernel','li t0,0x6000\ncsrs mstatus,t0\ncsrwi fcsr,0\ncall kernel'))
 (out/'kernel.ld').write_text((P/'cached-cpu-v1/cache-check/kernel.ld').read_text().replace('0x10440000','0x107f0000'))
 cpu=T.parent/'model/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
 baseline=C/'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
 assert sha(cpu)==sha(baseline)
 prepared=json.loads((C/'prepared.json').read_text())
 for path,want in prepared['source_sha256'].items():assert sha(C/path)==want,path
 sources=[Path(__file__),T/'native_cull.c',T/'native_cull_host.cpp',cpu,C/'prepared.json',C/'build.json',C/'obj/Vtb_system__ALL.a',C/'obj/libverilated.a',out/'inputs.json',out/'vectors.h',out/'start.S',out/'kernel.ld',old/'cpu-audit/cull-native/manifest.json',old/'float-cull/random-vectors.txt']
 before={str(p):sha(p)for p in sources}
 record={'status':'RUNNING','inputs_sha256':before,'commands':[],'vector_count':len(rows),'scope':'Quiet full native model. Actual exact current CPU computes each nine-operation cull DAG. No gameplay or performance claim.'}
 def run(argv,name):
  with (out/(name+'.log')).open('x')as f:r=subprocess.run(list(map(str,argv)),cwd=out,stdout=f,stderr=subprocess.STDOUT)
  record['commands'].append({'argv':list(map(str,argv)),'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))})
  (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
  assert not r.returncode,(name,(out/(name+'.log')).read_text()[-3000:])
 run(['riscv64-elf-gcc','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-O2','-fno-strict-aliasing','-ffreestanding','-fno-builtin','-fno-stack-protector','-msmall-data-limit=0','-nostdlib','-I'+str(out),'-Wl,--no-relax,-T,kernel.ld','start.S',T/'native_cull.c','-o','kernel.elf'],'build-kernel')
 run(['riscv64-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],'objcopy')
 with (out/'kernel.dis').open('x')as f:subprocess.run(['riscv64-elf-objdump','-d',out/'kernel.elf'],stdout=f,check=True)
 obj=C/'obj'
 run(['g++','-std=c++20','-fcoroutines','-DVM_TIMING=1','-O2','-I'+str(obj),'-I/usr/share/verilator/include','-I/usr/share/verilator/include/vltstd',T/'native_cull_host.cpp',obj/'Vtb_system__ALL.a',obj/'libverilated.a','-pthread','-latomic','-o',out/'host'],'build-host')
 run([out/'host',out/'kernel.bin',out/'outputs.bin',out/'result.json',str(len(rows))],'run')
 assert json.loads((out/'result.json').read_text())['pass']
 assert before=={str(p):sha(p)for p in sources},'Inputs changed during qualification'
 assert (out/'outputs.bin').stat().st_size==len(rows)*44
 record.update(status='PASS_CURRENT_NATIVE_CPU_ORACLE_CAPTURE',outputs_sha256={p.name:sha(p)for p in out.iterdir()if p.name!='manifest.json' and p.is_file()})
 (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(record['status'],len(rows),flush=True)

if __name__=='__main__':main()
