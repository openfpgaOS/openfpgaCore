#!/usr/bin/env python3
"""LOD5 real-upload functional proof; synthetic worker latency, not FPS."""
import argparse,csv,json,os,shutil,struct,subprocess,sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write,verify_capture,canonical
HERE=Path(__file__).resolve().parent
OUT=CAMP/'phase10/load-cram-functional-v1'
CAND=CAMP/'phase10/load-cram-driver-v1'
BASE=CAMP/'phase9/checks-state-f11'
PARENT=CAMP/'phase10/load-alias-functional-v2'
def rep(t,a,b):
    assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def symbols(p):
    with p.open('rb')as f:return {s.name:int(s['st_value'])for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
def prepare():
    OUT.mkdir(exist_ok=False);q=OUT/'functional-qsim';q.mkdir()
    for n in ('app.elf','gfx_pc.c'):shutil.copy2(CAND/n,OUT/n)
    for p in (PARENT/'functional-qsim').iterdir():
        if p.suffix in('.c','.h')or p.name=='Makefile':shutil.copy2(p,q/p.name)
    p=q/'load_model.h';t=p.read_text().replace('LOD4','LOD5').replace('lod4','lod5').replace('0x4c4f4404','0x4c4f4405')
    t=rep(t,'!gp(E,src,count*24)||!gp(E,m->output,count*32)', 'src<0xfc0000u||src>0x1000000u-count*24u||gpu_fast_tex_used(E->gpu)<src+count*24u||!gp(E,m->output,count*32)')
    t=rep(t,'memcpy(raw,gp(E,src+i*24,24),24);','memcpy(raw,gpu_fast_tex_data(E->gpu)+src+i*24,24);')
    t=rep(t,'    if(!off){*val=', '''    if(!off&&!strcmp(m->mode,"fullpool")) {
        static int injected;if(!injected) {injected=1;
            const char *a=getenv("LOD5_POOL_TOP_ADDR");if(!a)abort();
            wr32(E,(uint32_t)strtoul(a,NULL,0),0x01000000u);
        }
    }
    if(!off){*val=''')
    t+='''\nstatic void lm_stats_frame(qenv *E) {
    const char *path=getenv("LOD5_STATS_PATH"),*a=getenv("LOD5_POOL_STATS_ADDR");
    if(!path||!a)return;static FILE *f;
    if(!f){f=fopen(path,"w");if(!f)abort();fprintf(f,"frame,calls,hits,uploads,words,capacity_fail,probe_fail,probes,unavailable\\n");}
    uint32_t *stats=(uint32_t *)gp(E,(uint32_t)strtoul(a,NULL,0),32);
    fprintf(f,"%llu",(unsigned long long)E->flips);
    for(unsigned i=0;i<8;i++)fprintf(f,",%u",stats[i]);fputc('\\n',f);fflush(f);
}
''';p.write_text(t)
    p=q/'env.c';t=p.read_text();t=rep(t,'    uint64_t seq = E->flips++;','    lm_stats_frame(E);\n    uint64_t seq = E->flips++;')
    t=rep(t,'static int vec_wanted(qenv *E, uint64_t frame)','static int vec_wanted(qenv *E, uint64_t frame)') if False else t
    # Declaration before the flip callback, definition remains by MMIO model.
    t=rep(t,'static void frame_event(', 'static void lm_stats_frame(qenv *E);\nstatic void frame_event(')
    p.write_text(t)
    p=q/'main.c';t=p.read_text().replace('LOD4_STATS_ADDR','LOD5_STATS_ADDR').replace('lod4-driver-stats.bin','lod5-driver-stats.bin')
    t=rep(t,'    env_free(&E);','''    const char *pool=getenv("LOD5_POOL_TABLE_ADDR");
    if(pool){snprintf(path,sizeof path,"%s/lod5-pool.bin",out);FILE *f=fopen(path,"wb");if(!f)abort();fwrite(qsim_mem_ptr(E.cpu,(uint32_t)strtoul(pool,NULL,0),8192),8192,1,f);fclose(f);}
    env_free(&E);''');p.write_text(t)
    with(OUT/'build.log').open('x')as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'prepared.json',dict(elf_sha256=sha(OUT/'app.elf'),parent_manifest_sha256=sha(PARENT/'final-manifest.json'),simulator_sha256=sha(q/'qsim'),helper_sha256=sha(Path(__file__)),sources={p.name:sha(p)for p in q.iterdir()if p.suffix in('.c','.h')or p.name=='Makefile'},limits='Actual CPU lookup and upload instructions; native-FMA functional LOAD; synthetic two-poll completion and upload-ready. No physical timing claim.'))
