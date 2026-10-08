#!/usr/bin/env python3
"""Retained CPU fixtures for the new decoded buffer, with independent observations."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];ART=ROOT/'docs/measurements/os30-dual-20261004';OUT=ART/'validation'
LABEL='rvc_skid_immediate_second_decode';REF='rvc_skid_immediate_pingpong_pointer_packer'
CPU=ART/'decode-second-skid-draft-v1/immediate/VexiiRiscv.v'
SHA='f703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5'
MAP=OUT/'skid-pingpong-second-decode-map.json';PAYLOAD=CPU.parent/'payload-map.json'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def check():
 assert sha(CPU)==SHA
 for name in ['decode-second-skid-emitted-audit-v1','decode-second-skid-actual-proof-v1']:
  m=json.loads((ART/name/'manifest.json').read_text());assert m['passed'] and m['candidate_sha256']==SHA

def build():
 check();mapping=json.loads((OUT/'skid-pingpong-pointer-packer-map.json').read_text())
 mapping['derived_from_cpu_sha256']=mapping['source_sha256'];mapping['source_sha256']=SHA
 mapping['derivative_scope']='Age1025 decoded two-bank slot added; first-buffer aliases and local recurrences unchanged, input traffic/cycles may differ.'
 assert not MAP.exists();MAP.write_text(json.dumps(mapping,indent=2)+'\n')
 import run,skid_monitor,second_decode_monitor
 original=skid_monitor.instrument
 def instrument(tb,cpp,path):
  tb,cpp=original(tb,cpp,path)
  return second_decode_monitor.instrument(tb,cpp,PAYLOAD)
 skid_monitor.instrument=instrument
 run.build(LABEL,CPU,MAP)
 out=OUT/LABEL
 manifest=dict(candidate_sha256=SHA,driver_sha256=sha(Path(__file__)),
  added_monitor_sha256=sha(HERE/'second_decode_monitor.py'),payload_map_sha256=sha(PAYLOAD),
  original_monitor_sha256=sha(HERE/'skid_monitor.py'),reference=REF,
  scope='Same retained firmware and baseline testbench plus observation-only decoded-buffer monitor; no shared runner/source modified.')
 (out/'driver-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

def runs():
 check();out=OUT/LABEL
 for script in ['run.py','frontend_hazards.py','instruction_capacity.py','decode_traps.py','fp_legality.py']:
  with (out/(script+'.driver.log')).open('w') as f:
   subprocess.run([sys.executable,str(HERE/script),'run','--label',LABEL],cwd=ROOT,check=True,stdout=f,stderr=subprocess.STDOUT)
  print(script,'PASS',flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['build','run']);a=p.parse_args();{'build':build,'run':runs}[a.action]()
