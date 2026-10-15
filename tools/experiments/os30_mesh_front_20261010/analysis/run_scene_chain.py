#!/usr/bin/env python3
import argparse,hashlib,json,subprocess,os,struct,collections
from pathlib import Path
A=argparse.ArgumentParser();A.add_argument('--out',type=Path,required=True);a=A.parse_args();o=a.out.resolve();o.mkdir(parents=True,exist_ok=False)
w=Path(__file__).resolve().parents[1];vectors=w.parent/'work-app/native-vectors-v3/vectors.json';oracle=w/'tests/scene-cull-oracle-v1';v=json.loads(vectors.read_text())['vectors'];cpu=json.loads((oracle/'inputs.json').read_text());output=list(struct.iter_unpack('<11I',(oracle/'outputs.bin').read_bytes()));assert len(output)==len(cpu['rows'])
expect={(m['task'],m['triangle']):(row,out) for m,row,out in zip(cpu['mapping'],cpu['rows'],output)};words=[len(v)];outcomes=collections.Counter()
for x in v:
 cmd=x['commands_u32'];n=x['count'];first=x['destination'];raw=x['raw_expected_u32'];tri=[];failure=0
 assert len(raw)==n and len(bytes.fromhex(x['source_vertex_bytes_hex']))==n*24
 for i,indices in enumerate(x['triangles']):
  operands,out=expect[(x['native_task'],i)];vs=[raw[k-first] for k in indices];same=[item for row in vs for item in (row[0],row[1],row[4])]
  assert same==operands[:9] and [r[7] for r in vs]==operands[10:13] and x['cull_mode']==operands[13]
  assert not any((z&0x7fffffff)!=0 and (z>>23&255) in (0,255) for z in out[:9])
  reject=out[9];fb=0
  if reject==0:
   if any(r[7] for r in vs):fb=8
   elif any((r[3]>>31) or (r[3]>>23&255)==0 or r[3]>=0x46fffe00 for r in vs) or len({r[3] for r in vs})==1:fb=9
  if fb and not failure:failure=fb
  tri.extend([indices[0]|indices[1]<<5|indices[2]<<10,reject,fb])
 outcomes[str(failure)]+=1
 words.extend([x['native_task'],x['phase'],n,first,len(cmd)//2-1,len(x['triangles']),failure,x['cull_mode']]);words.extend(x['matrix_u32']);words.extend([x['aspect_u32'],*x['coefficient_u32'],x['ambient'],x['diffuse'],x['scale_u'],x['scale_v'],x['lit']]);words.extend(struct.unpack('<'+'I'*(n*6),bytes.fromhex(x['source_vertex_bytes_hex'])));words.extend(z for r in raw for z in r);words.extend(cmd);words.extend(tri)
(o/'vectors.bin').write_bytes(struct.pack('<'+'I'*len(words),*words));m=json.loads((w/'sources.json').read_text());sources=[Path(x['path']) for x in m['sources']]
for x in m['sources']:assert hashlib.sha256(Path(x['path']).read_bytes()).hexdigest()==x['sha256']
sources+=[w/'tests/geometry_mesh_chain.v',w.parent/'packet/geometry_mesh_packet.v',w.parents[1]/'queued-front-v1/native-v1/frozen/src/fpga/common/geometry_import_convert.v'];test=w/'tests/test_scene_chain.cpp';inputs=[*sources,test,Path(__file__).resolve(),vectors,vectors.parent/'manifest.json',oracle/'inputs.json',oracle/'outputs.bin',oracle/'manifest.json'];sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();before={str(p):sha(p) for p in inputs}
cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module','geometry_mesh_chain','--Mdir',str(o/'obj'),'-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',*map(str,sources),str(test)]
r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);(o/'build.log').write_text(r.stdout)
if r.returncode:print(r.stdout[-5000:]);raise SystemExit(r.returncode)
r=subprocess.run([str(o/'obj/Vgeometry_mesh_chain'),str(o/'vectors.bin')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'});(o/'run.log').write_text(r.stdout);print(r.stdout)
assert before=={str(p):sha(p) for p in inputs},'test inputs changed';m.update(status='PASS' if r.returncode==0 else 'FAIL',inputs_sha256=before,build_command=cmd,expected_outcomes=dict(outcomes),result=json.loads(r.stdout) if r.returncode==0 else r.stdout,scope='Actual baseline rawLOAD/DMA/sharedFMA/culler with exact638captured instancecontexts and24Binputs. Allraw8word outputs compared to unchanged native baseline, survivor order to current CPU arithmetic oracle. Every ordinary emitted packet compared to independent conversion under controlled materialflags; actual material/raster binding, command DMA, CPUrepair/fullframe performance not covered.')
(o/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');raise SystemExit(r.returncode)
