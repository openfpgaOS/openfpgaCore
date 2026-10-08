#!/usr/bin/env python3
"""Count actually executed alias transform misses without changing the guest."""
from pathlib import Path
import argparse,csv,difflib,hashlib,json,os,re,shutil,subprocess
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-batch-memory-20261008';S=R/'docs/measurements/quake2-optimize-20261008/demo-screen-v2';O=P/'alias-transform-observer-v1'
CONFIG={'baseline':(P/'work/base/.obj/quake2/app.elf','thorough_combined6'),'candidate':(P/'build-alias_positions2/app.elf','batchmem_alias_positions2')}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(p,o):p.write_text(json.dumps(o,indent=2)+'\n')
def points(elf):
    nm=subprocess.check_output(['llvm-nm','--defined-only',str(elf)],text=True);symbols={l.split()[2]:int(l.split()[0],16) for l in nm.splitlines() if len(l.split())==3}
    entries=[];assembly=[]
    for key in ['ofgl_cvert','ofgl_alias_cvert','ofgl_project_cvert']:
        names=[n for n in symbols if n==key or n.startswith(key+'.')]
        if not names:continue
        assert len(names)==1,names;n=names[0]
        dis=subprocess.check_output(['llvm-objdump','-d','--mattr=+m,+a,+f,+c','--no-show-raw-insn','--disassemble-symbols='+n,str(elf)],text=True);assembly.append(dis)
        if key=='ofgl_project_cvert':pc=symbols[n]
        else:
            flw=re.findall(r'^([0-9a-f]+):\s+flw\s',dis,re.M);assert flw;pc=int(flw[0],16)
        entries.append(dict(function=n,address=pc,kind='project' if key=='ofgl_project_cvert' else 'alias_transform' if 'alias_' in key else 'generic_transform'))
    alias=[n for n in symbols if n=='alias_direct' or n.startswith('alias_direct.')];assert len(alias)==1
    return entries,symbols[alias[0]],'\n'.join(assembly)
