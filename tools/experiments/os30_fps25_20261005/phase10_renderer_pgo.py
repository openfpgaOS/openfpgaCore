#!/usr/bin/env python3
"""One private two-TU edge-PGO screen; no production or hardware changes."""
import argparse, csv, hashlib, json, os, re, shlex, shutil, struct, subprocess, sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import ROOT, CAMP, INPUTS, module, sha, write

H=Path(__file__).resolve().parent
P=CAMP/'phase10'; OUT=P/'renderer-pgo-v1'; SELECTED=P/'load-submit-overlap-v1'
BUILD=P/'load-submit-rebuild-v1'; OLD=P/'lod5-recycle-functional-v1'
ELF='bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
NAMES=('gfx_pc','gfx_gpu'); WORK=OUT/'work'; PROFILE=OUT/'profile-data'
TRAIN_FLAGS=['-fprofile-arcs','-ftest-coverage','-fprofile-info-section',
             '-fprofile-update=single','-fprofile-dir='+str(PROFILE)]
USE_FLAGS=['-fprofile-use='+str(PROFILE),'-fprofile-partial-training',
           '-Werror=coverage-mismatch','-Werror=missing-profile']

EXPORT=r'''#include <stddef.h>
#include <stdlib.h>
#include <unistd.h>
struct gcov_info;
extern const struct gcov_info *const __gcov_info_start[];
extern const struct gcov_info *const __gcov_info_end[];
extern void __gcov_info_to_gcda(const struct gcov_info *,
 void (*)(const char *,void *),void (*)(const void *,unsigned,void *),
 void *(*)(unsigned,void *),void *);
extern void __gcov_filename_to_gcfn(const char *,
 void (*)(const void *,unsigned,void *),void *);
static void put(const char *p,unsigned n){
 while(n){int k=write(2,p,n);if(k<=0)_Exit(99);p+=k;n-=k;}
}
static void dump(const void *data,unsigned n,void *arg){
 (void)arg;const unsigned char *p=data;const char hex[]="0123456789abcdef";
 while(n){unsigned k=n>64?64:n;char b[133]={'P','G','O',':'};
  for(unsigned i=0;i<k;i++){b[4+i*2]=hex[p[i]>>4];b[5+i*2]=hex[p[i]&15];}
  b[4+k*2]='\n';put(b,5+k*2);p+=k;n-=k;
 }
}
static void filename(const char *p,void *arg){__gcov_filename_to_gcfn(p,dump,arg);}
static void *allocate(unsigned n,void *arg){(void)arg;void *p=malloc(n);if(!p)_Exit(98);return p;}
__attribute__((noreturn,used,noinline)) void pgo_export(void){
 put("PGO_BEGIN\n",10);
 for(const struct gcov_info *const *p=__gcov_info_start;p<__gcov_info_end;p++)
  __gcov_info_to_gcda(*p,filename,dump,allocate,0);
 put("PGO_END\n",8);_Exit(0);
}
'''
BRIDGE=r'''    if (getenv("RENDERER_PGO_EXPORT")) {
        const qsim_sym *s=qsim_syms_lookup(&E.syms,"pgo_export");
        if(!s||rc||nrows!=721) abort();
        uint64_t flips_before=E.flips;
        E.done=0; E.cpu->stop_req=0; E.cpu->pc=s->addr; E.cpu->x[1]=0;
        int er=qsim_run(E.cpu,100000000);
        if(er!=QSIM_RUN_STOP||E.done!=ENV_DONE_EXIT||E.flips!=flips_before) abort();
        fprintf(stderr,"PGO export executed after frozen timing/profile/report outputs\n");
    }
'''

def symbols(elf):
    with elf.open('rb') as f:
        return {s.name:(int(s['st_value']),int(s['st_size']))
                for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}

def rep(text,a,b):
    assert text.count(a)==1,a;return text.replace(a,b)

def run(command,log,cwd=None,env=None):
    with log.open('x') as f:
        f.write('COMMAND '+shlex.join(command)+'\n');f.flush()
        r=subprocess.run(command,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT)
    assert r.returncode==0,(r.returncode,str(log))

