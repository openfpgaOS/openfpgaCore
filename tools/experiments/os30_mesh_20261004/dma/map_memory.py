#!/usr/bin/env python3
"""Freeze a standalone mesh engine and synthesize its actual RAM inference."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    sources=[*(HERE.parent/'engine').glob('*.v'),*(HERE.parent/'kernel').glob('gpu_mesh*.v'),*HERE.glob('gpu_mesh*.v')]
    for source in sources:shutil.copy2(source,out/source.name)
    (out/'manifest.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},indent=2)+'\n')
    (out/'ap_core.qpf').write_text('PROJECT_REVISION = "ap_core"\n')
    (out/'ap_core.qsf').write_text('''set_global_assignment -name FAMILY "Cyclone V"
set_global_assignment -name DEVICE 5CEBA4F23C8
set_global_assignment -name TOP_LEVEL_ENTITY gpu_mesh_engine
set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files
set_global_assignment -name NUM_PARALLEL_PROCESSORS 2
set_global_assignment -name OPTIMIZATION_MODE "AGGRESSIVE AREA"
set_instance_assignment -name VIRTUAL_PIN ON -to *
'''+''.join(f'set_global_assignment -name VERILOG_FILE "{out/p.name}"\n' for p in sources))
    command=[str(ROOT/'tools/quartus-container.sh'),str(out),'quartus_map','ap_core']
    with (out/'run.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    report=(out/'output_files/ap_core.map.rpt').read_text(errors='replace')
    tables=[line for line in report.splitlines() if 'M10K block ;' in line]
    (out/'memory-inference.txt').write_text('\n'.join(tables)+'\n')
    print('\n'.join(tables))
if __name__=='__main__':main()