def prepare():
    assert not O.exists();O.mkdir();initial=json.loads((S/'prepared.json').read_text())
    for rel,h in initial['inputs'].items():
        if rel.startswith('qsim/'):
            assert sha(S/rel)==h;p=O/rel;p.parent.mkdir(exist_ok=True);shutil.copy2(S/rel,p)
    allpoints={};conditions=[]
    for label,(elf,oldlabel) in CONFIG.items():
        point,alias,assembly=points(elf);allpoints[label]=dict(elf_sha256=sha(elf),points=point,alias_direct=alias,label=oldlabel);(O/(label+'-assembly.txt')).write_text(assembly)
        for k,p in enumerate(point):conditions.append(f'if (kind=={0 if label=="baseline" else 1} && pc==0x{p["address"]:08x}u) {{ col={k}; alias_addr=0x{alias:08x}u; }}')
    pcs=sorted({p['address'] for v in allpoints.values() for p in v['points']})
    code='''
#include "env.h"
static uint64_t alias_counts[1024][6];
static const char *alias_count_file;
static void alias_counts_save(void) {
    if(!alias_count_file)return;FILE *f=fopen(alias_count_file,"w");if(!f)abort();
    fprintf(f,"frame,c0_alias,c1_alias,c2_alias,c0_other,c1_other,c2_other\\n");
    for(unsigned i=0;i<1024;i++)if(alias_counts[i][0]||alias_counts[i][1]||alias_counts[i][2]||alias_counts[i][3]||alias_counts[i][4]||alias_counts[i][5])fprintf(f,"%u,%llu,%llu,%llu,%llu,%llu,%llu\\n",i,(unsigned long long)alias_counts[i][0],(unsigned long long)alias_counts[i][1],(unsigned long long)alias_counts[i][2],(unsigned long long)alias_counts[i][3],(unsigned long long)alias_counts[i][4],(unsigned long long)alias_counts[i][5]);
    fclose(f);
}
static void alias_count(qsim_cpu *cpu,uint32_t pc) {
    static int initialized,kind;int col=-1;uint32_t alias_addr=0,mesh=0;
    if(!initialized){alias_count_file=getenv("QSIM_ALIAS_COUNT");const char *k=getenv("QSIM_ALIAS_KIND");kind=k?atoi(k):0;atexit(alias_counts_save);initialized=1;}
    if(!alias_count_file)return;
    CONDITIONS
    if(col<0)return;
    if(qsim_read32(cpu,alias_addr,&mesh))abort();uint64_t frame=((qenv*)cpu->env.ctx)->flips;if(frame>=1024)abort();alias_counts[frame][col+(mesh?0:3)]++;
}
'''.replace('CONDITIONS','\n    '.join(conditions))
    p=O/'qsim/cpu.c';old=p.read_text();anchor='#define QX_FN exec_fast\n';assert old.count(anchor)==1;new=old.replace(anchor,code+anchor);p.write_text(new);patch=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/cpu.c',tofile='b/cpu.c'))
    p=O/'qsim/cpu_exec.h';old=p.read_text();anchor='#define INSN() do { \\\n';cond=' || '.join(f'e->pc==0x{pc:08x}u' for pc in pcs);new=old.replace(anchor,anchor+f'        if (unlikely({cond})) alias_count(cpu,e->pc); \\\n');p.write_text(new);patch+=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/cpu_exec.h',tofile='b/cpu_exec.h'))
    (O/'source.patch').write_text(patch)
    with (O/'build.log').open('w') as f:subprocess.run(['make','-j2'],cwd=O/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
    put(O/'prepared.json',dict(config=allpoints,qsim_sha256=sha(O/'qsim/qsim'),sources={p.name:sha(p) for p in (O/'qsim').iterdir() if p.suffix in ['.c','.h']},scope='First float load in each transform miss block and projection function entry; integer-only passive host counters, no guest/model mutation.'))
def run(label):
    elf,oldlabel=CONFIG[label];prep=json.loads((O/'prepared.json').read_text());assert sha(elf)==prep['config'][label]['elf_sha256'];assert sha(O/'qsim/qsim')==prep['qsim_sha256']
    env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_'))};env.update(QSIM_ALIAS_COUNT=str(O/(label+'.csv')),QSIM_ALIAS_KIND='0' if label=='baseline' else '1')
    cmd=[str(O/'qsim/qsim'),str(elf),'--out',str(O/label),'--app',str(S/'quake2.app'),'--data-dir',str(S/'data'),'--profile','--frames','362','--max-insns','4e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp','--label',oldlabel]
    with (O/(label+'.log')).open('w') as f:r=subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT)
    put(O/(label+'-command.json'),dict(command=cmd,returncode=r.returncode));assert not r.returncode
    assert (O/label/'frames.csv').read_bytes()==(S/oldlabel/'frames.csv').read_bytes()
    a=json.loads((O/label/'summary.json').read_text());b=json.loads((S/oldlabel/'summary.json').read_text())
    for x in [a,b]:
        for k in ['wall_seconds','guest_mips','elf_abspath']:x.pop(k,None)
        x['env'].pop('gpuvec_captured');x['elf']=str(elf)
    assert a==b,[(k,a[k],b.get(k)) for k in a if a[k]!=b.get(k)]
    rows=[r for r in csv.DictReader((O/(label+'.csv')).open()) if 20<=int(r['frame'])<360]
    counts={c:sum(int(r[c]) for r in rows) for c in ['c0_alias','c1_alias','c2_alias','c0_other','c1_other','c2_other']}
    put(O/(label+'-result.json'),dict(passed=True,elf_sha256=sha(elf),original_statistics_equal=True,steady_frames=340,points=prep['config'][label]['points'],counts=counts,counts_per_frame={k:v/340 for k,v in counts.items()}));print((O/(label+'-result.json')).read_text())
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','baseline','candidate']);a=ap.parse_args()
    if a.action=='prepare':prepare()
    else:run(a.action)
