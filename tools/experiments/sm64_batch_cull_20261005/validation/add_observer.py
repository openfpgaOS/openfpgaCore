#!/usr/bin/env python3
"""Observe existing all-frame culling counters without changing guest execution."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
q=OUT/'qsim-observed';q.mkdir(exist_ok=False)
for p in (OUT/'qsim').iterdir():
 if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
p=q/'main.c';s=p.read_text()
a='    FILE *diagnostic_csv=NULL;';b=a+'\n    FILE *culling_csv=NULL;';assert s.count(a)==1;s=s.replace(a,b,1)
a='                const qsim_sym *diagnostic=qsim_syms_lookup(&E.syms,"sm64_perf_snapshot");'
b='''                const qsim_sym *culling=qsim_syms_lookup(&E.syms,"sm64_render_stats");
                if(culling) {
                    if(!culling_csv) {
                        snprintf(path,sizeof path,"%s/culling.csv",out);
                        culling_csv=fopen(path,"w");fprintf(culling_csv,"frame");
                        for(unsigned k=0;k<32;k++)fprintf(culling_csv,",stat%u",k);
                        fputc('\\n',culling_csv);
                    }
                    uint32_t data[32];
                    memcpy(data,qsim_mem_ptr(E.cpu,culling->addr,sizeof data),sizeof data);
                    fprintf(culling_csv,"%llu",(unsigned long long)fr->seq);
                    for(unsigned k=0;k<32;k++)fprintf(culling_csv,",%u",data[k]);
                    fputc('\\n',culling_csv);
                }
'''+a
assert s.count(a)==1;s=s.replace(a,b,1)
a='    if(diagnostic_csv)fclose(diagnostic_csv);';assert s.count(a)==1;s=s.replace(a,a+'\n    if(culling_csv)fclose(culling_csv);',1);p.write_text(s)
with (OUT/'qsim-observed-build.log').open('w') as f:subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
(OUT/'qsim-observed-manifest.json').write_text(json.dumps(dict(exe_sha256=sha(q/'qsim'),parent_manifest_sha256=sha(OUT/'qsim-manifest.json'),sources={p.name:sha(p) for p in q.iterdir() if p.is_file()},scope='Only read 32 existing sm64_render_stats words after each completed frame; no guest writes, hooks, instructions, clock or input changes.'),indent=2)+'\n')
print(q/'qsim')
