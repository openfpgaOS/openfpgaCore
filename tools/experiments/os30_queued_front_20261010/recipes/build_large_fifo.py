#!/usr/bin/env python3
"""Isolated native-only 2048-word output-capacity counterfactual.

Clone the already frozen QGF1 native-v1 model, change its one worker parameter,
and replay its exact compile argv with paths relocated. Never edits staging or
prior evidence. The unchanged 4096-word ingress makes this an area-unqualified
capacity experiment, not a physical candidate.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--label', default='native-large-fifo-v1')
    a = p.parse_args()
    assert Path(a.label).name == a.label
    source = HERE / 'native-v1'
    out = HERE / a.label
    assert not out.exists(), out
    base = json.loads((source / 'prepared.json').read_text())
    for rel, expected in base['source_sha256'].items():
        assert sha(source / rel) == expected, rel
    shutil.copytree(source / 'frozen', out / 'frozen')
    changed = 'frozen/src/fpga/common/gpu_core.v'
    target = out / changed
    before = target.read_text()
    old = 'gpu_queued_front #(.ADDR_BITS(RING_ADDR_BITS), .QUEUE_BITS(9)) worker ('
    new = 'gpu_queued_front #(.ADDR_BITS(RING_ADDR_BITS), .QUEUE_BITS(11)) worker ('
    assert before.count(old) == 1
    target.write_text(before.replace(old, new))
    command = [v.replace(str(source), str(out)) for v in base['command']]
    files = {str(p.relative_to(out)): sha(p)
             for p in (out / 'frozen').rglob('*') if p.is_file()}
    assert files.keys() == base['source_sha256'].keys()
    assert [n for n, h in files.items()
            if h != base['source_sha256'][n]] == [changed]
    (out / 'capacity-only.patch').write_text(''.join(difflib.unified_diff(
        before.splitlines(keepends=True), target.read_text().splitlines(keepends=True),
        fromfile='a/' + changed, tofile='b/' + changed)))
    prepared = dict(command=command, source_sha256=files,
        baseline_model=str(source), baseline_prepared_sha256=sha(source / 'prepared.json'),
        changed_inputs=[changed], recipe_sha256=sha(Path(__file__)),
        experimental=True, counterfactual=True,
        experiment='Output FIFO capacity only: 512 to 2048 words; ingress remains 4096 words.',
        output_queue_words=2048, ingress_ring_words=4096,
        limitations=[
            'Native RTL simulation only; no FPGA fit, timing, area or hardware qualification.',
            'The extra output storage exceeds the four-spare-M10K budget when ingress stays unchanged.',
            'Synthetic display/audio traffic and bounded guest services.',
            'Passive output RAM observer must index with 2047, not 511; pointer modulus stays 4096.'
        ])
    (out / 'prepared.json').write_text(json.dumps(prepared, indent=2) + '\n')
    manifest = dict(status='BUILDING_NATIVE_COUNTERFACTUAL',
        baseline_prepared_sha256=sha(source / 'prepared.json'),
        prepared_sha256=sha(out / 'prepared.json'),
        recipe_sha256=sha(Path(__file__)), source_count=len(files),
        changed_source=changed, before_sha256=base['source_sha256'][changed],
        after_sha256=files[changed], patch_sha256=sha(out / 'capacity-only.patch'),
        compile_argv_delta='Only absolute model paths relocated; no flags or source-list changes.',
        production_mutated=False, quartus_run=False, area_fit_claim=False)
    (out / 'counterfactual-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    start = time.monotonic()
    with (out / 'build.log').open('x') as f:
        rc = subprocess.run(command, cwd=out / 'frozen/src/fpga/test',
                            stdout=f, stderr=subprocess.STDOUT).returncode
    for rel, expected in files.items():
        assert sha(out / rel) == expected, rel
    for rel, expected in base['source_sha256'].items():
        assert sha(source / rel) == expected, rel
    (out / 'build.json').write_text(json.dumps(dict(returncode=rc,
        seconds=time.monotonic() - start, prepared_sha256=sha(out / 'prepared.json'),
        command=command), indent=2) + '\n')
    manifest['status'] = 'NATIVE_BUILD_PASS' if rc == 0 else 'NATIVE_BUILD_FAILED'
    manifest['build_sha256'] = sha(out / 'build.json')
    (out / 'counterfactual-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(a.label, 'build', rc, flush=True)
    raise SystemExit(rc)


if __name__ == '__main__':
    main()
