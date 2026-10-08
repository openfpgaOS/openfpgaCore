#!/usr/bin/env python3
"""Pixel-exact transport replay: geometry publisher versus frozen CPU uploader."""
import concurrent.futures,hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
N=P/'vcache-narrow-v1';R=P/'gpu-direct-ring-v1';OUT=P/'gpu-direct-ring-replay-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sub(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
OUT.mkdir(exist_ok=False);F=OUT/'frozen';shutil.copytree(N/'model/frozen',F)
shutil.copy2(R/'gpu_core.v',F/'common/gpu_core.v')
p=F/'tb_gpu.v';s=p.read_text()
s=sub(s,'    input  wire        reset_n,','''    input  wire        reset_n,
    input wire geo_request,input wire [12:0] geo_words,output wire geo_grant,
    input wire geo_valid,input wire [31:0] geo_data,output wire geo_ready,
    input wire geo_abort,output wire geo_done,output wire geo_owned,''')
s=sub(s,'gpu_core #(\n','gpu_core #(\n    .INCLUDE_GEOMETRY_INGRESS(1),\n')
s=sub(s,'    .gpu_enable(1\'b1),','''    .gpu_enable(1'b1),
    .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),
    .geo_valid(geo_valid),.geo_data(geo_data),.geo_ready(geo_ready),
    .geo_abort(geo_abort),.geo_done(geo_done),.geo_owned(geo_owned),
    .gpu_cpu_pending(reg_wr),.gpu_cpu_ready(),''')
p.write_text(s)
p=F/'replay.cpp';s=p.read_text()
a='''            for (uint32_t i = 0; i < n; i++) mmio_write(1, rd32());
            if ((mmio_read(5) & 0x300) != 0x100) {
                fprintf(stderr, "%s: CPU ring overflow/mode lost\\n", vec_path);
                rc = 3; break;
            }
            mmio_write(0, 8);   // publish'''
b='''            if(n) {
                tb->geo_words=n;tb->geo_request=1;tb->eval();
                uint64_t start=cycles;
                while(!tb->geo_grant) {
                    if(cycles-start>50000000ull)throw std::runtime_error("Geometry claim timeout");
                    tick();
                }
                tick();tb->geo_request=0;
                for(uint32_t i=0;i<n;i++) {
                    tb->geo_data=rd32();tb->geo_valid=1;tb->eval();
                    if(!tb->geo_ready)throw std::runtime_error("Geometry reservation lost");
                    tick();
                }
                tb->geo_valid=0;
                if(!tb->geo_done||tb->geo_owned)throw std::runtime_error("Geometry publish missing");
            }'''
s=sub(s,a,b);p.write_text(s)
old=json.loads((N/'model/build.json').read_text())['command']
cmd=[x.replace(str(N/'model/frozen'),str(F)).replace(str(N/'model/obj'),str(OUT/'obj')) for x in old]
with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
(OUT/'build.json').write_text(json.dumps(dict(command=cmd,source_sha256={str(p.relative_to(F)):sha(p) for p in F.rglob('*') if p.is_file()},recipe_sha256=sha(Path(__file__)),binary_sha256=sha(OUT/'obj/Vtb_gpu')),indent=2)+'\n')
def run(job):
 frame,traffic=job;vec=N/f'replays/frame_{frame:05d}.vec';reference=N/f'replays/narrow/{traffic}/frame_{frame:05d}.bin'
 prefix=OUT/f'frame_{frame:05d}-traffic{traffic}'
 cmd=[str(OUT/'obj/Vtb_gpu'),str(vec),str(prefix.with_suffix('.bin')),f'+memory_traffic={traffic}','+tagged_tex=1']
 with prefix.with_suffix('.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=900)
 assert prefix.with_suffix('.bin').read_bytes()==reference.read_bytes(),job
 return dict(frame=frame,traffic=traffic,passed=True,input_sha256=sha(vec),reference_sha256=sha(reference),output_sha256=sha(prefix.with_suffix('.bin')),log_sha256=sha(prefix.with_suffix('.log')),command=cmd)
jobs=[(f,t) for f in [40,360,600,720] for t in [0,1,2]]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(run,jobs))
(OUT/'result.json').write_text(json.dumps(dict(passed=True,scope='Transport-only identical complete GPU batch through geometry publisher; real configured GPU and SDRAM/CRAM replay',runs=rows,build_sha256=sha(OUT/'build.json'),ring_protocol_manifest_sha256=sha(R/'final-manifest.json')),indent=2)+'\n')
print('All',len(rows),'full color/depth/fence outputs exact')
