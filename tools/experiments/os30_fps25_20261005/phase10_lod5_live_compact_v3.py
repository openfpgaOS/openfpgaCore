#!/usr/bin/env python3
"""One exact staged compact-v3 rebind of live native LOD5 nominal integration."""
import json,shutil,sys
from pathlib import Path
import phase10_lod5_live_native as N
P=N.P;ROOT=N.ROOT;BASE=P/'lod5-live-native-v2';STAGE=P/'compact-lod5-core-v3';OUT=P/'lod5-live-compact-v3'
def prepare():
 prep=json.loads((STAGE/'prepared.json').read_text());old=json.loads((P/'compact-lod5-core-v2/prepared.json').read_text())
 names=prep['only_changed_files'];assert len(names)==4
 assert sorted(n for n,h in prep['source_sha256'].items()if old['source_sha256'][n]!=h)==sorted(names)
 OUT.mkdir(exist_ok=False);d=OUT/'candidate';d.mkdir();shutil.copytree(BASE/'queues/frozen',d/'frozen')
 for n in names:
  s=STAGE/'frozen'/n;assert N.sha(s)==prep['source_sha256'][n];shutil.copyfile(s,d/'frozen'/n)
 before=json.loads((BASE/'queues/prepared.json').read_text())
 changed=sorted(n[7:]for n,h in before['source_sha256'].items()if N.sha(d/n)!=h)
 assert changed==sorted(names),changed
 cmd=[x.replace(str(BASE/'queues'),str(d))for x in before['command']]
 cmd.insert(cmd.index('--cc'),'--assert');cmd.insert(cmd.index('--cc'),'+define+GEOMETRY_SCAN_CHECKS')
 N.save(d/'prepared.json',dict(command=cmd,only_changed_rtl=names,source_sha256={str(p.relative_to(d)):N.sha(p)for p in(d/'frozen').rglob('*')if p.is_file()}))
 shutil.copytree(BASE/'full-rgb/firmware',OUT/'firmware')
 N.save(OUT/'prepared.json',dict(stage_prepared_sha256=N.sha(STAGE/'prepared.json'),only_changed_rtl=names,firmware_sha256=N.sha(OUT/'firmware/kernel.bin'),parent_native_manifest_sha256=N.sha(BASE/'manifest.json'),recipe_sha256=N.sha(Path(__file__)),scope=__doc__,deployment_ready=False))
def model():N.OUT=OUT;N.model('candidate')
def run():N.OUT=OUT;N.run('candidate','nominal')
def seal():
 r=json.loads((OUT/'candidate/nominal-result.json').read_text());assert r['passed'];log=(OUT/'candidate/nominal.log').read_text()
 assert 'CPU_RESET_PRESERVED_LOAD'in log and 'FENCE_COMMANDS_EXACT'in log
 assert 'COUNTS 000090b8 0000006f 00000068 000005ec'in log
 j=json.loads((OUT/'candidate/prepared.json').read_text())
 for n,h in j['source_sha256'].items():assert N.sha(OUT/'candidate'/n)==h,n
 files=[OUT/'prepared.json',OUT/'candidate/prepared.json',OUT/'candidate/build.json',OUT/'candidate/build.log',OUT/'candidate/obj/Vtb_system',OUT/'candidate/nominal-result.json',OUT/'candidate/nominal.log',STAGE/'prepared.json',BASE/'manifest.json',Path(__file__)]
 files += [OUT/'candidate'/n for n in j['source_sha256']]
 files += [p for p in(OUT/'firmware').rglob('*')if p.is_file()]
 N.save(OUT/'manifest.json',dict(passed=True,deployment_ready=False,staged_source_bound=True,firmware_unchanged=True,scope='Same full-RGB actualCPU/liveLOD5 stress case, nominal scanout/texture contention/reset, exact staged compact-v3 RTL. Not wholegame or physical timing.',evidence_sha256={str(p.relative_to(ROOT)):N.sha(p)for p in files}));print(N.sha(OUT/'manifest.json'))
if __name__=='__main__':globals()[sys.argv[1]]()
