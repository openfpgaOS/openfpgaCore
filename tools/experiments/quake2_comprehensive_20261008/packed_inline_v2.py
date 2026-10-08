#!/usr/bin/env python3
"""Remove the measured out-of-line 68-byte memcpy from packed-v1."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-comprehensive-20261008'
B=P/'work/packed';W=P/'work/packed2';C=P/'components/packed-v2';OLD=P/'components/packed-v1'
REL='src/quake2/openfpga/of_emit_q2.c'
BASE='674c8f027f92579bcbcb66c176cd84484da809eff3ba594f440f3082175e86ec'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
assert sha(B/REL)==BASE and not W.exists() and not C.exists()
old=(B/REL).read_text();needle='\t\tmemcpy(w + 1, words, 17u * sizeof(*words));'
replacement='\t\t/* Keep this fixed payload inline: the target libc copy call costs\n\t\t * more than its seventeen aligned word loads/stores. */\n'
replacement+='\n'.join('\t\t'+' '.join('w[%d] = words[%d];'%(j+1,j) for j in range(i,min(i+4,17))) for i in range(0,17,4))
assert old.count(needle)==1
new=old.replace(needle,replacement)
C.mkdir(parents=True);shutil.copytree(B,W,ignore=shutil.ignore_patterns('.obj','build','__pycache__'))
(W/REL).write_text(new);(C/'of_emit_q2.c').write_text(new);shutil.copy2(__file__,C/'recipe.py')
(C/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+REL,tofile='b/'+REL)))
put(C/'prepared.json',dict(parent_sha256=BASE,candidate_sha256=sha(W/REL),dependency='packed-v1 exact payload/layout; changes only one fixed-size copy in owner',worktree=str(W),motivation='Measured packed-v1 emitter saves157898 cycles/frame but out-of-line memcpy adds181243 cycles/frame; unroll exactly17words to avoid libc call.'))
O=C/'checks';O.mkdir()
test=(OLD/'checks/packet.c').read_text();assert test.count(needle)==1
(O/'packet.c').write_text(test.replace(needle,replacement))
cmd=['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie',str(O/'packet.c'),'-o',str(O/'packet')]
commands=[cmd,[str(O/'packet')]];env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
for name,argv in zip(['build','run'],commands):
    with (O/(name+'.log')).open('w') as log:subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
put(O/'commands.json',commands)
put(C/'result.json',dict(passed=True,packet_state_and_caps_cases=1320,sanitizers=['ASan','UBSan'],same_renderer_source_as_packed_v1=True,target_assembly_check_pending=True,performance_measured=False))
put(C/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in C.rglob('*') if p.is_file()}))
print((O/'run.log').read_text().strip());print('owner',sha(W/REL),'manifest',sha(C/'manifest.json'))
