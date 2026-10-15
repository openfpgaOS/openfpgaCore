from pathlib import Path
import hashlib,json,shutil
R=Path(__file__).resolve().parent
O=Path('/tmp/quake2-gpu-20261011/bench')
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
pin=json.loads((O/'prepared.json').read_text())
for n,h in pin['pins'].items():assert sha(O/n)==h,n
assert not (R/'prepared.json').exists()
for n in ('qsim','inputs'):shutil.copytree(O/n,R/n)
shutil.copy2(O/'bench.py',R/'bench.py')
for n,h in pin['pins'].items():assert sha(R/n)==h,n
pin['parent']=str(O);pin['parent_manifest_sha256']=sha(O/'prepared.json')
pin['pins']['bench.py']=sha(R/'bench.py')
(R/'prepared.json').write_text(json.dumps(pin,indent=2)+'\n')
print('prepared',len(pin['pins']))
