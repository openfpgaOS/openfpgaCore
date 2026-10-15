#!/usr/bin/env python3
"""Freeze and compile one isolated depth-read ordering experiment."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / 'cached-cpu-v1/native-sdram-v2'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('label')
    p.add_argument('--gpu', type=Path)
    p.add_argument('--cache', type=Path)
    p.add_argument('--define', action='append', default=[])
    a = p.parse_args()
    assert Path(a.label).name == a.label
    out = HERE / a.label
    assert not out.exists(), out
    base = json.loads((BASE / 'prepared.json').read_text())
    for rel, expected in base['source_sha256'].items():
        assert sha(BASE / rel) == expected, rel
    shutil.copytree(BASE / 'frozen', out / 'frozen')
    patch = []
    for name, source in [('gpu_core.v', a.gpu), ('gpu_color_depth_cache.sv', a.cache)]:
        rel = 'frozen/src/fpga/common/' + name
        before = (BASE / rel).read_text()
        if source:
            shutil.copyfile(source, out / rel)
        after = (out / rel).read_text()
        patch.extend(difflib.unified_diff(before.splitlines(keepends=True),
            after.splitlines(keepends=True), fromfile='a/' + rel, tofile='b/' + rel))
    (out / 'core.patch').write_text(''.join(patch))
    command = [s.replace(str(BASE), str(out)) for s in base['command']]
    for define in a.define:
        assert define.replace('_', '').isalnum()
        command.insert(command.index('--cc'), '-D' + define)
    files = {str(p.relative_to(out)): sha(p)
             for p in (out / 'frozen').rglob('*') if p.is_file()}
    assert files.keys() == base['source_sha256'].keys()
    changed = [rel for rel, h in files.items() if base['source_sha256'][rel] != h]
    prepared = dict(command=command, source_sha256=files,
        baseline_model=str(BASE), baseline_prepared_sha256=sha(BASE / 'prepared.json'),
        changed_inputs=changed, added_defines=a.define,
        recipe_sha256=sha(Path(__file__)), experimental=True,
        limitations=['Native RTL simulation only; FPGA area/timing and hardware behavior unqualified.',
                      'Two fixed intervals with synthetic display/audio and bounded guest services.'])
    (out / 'prepared.json').write_text(json.dumps(prepared, indent=2) + '\n')
    start = time.monotonic()
    with (out / 'build.log').open('x') as log:
        rc = subprocess.run(command, cwd=out / 'frozen/src/fpga/test',
                            stdout=log, stderr=subprocess.STDOUT).returncode
    for rel, expected in files.items():
        assert sha(out / rel) == expected, rel
    (out / 'build.json').write_text(json.dumps(dict(returncode=rc,
        seconds=time.monotonic()-start, command=command,
        prepared_sha256=sha(out/'prepared.json')), indent=2)+'\n')
    print(a.label, 'build', rc, flush=True)
    raise SystemExit(rc)

if __name__ == '__main__':
    main()
