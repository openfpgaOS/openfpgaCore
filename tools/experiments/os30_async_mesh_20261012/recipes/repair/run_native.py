#!/usr/bin/env python3
"""Actual frozen CPU compares pure repair with source-extracted renderer math."""
import hashlib,json,random,struct,subprocess,sys
from pathlib import Path
H=Path(__file__).resolve().parent;P=H.parent.parent
C=P/'cached-cpu-v1/native-sdram-v2';T=P/'triangle-front-v1/tests'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def bits(f):return struct.unpack('<I',struct.pack('<f',f))[0]
def floats(u):return struct.unpack('<f',struct.pack('<I',u))[0]
def make_vectors(quick):
 path=P/'mesh-front-v1/work-app/native-vectors-v3/vectors.json'
 real=json.loads(path.read_text())['vectors'];out=[]
 if quick:real=real[:24]+real[130:138]
 for idx,v in enumerate(real):
  tr=v['matrix_u32']+[v['aspect_u32']]+v['coefficient_u32']+[v['ambient'],v['diffuse'],v['scale_u']|(v['scale_v']<<16),v['lit']]
  cmds=[v['commands_u32'][i:i+2]for i in range(2,len(v['commands_u32']),2)]
  out.append(dict(name='scene-'+str(v['native_task']),raw=v['raw_expected_u32'],commands=cmds,count=v['count'],vertices=list(bytes.fromhex(v['source_vertex_bytes_hex'])),transform=tr,cull=v['cull_mode'],material=[(0x7654 if idx%2 else 1<<18)|(idx%4!=0)<<16|(idx%3==0)<<17,8*(idx%127),8*(idx%31),255,160,120,160,120],check_transform=1,raw_valid=idx%2))
 rng=random.Random(0x414a3031)
 for idx in range(72 if quick else 512):
  raw=[]
  for k in range(3):
   w=rng.choice([0.125,1,2,10,1024,32767,65536])
   if idx%8==0:w=1
   if idx%11==0:w=-w
   x=rng.uniform(-2,2)*abs(w);y=rng.uniform(-2,2)*abs(w);z=rng.uniform(-2,2)*abs(w)
   if idx%5==0:x/=4;y/=4;z/=4
   uv=rng.getrandbits(32);color=rng.getrandbits(32)
   x,y,z,w=map(lambda a:floats(bits(a)),[x,y,z,w])
   clip=(x< -w)*32+(x>w)*16+(y< -w)*8+(y>w)*4+(z< -w)*2+(z>w)
   raw.append([bits(x),bits(y),bits(z),bits(w),bits(1/w),uv,color,int(clip)])
  if idx%17==0:raw[2]=raw[0][:]
  out.append(dict(name='directed-random-'+str(idx),raw=raw,commands=[[0x05000204,0]],count=3,vertices=[],transform=[0]*24,cull=idx%4,material=[rng.randrange(65536)|(idx%3!=0)<<16|(idx%2)<<17|(idx%5!=0)<<18,8*rng.randrange(65536),8*rng.randrange(65536),rng.randrange(256),rng.randrange(-4096,4097),rng.randrange(-4096,4097),rng.randrange(-1024,1025),rng.randrange(-1024,1025)],check_transform=0,raw_valid=1))
 special=[0,0x80000000,1,0x80000001,0x007fffff,0x00800000,0x00800001,0x3f7fffff,0x3f800000,0x3f800001,0xbf800000,0x7f7fffff,0xff7fffff,0x7f800000,0xff800000,0x7fc00000,0x7f800001]
 for idx,value in enumerate(special):
  for mode in range(2):
   matrix=[bits(1.0)if k%5==0 else 0 for k in range(16)]
   matrix[15]=value if mode else bits(1.0)
   vertices=[]
   for k in range(3):
    xyz=[value if k==0 and not mode else bits((-0.5,0.5,0.25)[k]),bits((0.5,-0.5,0.25)[k]),0]
    vertices+=list(struct.pack('<3Ih2h4BH',*xyz,0,17+k,-257+k,17,63,127,255,0))
   out.append(dict(name='raw-failure-boundary-%d-%d'%(idx,mode),raw=[[0]*8]*3,commands=[[0x05000204,0]],count=3,vertices=vertices,transform=matrix+[bits(1.0),bits(0.25),bits(0.5),bits(0.25),0x102030,0xc0d0e0,0xffffffff,idx%2],cull=0,material=[0x7654|(1<<16)|(1<<17)|(idx%2)<<18,8,16,255,160,120,160,120],check_transform=2,raw_valid=0))
 return out,path
