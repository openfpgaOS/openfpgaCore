#!/usr/bin/env python3
"""Remove only thirteen RAW upper-half qualifier preservation attributes."""
from pathlib import Path
import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
PARENT = ART / 'decode-second-skid-draft-v1/immediate/VexiiRiscv.v'
PARENT_SHA = 'f703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5'
NAMES = [f'aligner_raw_upper_sel_0_{n}' for n in range(7)] + [
    f'aligner_raw_upper_sel_1_{n}' for n in range(1, 7)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def declarations(name):
    return (f'  (* keep , syn_keep *) wire {name} /* synthesis syn_keep = 1 */;',
            f'  wire {name};')


def transform(old):
    actual = re.findall(r'^  \(\* keep , syn_keep \*\) wire '
                        r'(aligner_raw_upper_sel_\d_\d) /\* synthesis syn_keep = 1 \*/;$',
                        old, re.M)
    assert actual == NAMES, actual
    new = old
    for name in NAMES:
        before, after = declarations(name)
        assert old.count(before) == 1 and after not in old
        assert len(re.findall(r'\b' + name + r'\b', old)) == 3
        assert len(re.findall(r'^  assign ' + name + r' = ', old, re.M)) == 1
        new = new.replace(before, after, 1)
    restored = new
    for name in NAMES:
        before, after = declarations(name)
        assert restored.count(after) == 1
        restored = restored.replace(after, before, 1)
    assert restored == old
    # Normalizing these exact synthesis-only annotations is whole-source identity.
    normalized = old
    for name in NAMES:
        before, after = declarations(name)
        normalized = normalized.replace(before, after, 1)
    assert normalized == new
    return new


def run(command, log, **kwargs):
    with log.open('w') as stream:
        subprocess.run([str(x) for x in command], check=True, stdout=stream,
                       stderr=subprocess.STDOUT, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ART / 'decode-second-skid-raw-unkeep-v1/immediate')
    args = parser.parse_args()
    assert digest(PARENT) == PARENT_SHA
    old = PARENT.read_text()
    new = transform(old)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    target = out / 'VexiiRiscv.v'
    target.write_text(new)
    patch = out / 'raw-qualifier-unkeep.patch'
    patch.write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                              fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    replay = out / 'patch-replay'
    replay.mkdir()
    (replay / 'VexiiRiscv.v').write_text(new)
    for label, flags, expected in [('reverse', ['-R'], old), ('replay', [], new)]:
        run(['patch', '--batch', '--fuzz=0', *flags, '-p1', '-i', patch],
            out / (label + '.log'), cwd=replay)
        assert (replay / 'VexiiRiscv.v').read_text() == expected
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', target],
        out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',
                         (out / 'lint.log').read_text())
    shutil.copy2(__file__, out / Path(__file__).name)
    parent_qualification = PARENT.parent / 'qualification.json'
    assert json.loads(parent_qualification.read_text())['passed']
    manifest = {
        'passed': True,
        'parent_sha256': PARENT_SHA,
        'candidate_sha256': digest(target),
        'scope': 'Only KEEP/syn_keep attributes and synthesis comments on thirteen existing scalar wires.',
        'qualifiers': NAMES,
        'attribute_normalized_whole_source_identical': True,
        'exact_inverse_restores_parent': True,
        'zero_fuzz_patch_reverse_and_replay_passed': True,
        'full_netlist_lint_passed': True,
        'behavioral_equations_and_sequential_source_unchanged': True,
        'parent_qualification': str(parent_qualification),
        'parent_qualification_sha256': digest(parent_qualification),
        'script_sha256': digest(Path(__file__)),
        'patch_sha256': digest(patch),
        'physical_status': 'Not fitted; capacity and timing effects must be measured.',
        'semantic_limit': 'Synthesis mapping changes are allowed; RTL signal values and cycle behavior are identical for all inputs/states, including unknowns.'
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    recipe = {'script': str(Path(__file__).resolve()),
              'script_sha256': digest(Path(__file__)),
              'parent': str(PARENT), 'parent_sha256': PARENT_SHA,
              'candidate_sha256': digest(target),
              'reproduce': 'python3 tools/experiments/os30_dual_20261004/generator/unkeep_second_skid_raw.py --output <new-empty-output-directory>'}
    (out / 'recipe.json').write_text(json.dumps(recipe, indent=2) + '\n')
    print(json.dumps({'artifact': str(out), 'sha256': digest(target), 'passed': True}))


if __name__ == '__main__':
    main()
