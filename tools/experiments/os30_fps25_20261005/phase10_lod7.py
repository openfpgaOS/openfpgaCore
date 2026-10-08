#!/usr/bin/env python3
"""Private compact XYZ LOD7, position-RAM clip reuse, native proof and map only."""
import argparse,difflib,hashlib,json,shutil,subprocess
from pathlib import Path
import phase10_lod6 as base
ROOT=base.ROOT;P=base.P;COMP=P/'lod7-components-v1';CORE=P/'lod7-transform-core-v1'
BASE=P/'lod6-transform-core-v1';sha=base.sha;save=base.save;rep=base.rep
def prepare():
 old=P/'lod6-components-v1';sources=json.loads((old/'sources.json').read_text())
 for n,h in sources.items():assert sha(old/'frozen'/n)==h,n
 f=COMP/'frozen';shutil.copytree(old/'frozen',f)
 p=f/'phase10_load_alias.v';s=p.read_text().replace('LOD6','LOD7').replace("32'h4c4f4406","32'h4c4f4407")
 s=rep(s,'// Lighting registers are ignored; RGBA always preserves source bytes.','// Packed12-byte XYZ source only; lighting/scale registers are ignored.\n// Rows5/6 returnzero; CPU owns all UV and RGBA calculation.')
 s=rep(s,'reg [15:0] scale_u,scale_v,matrix_valid;','reg [15:0] matrix_valid;')
 s=rep(s,'.scale_u(scale_u),.scale_v(scale_v)',".scale_u(16'd0),.scale_v(16'd0)")
 s=rep(s,'scale_u<=0;scale_v<=0;','')
 s=rep(s,"     10'h00b:begin scale_u<=reg_wdata[15:0];scale_v<=reg_wdata[31:16];end\n",'')
 p.write_text(s)
 p=f/'geometry_raw_load.v';s=p.read_text();header=s[s.index('module geometry_raw_load'):s.index('\n localparam')]
 body=Path(__file__).with_name('phase10_xyz_load_body.vh').read_text()
 p.write_text('// Private LOD7: compact XYZ source and dead-position-RAM clip storage.\n'+header+'\n'+body)
 p=f/'phase10_load_alias_test.cpp';s=p.read_text().replace('0x4c4f4406','0x4c4f4407')
 s=rep(s,'for(unsigned k=0;k<6;k++)mem[j.h[1]+24*v+4*k]', 'for(unsigned k=0;k<3;k++)mem[j.h[1]+12*v+4*k]')
 s=rep(s,'j.d[v*15+12]=(j.d[v*15+4]>>16)|((j.d[v*15+5]&0xffffu)<<16);','j.d[v*15+11]=0;j.d[v*15+12]=0;')
 s=s.replace('input RGBA, even for fixtures whose original native CPU lit the vertex.','zero UV/RGBA rows; native geometry and clip remain unchanged.')
 s=s.replace('0x28u,0x30u','0x28u,0x2cu,0x30u')
 s=s.replace('0x00ffffe8u','0x00fffff4u').replace('0x00ffffe9u,0x00ffffecu','0x00fffff5u,0x00fffff8u')
 s=rep(s,' // Keep old output burst stalled while the next complete job is requested.', ''' // Every legal count at the exact aperture end and straddling a die/4K
 // boundary exercises variable-word burst tails, including odd word counts.
 for(unsigned n=1;n<=32;n++){
  Job j=jobs[0];j.h[0]=n;j.d.clear();
  for(unsigned v=0;v<n;v++){unsigned k=v%jobs[0].h[0];j.d.insert(j.d.end(),jobs[0].d.begin()+15*k,jobs[0].d.begin()+15*(k+1));}
  for(auto address:{0x01000000u-12*n,0x007ffffcu,0x00fc0ffcu}){
   j.h[1]=address;configure(j);put(8,1);done();check(j);
  }
  configure(j);unsigned oldbursts=dma_bursts;put(0xc,0x01000004u-12*n);put(8,1);done();
  need((reg(4)&0x60)==0x20&&!(reg(4)&4),"count-specific aperture overflow");need(dma_bursts==oldbursts,"overflow emitted source request");
 }
 // Keep old output burst stalled while the next complete job is requested.''')
 s=s.replace('raw_RGBA_exact\\\":230','zero_attribute_vertices\\\":230')
 p.write_text(s)
 shutil.copy2(__file__,COMP/'recipe.py');shutil.copy2(Path(__file__).with_name('phase10_xyz_load_body.vh'),COMP/'body.vh')
 save(COMP/'sources.json',{x.name:sha(x)for x in sorted(f.iterdir())})
 save(COMP/'abi.json',dict(id='0x4c4f4407',source='packed12B XYZ float bits, CRAM1 byte offset, count1..32, full12*count extent checked',
  result_alias='same32B stride at0x13f80000: rows0..4XYZW/inverse,row7clip,rows5/6zero',
  retired_offsets=['0x18','0x1c','0x20','0x24','0x28','0x2c','0x30'],
  software='CPU computes and preserves UV/RGBA for every job',reset_and_ownership='unchanged LOD5/LOD6',private_only=True))
 print(COMP)
