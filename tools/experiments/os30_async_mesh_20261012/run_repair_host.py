#!/usr/bin/env python3
"""Portable repair bounds/format/cancellation/FENV tests, not the CPU math oracle."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess
H=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve();assert not out.is_relative_to(H);out.mkdir(parents=True,exist_ok=False)
inputs=[H/'repair'/n for n in ['async_mesh_repair.c','async_mesh_repair.h','test_host.c']];sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();before={str(p):sha(p)for p in inputs}
cmd=['cc','-std=c11','-O2','-g','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-ffp-contract=off','-fno-fast-math','-fsanitize=address,undefined',str(inputs[0]),str(inputs[2]),'-lm','-o',str(out/'test')]
with(out/'build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
r=subprocess.run([str(out/'test')],text=True,capture_output=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'});(out/'run.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stdout+r.stderr
result=json.loads(r.stdout);result['inputs_sha256']=before;result['scope']='Host bounds/format/cancellation-prefix/FENV tests; exact arithmetic is covered only by the separate current native CPU receipt.';(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(r.stdout)
