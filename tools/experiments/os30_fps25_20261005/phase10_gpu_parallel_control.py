#!/usr/bin/env python3
"""Exact disjoint queue-finality/color-prefix composition; lint only, no CAD."""
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import difflib
import phase10_gpu_queue_finality_v18 as queue

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
O = P / 'gpu-parallel-control-v1'
B = P / 'gpu-control-owner-v1/gpu_core.v'
C = P / 'gpu-light-prefix-v1'
Q = P / 'gpu-queue-finality-v18'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, d):
    with p.open('x') as f:
        json.dump(d, f, indent=2, sort_keys=True); f.write('\n')


def run():
    assert sha(B) == queue.BASE_SHA
    evidence = {}
    for root, expected in [(C, '52abebc1ba922809b9b64595d8542ac57bc80663eb98ccfeb7d78d941b3fbdec'),
                           (Q, 'cc1880bbed36390f633f1503809fb0f39981caf5feab134ee541b4e78761afd0')]:
        manifest = root / 'manifest.json'; assert sha(manifest) == expected
        evidence[str(manifest.relative_to(R))] = expected
        m = json.loads(manifest.read_text()); assert m['passed']
        for name, digest in m.get('files', {}).items():
            p = root / name; assert sha(p) == digest; evidence[str(p.relative_to(R))] = digest
        for name, digest in m.get('evidence_sha256', {}).items():
            p = R / name; assert sha(p) == digest; evidence[name] = digest
    base = B.read_text(); color = (C / 'gpu_core.v').read_text()
    prefix = color[color.index('// Full unsigned six-bit increment'):color.index('function [15:0] rgb565_modulate;')]
    assert prefix.count('function [6:0] rgb565_light_scale;') == 1
    hunks = [dict(kind='queue finality', old=queue.OLD, new=queue.NEW),
             dict(kind='color prefix function', old='function [15:0] rgb565_modulate;', new=prefix+'function [15:0] rgb565_modulate;'),
             dict(kind='modulate scale', old="        scale = {1'b0, light} + 7'd1;", new='        scale = rgb565_light_scale(light);'),
             dict(kind='Gouraud green scale', old="        sg = {1'b0, g} + 7'd1;", new='        sg = rgb565_light_scale(g);')]
    def apply(s, hs):
        for h in hs:
            assert s.count(h['old']) == 1; s = s.replace(h['old'], h['new'], 1)
        return s
    assert apply(base, hunks[1:]) == color
    assert apply(base, hunks[:1]) == (Q/'gpu_core.v').read_text()
    selected = apply(base, hunks)
    for order in itertools.permutations(hunks):
        assert apply(base, order) == selected
        s = selected
        for h in reversed(order):
            assert s.count(h['new']) == 1; s = s.replace(h['new'], h['old'], 1)
        assert s == base
    assert [s for s in selected.splitlines() if '<=' in s] == [s for s in base.splitlines() if '<=' in s]
    O.mkdir()
    (O/'gpu_core.v').write_text(selected)
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff(base.splitlines(True),selected.splitlines(True),fromfile='e75/gpu_core.v',tofile='parallel-control/gpu_core.v')))
    save(O/'hunks.json', hunks)
    save(O/'prepared.json', dict(parent_sha256=sha(B), source_sha256=sha(O/'gpu_core.v'), queue_sha256=sha(Q/'gpu_core.v'), color_sha256=sha(C/'gpu_core.v'), literal_hunks=4, composition_orders=24, added_registers=0, added_cycles=0, production_modified=False, native_pending=True, physical_pending=True))
    # Lint the same selected GPU test configuration. The parent model's passive
    # monitors are retained exactly; only the color hunks are added to the
    # already built and proved queue model. No new simulation is performed.
    model = P/'gpu-queue-finality-review-v18/model'
    functional_model = model/'frozen/common/gpu_core.v'
    instrumented = apply(functional_model.read_text(), hunks[1:])
    lint = O/'lint'; lint.mkdir(); (lint/'gpu_core.v').write_text(instrumented)
    oldcmd = json.loads((model/'command.json').read_text()); command=[]; i=0
    while i < len(oldcmd):
        token=oldcmd[i]
        if token in ['--cc','--exe','--build']:
            i+=1; continue
        if token in ['-j','-CFLAGS','--Mdir']:
            i+=2; continue
        if token.endswith('.cpp'):
            i+=1; continue
        command.append(str(lint/'gpu_core.v') if token == str(functional_model) else token); i+=1
    command += ['--lint-only', '--Mdir', str(lint/'obj')]
    save(lint/'command.json', command)
    with (lint/'run.log').open('x') as f:
        subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)
    evidence[str(functional_model.relative_to(R))]=sha(functional_model)
    evidence[str((model/'command.json').relative_to(R))]=sha(model/'command.json')
    evidence[str(B.relative_to(R))]=sha(B)
    evidence[str(Path(__file__).relative_to(R))]=sha(Path(__file__))
    (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
    save(O/'result.json',dict(passed=True,source_sha256=sha(O/'gpu_core.v'),exact_component_composition=True,all_24_orders_and_reversals=True,all_sequential_lines_unchanged=True,selected_configuration_lint_passed=True,added_registers=0,added_cycles=0,full_native_pending=True,quartus_invoked=False))
    (O/'REPORT.txt').write_text('PASS: exact queue-finality and light-prefix composition over e75ac93. Four disjoint literal hunks commute and reverse in all24 orders. Every sequential line remains unchanged; zero functional registers/cycles. Selected GPU configuration lint passes with inherited passive proof instrumentation. Component literal/protocol/replay proofs remain separately bound. Full combined native replay and physical timing are pending; no CAD or production action.\n')
    save(O/'manifest.json',dict(passed=True,native_pending=True,physical_pending=True,files={str(f.relative_to(O)):sha(f) for f in O.rglob('*') if f.is_file()},evidence_sha256=evidence))
    print('GPU',sha(O/'gpu_core.v')); print('MANIFEST',sha(O/'manifest.json'))


if __name__ == '__main__':
    assert sys.argv[1:] == ['run']; run()
