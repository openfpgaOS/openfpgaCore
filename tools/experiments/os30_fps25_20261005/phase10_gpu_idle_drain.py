#!/usr/bin/env python3
"""Private one-predicate GPU idle fix: same pixel/barrier oracles, two cores."""
from pathlib import Path
import concurrent.futures,hashlib,json,re,shutil,subprocess
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/gpu-idle-drain-v1'
TEST=OUT/'tests-v4'
F=TEST/'frozen'
META=ROOT/'docs/measurements/os30-memory-opt-20261002/gpu-selected/os30/manifest.json'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    F.mkdir(parents=True,exist_ok=False)
    for p in [ROOT/'src/fpga/test/tb_gpu.v',ROOT/'src/fpga/test/tb_gpu_acceptance_main.cpp',
              *sorted((ROOT/'src/fpga/test').glob('*.h')),
              *[ROOT/'src/fpga/common'/n for n in ('gpu_edge_walker.v','gpu_tex_cache.v','gpu_color_depth_cache.sv')],
              HERE/'phase10_gpu_idle_drain.cpp']:
        shutil.copyfile(p,F/p.name)
    p=F/'tb_gpu.v';s=p.read_text();a='    // Status outputs';assert s.count(a)==1
    s=s.replace(a,'''    output wire obs_tail_hole,obs_premature_flush,obs_idle_flush,
    // Status outputs''')
    a='wire        gpu_busy_core;';assert s.count(a)==1
    s=s.replace(a,a+'''
assign obs_tail_hole=!busy && !gpu.fb_write_drain_complete;
assign obs_premature_flush=!gpu_busy_core && gpu_cache_has_lines
    && !gpu.fb_write_drain_complete && !gpu_cache_idle_flush;
assign obs_idle_flush=gpu_cache_idle_flush && gpu_render_cache.state==gpu_render_cache.IDLE;
''');p.write_text(s)
    p=F/'tb_gpu_acceptance_main.cpp';s=p.read_text()
    a='static void tick(int n = 1) {';assert s.count(a)==1
    s=s.replace(a,'''static uint64_t observed_busy_holes,observed_premature_flushes,observed_idle_flushes;
static void tick(int n = 1) {''')
    a='        tb->clk = 1;';assert s.count(a)==1
    s=s.replace(a,'''        if(tb->reset_n) {
            observed_busy_holes+=tb->obs_tail_hole;
            observed_premature_flushes+=tb->obs_premature_flush;
            observed_idle_flushes+=tb->obs_idle_flush;
        }
        tb->clk = 1;''');p.write_text(s)
    m=json.loads(META.read_text());(TEST/'parameters.json').write_text(json.dumps(m,indent=2)+'\n')
    (TEST/'sources.json').write_text(json.dumps({p.name:sha(p) for p in F.iterdir()},indent=2)+'\n')
    def run(label):
        dest=TEST/label;dest.mkdir();core=OUT/('baseline-gpu_core.v' if label=='baseline' else 'gpu_core.v')
        cmd=['verilator','--cc','--exe','--build','--trace','-j','2','-Wno-fatal','-Wno-BADVLTPRAGMA',
             '--top-module','tb_gpu','--Mdir',str(dest/'obj'),'-I'+str(F),
             '-CFLAGS',' '.join(m['cflags']),*["+define+"+d for d in m['variant']['defs'].split()],
             *m['flags'],str(F/'tb_gpu.v'),str(core),
             *[str(F/n) for n in ('gpu_edge_walker.v','gpu_tex_cache.v','gpu_color_depth_cache.sv','phase10_gpu_idle_drain.cpp')]]
        with (dest/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
        results={}
        for name,args in [('quiet',[]),('delayed',['+gpu_rd_latency=24','+gpu_rd_latency_var=1','+gpu_wr_latency=97','+gpu_rd_beat_gap=7'])]:
            with (dest/(name+'.log')).open('w') as f:subprocess.run([str(dest/'obj/Vtb_gpu'),*args],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=300)
            text=(dest/(name+'.log')).read_text();passed=re.search(r'Acceptance Results: (\d+) passed, (\d+) failed',text);assert passed and int(passed[2])==0
            r=re.search(r'DRAIN counters tail_holes=(\d+) premature_flush=(\d+) idle_flush=(\d+)',text);assert r
            o=re.search(r'OWNERSHIP first_idle_wrong_bytes=(\d+)',text);assert o
            tri=re.search(r'TRIANGLE first_idle_changed_after_fence=(\d+) pixels=(\d+)',text);assert tri
            results[name]={'passed':int(passed[1]),'tail_holes':int(r[1]),'premature_flush':int(r[2]),'idle_flush':int(r[3]),'first_idle_wrong_bytes':int(o[1]),'triangle_first_idle_changed_after_fence':int(tri[1]),'triangle_pixels':int(tri[2])}
        return label,results
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=dict(pool.map(run,['baseline','candidate']))
    assert all(r['tail_holes']==r['premature_flush']==r['first_idle_wrong_bytes']==r['triangle_first_idle_changed_after_fence']==0 for r in results['candidate'].values()),results
    assert any(r['tail_holes'] or r['premature_flush'] or r['first_idle_wrong_bytes'] for r in results['baseline'].values()),results
    (TEST/'result.json').write_text(json.dumps({'passed':True,'private_only':True,'runs':results,'candidate_sha256':sha(OUT/'gpu_core.v'),'baseline_sha256':sha(OUT/'baseline-gpu_core.v'),'limits':'Synthetic SDRAM latency and targeted ownership/barrier oracles; no game performance claim.'},indent=2)+'\n')
    print(json.dumps(results,indent=2))
if __name__=='__main__':main()
