#!/usr/bin/env python3
"""Private two-TU real edge-PGO screen, retaining Quake2's original LTO."""
import argparse, ast, hashlib, json, os, re, shlex, shutil, struct, subprocess, sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/quake2-optimize-20261008'
OUT=P/'renderer-pgo-v1'; WORK=OUT/'work'; PROFILE=OUT/'profile-data'
BUILD=P/'build-combined-v1'; BASE=P/'work/candidate'; SCREEN=P/'demo-screen-v2'
NAMES=('of_gl1','of_emit_q2')
BASE_SHA='ac52214eaec496fc24e0c749a784805ed145a5ce965c7b0d720fa613e38dbfb4'
SOURCE_SHA='3f2f3e2b0f3c7ea3820595ec51ba31a2cbf931faa5e37712fa340721c7941f63'
TRAIN=['-fprofile-arcs','-ftest-coverage','-fprofile-info-section','-fprofile-update=single','-fprofile-dir='+str(PROFILE)]
USE=['-fprofile-use='+str(PROFILE),'-fprofile-partial-training','-Werror=coverage-mismatch','-Werror=missing-profile']
SM64=ROOT/'tools/experiments/os30_fps25_20261005/phase10_renderer_pgo.py'
BRIDGE=r'''    if (getenv("QUAKE2_PGO_EXPORT")) {
        const qsim_sym *s=qsim_syms_lookup(&E.syms,"pgo_export");
        if(!s||rc||nrows!=362) abort();
        snprintf(path,sizeof path,"%s/pgo-export.txt",out);
        E.console=fopen(path,"wb");if(!E.console)abort();
        uint64_t flips_before=E.flips;
        E.done=0; E.cpu->stop_req=0; E.cpu->pc=s->addr; E.cpu->x[1]=0;
        int er=qsim_run(E.cpu,100000000);
        if(er!=QSIM_RUN_STOP||E.done!=ENV_DONE_EXIT||E.flips!=flips_before) abort();
        if(strcmp(E.done_msg,"guest exit(0)"))abort();
        fclose(E.console);E.console=NULL;
        fprintf(stderr,"PGO export executed after frozen timing/profile/report outputs\n");
    }
'''
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):Path(p).write_text(json.dumps(v,indent=2)+'\n')
def inventory(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def rep(t,a,b):assert t.count(a)==1,a;return t.replace(a,b)
def run(c,log,cwd=None,env=None):
    with Path(log).open('x') as f:
        f.write('COMMAND '+shlex.join(c)+'\n');f.flush()
        r=subprocess.run(c,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT)
    assert r.returncode==0,(r.returncode,str(log))
def docker(c,log):
    run(['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
         '-v',f'{OUT}:{OUT}','-w',str(WORK/'src/quake2'),'--entrypoint',c[0],
         'openfpgaos-firmware',*c[1:]],log)
def commands():return json.loads((OUT/'commands.json').read_text())
def guard():
    p=json.loads((OUT/'prepared.json').read_text())
    assert inventory(WORK/'src')==p['source_inputs']
    assert sha(OUT/'baseline.elf')==BASE_SHA
    return p
def symbols(p):
    with Path(p).open('rb') as f:return {s.name:(int(s['st_value']),int(s['st_size'])) for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
def prepare():
    assert sha(BUILD/'app.elf')==BASE_SHA
    assert sha(BASE/'src/quake2/openfpga/of_gl1.c')==SOURCE_SHA
    OUT.mkdir(exist_ok=False)
    subprocess.run(['cp','-a','--reflink=auto',str(BASE),str(WORK)],check=True)
    shutil.copy2(BUILD/'app.elf',OUT/'baseline.elf')
    cc={}
    for n in NAMES:
        lines=(BUILD/'build.log').read_text().splitlines() if n=='of_gl1' else (P/'baseline-build.log').read_text().splitlines()
        rows=[l for l in lines if l.startswith('riscv64-unknown-elf-gcc ') and l.endswith('/'+n+'.c')]
        assert len(rows)==1
        t=rows[0].replace(str(BASE),str(WORK)).replace(str(P/'work/baseline'),str(WORK))
        cc[n]=shlex.split(t)
    rows=[l for l in (BUILD/'build.log').read_text().splitlines() if l.startswith('riscv64-unknown-elf-gcc ') and ' -c 'not in l]
    assert len(rows)==1
    link=shlex.split(rows[0].replace(str(BASE),str(WORK)))
    assert all('-flto=auto' in c for c in [*cc.values(),link])
    write(OUT/'commands.json',dict(compile=cc,link=link,training_flags=TRAIN,use_flags=USE))
    tree=ast.parse(SM64.read_text());node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name)and t.id=='EXPORT' for t in n.targets))
    (OUT/'pgo_export.c').write_text(ast.literal_eval(node.value))
    t=(WORK/'src/sdk/app.ld').read_text()
    (OUT/'training.ld').write_text(rep(t,'    .eh_frame : {','    .gcov_info : { __gcov_info_start = .; KEEP(*(.gcov_info)) __gcov_info_end = .; } > SDRAM\n\n    .eh_frame : {'))
    q=OUT/'qsim';q.mkdir()
    before={}
    for f in (SCREEN/'qsim').iterdir():
        if f.suffix in('.c','.h') or f.name=='Makefile':shutil.copy2(f,q/f.name);before[f.name]=sha(f)
    f=q/'main.c';f.write_text(rep(f.read_text(),'    env_free(&E);',BRIDGE+'    env_free(&E);'))
    run(['make','-j4'],OUT/'qsim-build.log',q)
    write(OUT/'prepared.json',dict(passed=True,baseline_sha256=BASE_SHA,of_gl1_sha256=SOURCE_SHA,
        source_inputs=inventory(WORK/'src'),baseline_objects={str(f.relative_to(WORK)):sha(f)for f in (WORK/'.obj/quake2').rglob('*.o')},
        baseline_build_sha256=sha(BUILD/'result.json'),baseline_harness_sha256=sha(SCREEN/'prepared.json'),
        parent_qsim_sources=before,qsim_sources={f.name:sha(f)for f in q.iterdir()if f.suffix in('.c','.h')or f.name=='Makefile'},
        qsim_sha256=sha(q/'qsim'),inherited_export_recipe_sha256=sha(SM64),recipe_sha256=sha(__file__),
        edge_only=True,LTO_preserved=True,same_workload_train_eval=True,production_changed=False))
    shutil.copy2(__file__,OUT/'recipe-prepare.py')
    print(OUT)
