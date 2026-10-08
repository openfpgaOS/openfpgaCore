#!/usr/bin/env python3
"""LOD6 real-upload functional proof; synthetic worker latency, not FPS."""
import argparse,csv,json,os,shutil,struct,subprocess,sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write,verify_capture,canonical
HERE=Path(__file__).resolve().parent
OUT=CAMP/'phase10/lod6-functional-v1'
CAND=CAMP/'phase10/lod6-driver-v1'
BASE=CAMP/'phase9/checks-state-f11'
PARENT=CAMP/'phase10/load-alias-functional-v2'
def rep(t,a,b):
    assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def symbols(p):
    with p.open('rb')as f:return {s.name:int(s['st_value'])for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
def prepare():
    OUT.mkdir(exist_ok=False);q=OUT/'functional-qsim';q.mkdir()
    old=CAMP/'phase10/load-cram-functional-v1'
    for n in ('app.elf','gfx_pc.c'):shutil.copy2(CAND/n,OUT/n)
    for p in (old/'functional-qsim').iterdir():
        if p.suffix in('.c','.h')or p.name=='Makefile':
            (q/p.name).write_text(p.read_text().replace('LOD5','LOD6').replace('lod5','lod6').replace('0x4c4f4405','0x4c4f4406'))
    p=q/'load_model.h';t=p.read_text()
    a=t.index('        if(m->regs[0x30/4]&1) {');b=t.index('        out[8]=0;',a)
    t=t[:a]+t[b:]
    t=rep(t,'    if(off<0x140) {','    if(off==0x18||off==0x1c||off==0x20||off==0x24||off==0x28||off==0x30)return 0; /* LOD6 ignores removed lighting fields. */\n    if(off<0x140) {')
    p.write_text(t)
    with(OUT/'build.log').open('x')as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'prepared.json',dict(elf_sha256=sha(OUT/'app.elf'),parent_manifest_sha256=sha(old/'manifest.json'),simulator_sha256=sha(q/'qsim'),helper_sha256=sha(Path(__file__)),sources={p.name:sha(p)for p in q.iterdir()if p.suffix in('.c','.h')or p.name=='Makefile'},limits='CPU performs original memoized lighting after START. Functional hardware omits lighting, matching LOD6. Synthetic completion latency is not timing evidence.'))
def capture(mode):
    baseline=mode=='baseline';elf=BASE/'app.elf'if baseline else OUT/'app.elf';syms=symbols(elf)
    os.environ['LOD6_MODEL_MODE']='absent'if baseline else mode
    for n in ('LOD6_STATS_ADDR','LOD6_POOL_TOP_ADDR','LOD6_POOL_STATS_ADDR','LOD6_POOL_TABLE_ADDR','LOD6_STATS_PATH'):os.environ.pop(n,None)
    if not baseline:
        for key,sym in [('STATS_ADDR','load_driver_stats'),('POOL_TOP_ADDR','g_cram_vertex_top'),('POOL_STATS_ADDR','cram_vertex_stats'),('POOL_TABLE_ADDR','g_cram_vertex_source')]:os.environ['LOD6_'+key]=hex(syms[sym])
        os.environ['LOD6_STATS_PATH']=str(OUT/('pool-stats-'+mode+'.csv'))
    m=module('lod6_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),mode,'--elf',str(elf),'--exe',str(OUT/'functional-qsim/qsim'),'--geometry','0','--audio'];m.main()
def render_events(vec):
    events=[];address=0
    for e in canonical(vec)[0]:
        if e[:4]==b'TEX1':continue
        if e[:4]==b'MMIO':
            off,val=struct.unpack_from('<II',e,4)
            if off==8:
                address=val*4;continue
            if off==0x3c:
                if address<0xfc0000:events.append(b'UPDT'+struct.pack('<II',address,val))
                address=(address+4)&0xffffff;continue
        events.append(e)
    return events

def analyze(mode):
    before=OUT/'capture-baseline';after=OUT/('capture-'+mode)
    a=verify_capture(before,sha(BASE/'app.elf'));b=verify_capture(after,sha(OUT/'app.elf'))
    assert len(a)==len(b)==721
    for p in(before,after):assert 'FATAL'not in(p/'run.log').read_text()
    exact={n:sha(before/n)==sha(after/n)for n in('scene.csv','geometry.csv','culling.csv')};assert all(exact.values()),exact
    assert [r['gpu_cmd_words']for r in a]==[r['gpu_cmd_words']for r in b]
    v=module('lod6_vec',HERE/'phase7_validate.py');vectors=[]
    parser=OUT/'vector16.py'
    if not parser.exists():parser.write_text((HERE/'phase6_validate.py').read_text().replace('address + count <= 0x200000','address + count <= 0x1000000'))
    v.v=module('lod6_vec16',parser)
    for p in sorted((before/'gpuvec').glob('*.vec')):
        target=after/'gpuvec'/p.name;old,new=(v.v.vector(x.read_bytes())for x in(p,target))
        assert render_events(old)==render_events(new),(p.name,'render event mismatch')
        assert all(old[k]==new[k]for k in('depth','fence')),p.name
        # Prefix equals the old texture arena; appended high CRAM bytes are source storage.
        diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex())for i in range(0,len(old['texture']),4)if old['texture'][i:i+4]!=new['texture'][i:i+4]]
        vectors.append(dict(frame=int(p.stem.split('_')[1]),texture_changes=diffs,baseline_sha256=sha(p),candidate_sha256=sha(target)))
    assert len(vectors)==8
    stats=list(csv.DictReader((OUT/('pool-stats-'+mode+'.csv')).open()))
    write(OUT/f'result-{mode}.json',dict(passed=True,functional_only=True,mode=mode,all721_scene_geometry_culling_exact=exact,all721_wordcounts_exact=True,all8_render_streams_exact_except_source_pool_uploads=True,padding_check=v.padding(vectors,before,after,CAND/'SM64'),vectors=vectors,model=json.loads((after/'lod6-model.json').read_text()),driver_stats=list(struct.unpack('<16I',(after/'lod6-driver-stats.bin').read_bytes())),pool_stats_last=stats[-1],simulator_sha256=sha(OUT/'functional-qsim/qsim'),elf_sha256=sha(OUT/'app.elf'),qualified_for_deployment=False))
    print(mode,stats[-1])
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);p.add_argument('--mode',default='normal',choices=['baseline','normal','absent','faults','timeout','fullpool']);a=p.parse_args();globals()[a.action]()if a.action=='prepare'else globals()[a.action](a.mode)
