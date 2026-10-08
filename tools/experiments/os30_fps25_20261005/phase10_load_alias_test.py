#!/usr/bin/env python3
"""Freeze and test the private LOD4 raw-BRAM cacheable view."""
from pathlib import Path
import argparse,hashlib,json,re,shutil,subprocess
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
p=argparse.ArgumentParser();p.add_argument('name');a=p.parse_args()
assert Path(a.name).name==a.name
OUT=BASE/a.name;OUT.mkdir(exist_ok=False)
sources={n:HERE/n for n in ['phase10_load_alias.v','phase10_bram_alias.v','phase10_load_alias_test.cpp','phase10_float_recip2.v','phase10_raw_vtx_dma.v']}
sources['GeometryFmaProbe.v']=BASE/'fpu-feasibility/rtl/GeometryFmaProbe.v'
sources['geometry_lighting_batch.v']=HERE/'phase10_lighting/geometry_lighting_batch_pipelined_shared.v'
for n in ['geometry_color_rtz.v','geometry_float_batch_recip.v','geometry_transform_light.v','geometry_raw_load.v']:sources[n]=HERE/'phase10_float_batch'/n
sources['native.bin']=BASE/'raw-load-v1/native.bin'
for n,s in sources.items():shutil.copyfile(s,OUT/n)
header=(OUT/'phase10_bram_alias.v').read_text().split(')(\n',1)[1].split('\n);',1)[0]
ports=header.split(' output wire [7:0] view_read_addr')[0].rstrip().rstrip(',')
names=re.findall(r'\b(?:input|output) wire(?: \[[^]]+\])? (\w+)',ports)
names[1:1]=['reset','cpu_reset']
extra='''input wire reg_wr,input wire[9:0]reg_addr,input wire[31:0]reg_wdata,input wire[3:0]reg_wstrb,output wire[31:0]reg_rdata,
 output wire dma_arvalid,input wire dma_arready,output wire[31:0]dma_araddr,output wire[7:0]dma_arlen,
 input wire dma_rvalid,output wire dma_rready,input wire[31:0]dma_rdata,input wire[1:0]dma_rresp,input wire dma_rlast,
 output wire obs_active,obs_block'''
tb='module tb_load_alias(\n'+ports+',\n '+extra+');\n'
tb+='wire[7:0]view_read_addr;wire[31:0]view_read_data;wire view_active,view_block,view_valid;wire[5:0]view_count;\n'
tb+='assign obs_active=view_active;assign obs_block=view_block;\n'
tb+='geometry_bram_alias alias_view('+','.join('.'+n+'('+n+')' for n in names)+',\n'
tb+=','.join('.'+n+'('+n+')' for n in ['view_read_addr','view_read_data','view_active','view_block','view_valid','view_count'])+');\n'
tb+='geometry_load_alias load_view('+','.join('.'+n+'('+n+')' for n in ['clk','reset','reg_wr','reg_addr','reg_wdata','reg_wstrb','reg_rdata','view_read_addr','view_read_data','view_active','view_block','view_valid','view_count'])+',\n'
tb+=','.join('.m_'+n+'(dma_'+n+')' for n in ['arvalid','arready','araddr','arlen','rvalid','rready','rdata','rresp','rlast'])+');\nendmodule\n'
(OUT/'tb_load_alias.v').write_text(tb)
manifest={n:{'source':str(s.relative_to(ROOT)),'sha256':hashlib.sha256((OUT/n).read_bytes()).hexdigest()} for n,s in sources.items()}
manifest['tb_load_alias.v']={'source':'generated','sha256':hashlib.sha256(tb.encode()).hexdigest()}
(OUT/'sources.json').write_text(json.dumps(manifest,indent=2)+'\n')
cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','tb_load_alias','--Mdir','obj','-CFLAGS','-O2 -std=c++17']+[n for n in manifest if n.endswith('.v')]+['phase10_load_alias_test.cpp']
with (OUT/'build.log').open('w') as log:subprocess.run(cmd,cwd=OUT,stdout=log,stderr=subprocess.STDOUT,check=True)
with (OUT/'run.log').open('w') as log:subprocess.run(['./obj/Vtb_load_alias','native.bin'],cwd=OUT,stdout=log,stderr=subprocess.STDOUT,check=True)
result=json.loads((OUT/'run.log').read_text().splitlines()[-1]);(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
