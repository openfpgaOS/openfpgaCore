#!/usr/bin/env python3
"""Passive host observation of unchanged alias draws and exact MD2 seam scans."""
from pathlib import Path
import argparse,csv,difflib,hashlib,json,os,shutil,struct,subprocess
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-batch-memory-20261008'
S=R/'docs/measurements/quake2-optimize-20261008/demo-screen-v2';O=P/'alias-observer-v1'
ELF=P/'work/base/.obj/quake2/app.elf'
PIN='40c9b219592e0a2781988492afbe50aa6d05e86404b801ed8f9274eb946aa982'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
PROBE=r'''
/* Host-only read observer: no guest memory, timing, cache or scheduling writes.
 * Integer operations only, preserving the lazy host FP exception state. */
static void alias_probe(qsim_cpu *cpu,uint32_t pc)
{
    static FILE *out;static int checked;static char model[65];
    if (!checked) {const char *p=getenv("QSIM_ALIAS_TRACE");if(p){out=fopen(p,"w");if(!out){perror("alias trace");exit(2);}fprintf(out,"frame,model,vertices,source_xyz,triangles,seam_misses,source_misses,seam_collisions,source_collisions,staging_bytes\n");}checked=1;}
    if(!out)return;
    if(pc==0x104bd280u) {uint32_t p=cpu->x[11]+4;if(qsim_read(cpu,p,model,64))abort();model[64]=0;return;}
    uint32_t mesh,header[3];if(qsim_read32(cpu,0x1054117cu,&mesh))abort();if(!mesh)return;
    if(qsim_read(cpu,mesh,header,sizeof(header)))abort();
    unsigned nv=header[1],ni=header[2];if(nv<1||nv>65535||ni>65535)abort();
    uint32_t *v=malloc(nv*12);uint16_t *idx=malloc(ni*2);if(!v||!idx)abort();
    if(qsim_read(cpu,mesh+12,v,nv*12)||qsim_read(cpu,mesh+12+nv*12,idx,ni*2))abort();
    int a[256],b[256];unsigned seen[2048]={0},unique=0,am=0,bm=0,ac=0,bc=0;
    for(unsigned i=0;i<256;i++)a[i]=b[i]=-1;
    for(unsigned i=0;i<nv;i++){unsigned x=v[3*i];if(x>=2048)abort();if(!seen[x]){seen[x]=1;unique++;}}
    for(unsigned i=0;i<ni;i++) {unsigned n=idx[i],x=v[3*n];if(a[n&255]!=(int)n){am++;a[n&255]=n;}if(b[x&255]!=(int)x){bm++;b[x&255]=x;}}
    for(unsigned i=0;i+2<ni;i+=3){unsigned a0=idx[i]&255,a1=idx[i+1]&255,a2=idx[i+2]&255,b0=v[3*idx[i]]&255,b1=v[3*idx[i+1]]&255,b2=v[3*idx[i+2]]&255;ac+=a0==a1||a1==a2||a0==a2;bc+=b0==b1||b1==b2||b0==b2;}
    fprintf(out,"%llu,%s,%u,%u,%u,%u,%u,%u,%u,%u\n",(unsigned long long)((qenv*)cpu->env.ctx)->flips,model,nv,unique,ni/3,am,bm,ac,bc,nv*12);
    free(v);free(idx);
}
'''
def prepare():
    assert sha(ELF)==PIN and not O.exists();O.mkdir()
    initial=json.loads((S/'prepared.json').read_text())
    for rel,h in initial['inputs'].items():
        if rel.startswith('qsim/'):
            assert sha(S/rel)==h;dst=O/rel;dst.parent.mkdir(exist_ok=True);shutil.copy2(S/rel,dst)
    p=O/'qsim/cpu.c';old=p.read_text();anchor='#define QX_FN exec_fast\n';assert old.count(anchor)==1
    new=old.replace(anchor,'#include "env.h"\n'+PROBE+'\n'+anchor);p.write_text(new)
    patch=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/cpu.c',tofile='b/cpu.c'))
    p=O/'qsim/cpu_exec.h';old=p.read_text();anchor='#define INSN() do { \\\n';assert old.count(anchor)==1
    new=old.replace(anchor,anchor+'        if (unlikely(e->pc == 0x104bd280u || e->pc == 0x104b83c0u)) alias_probe(cpu,e->pc); \\\n');p.write_text(new)
    patch+=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/cpu_exec.h',tofile='b/cpu_exec.h'))
    (O/'source.patch').write_text(patch)
    with (O/'build.log').open('w') as f:subprocess.run(['make','-j2'],cwd=O/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
    put(O/'prepared.json',dict(elf_sha256=PIN,original_qsim_sha256=sha(S/'qsim/qsim'),observer_qsim_sha256=sha(O/'qsim/qsim'),sources={p.name:sha(p) for p in (O/'qsim').iterdir() if p.suffix in ['.c','.h']},points={'R_DrawAliasModel':'0x104bd280','R_ApplyGLBuffer':'0x104b83c0','alias_direct':'0x1054117c'},scope='Passive reads at existing function entries; no guest/model mutation, requires unchanged full statistics.'))
def run():
    prep=json.loads((O/'prepared.json').read_text());assert sha(ELF)==PIN and sha(O/'qsim/qsim')==prep['observer_qsim_sha256']
    for n,h in prep['sources'].items():assert sha(O/'qsim'/n)==h
    env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_'))};env['QSIM_ALIAS_TRACE']=str(O/'draws.csv')
    cmd=[str(O/'qsim/qsim'),str(ELF),'--out',str(O/'run'),'--app',str(S/'quake2.app'),'--data-dir',str(S/'data'),'--profile','--frames','362','--max-insns','4e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp','--label','thorough_combined6']
    with (O/'run.log').open('w') as f:r=subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT)
    put(O/'run-command.json',dict(command=cmd,returncode=r.returncode));assert not r.returncode
    analyze()
