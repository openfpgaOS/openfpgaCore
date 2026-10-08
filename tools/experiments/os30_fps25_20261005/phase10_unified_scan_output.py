#!/usr/bin/env python3
"""Private direct accumulator read for clip SCAN; no new state or clock cycle."""
import hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'unified-load-components-v2';CORE=P/'compact-lod5-core-v2/frozen/src/fpga/common'
OUT=P/'unified-load-components-v3'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
    OUT.mkdir(exist_ok=False);f=OUT/'frozen';shutil.copytree(BASE/'frozen',f)
    bound={}
    for p in f.glob('*.v'):
        parent=CORE/p.name
        if parent.exists():
            assert sha(parent)==sha(p),(p.name,'selected compact-v2 differs from native parent')
            bound[p.name]=sha(parent)
    p=f/'geometry_transform_light.v';s=p.read_text()
    s=rep(s,'output wire [31:0] output_data,output wire [23:0] output_rgb,','output wire [31:0] output_data,output wire [31:0] output_position_data,output wire [23:0] output_rgb,')
    s=rep(s,'assign output_data=output_inverse?inv_q:acc_q;','''assign output_data=output_inverse?inv_q:acc_q;
 // Raw clip SCAN only reads XYZW, so expose the same synchronous RAM result
 // without the idle inverse-view mux. No new port on the underlying RAM.
 assign output_position_data=acc_q;''');p.write_text(s)
    p=f/'geometry_raw_load.v';s=p.read_text()
    s=rep(s,'wire [31:0] compute_data;','wire [31:0] compute_data,compute_position_data;')
    s=rep(s,'.output_addr(compute_addr),.output_data(compute_data),.output_rgb(compute_rgb),','.output_addr(compute_addr),.output_data(compute_data),.output_position_data(compute_position_data),.output_rgb(compute_rgb),')
    s=rep(s,'wire [1:0] clip_pair={float_lt(compute_data,neg_w),float_lt(clip_w,compute_data)};','wire [1:0] clip_pair={float_lt(compute_position_data,neg_w),float_lt(clip_w,compute_position_data)};')
    s=rep(s,'0:clip_w<=compute_data;','0:clip_w<=compute_position_data;')
    s=rep(s,'endmodule','''`ifdef GEOMETRY_SCAN_CHECKS
 always @(posedge clk) if(!reset && state==SCAN && read_valid) begin
  assert(!compute_busy);
  assert(compute_position_data==compute_data);
 end
`endif
endmodule''');p.write_text(s)
    save(OUT/'sources.json',{p.name:sha(p) for p in sorted(f.iterdir())})
    save(OUT/'prepared.json',dict(selected_compact_sources_sha256=bound,parent_manifest_sha256=sha(BASE/'manifest.json'),
        only_changed_rtl=['geometry_transform_light.v','geometry_raw_load.v'],added_state_bits=0,added_cycles=0,
        source_id='0x4c4f4405',ABI_unchanged=True,no_map=True,no_fit=True))
    shutil.copy2(__file__,OUT/'recipe.py')
def wrapper():
    f=OUT/'frozen';d=OUT/'wrapper';d.mkdir(exist_ok=False)
    cmd=['verilator','--cc','--exe','--build','--assert','+define+GEOMETRY_SCAN_CHECKS','-j','4','-Wno-fatal','-Wno-WIDTH','--top-module','tb_load_alias','--Mdir',str(d/'obj'),'-CFLAGS','-O2 -std=c++17']+[str(x) for x in sorted(f.glob('*.v'))]+[str(f/'phase10_load_alias_test.cpp')]
    save(d/'build-command.json',cmd)
    with (d/'build.log').open('x') as o:subprocess.run(cmd,stdout=o,stderr=subprocess.STDOUT,check=True)
    with (d/'run.log').open('x') as o:subprocess.run([str(d/'obj/Vtb_load_alias'),str(f/'native.bin')],stdout=o,stderr=subprocess.STDOUT,check=True)
    result=json.loads((d/'run.log').read_text().splitlines()[-1]);assert result['passed']
    old=json.loads((BASE/'wrapper/run.log').read_text().splitlines()[-1]);assert result==old
    save(d/'result.json',dict(**result,cycle_and_output_counts_equal_v2=True,assert_unmuxed_scan_equivalent=True))
    print(json.dumps(result,indent=2))
def arithmetic():
    s=(BASE/'arithmetic/recipe.py').read_text().replace("D=P/'unified-load-components-v2'","D=P/'unified-load-components-v3'")
    s=s.replace("T=R/'tools/experiments/os30_fps25_20261005/phase10_float_batch'","T=P/'unified-load-components-v2/arithmetic/frozen'")
    s=s.replace("shutil.copy2(P/'transform-light/v2/native.bin'", "p.write_text(p.read_text().replace(' wire fma_request_valid',' wire [31:0] output_position_data;\\n wire fma_request_valid'))\nshutil.copy2(P/'transform-light/v2/native.bin'")
    p=OUT/'arithmetic-runner.py';p.write_text(s);subprocess.run([sys.executable,str(p)],check=True)
    assert json.loads((OUT/'arithmetic/result.json').read_text())==json.loads((BASE/'arithmetic/result.json').read_text())
def latency():
    s=(ROOT/'tools/experiments/os30_fps25_20261005/phase10_unified_latency_bram.py').read_text().replace("OUT=P/'unified-load-latency-v2'","OUT=P/'unified-load-latency-v3'").replace("SRC=P/'unified-load-components-v2/frozen'","SRC=P/'unified-load-components-v3/frozen'")
    s=s.replace("ROOT=Path(__file__).resolve().parents[3]", "ROOT=Path('/home/alberto/Repos/openfpgaCore')")
    s=s.replace("cmd=['verilator','--cc'","cmd=['verilator','--assert','+define+GEOMETRY_SCAN_CHECKS','--cc'")
    p=OUT/'latency-runner.py';p.write_text(s);subprocess.run([sys.executable,str(p)],check=True)
    assert (P/'unified-load-latency-v3/latency.csv').read_bytes()==(P/'unified-load-latency-v2/latency.csv').read_bytes()
if __name__=='__main__':
    assert sys.argv[1] in ('prepare','wrapper','arithmetic','latency');globals()[sys.argv[1]]()
