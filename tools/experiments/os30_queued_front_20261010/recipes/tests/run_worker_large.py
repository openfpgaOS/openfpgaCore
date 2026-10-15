#!/usr/bin/env python3
import hashlib,json,math,os,struct,subprocess,sys
from pathlib import Path
T=Path(__file__).resolve().parent;C=T.parent/'native-large-fifo-v1/frozen/src/fpga/common'
O=T.parent.parent/'triangle-front-v1/tests/cpu-arithmetic-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out=T/sys.argv[1];assert out.parent==T and not out.exists();out.mkdir()
paths=[Path(__file__),T/'tb_queued_front_large.v',T/'test_queued_front_large.cpp',C/'gpu_queued_front.v',C/'geometry_import_view.v',C/'geometry_float_cull_mem.v',C/'geometry_import_convert.v',C/'GeometryFmaProbe.v',C/'gpu_core.v',T.parent/'native-large-fifo-v1/prepared.json',O/'inputs.json',O/'outputs.bin',O/'manifest.json']
before={str(p):sha(p)for p in paths};report={'status':'RUNNING','inputs_sha256':before,'commands':[],'scope':'Actual queued worker, snapshot copier/read ownership, current CPU cull oracle, conversion, command FIFO; shared-FMA grant modeled separately from already-tested actual wrapper arbiter.'}
rows=json.loads((O/'inputs.json').read_text())['rows'];outputs=list(struct.iter_unpack('<11I',(O/'outputs.bin').read_bytes()))
f=lambda x:struct.unpack('<f',struct.pack('<I',x))[0]
normal=lambda x: x&0x7fffffff==0 or 0<(x>>23&255)<255
eligible=[]
for i,(a,b)in enumerate(zip(rows,outputs)):
 if a[9] or any(a[10:13]) or any(not normal(x)for x in a[:9]):continue
 if any(not(0<f(a[j*3+2])<math.inf)for j in range(3)):continue
 if any(abs(f(a[j*3+k]))>32764 for j in range(3)for k in [0,1]):continue
 eligible.append(i)
selected=sorted(set(eligible[::8])|{i for i in eligible if any((x&0x7fffffff)!=0 and (x>>23&255)==0 for x in outputs[i][:9])})
(out/'vectors.bin').write_bytes(b''.join(struct.pack('<14I',*rows[i])for i in selected));(out/'expected.bin').write_bytes(b''.join(struct.pack('<11I',*outputs[i])for i in selected))
report['selected_original_vector_indices']=selected
def run(argv,name):
 with (out/(name+'.log')).open('x')as log:r=subprocess.run(list(map(str,argv)),stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1'))
 report['commands'].append({'argv':list(map(str,argv)),'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))})
 (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 assert r.returncode==0,(name,(out/(name+'.log')).read_text()[-6000:])
run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','tb_queued_front','--Mdir',out/'obj','-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',T/'tb_queued_front_large.v',C/'gpu_queued_front.v',C/'geometry_import_view.v',C/'geometry_float_cull_mem.v',C/'geometry_import_convert.v',C/'GeometryFmaProbe.v',T/'test_queued_front_large.cpp'],'build')
run([out/'obj/Vtb_queued_front',out/'vectors.bin',out/'expected.bin'],'run')
assert before=={str(p):sha(p)for p in paths},'Inputs changed during tests'
report.update(status='PASS_ACTUAL_QGF_LARGE_FIFO_ARITHMETIC_ORDER_REPAIR_AND_PRESSURE',result=json.loads((out/'run.log').read_text()),executable_sha256=sha(out/'obj/Vtb_queued_front'),vectors_sha256=sha(out/'vectors.bin'),expected_sha256=sha(out/'expected.bin'))
(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['result']))
