#!/usr/bin/env python3
"""Read cumulative diagnostic coverage symbols from the unchanged functional CPU."""
from pathlib import Path
import hashlib,json,shutil,subprocess
ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-mesh-20261004/runtime-qsim-v3'
OUT=BASE.parent/'runtime-qsim-coverage-v1'
OUT.mkdir(exist_ok=False)
for p in BASE.iterdir():
    if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,OUT/p.name)
p=OUT/'main.c';s=p.read_text();where=s.index('static void mesh_capture(')
s=s[:where]+r'''
static void mesh_coverage_capture(qenv *env,const char *out,uint64_t frame) {
    if(frame<358)return;
    const char *names[]={"mesh_coverage","mesh_coverage_backend","mesh_lazy_stats","mesh_shadow_stats"};
    const unsigned lengths[]={128,128,8,16};
    char path[4096];snprintf(path,sizeof path,"%s/coverage-%05llu.bin",out,(unsigned long long)frame);
    FILE *f=fopen(path,"wb");if(!f)abort();
    for(unsigned i=0;i<4;i++) {
        const qsim_sym *sym=qsim_syms_lookup(&env->syms,names[i]);
        if(!sym) {fprintf(stderr,"missing %s\n",names[i]);abort();}
        if(fwrite(qsim_mem_ptr(env->cpu,sym->addr,lengths[i]*4),lengths[i]*4,1,f)!=1)abort();
    }
    fclose(f);
}
''' + s[where:]
needle='                const char *names[]={"sm64_geometry_batches","sm64_geometry_vertices",'
assert s.count(needle)==1;s=s.replace(needle,'                mesh_coverage_capture(&E,out,fr->seq);\n'+needle);p.write_text(s)
with (OUT/'build.log').open('w') as log:subprocess.run(['make','-j3'],cwd=OUT,stdout=log,stderr=subprocess.STDOUT,check=True)
(OUT/'sources.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in OUT.iterdir()if p.suffix in('.c','.h')},indent=2)+'\n')
print(OUT/'qsim')
