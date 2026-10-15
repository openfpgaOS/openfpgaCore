#!/usr/bin/env python3
from pathlib import Path
import hashlib,json,shutil,subprocess,sys
d=Path(__file__).resolve().parent
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=False)
for name in ['audio_sample_cache.v','test.cpp','run.py']:shutil.copy2(d/name,out/name)
cmd=['verilator','--cc','--exe','--build','--assert','-j','3','-Wno-fatal','--top-module','audio_sample_cache','--Mdir',str(out/'obj'),str(out/'audio_sample_cache.v'),str(out/'test.cpp')]
with (out/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
with (out/'result.json').open('w') as f:subprocess.run([str(out/'obj/Vaudio_sample_cache')],stdout=f,check=True)
receipt={'passed':True,'command':cmd,'sources':{n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in ['audio_sample_cache.v','test.cpp','run.py']},'result':json.loads((out/'result.json').read_text()),'limits':['Standalone AXI cache model with synthetic bounded stalls, not physical SDRAM or mixer integration.','Dynamic tests, not a formal proof.']}
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
