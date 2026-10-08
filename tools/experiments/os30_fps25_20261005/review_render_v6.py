#!/usr/bin/env python3
"""Bind the label-only revision to reviewed v5 inputs, objects, and ELF bytes."""
import hashlib
import json
from pathlib import Path
import shlex
import struct
import subprocess

from review_render_v5 import CAMPAIGN, STAGE, OUT, SOURCES, INPUTS

CANDIDATE = CAMPAIGN / 'render-v6'
PARENT = CAMPAIGN / 'render-v5'
OLD = b'LEVEL %u CPU %u MHZ'
NEW = b'LVL %u CPU %u MHZ  '


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def section_at(data, offset, length):
    assert data[:6] == b'\x7fELF\x01\x01'
    header = struct.unpack_from('<16sHHIIIIIHHHHHH', data)
    start, stride, count, strings = header[6], header[11], header[12], header[13]
    sections = [struct.unpack_from('<IIIIIIIIII', data, start + i * stride)
                for i in range(count)]
    strsec = sections[strings]
    names = data[strsec[4]:strsec[4] + strsec[5]]
    found = [s for s in sections if s[1] != 8 and s[4] <= offset and
             offset + length <= s[4] + s[5]]
    assert len(found) == 1
    s = found[0]
    name = names[s[0]:].split(b'\0', 1)[0].decode()
    return {'name': name, 'flags': s[2], 'offset': offset,
            'virtual_address': s[3] + offset - s[4]}