def docker(command,log,cwd=None):
    run(['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
         '-v',f'{OUT}:{OUT}','-w',str(cwd or WORK/'src/sm64'),
         '--entrypoint',command[0],'openfpgaos-firmware',*command[1:]],log)

def commands():
    return json.loads((OUT/'commands.json').read_text())

def source_guard():
    prep=json.loads((OUT/'prepared.json').read_text())
    for name,rel in INPUTS.items():assert sha(WORK/rel)==prep['sources'][name]
    return prep

def prepare():
    assert sha(SELECTED/'app.elf')==sha(BUILD/'app.elf')==ELF
    OUT.mkdir(exist_ok=False)
    subprocess.run(['cp','-a','--reflink=auto',str(BUILD/'SM64'),str(WORK)],check=True)
    for name,rel in INPUTS.items():assert sha(WORK/rel)==sha(SELECTED/name)
    for name in INPUTS:shutil.copy2(SELECTED/name,OUT/name)
    shutil.copy2(SELECTED/'app.elf',OUT/'baseline.elf')
    logs=(BUILD/'build-normal.log').read_text().splitlines()
    cc={}
    for name in NAMES:
        lines=[l for l in logs if l.startswith('riscv64-unknown-elf-gcc ') and l.endswith('/'+name+'.c')]
        assert len(lines)==1
        cc[name]=shlex.split(lines[0].replace(str(BUILD/'SM64'),str(WORK)))
    links=[l for l in logs if l.startswith('riscv64-unknown-elf-gcc ') and ' -c 'not in l]
    assert len(links)==1
    link=shlex.split(links[0].replace(str(BUILD/'SM64'),str(WORK)))
    write(OUT/'commands.json',dict(compile=cc,link=link,training_flags=TRAIN_FLAGS,use_flags=USE_FLAGS))
    (OUT/'pgo_export.c').write_text(EXPORT)
    ld=(WORK/'src/sdk/app.ld').read_text()
    ld=rep(ld,'    .eh_frame : {',
        '    .gcov_info : { __gcov_info_start = .; KEEP(*(.gcov_info)) __gcov_info_end = .; } > SDRAM\n\n    .eh_frame : {')
    (OUT/'training.ld').write_text(ld)
    q=OUT/'qsim';q.mkdir()
    for f in (OLD/'functional-qsim').iterdir():
        if f.suffix in('.c','.h')or f.name=='Makefile':shutil.copy2(f,q/f.name)
    f=q/'main.c';f.write_text(rep(f.read_text(),'    env_free(&E);',BRIDGE+'    env_free(&E);'))
    run(['make','-j4'],OUT/'qsim-build.log',q)
    write(OUT/'prepared.json',dict(selected_elf_sha256=ELF,
        sources={n:sha(SELECTED/n)for n in INPUTS},
        baseline_objects={str(f.relative_to(WORK)):sha(f)for f in (WORK/'.obj/sm64').rglob('*.o')},
        qsim_sources={f.name:sha(f)for f in q.iterdir()if f.suffix in('.c','.h')or f.name=='Makefile'},
        qsim_sha256=sha(q/'qsim'),original_build_sha256=sha(BUILD/'build-normal.json'),
        helper_sha256=sha(Path(__file__)),native_fma=True,edge_only=True,
        same_workload_train_eval=True,production_changed=False))
    shutil.copy2(__file__,OUT/'recipe-prepare.py')
    print(OUT)

