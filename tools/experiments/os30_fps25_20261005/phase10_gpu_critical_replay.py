#!/usr/bin/env python3
"""Maximal-rate F11 command supply; real GPU/memory and required upload barriers."""
import concurrent.futures,importlib.util,json,os,re,shutil,subprocess,sys
from pathlib import Path
import phase10_gpu_critical as c
ROOT=c.ROOT;P=c.P;OUT=c.OUT;HERE=c.HERE;sha=c.sha;save=c.save;rep=c.rep
def capture():
    d=OUT/'capture-native';d.mkdir(exist_ok=False)
    exe=P/'gpu-direct-native-functional-v1/functional-qsim/qsim';elf=P.parent/'phase9/checks-state-f11/app.elf'
    app=ROOT/'docs/measurements/sm64-bob-20261002/sm64.app'
    assets=ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'
    cmd=[str(exe),str(elf),'--app',str(app),'--data-dir',str(assets),'--out',str(d),'--frames','368','--profile','--gpuvec',','.join(map(str,range(360,368))),'--gpu-sched','eager','--max-insns','5000000000']
    env=os.environ.copy();env.update(SM64_GEOMETRY_CAP='0',SM64_AUDIO='1',GDR1_MODEL_MODE='absent')
    for key in ['SM64_COMMAND_DMA','SM64_PERF_PANEL','SM64_WORKER_CAP','SM64_ATTRIBUTES_CAP','GDR1_STATS_ADDR']:env.pop(key,None)
    save(d/'inputs.json',dict(command=cmd,source_elf_sha256=sha(elf),simulator_sha256=sha(exe),scope='Validated native-FMA software capture; GPU cached-vertex commands are captured for separate RTL replay.'))
    with (d/'run.log').open('x') as f:subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    assert 'FATAL' not in (d/'run.log').read_text()
def prepare():
    old=P/'vcache-narrow-v1/model';d=OUT/'replay/model';shutil.copytree(old/'frozen',d/'frozen')
    p=d/'frozen/tb_gpu.v';s=p.read_text();s=rep(s,'    input  wire        clk,','    input wire critical_enable,\n    input  wire        clk,');s=rep(s,'endmodule',c.PROBE+'\nendmodule');p.write_text(s)
    p=d/'frozen/replay.cpp';s=p.read_text()
    s=rep(s,'    tb->reset_n = 0; tb->reg_wr = 0;', '    tb->critical_enable=0;\n    tb->reset_n = 0; tb->reg_wr = 0;')
    s=rep(s,'    obs_enabled = true;','    obs_enabled = true;tb->critical_enable=1;')
    s=rep(s,'            for (uint32_t i = 0; i < n; i++) mmio_write(1, rd32());','''            uint64_t reserve_start=cycles;
            while((4095u-(((mmio_read(1)-mmio_read(4))&16383u)/4u))<n) {
                if(cycles-reserve_start>50000000ull)throw std::runtime_error("ring reserve timeout");
                tick();
            }
            for (uint32_t i = 0; i < n; i++) mmio_write(1, rd32());''')
    a='''            if (!wait_idle(50000000ull)) {
                fprintf(stderr, "%s: batch %u hang (state=%u aux=%08x frag=%08x)\\n",
                        vec_path, batches, tb->dbg_state, tb->dbg_aux, tb->dbg_frag);
                rc = 4; break;
            }'''
    s=rep(s,a,'            // Maximal-rate producer: the next batch may overlap this one.')
    s=rep(s,'        } else if (tag == GV_TAG_DUMP) {','''        } else if (tag == GV_TAG_DUMP) {
            if(!wait_idle(50000000ull))throw std::runtime_error("final drain timeout");''')
    p.write_text(s)
    cmd=[x.replace(str(old),str(d)) for x in json.loads((old/'build.json').read_text())['command']]
    save(d/'inputs.json',dict(scope=__doc__,sources={str(f.relative_to(d)):sha(f) for f in d.rglob('*') if f.is_file()},recipe_sha256=sha(Path(__file__))))
    save(d/'command.json',cmd)
def build():
    d=OUT/'replay/model';cmd=json.loads((d/'command.json').read_text())
    with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    save(d/'build.json',dict(command=cmd,simulator_sha256=sha(d/'obj/Vtb_gpu')))
def run():
    d=OUT/'replay';exe=d/'model/obj/Vtb_gpu'
    path=HERE/'phase4_combined_validate.py';spec=importlib.util.spec_from_file_location('depth_adapter',path);a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
    jobs=[]
    for frame in range(360,368):
        stem=d/f'frame_{frame:05}';vec=stem.with_suffix('.vec');_,depth,_=a.with_depth(OUT/f'capture-native/gpuvec/frame_{frame:05}.vec',vec);assert depth
        jobs.append((stem,[str(exe),'+memory_traffic=1','+fast_tex=1','+tagged_tex=1',str(vec),str(stem.with_suffix('.bin'))]))
    def one(job):
        stem,cmd=job
        with stem.with_suffix('.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
        assert 'FATAL' not in stem.with_suffix('.log').read_text()
        return dict(frame=int(stem.stem.split('_')[1]),command=cmd,stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',stem.with_suffix('.log').read_text())},output_sha256=sha(stem.with_suffix('.bin')),log_sha256=sha(stem.with_suffix('.log')),input_sha256=sha(stem.with_suffix('.vec')))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,jobs))
    save(d/'result.json',dict(scope=__doc__,runs=results,simulator_sha256=sha(exe)))
if __name__=='__main__':
    for action in sys.argv[1:]:globals()[action]()
