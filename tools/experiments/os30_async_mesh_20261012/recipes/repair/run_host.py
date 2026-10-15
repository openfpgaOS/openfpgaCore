#!/usr/bin/env python3
import hashlib,json,os,subprocess,sys
from pathlib import Path
H=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out=H/sys.argv[1];assert out.parent==H and not out.exists();out.mkdir()
inputs={str(p):sha(p)for p in [Path(__file__),H/'test_host.c',H/'async_mesh_repair.c',H/'async_mesh_repair.h']}
commands=[]
def run(argv,name):
 with (out/(name+'.log')).open('x')as f:
  env=dict(os.environ);env['ASAN_OPTIONS']='detect_leaks=0'
  r=subprocess.run(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,env=env)
 commands.append({'argv':list(map(str,argv)),'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))})
 assert r.returncode==0,(name,(out/(name+'.log')).read_text()[-3000:])
run(['cc','-std=c11','-O2','-g','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-ffp-contract=off','-fno-fast-math','-fsanitize=address,undefined',H/'async_mesh_repair.c',H/'test_host.c','-lm','-o',out/'test'],'build')
run([out/'test'],'run')
result=json.loads((out/'run.log').read_text());assert inputs=={p:sha(Path(p))for p in inputs}
result.update(inputs_sha256=inputs,commands=commands,binary_sha256=sha(out/'test'),scope='Bounds/format/cancellation-prefix/FENV tests. Exact arithmetic uses the separate actual CPU differential.',sanitizers='ASan+UBSan; leak detector disabled because ptrace cannot support LSan and helper performs no allocation')
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
