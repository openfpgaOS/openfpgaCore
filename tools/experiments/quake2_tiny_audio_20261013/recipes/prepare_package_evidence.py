#!/usr/bin/env python3
"""Pin a narrow evidence/fixture allowlist for later final assembly."""
import argparse
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    p.add_argument('--extra-plan',type=Path,help='Additional explicit source/destination items from the owner.')
    a=p.parse_args();items=[]
    def add(source,destination):
        path=HERE/source
        if path.is_symlink()or not path.is_file():raise ValueError('Missing/linked input:'+str(path))
        if any(part in ('obj','obj_dir','db','incremental_db','vendor_altmult_add.v','vendor_families.v','altera_mf.v')for part in path.parts):raise ValueError('Excluded input:'+str(path))
        items.append(dict(source=source,destination=destination,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    for name in ['run.py','tb_audio_mixer.v','tb_audio_mixer_main.cpp','independent_reference.hpp','sources/sync_fifo.v','sources/mister_audio_output.v']:
        add('tests/'+name,'tests/mixer/'+name)
    for name in ['run.py','test.cpp']:
        add('cache/'+name,'tests/cache/'+name)
    for name in ['build.py','main.cpp','slave.vh','analyze.py','README.txt']:
        add('contention/'+name,'tests/contention/'+name)
    audit=json.loads((HERE/'integration-tests/audit-final-ring.json').read_text())
    for name,digest in audit['owned_fixture_inventory'].items():
        path=HERE/'integration-tests'/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Audited fixture changed:'+name)
        add('integration-tests/'+name,'tests/integration/'+name)
    worker_root='area-geometry/ring-lookahead/'
    worker=json.loads((HERE/worker_root/'portable-allowlist.json').read_text())
    for name,digest in worker['files'].items():
        path=HERE/worker_root/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Audited worker fixture changed:'+name)
        add(worker_root+name,'tests/worker/'+name)
    add(worker_root+'portable-allowlist.json','tests/worker/portable-allowlist.json')
    simple=['app-clean-rebuild.json','package-candidate-changes.json','package-candidate-diff.patch','package-security-tests-v4.json',
            'package-preflight-export-v2.json',
            'portable-app/build-receipt.json','portable-app/app.log','portable-app/host-receipt.json',
            'portable-app/renderer.log','portable-app/geometry.log','portable-app/textures.log',
            'integration-tests/audit-final-ring.json','component-synth/compact-v4-reuse-readaddr/component-receipt.json',
            'component-synth/compact-v4-reuse-readaddr/README.txt','component-synth/compact-v4-reuse-readaddr/map.log',
            'component-synth/compact-v4-reuse-readaddr/output_files/mixer.map.rpt','cache/tail-v2/receipt.json','cache/tail-v2/result.json','cache/pins.json']
    for name in simple:add(name,'evidence/'+name)
    add('prepare_package_evidence.py','recipes/prepare_package_evidence.py')
    for name in ['receipt.json','source-pins.json','inherited.log','reference.log','capacity.csv']:
        add('component-synth/compact-v4-reuse-readaddr/tests/'+name,'evidence/mixer/'+name)
    for name in ['receipt.json','source-pins.json','periph/audio-result-0.log','periph/audio-result-1.log','mixer-cache/receipt.json','mixer-cache/source-pins.json','mixer-cache/inherited.log','mixer-cache/reference.log','mixer-cache/capacity.csv']:
        add('integration-tests/portable-proof-final-ring/'+name,'evidence/integration/'+name)
    for run in ['integrated-final-ring-v1','integrated-final-ring-all22-v1']:
        for name in ['receipt.json','results.json','summary.json','tb_contention.v','build.log']:
            add('contention/'+run+'/'+name,'evidence/contention/'+run+'/'+name)
        for path in sorted((HERE/'contention'/run/'inputs').iterdir()):
            if path.is_file():add(str(path.relative_to(HERE)),'evidence/contention/'+run+'/inputs/'+path.name)
    if a.extra_plan:
        for item in json.loads(a.extra_plan.read_text())['files']:
            add(item['source'],item['destination'])
    names=[item['destination']for item in items]
    if len(names)!=len(set(names)):raise ValueError('Duplicate destinations')
    a.output.write_text(json.dumps(dict(format=1,scope='Owned tests and selected logical evidence; final physical sweep added separately.',files=items),indent=2)+'\n')
    print('Pinned',len(items),'files')


if __name__=='__main__':main()
