#!/usr/bin/env python3
"""One map-only full-core screen of CPU queues; no fit or sweep."""
import copy,hashlib,json,shutil,subprocess,sys
from phase10_cpu_queue_adapter import adapt
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cpu-queues-v1';BASE=P/'gpu-direct-core-v1';DEST=OUT/'map-gpu-direct';CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
DEST.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',DEST/'frozen')
original=json.loads((BASE/'prepared.json').read_text())['source_sha256']
for p,h in original.items():assert sha(DEST/'frozen'/p)==h,p
cpu=OUT/'VexiiRiscv.v';assert sha(cpu)==json.loads((OUT/'generated.json').read_text())['rtl_sha256']
shutil.copy2(cpu,DEST/'frozen'/CPU)
wrapper=DEST/'frozen/src/fpga/common/cpu_system.v';wrapper.write_text(adapt(wrapper.read_text()))
sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
row=copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=sha(cpu)
directory,project=fit.prepare(DEST/'frozen',DEST/'job',row,2,4)
cmd=fit.docker(DEST,'pocket',directory,['quartus_map',project])
with (DEST/'map.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
report=directory/'output_files/ap_core.map.rpt';text=report.read_text(errors='replace');entities=[]
for line in text.split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
 r=[x.strip() for x in line.split(';')][1:-1]
 if len(r)>=10 and r[-2] in ['VexiiRiscv','cpu_system','apf_top','geometry_direct_mmio']:
  entities.append(dict(entity=r[-2],aluts=r[1],registers=r[2],memory_bits=r[3],dsp=r[4],path=r[-3]))
result=dict(passed_map=True,scope='Map-only architecture cost screen; no placement/timing/performance qualification',command=cmd,cpu_sha256=sha(cpu),only_changed_from_gpu_direct_v1=[CPU,'src/fpga/common/cpu_system.v'],source_sha256={str(p.relative_to(DEST/'frozen')):sha(p) for p in (DEST/'frozen').rglob('*') if p.is_file()},entities=entities,map_report_sha256=sha(report),recipe_sha256=sha(Path(__file__)))
(DEST/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(entities,indent=2))