def carray(a):return '{'+','.join(carray(x)if isinstance(x,list)else str(x)+'u' if x>=0 else str(x)for x in a)+'}'
def main():
 out=H/sys.argv[1];assert out.parent==H and not out.exists();out.mkdir()
 vectors,source=make_vectors('--quick'in sys.argv)
 (out/'inputs.json').write_text(json.dumps(vectors,indent=2)+'\n')
 s='struct test_vector {struct am_repair_job job;uint32_t raw[32][8],commands[64][2];unsigned char vertices[768];unsigned check_transform;};\n#define VECTOR_COUNT %du\nstatic const struct test_vector vectors[VECTOR_COUNT]={\n'%len(vectors)
 for v in vectors:
  t=v['transform'];m=v['material'];job='{.count=%du,.command_count=%du,.cull=%du,.raw_valid=%du,.transform={%s,%du,%s,%du,%du,%du,%du},.material=%s}'%(v['count'],len(v['commands']),v['cull'],v['raw_valid'],carray(t[:16]),t[16],carray(t[17:20]),*t[20:24],carray(m))
  s+='{'+job+','+carray(v['raw'])+','+carray(v['commands'])+','+carray(v['vertices'])+','+str(v['check_transform'])+'},\n'
 (out/'vectors.h').write_text(s+'};\n')
 (out/'start.S').write_text((T/'cpu-arithmetic-v1/start.S').read_text())
 (out/'kernel.ld').write_text((T/'cpu-arithmetic-v1/kernel.ld').read_text())
 (out/'host.cpp').write_text((T/'native_cull_host.cpp').read_text().replace('k<30000000','k<180000000'))
 (out/'string.h').write_text('#include <stddef.h>\nvoid *memcpy(void *,const void *,size_t);\nvoid *memset(void *,int,size_t);\n')
 (out/'math.h').write_text('#define lrintf __builtin_lrintf\n#define fabsf __builtin_fabsf\n')
 subprocess.run([sys.executable,H/'make_reference.py',out/'reference.c'],check=True)
 sources=[Path(__file__),H/'make_reference.py',H/'native_test.c',H/'async_mesh_repair.c',H/'async_mesh_repair.h',source,C/'prepared.json',C/'build.json',C/'obj/Vtb_system__ALL.a',C/'obj/libverilated.a',out/'vectors.h',out/'reference.c',out/'reference.json',out/'start.S',out/'kernel.ld',out/'host.cpp',out/'string.h',out/'math.h']
 before={str(p):sha(p)for p in sources};record={'status':'RUNNING','inputs_sha256':before,'vectors':len(vectors),'commands':[],'scope':'Arithmetic/packet differential only on actual frozen Vexii CPU. Reference extracts baseline source functions; this is not a full-frame performance test.'}
 def run(args,name):
  with (out/(name+'.log')).open('x')as f:r=subprocess.run(list(map(str,args)),cwd=out,stdout=f,stderr=subprocess.STDOUT)
  record['commands'].append({'argv':list(map(str,args)),'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))});(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
  assert not r.returncode,(name,(out/(name+'.log')).read_text()[-4000:])
 cc=['riscv64-elf-gcc','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-O3','-fno-strict-aliasing','-ffreestanding','-fno-builtin','-fno-stack-protector','-msmall-data-limit=0','-I'+str(H),'-I'+str(out)]
 run(cc+['-fno-fast-math','-ffp-contract=off','-c',H/'async_mesh_repair.c','-o','repair.o'],'compile-repair')
 run(cc+['-ffast-math','-c','reference.c','-o','reference.o'],'compile-reference')
 run(cc+['-fno-fast-math','-ffp-contract=off','-nostdlib','-Wl,--no-relax,-T,kernel.ld','start.S',H/'native_test.c','repair.o','reference.o','-lgcc','-o','kernel.elf'],'build-kernel')
 run(['riscv64-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],'objcopy')
 with (out/'kernel.dis').open('x')as f:subprocess.run(['riscv64-elf-objdump','-d',out/'kernel.elf'],stdout=f,check=True)
 obj=C/'obj';run(['g++','-std=c++20','-fcoroutines','-DVM_TIMING=1','-O2','-I'+str(obj),'-I/usr/share/verilator/include','-I/usr/share/verilator/include/vltstd',out/'host.cpp',obj/'Vtb_system__ALL.a',obj/'libverilated.a','-pthread','-latomic','-o',out/'host'],'build-host')
 run([out/'host',out/'kernel.bin',out/'outputs.bin',out/'result.json',str(len(vectors))],'run')
 rows=list(struct.iter_unpack('<11I',(out/'outputs.bin').read_bytes()));assert len(rows)==len(vectors)
 failures=[];words=0
 for i,r in enumerate(rows):
  words+=r[1]
  if r[0]!=0 or r[1]!=r[2] or r[3]!=0xffffffff or r[6]!=0xffffffff or r[9]!=r[10]:failures.append({'index':i,'name':vectors[i]['name'],'row':r})
 record.update(status='FAIL'if failures else'PASS',failures=failures,compared_words=words)
 assert before=={str(p):sha(p)for p in sources}
 record['outputs_sha256']={p.name:sha(p)for p in out.iterdir()if p.name!='manifest.json'and p.is_file()}
 (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(record['status'],len(vectors),words,'failures',len(failures),flush=True)
 if failures:print(json.dumps(failures[:10],indent=2));sys.exit(1)
if __name__=='__main__':main()
