#!/usr/bin/env python3
"""Independently bind final phase9 composition and each compiled source closure."""
import argparse
import json
import shlex
from pathlib import Path

from phase9_experiment import CAMP, HERE, INPUTS, PARENT, directory, module, sha, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['normal', 'diagnostic'], default='normal')
    args = parser.parse_args()
    candidate = directory('checks-state-f11')
    output = candidate / ('independent-composition-' + args.mode)
    output.mkdir(exist_ok=False)
    parent = json.loads((CAMP / 'phase7/QUALIFIED.json').read_text())
    composition = json.loads((candidate / 'composition.json').read_text())
    sources = {name: sha(candidate / name) for name in INPUTS}
    assert sources == composition['source_sha256']
    assert sources['gfx_pc.c'] == sha(directory('w-guard-v1') / 'gfx_pc.c')
    assert sources['gfx_gpu.c'] == sha(directory('direct-state-v1') / 'gfx_gpu.c')
    for name in set(INPUTS) - {'gfx_pc.c', 'gfx_gpu.c', 'perf_pocket.c'}:
        assert sources[name] == parent['source_sha256'][name], name
    old = (PARENT / 'perf_pocket.c').read_text()
    assert old.count('PERF F9 TRI') == 1
    assert old.replace('PERF F9 TRI', 'PERF F11 PREP') == (candidate / 'perf_pocket.c').read_text()
    expected = dict(parent['source_tree_sha256'])
    assert len(expected) == 6747
    for name, relative in INPUTS.items():
        expected[relative] = sources[name]
    stage = candidate / 'SM64'
    guards = module('phase9_read_only_inventory', HERE / 'phase2_publish.py')
    assert guards.inventory(stage) == set(expected)
    for relative, digest in expected.items():
        assert sha(stage / relative) == digest, relative
    changed = [relative for relative in expected if expected[relative] != parent['source_tree_sha256'][relative]]
    assert set(changed) == {INPUTS[name] for name in ('gfx_pc.c', 'gfx_gpu.c', 'perf_pocket.c')}
    build_path = candidate / ('build-' + args.mode + '.json')
    build = json.loads(build_path.read_text())
    assert build['returncode'] == 0 and build['diagnostic'] == (args.mode == 'diagnostic')
    assert {name: item['sha256'] for name, item in build['inputs'].items()} == sources
    elf = candidate / ('app.elf' if args.mode == 'normal' else 'diagnostic.elf')
    objects = stage / '.obj' / ('sm64' if args.mode == 'normal' else 'sm64-perfdiag')
    assert sha(elf) == sha(objects / 'app.elf') == build['elf_sha256']
    depfiles = sorted(objects.rglob('*.d'))
    assert len(depfiles) == 298
    dependencies = set()
    for depfile in depfiles:
        assert depfile.with_suffix('.o').is_file()
        first = depfile.read_text().replace('\\\n', ' ').splitlines()[0]
        for token in shlex.split(first.split(':', 1)[1]):
            dep = Path(token)
            if not dep.is_absolute():
                dep = stage / 'src/sm64' / dep
            relative = str(dep.resolve().relative_to(stage))
            assert relative in expected
            dependencies.add(relative)
    report = dict(passed=True, mode=args.mode, candidate='checks-state-f11',
        helper_sha256=sha(Path(__file__)), composition_sha256=sha(candidate / 'composition.json'),
        parent_qualified_sha256=sha(CAMP / 'phase7/QUALIFIED.json'),
        source_inputs=len(expected), changed_inputs=changed, all_other_source_inputs_unchanged=True,
        source_sha256=sources, source_tree_sha256=expected, dependency_files=len(depfiles),
        compiler_dependency_inputs=sorted(dependencies), all_compiler_dependencies_in_closed_tree=True,
        build_sha256=sha(build_path), elf_sha256=sha(elf),
        finite_input_contract=composition['numerical_contract'],
        scope='Exact composition and build-source closure only; numerical, output and performance evidence are separate. No production writes.')
    write(output / 'review.json', report)
    with (output / 'review-recipe.py').open('xb') as recipe:
        recipe.write(Path(__file__).read_bytes())
    print(json.dumps(dict(passed=True, mode=args.mode, inputs=len(expected), changed=changed,
                         compiler_dependency_inputs=len(dependencies)), indent=2))


if __name__ == '__main__':
    main()