def analyze():
    original=S/'thorough_combined6'
    assert (O/'run/frames.csv').read_bytes()==(original/'frames.csv').read_bytes()
    a=json.loads((O/'run/summary.json').read_text());b=json.loads((original/'summary.json').read_text())
    for x in (a,b):
        for k in ['wall_seconds','guest_mips']:x.pop(k)
        x['env'].pop('gpuvec_captured');x['elf_sha256']=PIN;x['elf']=str(ELF);x['elf_abspath']=str(ELF)
    assert a==b,[(k,a[k],b.get(k)) for k in a if a[k]!=b.get(k)]
    draws=list(csv.DictReader((O/'draws.csv').open()));steady=[d for d in draws if 20<=int(d['frame'])<360]
    cols=['vertices','source_xyz','triangles','seam_misses','source_misses','seam_collisions','source_collisions','staging_bytes'];tot={k:sum(int(d[k]) for d in steady) for k in cols};models={}
    for d in steady:
        m=models.setdefault(d['model'],dict(draws=0,**{k:0 for k in cols}));m['draws']+=1
        for k in cols:m[k]+=int(d[k])
    put(O/'result.json',dict(passed=True,frame_count=362,steady_frames=340,draws=len(steady),totals=tot,per_frame={k:v/340 for k,v in tot.items()},source_transform_reduction_percent=100*(1-tot['source_misses']/tot['seam_misses']),model=sorted([dict(model=k,**v) for k,v in models.items()],key=lambda r:r['seam_misses'],reverse=True),limitations=['Cache miss counts simulate the identical index streams; projected survival/backface reuse is not inferred.','No claim that all saved transforms turn directly into whole-frame improvement.'],original_statistics_bitwise_equal=True))
    print((O/'result.json').read_text()[:2200])
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','run','analyze']);a=ap.parse_args();globals()[a.action]()