def main():
    previous_path = OUT / 'render-v5-source-abi.json'
    previous = json.loads(previous_path.read_text())
    assert previous['source_review_passed'] and not previous['runtime_qualified']
    tree5_path = OUT / 'render-v5-stage-inputs.json'
    assert sha(tree5_path) == previous['stage_input_closure']['manifest_sha256']
    tree5 = json.loads(tree5_path.read_text())
    baseline_path = CAMPAIGN / 'source-baseline.json'
    assert sha(baseline_path) == tree5['source_baseline_sha256']
    baseline = json.loads(baseline_path.read_text())['files']

    source_proof = {}
    for name, relative in SOURCES.items():
        old, new = (PARENT / name).read_bytes(), (CANDIDATE / name).read_bytes()
        assert hashlib.sha256(old).hexdigest() == tree5['files'][relative]
        if name == 'perf_pocket.c':
            assert len(OLD) == len(NEW) and old.count(OLD) == 1
            assert old.replace(OLD, NEW) == new
        else:
            assert old == new, name
        source_proof[name] = {'parent_sha256': sha(PARENT / name),
                              'source_sha256': sha(CANDIDATE / name),
                              'change': 'one equal-length format string' if old != new else 'unchanged'}

    expected = dict(baseline)
    for name, relative in SOURCES.items():
        expected[relative] = sha(CANDIDATE / name)
    current = {str(p.relative_to(STAGE)): p for p in STAGE.rglob('*') if p.is_file()}
    assert not set(baseline) - set(current), 'Missing frozen inputs'
    observed = {p: sha(current[p]) for p in baseline}
    assert observed == expected, 'Unexpected stage source change'
    generated = set(current) - set(baseline)
    assert generated == set(tree5['allowed_generated_outputs']), 'Unexpected generated paths'
    generated_hashes = {p: sha(current[p]) for p in sorted(generated)}
    output_changes = {p: {'before': tree5['allowed_generated_outputs'][p], 'after': generated_hashes[p]}
                      for p in generated if tree5['allowed_generated_outputs'][p] != generated_hashes[p]}
    assert set(output_changes) == {'.obj/sm64-perfdiag/app.elf',
                                   '.obj/sm64-perfdiag/pocket/perf_pocket.o'}

    builds = {}
    for mode, diagnostic in [('normal', False), ('diagnostic', True)]:
        record_path = CANDIDATE / f'build-{mode}.json'
        record = json.loads(record_path.read_text())
        assert record['returncode'] == 0 and record['diagnostic'] == diagnostic
        assert set(record['inputs']) == set(INPUTS)
        for name, relative in INPUTS.items():
            assert record['inputs'][name]['sha256'] == expected[relative]
        elf = Path(record['elf'])
        assert elf.parent == CANDIDATE and sha(elf) == record['elf_sha256']
        parent_elf = PARENT / ('diagnostic.elf' if diagnostic else 'app.elf')
        assert sha(parent_elf) == previous['build_bindings'][mode]['elf_sha256']
        before, after = parent_elf.read_bytes(), elf.read_bytes()
        objroot = '.obj/sm64-perfdiag' if diagnostic else '.obj/sm64'
        assert sha(STAGE / objroot / 'app.elf') == sha(elf)
        log = CANDIDATE / f'build-{mode}.log'
        commands = [shlex.split(line) for line in log.read_text().splitlines()
                    if line.startswith('riscv64-unknown-elf-gcc ') and ' -c ' in line]
        assert len(commands) == 1
        args = commands[0]
        assert args[-1] == 'pocket/perf_pocket.c'
        assert '-march=rv32imafc' in args and '-mabi=ilp32f' in args
        assert [x for x in args if x.startswith('-mtune=')] == ['-mtune=sifive-5-series']
        assert Path(args[args.index('-o') + 1]) == STAGE / objroot / 'pocket/perf_pocket.o'
        assert f'-DSM64_PERF_DIAG={int(diagnostic)}' in args
        binding = {'record_sha256': sha(record_path), 'log_sha256': sha(log),
                   'elf_sha256': sha(elf), 'parent_elf_sha256': sha(parent_elf),
                   'all7_input_hashes': {n: x['sha256'] for n, x in record['inputs'].items()},
                   'compiled_translation_units': 1,
                   'remaining_objects_exact_parent_hashes': True,
                   'isa': 'rv32imafc', 'abi': 'ilp32f', 'tune': 'sifive-5-series'}
        if diagnostic:
            assert before.count(OLD) == after.count(NEW) == 1
            assert before.replace(OLD, NEW) == after
            objdata = (STAGE / objroot / 'pocket/perf_pocket.o').read_bytes()
            assert objdata.count(NEW) == 1
            objsection = section_at(objdata, objdata.index(NEW), len(NEW))
            assert objsection['name'].startswith('.rodata') and not (objsection['flags'] & 4)
            binding['elf_change'] = {'sole_equal_length_label_replacement': True,
                                    'file_size': len(after), 'string_length': len(OLD),
                                    'changed_byte_count': sum(a != b for a, b in zip(before, after)),
                                    'section': section_at(after, before.index(OLD), len(OLD)),
                                    'input_object_string_section': objsection,
                                    'section_note': 'Unchanged SDK linker script merges read-only strings into the executable output .text section.',
                                    'old': OLD.decode(), 'new': NEW.decode(),
                                    'all_other_elf_bytes_identical': True}
            nm = subprocess.check_output(['/usr/bin/llvm-nm', '-S', str(elf)], text=True)
            symbol = [l.split() for l in nm.splitlines() if l.endswith(' sm64_perf_snapshot')]
            assert len(symbol) == 1 and int(symbol[0][1], 16) == 156
            binding['snapshot_target_size_verified'] = 156
        else:
            assert before == after
            binding['elf_byte_identical_to_parent'] = True
        builds[mode] = binding

    changes = {p: {'before': baseline[p], 'after': observed[p]}
               for p in baseline if baseline[p] != observed[p]}
    assert set(changes) == set(SOURCES.values())
    sdk = [p for p in baseline if p.startswith('src/sdk/')]
    assert all(observed[p] == baseline[p] for p in sdk)
    stage_path = OUT / 'render-v6-stage-inputs.json'
    stage_path.write_text(json.dumps({'source_baseline_sha256': sha(baseline_path),
                                     'files': observed, 'allowed_generated_outputs': generated_hashes}, indent=2) + '\n')
    closure = dict(previous['stage_input_closure'])
    closure['manifest_sha256'] = sha(stage_path)
    closure['dependency_closure_inherited_after_exact_all_dependency_file_comparison'] = True
    result = {'candidate': 'render-v6', 'source_review_passed': True, 'runtime_qualified': False,
              'script_sha256': sha(Path(__file__)), 'prior_source_review_sha256': sha(previous_path),
              'source_proof': source_proof, 'source_changes': changes,
              'stage_input_closure': closure, 'build_bindings': builds,
              'generated_output_changes_from_v5': output_changes,
              'publication_scope': previous['publication_scope'],
              'limits': previous['limits'] + ['Label layout smoke and final runtime replay qualification remain separate evidence.']}
    (OUT / 'render-v6-source-abi.json').write_text(json.dumps(result, indent=2) + '\n')
    scope = (OUT / 'render-v5-source-scope.txt').read_text()
    scope = scope.replace('Render-v5', 'Render-v6')
    scope = scope.replace('Both modes rebuilt every one of their 298 translation units with -mtune=sifive-5-series.',
                          'Both modes inherit all 298 single-issue scheduled v5 units; v6 rebuilt perf_pocket.c only.\nAll remaining object and dependency hashes match v5 exactly.\nNormal ELF is byte-identical to v5. F4 ELF differs only in the equal-length panel label.\nPanel label: LEVEL %u CPU %u MHZ -> LVL %u CPU %u MHZ plus two trailing spaces.')
    for mode in builds:
        scope = scope.replace(previous['build_bindings'][mode]['elf_sha256'], builds[mode]['elf_sha256'])
    (OUT / 'render-v6-source-scope.txt').write_text(scope)
    print(json.dumps({'source_review_passed': True, 'stage_input_closure': closure,
                      'build_bindings': builds}, indent=2))


if __name__ == '__main__':
    main()