def build_train():
    prep=source_guard();record=commands();dest=OUT/'training';dest.mkdir(exist_ok=False);PROFILE.mkdir()
    for name in NAMES:
        cmd=record['compile'][name]+TRAIN_FLAGS
        docker(cmd,dest/(name+'-compile.log'))
    cmd=record['compile']['gfx_pc'][:]
    cmd[cmd.index('-o')+1]=str(dest/'pgo_export.o');cmd[-1]=str(OUT/'pgo_export.c')
    docker(cmd,dest/'export-compile.log')
    link=record['link'][:];link[link.index('-o')+1]=str(dest/'app.elf')
    link[link.index('-T')+1]=str(OUT/'training.ld')
    pos=link.index('-Wl,--start-group');link[pos:pos]=[str(dest/'pgo_export.o'),'-Wl,-u,pgo_export']
    link.insert(link.index('-Wl,--end-group'),'-lgcov')
    docker(link,dest/'link.log')
    original=prep['baseline_objects']; changed=[]
    for name,digest in original.items():
        if sha(WORK/name)!=digest:changed.append(name)
    assert sorted(Path(n).stem for n in changed)==sorted(NAMES),changed
    for name in NAMES:
        obj=Path(record['compile'][name][record['compile'][name].index('-o')+1])
        for suffix in('.o','.gcno'):shutil.copy2(obj.with_suffix(suffix),dest/(name+suffix))
    with (dest/'app.elf').open('rb')as f:
        e=ELFFile(f);sec=e.get_section_by_name('.gcov_info');assert sec and sec['sh_size']==8
        names=[s.name for s in e.get_section_by_name('.symtab').iter_symbols()]
        counters=[n for n in names if n.startswith('__gcov0.')];assert len(counters)>10
        assert not any(n in names for n in ('__gcov_indirect_call_profiler_v4','__gcov_time_profiler_counter'))
    source_guard()
    write(dest/'result.json',dict(passed=True,elf_sha256=sha(dest/'app.elf'),changed_objects=changed,
        info_pointers=2,counter_symbols=len(counters),value_profiling=False,
        gcno_sha256={n:sha(dest/(n+'.gcno'))for n in NAMES},
        commands=record,link_command=link,source_exact=True))
    print('TRAIN ELF',sha(dest/'app.elf'))

