#!/usr/bin/env python3
import hashlib,json,os,struct,subprocess,sys
from pathlib import Path
T=Path(__file__).resolve().parent;C=T.parent/'model/frozen/src/fpga/common'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out=T/sys.argv[1];assert out.parent==T and not out.exists();out.mkdir()
paths=[Path(__file__),T/'test_preflight_wrapper.cpp',T/'geometry_raw_load_stub.v',C/'phase10_load_alias.v',C/'geometry_import_view.v',C/'geometry_triangle_preflight.v',C/'geometry_float_cull_mem.v',C/'GeometryFmaProbe.v']
before={str(p):sha(p)for p in paths};report={'status':'RUNNING','inputs_sha256':before,'commands':[]}
def run(argv,name):
 with (out/(name+'.log')).open('x')as f:r=subprocess.run(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1'))
 report['commands'].append({'argv':list(map(str,argv)),'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))})
 (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 assert r.returncode==0,(name,(out/(name+'.log')).read_text()[-5000:])
run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','-DINCLUDE_GPU_BRAM_IMPORT','-DINCLUDE_TRIANGLE_PREFLIGHT','--top-module','geometry_load_alias','--Mdir',out/'obj','-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',C/'phase10_load_alias.v',C/'geometry_import_view.v',C/'geometry_triangle_preflight.v',C/'geometry_float_cull_mem.v',C/'GeometryFmaProbe.v',T/'geometry_raw_load_stub.v',T/'test_preflight_wrapper.cpp'],'build')
run([out/'obj/Vgeometry_load_alias'],'run')
assert before=={str(p):sha(p)for p in paths},'Inputs changed during tests'
report.update(status='PASS_ACTUAL_TFP_WRAPPER_ARBITRATION_AND_RESET',result=json.loads((out/'run.log').read_text()),executable_sha256=sha(out/'obj/Vgeometry_load_alias'))
(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['result']))
