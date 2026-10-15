#!/usr/bin/env python3
"""Run captured real triangle operands through unchanged current native CPU."""
import argparse,csv,hashlib,json,struct,subprocess,shutil
from pathlib import Path
A=argparse.ArgumentParser();A.add_argument('--capture',type=Path,required=True);A.add_argument('--out',type=Path,required=True);a=A.parse_args();c=a.capture.resolve();o=a.out.resolve();o.mkdir(parents=True,exist_ok=False)
w=Path(__file__).resolve().parents[1];p=w.parents[1];prior=p/'triangle-front-v1/tests/cpu-arithmetic-v1';source=p/'triangle-front-v1/tests/native_cull.c';rows=[];mapping=[]
raw={}
with (c/'run/task-raw-vertices.tsv').open() as f:
 for r in csv.DictReader(f,delimiter='\t'):raw.setdefault(int(r['task']),{})[int(r['slot'])]={k:int(v) for k,v in r.items()}
tasks=json.loads((c/'tasks-classified.json').read_text())
for t in tasks:
 if not t['raw_success']:continue
 for i,tri in enumerate(t['triangles']):
  vs=[raw[t['task']][idx-t['first']] for idx in tri];mode=t['geometry_mode']>>9&3
  data=[v[k] for v in vs for k in ('x','y','invw')];neg=sum((1<<j) for j,v in enumerate(vs) if v['w']>>31 and v['w']&0x7fffffff)
  rows.append(data+[neg,*[v['clip'] for v in vs],mode]);mapping.append({'task':t['task'],'phase':t['phase'],'triangle':i,'indices':tri})
(o/'inputs.json').write_text(json.dumps({'rows':rows,'mapping':mapping},indent=2)+'\n')
(o/'vectors.h').write_text('#define VECTOR_COUNT %du\nstatic const uint32_t vectors[VECTOR_COUNT][14]={\n'%len(rows)+',\n'.join('{'+','.join('0x%08xu'%v for v in row)+'}'for row in rows)+'\n};\n')
for f in ('start.S','kernel.ld'):shutil.copy2(prior/f,o/f)
inputs=[Path(__file__).resolve(),source,prior/'host',prior/'manifest.json',c/'tasks-classified.json',c/'run/task-raw-vertices.tsv',o/'vectors.h',o/'inputs.json',o/'start.S',o/'kernel.ld'];sha=lambda x:hashlib.sha256(x.read_bytes()).hexdigest();before={str(x):sha(x)for x in inputs}
cmds=[['riscv64-elf-gcc','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-O2','-fno-strict-aliasing','-ffreestanding','-fno-builtin','-fno-stack-protector','-msmall-data-limit=0','-nostdlib','-I'+str(o),'-Wl,--no-relax,-T,kernel.ld','start.S',str(source),'-o','kernel.elf'],['riscv64-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],[str(prior/'host'),str(o/'kernel.bin'),str(o/'outputs.bin'),str(o/'result.json'),str(len(rows))]]
for n,cmd in enumerate(cmds):
 r=subprocess.run(cmd,cwd=o,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);(o/f'run{n}.log').write_text(r.stdout);assert r.returncode==0,r.stdout[-3000:]
assert json.loads((o/'result.json').read_text())['pass'];assert (o/'outputs.bin').stat().st_size==len(rows)*44;assert before=={str(x):sha(x)for x in inputs}
manifest={'status':'PASS_ACTUAL_CURRENT_CPU_SCENE_CULL_ORACLE','vectors':len(rows),'inputs_sha256':before,'commands':cmds,'outputs_sha256':{f:sha(o/f)for f in ('kernel.elf','kernel.bin','outputs.bin','result.json')},'scope':'Captured transformed triangle operands executed through exact unchanged native CPU nineoperation cull kernel. No sourceDMA or packet test; consumed separately by mesh chain.'};(o/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(manifest['status'],len(rows))
