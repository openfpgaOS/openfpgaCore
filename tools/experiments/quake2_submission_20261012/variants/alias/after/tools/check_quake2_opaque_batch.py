#!/usr/bin/env python3
"""Bounded capacity and ordering checks using actual opaque-batch owner code."""
import hashlib,json,os,re,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'src/quake2/openfpga/of_emit_q2.c'
s=source.read_text()
names=['ofq2_tri_batch_commit','OFQ2_EndOpaqueTriBatch','OFQ2_BeginOpaqueTriBatch','ofq2_tri_batch_refill','OFQ2_DrawVertTriRGBPacked']
def extract(name):
    m=re.search(r'(?m)^(?:static )?(?:void|int)(?: __attribute__\(\(noinline\)\))?\n'+name+r'\([^;]*?\)\n\{',s)
    assert m,name
    mask=re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',lambda m:' '*len(m[0]),s,flags=re.S)
    end=s.index('{',m.start())+1;depth=1
    while depth:
        depth+=(mask[end]=='{')-(mask[end]=='}');end+=1
    return s[m.start():end]
with tempfile.TemporaryDirectory(prefix='q2-opaque-') as tmp:
    path=Path(tmp);(path/'opaque_functions.inc').write_text('\n'.join(extract(n) for n in names))
    exe=path/'test'
    cmd=[os.environ.get('CC','cc'),'-std=c11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie','-I'+tmp,str(ROOT/'tools/tests/quake2_opaque_batch_test.c'),'-o',str(exe)]
    subprocess.run(cmd,check=True)
    r=subprocess.run([str(exe)],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1'))
    print(r.stdout+r.stderr,end='');r.check_returncode()
    result={'passed':True,'command':cmd,'stdout':r.stdout,'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [source,Path(__file__),ROOT/'tools/tests/quake2_opaque_batch_test.c']}}
    (ROOT/'evidence/opaque-batch-v1.json').write_text(json.dumps(result,indent=2)+'\n')
