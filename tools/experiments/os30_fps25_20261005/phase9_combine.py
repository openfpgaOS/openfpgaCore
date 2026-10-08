#!/usr/bin/env python3
"""Compose the finite-W guard and state-check cleanup; stage F11 PREP privately."""
import difflib
import json
from pathlib import Path

from phase9_experiment import INPUTS, OUT, PARENT, parent_inputs, sha, write

LABEL = 'checks-state-f11'

def main():
    parent = parent_inputs()
    guard, state = OUT/'w-guard-v1', OUT/'direct-state-v1'
    dest = OUT/LABEL
    sources = {}
    for directory in (guard, state):
        build = json.loads((directory/'build-normal.json').read_text())
        assert sha(directory/'app.elf') == build['elf_sha256']
        for name in INPUTS:
            assert sha(directory/name) == build['inputs'][name]['sha256']
    for name in INPUTS:
        source = guard/name if name == 'gfx_pc.c' else state/name
        sources[name] = source.read_text()
    assert sources['perf_pocket.c'].count('PERF F9 TRI') == 1
    sources['perf_pocket.c'] = sources['perf_pocket.c'].replace('PERF F9 TRI', 'PERF F11 PREP')
    dest.mkdir(exist_ok=False)
    patches = []
    for name, data in sources.items():
        (dest/name).write_text(data)
        old = (PARENT/name).read_text()
        patches += difflib.unified_diff(old.splitlines(True), data.splitlines(True),
                                       fromfile='F9/'+name, tofile=LABEL+'/'+name)
    (dest/'candidate.patch').write_text(''.join(patches))
    write(dest/'composition.json', dict(
        parent='phase7/combined-v1/F9', parent_normal_elf_sha256=parent['elf_sha256'],
        source_sha256={n:sha(dest/n) for n in INPUTS},
        guard_source_sha256=sha(guard/'gfx_pc.c'),
        state_source_sha256=sha(state/'gfx_gpu.c'),
        recipe_sha256=sha(Path(__file__)), qualified=False,
        scope='Integer positive-W eligibility guard and fewer unchanged render-state checks. F11 PREP marker only changes diagnostic builds.',
        numerical_contract='Finite geometry W values preserve original predicate. Nonfinite NaNs conservatively use the existing fallback; this differs from NaN acceptance in fast-math F9 machine code. No universal exceptional-value or floating exception-flag equivalence is claimed.',
        pending=['Actual-A finite guard performance', 'Exact normal and diagnostic captures',
                 'Final codegen review', 'Coupled performance and image checks']))
    print(dest)

if __name__ == '__main__':
    main()
