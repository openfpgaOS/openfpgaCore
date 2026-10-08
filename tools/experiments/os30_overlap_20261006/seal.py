#!/usr/bin/env python3
"""Bind final read-only model conclusions, tools, sources and run evidence."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    short=json.loads((OUT/'results.json').read_text());long=json.loads((OUT/'long-results.json').read_text())
    assert short['passed'] and long['passed']
    assert sha(HERE/'analyze.py')==short['analyzer_sha256']
    assert sha(HERE/'analyze_long.py')==long['analyzer_sha256']
    assert sha(HERE/'analysis_common.py')==long['shared_analyzer_sha256']
    frozen=OUT/'source-tools';frozen.mkdir(exist_ok=False)
    tools={}
    for p in sorted(HERE.iterdir()):
        if p.is_file():shutil.copy2(p,frozen/p.name);tools[str(p.relative_to(ROOT))]=sha(p)
    evidence={}
    for name in ['results.json','long-results.json','REPORT.txt','LONG_REPORT.txt','qsim-sources.json',
                 'model/inputs.json','model/build.json','model/obj/Vtb_gpu']:
        p=OUT/name;evidence[str(p.relative_to(ROOT))]=sha(p)
    runs=[]
    for d in sorted(OUT.glob('run-*')):
        m=json.loads((d/'inputs.json').read_text());assert m['success']
        for n,h in m['results_sha256'].items():assert sha(d/n)==h,(d,n)
        for n,h in m['sha256'].items():assert sha(Path(n))==h,n
        for p in d.rglob('*'):
            if p.is_file():evidence[str(p.relative_to(ROOT))]=sha(p)
        runs.append(dict(directory=str(d.relative_to(ROOT)),elf_sha256=sha(d/'sm64.elf'),
            start=m['start'],frames=m['frames'],traffic=m['traffic'],inputs_sha256=sha(d/'inputs.json')))
    assert len(runs)==22
    m=json.loads((OUT/'model/inputs.json').read_text())
    for n,h in m['sources'].items():
        p=OUT/'model'/n;assert sha(p)==h;evidence[str(p.relative_to(ROOT))]=h
    for n,h in json.loads((OUT/'qsim-sources.json').read_text()).items():
        p=OUT/'qsim'/n;assert sha(p)==h;evidence[str(p.relative_to(ROOT))]=h
    for p in frozen.iterdir():evidence[str(p.relative_to(ROOT))]=sha(p)
    result=dict(passed=True,hardware_changes=False,deployment=False,
        retained_core_sha256=short['retained_core_sha256'],simulator_sha256=sha(OUT/'model/obj/Vtb_gpu'),
        tool_sha256=tools,evidence_sha256=evidence,runs=runs,
        validated_coupled_frames=272,exact_paired_color_depth_frames=160,independent_start_replay_matches=22,
        scope='Read-only comparison evidence; qualification/publication is external. CPU time is modeled, not Pocket FPS.')
    with (OUT/'final-manifest.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print('sealed',len(evidence),'evidence files;',sha(OUT/'final-manifest.json'))
if __name__=='__main__':main()
