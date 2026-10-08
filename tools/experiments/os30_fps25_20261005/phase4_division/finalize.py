#!/usr/bin/env python3
"""Close bounded aspect validation; never publish or deploy."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
CAMPAIGN=ROOT/'docs/measurements/os30-fps25-20261005'
OUT=CAMPAIGN/'phase4/division'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def main():
    codegen=read(OUT/'codegen-and-closure.json');assert codegen['passed']
    dimensions=read(OUT/'dimension-proof/proof.json');assert dimensions['passed']
    source=read(OUT/'independent-review/aspect-v1-source-audit.json')
    assert source['source_review_passed']
    for p,h in source['evidence_sha256'].items():assert sha(Path(p))==h,p
    rows=read(OUT/'replay-results-aspect-normal-aspect-fps.json');assert len(rows)==32
    comparisons=[];evidence={}
    def bind(p):evidence[str(p.relative_to(ROOT))]=sha(p)
    for mode in ['normal','fps']:
        baseline_path=CAMPAIGN/'validation'/('replay-results-phase2-exact-v1-'+mode+'.json')
        baseline={(r['mode'],r['frame']):r for r in read(baseline_path)};assert len(baseline)==16;bind(baseline_path)
        for row in rows:
            if row['label']!='aspect-'+mode:continue
            old=baseline[(row['mode'],row['frame'])]
            assert row['hardware_sha256']==old['hardware_sha256']
            assert row['commands']==old['commands']
            a=ROOT/old['binary'];b=ROOT/row['binary'];aa=a.read_bytes();bb=b.read_bytes()
            assert len(aa)==len(bb)==307212
            assert aa[:153600]==bb[:153600] and aa[153612:]==bb[153612:]
            assert sha(b.with_suffix('.log'))==row['log_sha256']
            assert sha(OUT/('capture-aspect-'+mode)/'gpuvec'/f"frame_{row['frame']:05}.vec")==row['vector_sha256']
            for p in [a,b,b.with_suffix('.log'),b.with_suffix('.vec')]:bind(p)
            comparisons.append({'build':mode,'memory_mode':row['mode'],'frame':row['frame'],'color_exact':True,'depth_exact':True,'old_gpu_cycles':old['stats']['cycles'],'new_gpu_cycles':row['stats']['cycles']})
    assert len(comparisons)==32
    for name in ['codegen-and-closure.json','dimension-proof/proof.json','independent-review/aspect-v1-source-audit.json','results-normal.json','results-fps.json','replay-results-aspect-normal-aspect-fps.json']:
        bind(OUT/name)
    for label in ['control-f5','aspect-v1']:
        for name in ['app.elf','diagnostic.elf','build-normal.json','build-diagnostic.json','build-normal.log','build-diagnostic.log']:bind(OUT/label/name)
    bind(Path(__file__))
    result={'passed':True,'candidate':'aspect-v1','parent':'shipped F5 phase2/exact-v1','normal':read(OUT/'results-normal.json'),'fps':read(OUT/'results-fps.json'),'gpu_comparisons':comparisons,'all32_color_depth_exact':True,'evidence_sha256':evidence,'physical_performance_verified':False,'published':False,'deployed':False,'scope':'Small exact-output software cleanup only. Estimated cycle gains are not Pocket FPS measurements. No FPGA change.'}
    (OUT/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS32/32 exact color/depth; private candidate only')
if __name__=='__main__':main()
