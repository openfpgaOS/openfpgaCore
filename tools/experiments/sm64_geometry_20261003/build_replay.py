"""Replay real SM64 command captures on os30's actual 100 MHz memory path.
No CPU execution/pacing/audio-mixer model: cycles are GPU workload time, not FPS.
Textures use SDRAM. Idle boundaries flush the render cache like tb_gpu; CPU
submission overlap is not modeled. Identical full-memory output is required.
"""
import argparse,hashlib,importlib.util,json,shutil,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
HERE=ROOT/'docs/measurements/sm64-geometry-20261003'
sys.path.insert(0,str(ROOT/'tools'))
from check_os30_memory import prepare
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('label');p.add_argument('--common',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True)
a=p.parse_args();out=HERE/a.label;out.mkdir(exist_ok=False)
manifest=json.loads(a.manifest.read_text())
assert manifest['variant']['variant']=='os30'
defs=set(manifest['variant']['defs'].split())
assert 'INCLUDE_GPU_WQ16' in defs
assert not defs & {'INCLUDE_CLK90','INCLUDE_CLK96','INCLUDE_OS30_SDRAM166'}
f,memory=prepare(out,manifest,a.common.resolve(),16)
top=f/'tb_gpu.v';s=top.read_text().replace("{10'd0,gpu_rd_araddr[21:0]}","{6'd0,gpu_rd_araddr[25:0]}").replace("{10'd0,gpu_wr_awaddr[21:0]}","{6'd0,gpu_wr_awaddr[25:0]}")
# GPU counters are simulator-only, including all command/setup/flush work.
spec=importlib.util.spec_from_file_location('bn',ROOT/'tools/experiments/sm64_bottleneck_20260929/build.py')
bn=importlib.util.module_from_spec(spec);spec.loader.exec_module(bn)
counters=bn.COUNTERS.replace('render_cache.state','gpu_render_cache.state').replace('cache_hits, cache_misses, cache_writebacks','0, 0, 0')
counters=counters.replace('always @(posedge clk) if (reset_n)', 'always @(posedge clk) if (reset_n && busy)')
pos=s.rindex('endmodule');s=s[:pos]+counters+s[pos:];top.write_text(s)
cpp=(ROOT/'tools/tests/gpu_qsim_replay.cpp').read_text()
cpp=cpp.replace('static inline uint32_t &sdram_word(uint32_t widx)', 'static inline uint32_t sdram_word(uint32_t widx)')
cpp=cpp.replace('return tb->rootp->tb_gpu__DOT__sdram_mem[widx & 0xFFFFFF];', '''uint32_t value;
    memcpy(&value, &tb->rootp->tb_gpu__DOT__memory__DOT__sdram_chip__DOT__mem[(widx & 0xFFFFFF)*2], sizeof value);
    return value;''')
cpp=cpp.replace('''    uint32_t &w = sdram_word(a >> 2);
    int sh = (a & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh);''', '''    auto &half = tb->rootp->tb_gpu__DOT__memory__DOT__sdram_chip__DOT__mem[a >> 1];
    unsigned sh = (a & 1) * 8;
    half = (half & ~(0xffu << sh)) | (uint16_t(v) << sh);''')
cpp=cpp.replace('for (uint32_t i = 0; i < 0x1000000; i++) sdram_word(i) = 0;',
    'for (uint32_t i = 0; i < 0x2000000; i++) tb->rootp->tb_gpu__DOT__memory__DOT__sdram_chip__DOT__mem[i] = 0;')
cpp=cpp.replace('static void hard_reset() {','static void hard_reset() {\n    delete tb; tb = new Vtb_gpu;')
cpp=cpp.replace('    tick(5);','    tick(30000);')
cpp=cpp.replace('    return rc;\n}', '    tb->final();\n    return rc;\n}',1)
cpp=cpp.replace('    tb->final();\n    delete tb;','    delete tb;')
(f/'replay.cpp').write_text(cpp)
shutil.copy2(ROOT.parent/'openfpgaSDK/tools/qsim/gpuvec.h',f/'gpuvec.h')
sources=[f/x for x in ['tb_gpu.v','tb_sdram_cont.v','io_sdram_pocket_test.v','sdram_model_full.v','pocket_sdram_timing_checker.sv','altsyncram_stub.v','synch_3.v','replay.cpp']]
sources += [f/'common'/x for x in ['gpu_core.v','gpu_edge_walker.v','gpu_tex_cache.v','gpu_color_depth_cache.sv','axi_sdram_arbiter.v','axi_sdram_slave.v','sync_fifo.v']]
cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_gpu','--Mdir',str(out/'obj'),'-I'+str(f/'common'),'-CFLAGS','-std=c++17 -O2',*['+define+'+d for d in manifest['variant']['defs'].split()],*manifest['flags'],*map(str,sources)]
(out/'manifest.json').write_text(json.dumps({'scope':__doc__,'gpu':manifest,'sdram':memory,'command':cmd,'sources':{str(x.relative_to(out)):hashlib.sha256(x.read_bytes()).hexdigest() for x in f.rglob('*') if x.is_file()}},indent=2)+'\n')
with (out/'build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
print(out/'obj/Vtb_gpu',flush=True)