def build_train():
    p=guard();r=commands();d=OUT/'training';d.mkdir(exist_ok=False);PROFILE.mkdir()
    docker(['riscv64-unknown-elf-gcc','--version'],d/'compiler.log')
    assert '13.2.0' in (d/'compiler.log').read_text()
    for n in NAMES:docker(r['compile'][n]+TRAIN,d/(n+'-compile.log'))
    c=r['compile'][NAMES[0]][:];c[c.index('-o')+1]=str(d/'pgo_export.o');c[-1]=str(OUT/'pgo_export.c')
    docker(c,d/'export-compile.log')
    c=r['link'][:];c[c.index('-o')+1]=str(d/'app.elf');c[c.index('-T')+1]=str(OUT/'training.ld')
    i=c.index('-Wl,--start-group');c[i:i]=[str(d/'pgo_export.o'),'-Wl,-u,pgo_export']
    c.insert(c.index('-Wl,--end-group'),'-lgcov');docker(c,d/'link.log')
    changed=[n for n,h in p['baseline_objects'].items()if sha(WORK/n)!=h]
    assert sorted(Path(n).stem for n in changed)==sorted(NAMES),changed
    for n in NAMES:
        o=Path(r['compile'][n][r['compile'][n].index('-o')+1])
        for suffix in('.o','.gcno'):shutil.copy2(o.with_suffix(suffix),d/(n+suffix))
    with (d/'app.elf').open('rb')as f:
        e=ELFFile(f);s=e.get_section_by_name('.gcov_info');assert s and s['sh_size']==8
        names=[x.name for x in e.get_section_by_name('.symtab').iter_symbols()]
        counters=[x for x in names if x.startswith('__gcov0.')];assert len(counters)>10
        assert not any(x in names for x in ('__gcov_indirect_call_profiler_v4','__gcov_time_profiler_counter'))
    guard();write(d/'result.json',dict(passed=True,elf_sha256=sha(d/'app.elf'),changed_objects=changed,
        info_pointers=2,counter_symbols=len(counters),value_profiling=False,link_command=c,
        gcno_sha256={n:sha(d/(n+'.gcno'))for n in NAMES}))
    print('TRAIN',sha(d/'app.elf'))