def test():
 f=COMP/'frozen';d=COMP/'wrapper';d.mkdir(exist_ok=False)
 cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','-Wno-WIDTH','--top-module','tb_load_alias','--Mdir',str(d/'obj'),'-CFLAGS','-O2 -std=c++17']+[str(x)for x in sorted(f.glob('*.v'))]+[str(f/'phase10_load_alias_test.cpp')]
 save(d/'build-command.json',cmd)
 with(d/'build.log').open('x')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 with(d/'run.log').open('x')as log:subprocess.run([str(d/'obj/Vtb_load_alias'),str(f/'native.bin')],stdout=log,stderr=subprocess.STDOUT,check=True)
 result=json.loads((d/'run.log').read_text().splitlines()[-1]);assert result['passed'] and result['zero_attribute_vertices']==230
 result.update(source_id='0x4c4f4407',compact12B=True,count_specific_valid_end_and_die_4k_cases=96,count_specific_rejected_extent_cases=32,
  retired_scale_and_lighting_registers_tested=True,source_manifest_sha256=sha(COMP/'sources.json'))
 save(d/'result.json',result);print(json.dumps(result,indent=2))
def stage():
 assert json.loads((COMP/'wrapper/result.json').read_text())['passed']
 original=json.loads((BASE/'prepared.json').read_text())['source_sha256']
 for n,h in original.items():assert sha(BASE/'frozen'/n)==h,n
 CORE.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',CORE/'frozen')
 changes=['src/fpga/common/geometry_raw_load.v','src/fpga/common/phase10_load_alias.v']
 for n in changes:shutil.copy2(COMP/'frozen'/Path(n).name,CORE/'frozen'/n)
 actual={str(x.relative_to(CORE/'frozen')):sha(x)for x in sorted((CORE/'frozen').rglob('*'))if x.is_file()}
 assert[n for n,h in actual.items()if original.get(n)!=h]==changes
 save(CORE/'prepared.json',dict(private_only=True,source_sha256=actual,only_changed=changes,parent_prepared_sha256=sha(BASE/'prepared.json'),
  component_test_sha256=sha(COMP/'wrapper/result.json'),cpu_cache_kib=128,configured_mhz=100,seed=2,seed_sweep=False,fitted=False,deployed=False))
 patch=[]
 for n in changes:patch+=difflib.unified_diff((BASE/'frozen'/n).read_text().splitlines(True),(CORE/'frozen'/n).read_text().splitlines(True),fromfile='LOD6/'+n,tofile='LOD7/'+n)
 (CORE/'integration.patch').write_text(''.join(patch));shutil.copy2(__file__,CORE/'recipe.py');print(CORE)
def map_only():
 base.CORE=CORE;base.BASE=P/'lod5-cram-span-core-v2';base.map_only()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','test','stage','map_only']);globals()[p.parse_args().action]()