def capture(mode):
    baseline=mode=='baseline';elf=BASE/'app.elf'if baseline else OUT/'app.elf';syms=symbols(elf)
    os.environ['LOD5_MODEL_MODE']='absent'if baseline else mode
    for n in ('LOD5_STATS_ADDR','LOD5_POOL_TOP_ADDR','LOD5_POOL_STATS_ADDR','LOD5_POOL_TABLE_ADDR','LOD5_STATS_PATH'):os.environ.pop(n,None)
    if not baseline:
        for key,sym in [('STATS_ADDR','load_driver_stats'),('POOL_TOP_ADDR','g_cram_vertex_top'),('POOL_STATS_ADDR','cram_vertex_stats'),('POOL_TABLE_ADDR','g_cram_vertex_source')]:os.environ['LOD5_'+key]=hex(syms[sym])
        os.environ['LOD5_STATS_PATH']=str(OUT/('pool-stats-'+mode+'.csv'))
    m=module('lod5_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
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
    v=module('lod5_vec',HERE/'phase7_validate.py');vectors=[]
    parser=OUT/'vector16.py'
    if not parser.exists():parser.write_text((HERE/'phase6_validate.py').read_text().replace('address + count <= 0x200000','address + count <= 0x1000000'))
    v.v=module('lod5_vec16',parser)
    for p in sorted((before/'gpuvec').glob('*.vec')):
        target=after/'gpuvec'/p.name;old,new=(v.v.vector(x.read_bytes())for x in(p,target))
        assert render_events(old)==render_events(new),(p.name,'render event mismatch')
        assert all(old[k]==new[k]for k in('depth','fence')),p.name
        # Prefix equals the old texture arena; appended high CRAM bytes are source storage.
        diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex())for i in range(0,len(old['texture']),4)if old['texture'][i:i+4]!=new['texture'][i:i+4]]
        vectors.append(dict(frame=int(p.stem.split('_')[1]),texture_changes=diffs,baseline_sha256=sha(p),candidate_sha256=sha(target)))
    assert len(vectors)==8
    stats=list(csv.DictReader((OUT/('pool-stats-'+mode+'.csv')).open()))
    write(OUT/f'result-{mode}.json',dict(passed=True,functional_only=True,mode=mode,all721_scene_geometry_culling_exact=exact,all721_wordcounts_exact=True,all8_render_streams_exact_except_source_pool_uploads=True,padding_check=v.padding(vectors,before,after,CAND/'SM64'),vectors=vectors,model=json.loads((after/'lod5-model.json').read_text()),driver_stats=list(struct.unpack('<16I',(after/'lod5-driver-stats.bin').read_bytes())),pool_stats_last=stats[-1],simulator_sha256=sha(OUT/'functional-qsim/qsim'),elf_sha256=sha(OUT/'app.elf'),qualified_for_deployment=False))
    print(mode,stats[-1])
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);p.add_argument('--mode',default='normal',choices=['baseline','normal','absent','faults','timeout','fullpool']);a=p.parse_args();globals()[a.action]()if a.action=='prepare'else globals()[a.action](a.mode)