def capture():
    guard();p=json.loads((OUT/'prepared.json').read_text());assert sha(OUT/'qsim/qsim')==p['qsim_sha256']
    env={k:v for k,v in os.environ.items()if not k.startswith(('SM64_','QSIM_','QUAKE2_PGO_'))};env['QUAKE2_PGO_EXPORT']='1'
    d=OUT/'capture-train';assert not d.exists()
    c=[str(OUT/'qsim/qsim'),str(OUT/'training/app.elf'),'--out',str(d),'--app',str(SCREEN/'quake2.app'),
        '--data-dir',str(SCREEN/'data'),'--profile','--frames','362','--max-insns','4e9','--gpu-sched','eager',
        '--mem','flat','--no-normalize-build-stamp','--label','pgo-training']
    run(c,OUT/'capture-train.log',SCREEN,env)
    s=json.loads((d/'summary.json').read_text());assert s['frames']==362 and s['stop_reason']=='frame limit'
    assert s['env']['unmapped_mmio']==0 and s['gpu_sync']['ring_overflows']==0
    assert 'GEOMETRY=0' in (d/'console.log').read_text()
    write(OUT/'capture-result.json',dict(passed=True,command=c,elf_sha256=sha(OUT/'training/app.elf'),
        summary_sha256=sha(d/'summary.json'),export_sha256=sha(d/'pgo-export.txt'),training_not_performance=True))
def collect():
    d=OUT/'training';t=(OUT/'capture-train/pgo-export.txt').read_text();assert t.count('PGO_BEGIN\n')==t.count('PGO_END\n')==1
    rows=t.split('PGO_BEGIN\n')[1].split('PGO_END\n')[0].splitlines();assert all(re.fullmatch(r'PGO:[0-9a-f]+',l)for l in rows)
    b=bytes.fromhex(''.join(l[4:]for l in rows));(d/'profile.stream').write_bytes(b)
    ns=re.findall(rb'[^\x00\x01-\x1f\x7f-\xff]{5,}\.gcda',b);assert len(ns)==2,ns
    for n in ns:
        p=Path(n.decode());assert p.is_absolute()and p.resolve().is_relative_to(PROFILE.resolve()),p
        assert any(p.name.endswith(x+'.gcda')for x in NAMES);p.parent.mkdir(parents=True,exist_ok=True)
    docker(['riscv64-unknown-elf-gcov-tool','merge-stream',str(d/'profile.stream')],d/'merge-stream.log')
    fs=sorted(PROFILE.rglob('*.gcda'));assert len(fs)==2
    for f in fs:docker(['riscv64-unknown-elf-gcov-dump','-l',str(f)],d/(f.name.split('#')[-1]+'.dump'))
    for f in d.glob('*.gcno'):docker(['riscv64-unknown-elf-gcov-dump','-l',str(f)],d/(f.name+'.dump'))
    write(d/'counter-provenance.json',dict(real_guest_counters=True,edge_only=True,
        elf_sha256=sha(d/'app.elf'),capture_sha256=sha(OUT/'capture-result.json'),
        stream_sha256=sha(d/'profile.stream'),export_sha256=sha(OUT/'capture-train/pgo-export.txt'),
        gcda_sha256={f.name:sha(f)for f in fs},summaries_unmodified=True,
        profile_ids_not_rewritten=True,export_after_final_profile=True))
def build_use():
    p=guard();r=commands();d=OUT/'use';d.mkdir(exist_ok=False)
    for n in NAMES:docker(r['compile'][n]+USE+['-fdump-ipa-profile-details='+str(d/(n+'-profile.txt'))],d/(n+'-compile.log'))
    c=r['link'][:];c[c.index('-o')+1]=str(d/'app.elf');docker(c,d/'link.log')
    assert not any(n.startswith('__gcov')or n=='pgo_export'for n in symbols(d/'app.elf'))
    with (d/'app.elf').open('rb')as f:assert ELFFile(f).get_section_by_name('.gcov_info')is None
    for n in NAMES:
        t=(d/(n+'-compile.log')).read_text().split('\n',1)[1]
        assert not re.search('coverage-mismatch|missing-profile',t)
        o=Path(r['compile'][n][r['compile'][n].index('-o')+1]);shutil.copy2(o,d/(n+'.o'))
    changed=[n for n,h in p['baseline_objects'].items()if sha(WORK/n)!=h]
    assert sorted(Path(n).stem for n in changed)==sorted(NAMES)
    guard();write(d/'result.json',dict(passed=True,elf_sha256=sha(d/'app.elf'),flags=USE,link_command=c,
        changed_objects=changed,instrumentation_absent=True,source_exact=True,link_order_unchanged=True,
        counter_provenance_sha256=sha(OUT/'training/counter-provenance.json')))
    print('USE',sha(d/'app.elf'))
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','build_train','capture','collect','build_use']);a=ap.parse_args();globals()[a.action]()
