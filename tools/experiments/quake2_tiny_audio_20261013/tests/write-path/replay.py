#!/usr/bin/env python3
"""Replay pinned current-QSF GPU/SDRAM evidence in a NEW output directory."""
import argparse,hashlib,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('evidence',type=Path)
p.add_argument('output',type=Path)
a=p.parse_args();src=a.evidence.resolve();out=a.output.resolve()
receipt=json.loads((src/'receipt.json').read_text())
input_pins=receipt.get('test_input_pins',receipt.get('source_and_evidence_sha256'))
for name,pin in input_pins.items():
 assert hashlib.sha256((src/'inputs'/name).read_bytes()).hexdigest()==pin,name
out.mkdir(parents=True,exist_ok=False)
command=receipt.get('build_command',receipt.get('command'))
oldobj=command[command.index('--Mdir')+1]
oldroot=str(Path(oldobj).parent)
command=[s.replace(oldroot,str(src)) for s in command]
command[command.index('--Mdir')+1]=str(out/'obj')
with (out/'build.log').open('w') as log:
 subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
with (out/'run.log').open('w') as log:
 subprocess.run([str(out/'obj/Vtb_gpu_wrpath')],cwd=out,stdout=log,stderr=subprocess.STDOUT,check=True)
assert (out/'run.log').read_bytes()==(src/'run.log').read_bytes(),'run transcript differs'
(out/'receipt.json').write_text(json.dumps({'status':'PASS','evidence_receipt_sha256':hashlib.sha256((src/'receipt.json').read_bytes()).hexdigest(),'transcript_exact':True,'command':command},indent=2)+'\n')
print('PASS: pinned inputs and exact complete GPU/SDRAM transcript')
