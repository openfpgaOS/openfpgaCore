#!/usr/bin/env python3
"""Prepare/check private os30 build integration; never publish or invoke Quartus."""
import collections
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
SELECTED = P / 'compact-lod5-core-v14'
CPU = P / 'cpu-os30-repro-v10'
PARENT = P / 'compact-lod5-core-v7'
COMPONENT = P / 'cpu-fetch-v7-v12'
OUT = P / 'normal-build-integration-v12'
TARGET = 'src/fpga/targets/pocket'
VENDOR = 'src/fpga/vendor/vexriscv'
CPU_PATH = VENDOR + '/VexiiRiscv/VexiiRiscv_os30.v'
KINDS = {'VERILOG_FILE', 'SYSTEMVERILOG_FILE', 'VHDL_FILE', 'QIP_FILE',
         'SDC_FILE', 'MIF_FILE', 'SOURCE_FILE', 'QSYS_FILE'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')


def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def replace_once(text, old, new):
    assert text.count(old) == 1, old[:100]
    return text.replace(old, new, 1)


def module_destination(name):
    path = Path(name)
    if name.startswith('src/fpga/common/'):
        return str(path.parent / 'os30' / path.name)
    if name in (TARGET + '/core_top.v', TARGET + '/io_sdram.v'):
        return TARGET + '/os30/' + path.name
    assert name == CPU_PATH
    return name


def prepare():
    prep = json.loads((SELECTED / 'prepared.json').read_text())
    parent = json.loads((PARENT / 'prepared.json').read_text())
    assert set(prep['source_sha256']) == set(parent['source_sha256'])
    changed_from_parent = [n for n in prep['source_sha256']
                           if prep['source_sha256'][n] != parent['source_sha256'][n]]
    assert changed_from_parent == [CPU_PATH], changed_from_parent
    for name, digest in parent['source_sha256'].items():
        assert sha(PARENT / 'frozen' / name) == digest, name
    assert (SELECTED / 'frozen' / TARGET / 'seeds/os30.seed').read_text().strip() == '2'
    cpu_prep = json.loads((CPU / 'prepared.json').read_text())
    assert cpu_prep['candidate_cpu_sha256'] == sha(COMPONENT / 'VexiiRiscv.v')
    assert sha(SELECTED / 'frozen' / CPU_PATH) == sha(COMPONENT / 'VexiiRiscv.v')
    for name, digest in prep['source_sha256'].items():
        assert sha(SELECTED / 'frozen' / name) == digest, name
    for name, digest in json.loads((CPU / 'manifest.json').read_text())['files'].items():
        assert sha(CPU / name) == digest, name
    changed = [name for name, digest in prep['source_sha256'].items()
               if not (ROOT / name).exists() or sha(ROOT / name) != digest]
    assert len(changed) == 19, changed
    rtl = [name for name in changed if name.endswith(('.v', '.sv'))]
    assert len(rtl) == 18 and TARGET + '/ap_core.qsf' in changed
    OUT.mkdir(exist_ok=False)
    baseline = OUT / 'baseline'
    # Read only production inputs. Missing new experiment modules are omitted.
    inputs = {name for name in prep['source_sha256'] if (ROOT / name).is_file()}
    inputs.update(str(p.relative_to(ROOT)) for p in (ROOT / VENDOR).glob('*.pl'))
    inputs.update(str(p.relative_to(ROOT)) for p in (ROOT / VENDOR).glob('*.sh'))
    inputs.update(str(p.relative_to(ROOT)) for p in (ROOT / VENDOR / 'configs').glob('*.cfg'))
    inputs.update(str(p.relative_to(ROOT)) for p in (ROOT / VENDOR / 'patches').glob('*.patch'))
    inputs.update([VENDOR + '/VexiiRiscv/build.sbt',
                   VENDOR + '/VexiiRiscv/ext/SpinalHDL/build.sbt',
                   'tools/sweep_target_matrix.py', 'tools/prepare_mister_variant.py',
                   'tools/netlist_hash.sh', 'tools/tests/test_pocket_variant_timing.py'])
    inputs.update(str(p.relative_to(CPU / 'proposed'))
                  for p in (CPU / 'proposed').rglob('*')
                  if p.is_file() and (ROOT / p.relative_to(CPU / 'proposed')).exists())
    for name in sorted(inputs):
        copy(ROOT / name, baseline / name)
    retained = {name: sha(ROOT / name) for name in sorted(inputs)}
    save(OUT / 'production-before.json', retained)
    proposed = OUT / 'proposed'
    inventory = []
    overrides = {}
    for name in changed:
        row = dict(selected_path=name, selected_sha256=prep['source_sha256'][name],
                   production_sha256=retained.get(name), new=not (ROOT / name).exists())
        if name in rtl:
            dest = module_destination(name)
            row['proposed_path'] = dest
            copy(SELECTED / 'frozen' / name, proposed / dest)
            if not row['new'] and name != CPU_PATH:
                old = os.path.relpath(name, TARGET)
                overrides[old] = os.path.relpath(dest, TARGET)
        else:
            row['replacement'] = 'Variant source map, os30.qsf and os30.mk; master QSF unchanged.'
        inventory.append(row)
    assert len(overrides) == 6
    # Compose the separately qualified CPU configuration/freshness changes.
    for p in sorted((CPU / 'proposed').rglob('*')):
        if p.is_file():
            copy(p, proposed / p.relative_to(CPU / 'proposed'))
    makefile = proposed / TARGET / 'Makefile'
    text = makefile.read_text()
    text = replace_once(text,
        'NETLIST_EXTRA_FILES := $(VARIANT_QSF) $(wildcard variants/$(VARIANT).sdc)',
        '# Optional exact-source snapshots for one variant; other variants retain the master.\n'
        'VARIANT_SOURCE_MAP := $(wildcard variants/$(VARIANT).sources)\n'
        'NETLIST_EXTRA_FILES := $(VARIANT_QSF) $(VARIANT_SOURCE_MAP) $(wildcard variants/$(VARIANT).sdc)')
    text = replace_once(text,
        'variants/$(VARIANT).mk $(VARIANT_QSF) Makefile | $(VEXII_OUT)',
        'variants/$(VARIANT).mk $(VARIANT_QSF) $(VARIANT_SOURCE_MAP) Makefile | $(VEXII_OUT)')
    text = replace_once(text, '\t@awk -v base="$(CURDIR)" \'\\\n',
        '\t@awk -v base="$(CURDIR)" \'\\\n'
        '\t  FILENAME ~ /\\.sources$$/ { \\\n'
        '\t    if (!NF || $$1 ~ /^#/) next; \\\n'
        '\t    if (NF != 2 || $$1 in overrides) { print "Invalid source override" > "/dev/stderr"; exit 1 } \\\n'
        '\t    overrides[$$1] = $$2; next } \\\n')
    text = replace_once(text,
        '\t    if ($$4 !~ /^\\//) $$4 = base "/" $$4; print; next } \\\n',
        '\t    if (($$3=="VERILOG_FILE"||$$3=="SYSTEMVERILOG_FILE") && $$4 in overrides) { \\\n'
        '\t      used[$$4]++; $$4 = overrides[$$4] } \\\n'
        '\t    if ($$4 !~ /^\\//) $$4 = base "/" $$4; print; next } \\\n')
    text = replace_once(text,
        '\t  { print }\' ap_core.qsf $(VARIANT_QSF) > $(BDIR)/ap_core.qsf',
        '\t  { print } \\\n'
        '\t  END { for (name in overrides) if (used[name] != 1) { \\\n'
        '\t    print "Source override must match once: " name > "/dev/stderr"; exit 1 } }\' \\\n'
        '\t  $(VARIANT_SOURCE_MAP) ap_core.qsf $(VARIANT_QSF) > $(BDIR)/ap_core.qsf')
    # A rejected map must not leave a partial project that the next make can
    # mistake for an up-to-date QSF. Publish only the completed project.
    start = text.index('$(BDIR)/ap_core.qsf:')
    end = text.index('\n# Variant-named (non-reversed)', start)
    block = text[start:end]
    block = block.replace('> $(BDIR)/ap_core.qsf', '> $(BDIR)/ap_core.qsf.tmp')
    block = replace_once(block,
        '\t  $(VARIANT_SOURCE_MAP) ap_core.qsf $(VARIANT_QSF) > $(BDIR)/ap_core.qsf.tmp',
        '\t  $(VARIANT_SOURCE_MAP) ap_core.qsf $(VARIANT_QSF) > $(BDIR)/ap_core.qsf.tmp || \\\n'
        '\t  { rm -f $(BDIR)/ap_core.qsf.tmp; exit 1; }')
    block = replace_once(block, '\t@cp ap_core.qpf $(BDIR)/ap_core.qpf\n',
        '\t@cp ap_core.qpf $(BDIR)/ap_core.qpf\n'
        '\t@mv $(BDIR)/ap_core.qsf.tmp $(BDIR)/ap_core.qsf\n')
    text = text[:start] + block + text[end:]
    makefile.write_text(text)
    source_map = proposed / TARGET / 'variants/os30.sources'
    source_map.parent.mkdir(parents=True, exist_ok=True)
    source_map.write_text('# Exact compact-v14 candidate snapshots; relative to the Pocket target.\n'
                          '# These are os30-only copies, pending future common-module consolidation.\n' +
                          ''.join(f'{old} {new}\n' for old, new in sorted(overrides.items())))
    variant = proposed / TARGET / 'variants/os30.mk'
    variant.write_text((baseline / TARGET / 'variants/os30.mk').read_text() +
        '\n# Exact compact-v14 same-triangle span lookahead (private integration candidate).\n'
        'DEFS += INCLUDE_SPAN_LOOKAHEAD\n')
    extra = proposed / TARGET / 'variants/os30.qsf'
    selected_qsf = (SELECTED / 'frozen' / TARGET / 'ap_core.qsf').read_text()
    master = (baseline / TARGET / 'ap_core.qsf').read_text()
    assert selected_qsf.startswith(master)
    additions = selected_qsf[len(master):]
    additions = additions.replace('set_global_assignment -name VERILOG_MACRO INCLUDE_SPAN_LOOKAHEAD\n', '')
    for name in rtl:
        if name != CPU_PATH:
            additions = additions.replace(os.path.relpath(name, TARGET),
                                          os.path.relpath(module_destination(name), TARGET))
    additions = additions.replace('# Private LOD4 area screen; original CPU retains speed-oriented synthesis.',
                                   '# Exact selected compact-v14 settings; no timing-constraint changes.')
    additions = additions.replace('# Private LOD5 + exact span lookahead; retained CPU, clocks and seed.',
                                   '# LOD5 shares the full-capacity CRAM texture bus.')
    extra.write_text('# os30-only candidate sources/settings. Common files and other variants are unchanged.\n' + additions)
    # Frozen projects must resolve the same os30 source map as normal make.
    sweep = (baseline / 'tools/sweep_target_matrix.py').read_text()
    anchor = '\ndef prepare(frozen, dest, row, seed, processors):\n'
    function = '''
def pocket_source_overrides(text, source_map):
    """Select an optional variant snapshot without changing other variants."""
    if not source_map.is_file():
        return text
    overrides = {}
    for line in source_map.read_text().splitlines():
        fields = line.split()
        if not fields or fields[0].startswith('#'):
            continue
        if len(fields) != 2 or fields[0] in overrides:
            raise ValueError('Invalid source override: ' + line)
        overrides[fields[0]] = fields[1]
    for old, new in overrides.items():
        pattern = r'^(set_global_assignment -name (?:VERILOG_FILE|SYSTEMVERILOG_FILE) )' + re.escape(old) + r'$'
        text, count = re.subn(pattern, lambda match: match[1] + new, text, flags=re.M)
        if count != 1:
            raise ValueError('Source override must match once: ' + old)
    return text

'''
    sweep = replace_once(sweep, anchor, '\n' + function + anchor)
    sweep = replace_once(sweep,
        "    legacy_selector = row['target'] == 'mister' and 'source cpu_select.tcl' in text",
        "    if row['target'] == 'pocket':\n"
        "        text = pocket_source_overrides(text, directory / 'variants' / (row['variant'] + '.sources'))\n"
        "    legacy_selector = row['target'] == 'mister' and 'source cpu_select.tcl' in text")
    (proposed / 'tools/sweep_target_matrix.py').write_text(sweep)
    stage = OUT / 'stage'
    shutil.copytree(baseline, stage)
    shutil.copytree(proposed, stage, dirs_exist_ok=True)
    patch = ''
    for p in sorted(proposed.rglob('*')):
        if not p.is_file():
            continue
        name = str(p.relative_to(proposed))
        old = ROOT / name
        patch += ''.join(difflib.unified_diff(
            old.read_text().splitlines(True) if old.exists() else [],
            p.read_text().splitlines(True), fromfile='a/' + name if old.exists() else '/dev/null',
            tofile='b/' + name))
    (OUT / 'candidate.patch').write_text(patch)
    copy(Path(__file__), OUT / 'recipe.py')
    save(OUT / 'prepared.json', dict(scope=__doc__, selected_prepared_sha256=sha(SELECTED / 'prepared.json'),
        cpu_manifest_sha256=sha(CPU / 'manifest.json'), inventory=inventory, overrides=overrides,
        proposed_sha256={str(p.relative_to(proposed)): sha(p) for p in sorted(proposed.rglob('*')) if p.is_file()},
        stage_sha256={str(p.relative_to(stage)): sha(p) for p in sorted(stage.rglob('*')) if p.is_file()},
        recipe_sha256=sha(Path(__file__)), deployment_ready=False, quartus_invoked=False,
        parent_prepared_sha256=sha(PARENT / 'prepared.json'),
        only_selected_parent_change=CPU_PATH,
        component_manifest_sha256=sha(COMPONENT / 'manifest.json'),
        saved_seed=2, effective_seed=2, seed_metadata_changed=False))
    print(OUT)


def qsf_summary(qsf, tree, reverse=None):
    """Read assignment files only, including QIP source references; do not run Tcl/SDC."""
    reverse = reverse or {}
    records, settings, macros, visited = [], [], [], set()
    def path_name(path):
        name = str(path.resolve().relative_to(tree.resolve()))
        return reverse.get(name, name)
    def read(path, qip=False):
        if path.resolve() in visited:
            return
        visited.add(path.resolve())
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Quartus QIPs use a single file-join expression for local paths.
            pattern = r'\[file join \$::quartus\(qip_path\) (?:"([^"]+)"|([^\s\]]+))\]'
            line = re.sub(pattern, lambda m: shlex.quote(str(path.parent / (m[1] or m[2]))), line)
            fields = shlex.split(line)
            if fields[:2] == ['set_global_assignment', '-name']:
                kind = fields[2]
                if kind in KINDS:
                    assert len(fields) == 4, (path, fields)
                    src = Path(fields[3])
                    if not src.is_absolute():
                        src = path.parent / src
                    missing_cpu = not src.exists() and src.name in ('VexiiRiscv_os20.v', 'VexiiRiscv_os25.v')
                    assert src.is_file() or missing_cpu, src
                    records.append(dict(kind=kind, path=path_name(src),
                                        sha256=None if missing_cpu else sha(src)))
                    if kind == 'QIP_FILE':
                        read(src, True)
                elif not qip:
                    if kind == 'VERILOG_MACRO':
                        macros.append(fields[3])
                    elif kind != 'SEARCH_PATH':
                        settings.append(fields)
            elif fields and fields[0] in ('set_instance_assignment', 'set_location_assignment') and not qip:
                settings.append(fields)
            else:
                assert fields[0] in ('set_global_assignment', 'set_instance_assignment', 'set_location_assignment'), (path, fields)
    read(qsf)
    # QIP hashes and source-content identity are preserved. Assignment order
    # among independent source files/macros does not alter these definitions.
    return dict(sources=sorted(records, key=lambda row: (row['path'], row['kind'])),
                macros=sorted(macros), settings=sorted(settings),
                implicit_initialization={n:sha(tree / TARGET / n)
                                         for n in ('firmware.mif', 'apf/build_id.mif')})


def run_make(tree, variant, job, log, expect=0):
    target = tree / TARGET
    cpu = '../../vendor/vexriscv/VexiiRiscv/VexiiRiscv_' + variant + '.v'
    command = ['make', '-s', '--no-print-directory', f'VARIANT={variant}', f'JOB={job}',
               'QPROCS=4', '-o', cpu, f'bld/{job}/ap_core.qsf']
    result = subprocess.run(command, cwd=target, text=True, capture_output=True)
    log.write_text('COMMAND ' + shlex.join(command) + '\n' + result.stdout + result.stderr)
    assert result.returncode == expect, result.stderr
    return target / f'bld/{job}/ap_core.qsf'


def check():
    prep = json.loads((OUT / 'prepared.json').read_text())
    assert prep['recipe_sha256'] == sha(Path(__file__))
    assert prep['selected_prepared_sha256'] == sha(SELECTED / 'prepared.json')
    assert prep['cpu_manifest_sha256'] == sha(CPU / 'manifest.json')
    assert prep['parent_prepared_sha256'] == sha(PARENT / 'prepared.json')
    assert prep['component_manifest_sha256'] == sha(COMPONENT / 'manifest.json')
    assert prep['saved_seed'] == prep['effective_seed'] == 2
    assert prep['seed_metadata_changed'] is False
    assert not any('/seeds/' in n for n in prep['proposed_sha256'])
    for n, h in prep['proposed_sha256'].items():
        assert sha(OUT / 'proposed' / n) == h, n
    stage = OUT / 'stage'
    for n, h in prep['stage_sha256'].items():
        assert sha(stage / n) == h, n
    checks = OUT / 'checks'
    checks.mkdir(exist_ok=False)
    reverse = {row['proposed_path']: row['selected_path'] for row in prep['inventory'] if 'proposed_path' in row}
    fit_qsf = SELECTED / 'map-core/job' / TARGET / 'ap_core.qsf'
    fit_tree = SELECTED / 'map-core/job'
    selected_map = json.loads((SELECTED / 'map-core/invocation.json').read_text())
    assert selected_map['seed'] == 2 and selected_map['fitter_invoked'] is False
    assert json.loads((SELECTED / 'map-core/result.json').read_text())['passed_map']
    assert sha(fit_qsf) == selected_map['qsf_sha256']
    expected = qsf_summary(fit_qsf, fit_tree)
    save(checks / 'selected-binding.json', dict(
        selected_prepared_sha256=sha(SELECTED / 'prepared.json'),
        map_invocation_sha256=sha(SELECTED / 'map-core/invocation.json'),
        map_result_sha256=sha(SELECTED / 'map-core/result.json'),
        mapped_qsf_sha256=sha(fit_qsf), seed=2, fitter_invoked=False))
    save(checks / 'selected-map-closure.json', expected)
    outputs = {}
    for variant in ('os20', 'os25', 'os30'):
        qsf = run_make(stage, variant, variant, checks / f'{variant}-make.log')
        got = qsf_summary(qsf, stage, reverse)
        save(checks / f'{variant}-normal-closure.json', got)
        if variant == 'os30':
            wanted = expected
        else:
            old = run_make(OUT / 'baseline', variant, variant, checks / f'{variant}-baseline.log')
            wanted = qsf_summary(old, OUT / 'baseline')
            save(checks / f'{variant}-baseline-closure.json', wanted)
        assert got == wanted, f'{variant}: generated source/define/constraint closure differs'
        outputs[variant] = dict(exact_match=True, sources=len(got['sources']), macros=len(got['macros']),
                                missing_generated_cpu=variant != 'os30')
        # Clean only this newly-created private QSF and exercise regeneration.
        before = qsf.read_bytes()
        qsf.unlink()
        again = run_make(stage, variant, variant, checks / f'{variant}-regenerate.log')
        assert again.read_bytes() == before
    # Independently run the frozen-project generator (no synthesis).
    sys.path.insert(0, str(stage / 'tools'))
    spec = importlib.util.spec_from_file_location('private_sweep', stage / 'tools/sweep_target_matrix.py')
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    for variant in ('os20', 'os25', 'os30'):
        normal = json.loads((checks / f'{variant}-normal-closure.json').read_text())
        seed = int((stage / TARGET / f'seeds/{variant}.seed').read_text().strip())
        # Avoid copying the generated bld trees into each frozen fixture.
        dest = checks / ('frozen-' + variant)
        freeze = checks / 'input'
        if not freeze.exists():
            shutil.copytree(stage, freeze, ignore=shutil.ignore_patterns('bld', '__pycache__'))
        directory, project = sweep.prepare(freeze, dest,
            dict(target='pocket', variant=variant, defs=' '.join(normal['macros'])), seed, 4)
        got = qsf_summary(directory / (project + '.qsf'), dest, reverse)
        assert got == normal, f'{variant}: normal/frozen generators differ'
        save(checks / f'{variant}-frozen-closure.json', got)
    # Rejection tests prevent stale/duplicate maps from silently selecting a
    # different design. These are scratch copies, not the proposed snapshots.
    negative = checks / 'negative'
    shutil.copytree(stage, negative, ignore=shutil.ignore_patterns('bld', '__pycache__'))
    source_map = negative / TARGET / 'variants/os30.sources'
    original = source_map.read_text()
    negative_results = {}
    for label, body in (
        ('missing', original + 'missing.v os30/missing.v\n'),
        ('duplicate', original + next(line for line in original.splitlines() if not line.startswith('#')) + '\n'),
        ('malformed', original + 'bad.v\n')):
        source_map.write_text(body)
        rejected = run_make(negative, 'os30', label, checks / f'negative-{label}.log', expect=2)
        assert not rejected.exists() and not rejected.with_suffix('.qsf.tmp').exists()
        try:
            sweep.pocket_source_overrides((negative / TARGET / 'ap_core.qsf').read_text(), source_map)
        except ValueError:
            negative_results[label] = True
        else:
            raise AssertionError(label)
    source_map.write_text(original)
    # Existing normal-build tests check that the composed Makefile still
    # honors CPU freshness and per-variant timing fragments.
    tests = ['tools/tests/test_pocket_variant_timing.py',
             'tools/tests/test_pocket_cpu_freshness.py', 'tools/tests/test_vexii_publish.py']
    test_results = {}
    for name in tests:
        result = subprocess.run([sys.executable, name], cwd=stage, text=True, capture_output=True)
        (checks / (Path(name).stem + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
        test_results[name] = True
    for n, h in json.loads((OUT / 'production-before.json').read_text()).items():
        assert sha(ROOT / n) == h, n
    save(OUT / 'result.json', dict(passed=True, quartus_invoked=False, deployment_ready=False,
        selected_map_source_exact=True, other_variants_unchanged=True, variants=outputs,
        normal_and_frozen_generators_agree=True, negative_map_tests=negative_results,
        existing_tests=test_results, production_inputs_unchanged=True,
        prepared_sha256=sha(OUT / 'prepared.json'),
        limits=['QSF generation uses make -o for CPU netlists; actual CPU regeneration is a separate bound proof.',
                'os20/os25 generated netlists are absent in this workspace; unchanged references are checked, not regenerated.',
                'Source-content, defines, all non-search-path assignments and SDC/QIP/MIF bytes match; physical file paths differ.',
                'Selected snapshots deliberately duplicate shared modules only for os30. No deployment or new fit is performed.']))
    print('PASS: exact selected os30 closure; os20/os25 unchanged; normal/frozen generation agrees')


def seal():
    result = json.loads((OUT / 'result.json').read_text())
    assert result['passed'] and not result['quartus_invoked']
    regeneration = P / 'cpu-os30-regenerate-v10/manifest.json'
    for name, digest in json.loads(regeneration.read_text()).items():
        assert sha(regeneration.parent / name) == digest, name
    actual = json.loads((regeneration.parent / 'result.json').read_text())
    assert actual['passed'] and actual['exact_match'] and actual['production_unchanged']
    assert actual['returncode'] == 0
    assert actual['output_sha256'] == sha(SELECTED / 'frozen' / CPU_PATH)
    assert sha(OUT / 'proposed' / CPU_PATH) == actual['output_sha256']
    save(OUT / 'cpu-regeneration-binding.json', dict(manifest_path=str(regeneration.relative_to(ROOT)),
        manifest_sha256=sha(regeneration), selected_cpu_sha256=sha(SELECTED / 'frozen' / CPU_PATH),
        scope='Actual private Scala generation and complete helper chain; see the bound regeneration manifest.'))
    (OUT / 'REPORT.txt').write_text(
        'Private normal-build integration of compact v14\n\n'
        'Candidate is exact compact v7 plus the final fetch-ready factor only. Seed remains 2; no seed metadata is proposed. '
        'GPU/cache/audio/wrapper/SDRAM snapshots remain exactly v7.\n\n'
        'PASS: clean os30 QSF generation resolves to the exact selected map source content, defines, settings, '
        'constraints and initialization images. Normal and frozen-project generators agree. os20/os25 source '
        'closures and settings are unchanged. No production files or Quartus outputs were modified.\n\n'
        'The selected tree had 19 differences: 18 RTL/netlist files and the experimental master QSF. '
        'The six changed shared/top modules are selected through variants/os30.sources; their exact snapshots '
        'including the os30-only io_sdram snapshot and the eleven new geometry files live in os30 directories. The CPU remains its existing per-variant path. '
        'These duplicate modules preserve the reviewed candidate and are not a new general architecture. '
        'The master QSF is unchanged; new sources and AREA settings are in variants/os30.qsf, and span lookahead '
        'is in os30.mk. The previously qualified CPU generator/config/freshness proposal is composed separately.\n\n'
        'Validation regenerates each private QSF, compares recursive source/QIP/SDC content, checks seed and '
        'processor settings, and rejects missing/duplicate/malformed source maps in both generators. Existing '
        'variant-timing, CPU-freshness and atomic CPU-publish tests pass. CPU generation is bypassed for this '
        'QSF-only test; separately bound actual Scala generation reproduces the exact selected CPU. The absent os20/os25 netlists remain '
        'unchanged references, not newly tested CPU implementations.\n\n'
        'No fitter or deployment is authorized by this integration report. The physical clock requirement remains a '
        'separate gate; a source-equivalent QSF is not timing closure.\n')
    evidence = {str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*'))
                if p.is_file() and '__pycache__' not in p.parts and p.name != 'manifest.json'}
    save(OUT / 'manifest.json', evidence)
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    assert sys.argv[1:] in (['prepare'], ['check'], ['seal'])
    {'prepare':prepare, 'check':check, 'seal':seal}[sys.argv[1]]()