def capture(mode):
    assert mode in ('train','baseline','train2','baseline2','use')
    elf=OUT/'training/app.elf'if mode.startswith('train')else OUT/'baseline.elf'if mode.startswith('baseline')else OUT/'use/app.elf'
    syms=symbols(elf)
    os.environ['LOD5_MODEL_MODE']='normal'
    for k,n in [('STATS_ADDR','load_driver_stats'),('POOL_TOP_ADDR','g_cram_vertex_top'),
                ('POOL_STATS_ADDR','cram_vertex_stats'),('POOL_TABLE_ADDR','g_cram_vertex_source'),
                ('RECYCLE_STATS_ADDR','cram_vertex_recycle_stats')]:os.environ['LOD5_'+k]=hex(syms[n][0])
    os.environ['LOD5_STATS_PATH']=str(OUT/('pool-'+mode+'.csv'))
    os.environ.pop('RENDERER_PGO_EXPORT',None)
    if mode.startswith('train'):os.environ['RENDERER_PGO_EXPORT']='1'
    q=OUT/('qsim-export-v2'if mode in ('train2','baseline2','use')else'qsim')
    m=module('pgo_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(__file__),mode,'--elf',str(elf),'--exe',str(q/'qsim'),'--geometry','0','--audio'];m.main()

def export_v2():
    q=OUT/'qsim-export-v2';q.mkdir(exist_ok=False)
    for p in (OUT/'qsim').iterdir():
        if p.suffix in('.c','.h')or p.name=='Makefile':shutil.copy2(p,q/p.name)
    f=q/'main.c';t=f.read_text()
    t=rep(t,'        uint64_t flips_before=E.flips;',
        '        snprintf(path,sizeof path,"%s/pgo-export.txt",out);\n'
        '        E.console=fopen(path,"wb");if(!E.console)abort();\n'
        '        uint64_t flips_before=E.flips;')
    t=rep(t,'        fprintf(stderr,"PGO export executed after frozen timing/profile/report outputs\\n");',
        '        if(strcmp(E.done_msg,"guest exit(0)"))abort();\n'
        '        fclose(E.console);E.console=NULL;\n'
        '        fprintf(stderr,"PGO export executed after frozen timing/profile/report outputs\\n");')
    f.write_text(t);run(['make','-j4'],OUT/'qsim-export-v2-build.log',q)
    write(OUT/'export-v2.json',dict(reason='Original qsim closes its console before final reports; first export had no stream. Preserve first run and open a distinct untimed sink.',
        parent_main_sha256=sha(OUT/'qsim/main.c'),main_sha256=sha(f),qsim_sha256=sha(q/'qsim'),
        export_exit0_required=True,changes_guest_or_render_semantics=False))

def collect():
    dest=OUT/'training';console=(OUT/'capture-train2/pgo-export.txt').read_text()
    assert console.count('PGO_BEGIN\n')==console.count('PGO_END\n')==1
    body=console.split('PGO_BEGIN\n')[1].split('PGO_END\n')[0]
    lines=body.splitlines();assert all(re.fullmatch(r'PGO:[0-9a-f]+',l)for l in lines)
    data=bytes.fromhex(''.join(l[4:]for l in lines));(dest/'profile.stream').write_bytes(data)
    # Stream encoder filenames are read from the guest data, not fabricated.
    names=re.findall(rb'[^\x00\x01-\x1f\x7f-\xff]{5,}\.gcda',data)
    assert len(names)==2,names
    for n in names:
        path=Path(n.decode());assert path.is_absolute()and path.resolve().is_relative_to(PROFILE.resolve()),path
        assert any(path.name.endswith(x+'.gcda')for x in NAMES)
        path.parent.mkdir(parents=True,exist_ok=True)
    docker(['riscv64-unknown-elf-gcov-tool','merge-stream',str(dest/'profile.stream')],dest/'merge-stream.log')
    counters()

def counters():
    dest=OUT/'training'
    gcda=sorted(PROFILE.rglob('*.gcda'));assert len(gcda)==2
    for f in gcda:docker(['riscv64-unknown-elf-gcov-dump','-l',str(f)],dest/(f.name.split('#')[-1]+'.dump'))
    for f in dest.glob('*.gcno'):docker(['riscv64-unknown-elf-gcov-dump','-l',str(f)],dest/(f.name+'.dump'))
    write(dest/'counter-provenance.json',dict(elf_sha256=sha(dest/'app.elf'),
        captured_inputs_sha256=sha(OUT/'capture-train2/inputs.json'),
        console_sha256=sha(OUT/'capture-train2/pgo-export.txt'),stream_sha256=sha(dest/'profile.stream'),
        gcda_sha256={f.name:sha(f)for f in gcda},real_guest_counters=True,
        export_after_final_profile=True,zero_synthetic_value_histograms=True))
    print('GCDA',[(f.name,f.stat().st_size)for f in gcda])

def build_use():
    source_guard();record=commands();dest=OUT/'use';dest.mkdir(exist_ok=False)
    for name in NAMES:docker(record['compile'][name]+USE_FLAGS+['-fdump-ipa-profile-details='+str(dest/(name+'-profile.txt'))],dest/(name+'-compile.log'))
    link=record['link'][:];link[link.index('-o')+1]=str(dest/'app.elf');docker(link,dest/'link.log')
    names=symbols(dest/'app.elf');assert not any(n.startswith('__gcov')or n=='pgo_export'for n in names)
    assert '.gcov_info'not in subprocess.check_output(['readelf','-S',str(dest/'app.elf')],text=True)
    for n in NAMES:
        log=(dest/(n+'-compile.log')).read_text();assert 'coverage-mismatch'not in log.split('\n',1)[1]and'missing-profile'not in log.split('\n',1)[1]
        obj=Path(record['compile'][n][record['compile'][n].index('-o')+1]);shutil.copy2(obj,dest/(n+'.o'))
    source_guard();write(dest/'result.json',dict(passed=True,elf_sha256=sha(dest/'app.elf'),
        flags=USE_FLAGS,instrumentation_absent=True,source_exact=True,link_order_unchanged=True,
        counter_provenance_sha256=sha(OUT/'training/counter-provenance.json')))
    print('USE ELF',sha(dest/'app.elf'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build_train','export_v2','capture','collect','build_use']);p.add_argument('--mode',choices=['train','baseline','train2','baseline2','use']);a=p.parse_args()
    capture(a.mode)if a.action=='capture'else globals()[a.action]()
