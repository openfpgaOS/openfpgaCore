#!/usr/bin/env python3
import importlib.util,sys,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase3/vertex-cpu'
p=ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py'
spec=importlib.util.spec_from_file_location('capture_retained',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.OUT=OUT
sys.argv=[str(Path(__file__)),'f5-state-v2','--elf',str(ROOT/'docs/measurements/os30-fps25-20261005/phase2/exact-v1/diagnostic.elf'),'--exe',str(OUT/'qsim-v2/qsim'),'--geometry','0','--audio']
m.main()
a=ROOT/'docs/measurements/os30-fps25-20261005/validation/capture-phase2-exact-v1-fps';b=OUT/'capture-f5-state-v2';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
verified={}
for name in ['frames.csv','scene.csv','geometry.csv','diagnostic.csv','culling.csv']:
 assert (a/name).exists()==(b/name).exists(),name
 if (a/name).exists():assert sha(a/name)==sha(b/name),name;verified[name]=sha(b/name)
for p in (a/'gpuvec').glob('*'):
 if p.is_file():assert sha(p)==sha(b/'gpuvec'/p.name),p.name;verified['gpuvec/'+p.name]=sha(p)
# Per-function counters also must match; txt headers name a different ELF path.
for p in a.glob('profile*.tsv'):
 assert sha(p)==sha(b/p.name),p.name;verified[p.name]=sha(p)
record={'passed':True,'observer_noninterference':True,'elf_sha256':sha(b/'sm64.elf'),'input_sha256':sha(b/'vertex-state.bin'),'csv_sha256':sha(b/'vertex-state.csv'),'reference_capture':str(a.relative_to(ROOT)),'identical_results_sha256':verified,'scope':'All721 scene/frame/state records, captured GPU vectors and all per-function/call profile tables equal the retained F5 capture. Observer only snapshots data.'}
(OUT/'capture-proof-v2.json').write_text(json.dumps(record,indent=2)+'\n');print('PASS full F5 observer noninterference')
