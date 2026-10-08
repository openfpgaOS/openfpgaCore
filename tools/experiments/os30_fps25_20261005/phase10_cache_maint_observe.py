#!/usr/bin/env python3
"""Private passive render-cache maintenance accounting and one-line GPU comparison."""
import sys,json,hashlib,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(label,candidate=False):
 old=P/'gpu-cache-maint-observe-v1';out=P/label;out.mkdir(exist_ok=False)
 shutil.copytree(old/'model/frozen',out/'model/frozen');q=out/'qsim';q.mkdir()
 for f in (old/'qsim').iterdir():
  if f.suffix in ('.c','.h') or f.name=='Makefile':shutil.copy2(f,q/f.name)
 tb=out/'model/frozen/tb_gpu.v';s=tb.read_text();assert s.count(' if(om_active)begin')==1
 s=s.replace(' if(om_active)begin',' if(gpu_render_cache.maintenance)begin');tb.write_text(s)
 if candidate:
  f=P/'gpu-idle-drain-v1/gpu_core.v';assert sha(f)=='7aa7875d97e910de1c5a59c81de863ed448a742923d10b99783be3b9419b7f3f';shutil.copy2(f,out/'model/frozen/common/gpu_core.v')
 cmd=[s.replace(str(old),str(out)) for s in json.loads((old/'build-command.json').read_text())]
 (out/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
 (out/'inputs.json').write_text(json.dumps(dict(private_only=True,observer_corrected='Count only actual cache.maintenance cycles, not an observer latch awaiting flush_done. An explicit barrier may leave MAINT_DONE without that pulse.',candidate=candidate,helper_sha256=sha(Path(__file__)),sources={str(f.relative_to(out)):sha(f) for f in out.rglob('*') if f.is_file()}),indent=2)+'\n')
 with (out/'build-qsim.log').open('x') as log:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (out/'model/build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 (out/'model/build.json').write_text(json.dumps(dict(command=cmd,simulator_sha256=sha(out/'model/obj/Vtb_gpu'),passive_only=not candidate),indent=2)+'\n')
if __name__=='__main__':main(sys.argv[1],len(sys.argv)>2 and sys.argv[2]=='candidate')
