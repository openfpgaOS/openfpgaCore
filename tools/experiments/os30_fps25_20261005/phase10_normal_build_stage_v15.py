#!/usr/bin/env python3
"""Private unchanged-v7 MAX_FANOUT4 normal-build closure; no fitter or publication."""
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
SELECTED = P / 'compact-lod5-v7-lanefan4-v1'
PARENT_NORMAL = P / 'normal-build-integration-v4'
FANOUT = 'set_instance_assignment -name MAX_FANOUT 4 -to ic|cpu|cpu|execute_ctrl3_up_LANE_SEL_lane0'
VARIANT_QSF = 'src/fpga/targets/pocket/variants/os30.qsf'
SEED_PATH = TARGET_SEED = 'src/fpga/targets/pocket/seeds/os30.seed'
CPU = P / 'cpu-os30-repro-v5'
OUT = P / 'normal-build-integration-v15'
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
    source_map.write_text('# Exact compact-v7 candidate snapshots; relative to the Pocket target.\n'
                          '# These are os30-only copies, pending future common-module consolidation.\n' +
                          ''.join(f'{old} {new}\n' for old, new in sorted(overrides.items())))
    variant = proposed / TARGET / 'variants/os30.mk'
    variant.write_text((baseline / TARGET / 'variants/os30.mk').read_text() +
        '\n# Exact compact-v7 same-triangle span lookahead (private integration candidate).\n'
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
                                   '# Exact selected compact-v7 settings; no timing-constraint changes.')
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
    # Reproduce the qualified normal-v4 proposal exactly before adding the one
    # os30-only fanout setting. Saved seed2 remains unchanged; refresh only its
    # source fingerprint because the variant QSF is part of that identity.
    parent = json.loads((PARENT_NORMAL / 'prepared.json').read_text())
    for name, digest in json.loads((PARENT_NORMAL / 'manifest.json').read_text()).items():
        assert sha(PARENT_NORMAL / name) == digest, name
    proposal_before_seed = {str(p.relative_to(proposed)):sha(p)
                           for p in sorted(proposed.rglob('*')) if p.is_file()}
    assert proposal_before_seed == parent['proposed_sha256']
    assert len(proposal_before_seed) == 29
    assert int((SELECTED / 'frozen' / SEED_PATH).read_text()) == 2
    assert prep['seed'] == prep['saved_seed'] == 2
    assert prep['effective_job_qsf_addition'] == FANOUT
    assert prep['rtl_byte_identical_to_parent']
    # Keep saved seed2 untouched; only the os30 setting and its source fingerprint change.
    with extra.open('a') as stream:
        stream.write('\n' + FANOUT + '\n')
    stage = OUT / 'stage'
    shutil.copytree(baseline, stage)
    shutil.copytree(proposed, stage, dirs_exist_ok=True)
    scratch = OUT / 'seed-fingerprint' / 'tree'
    shutil.copytree(stage, scratch)
    scratch_qsf = run_make(scratch, 'os30', 'seed-fingerprint',
                           OUT / 'seed-fingerprint' / 'make.log')
    fingerprint, command = seed_fingerprint(scratch, scratch_qsf)
    assert re.fullmatch(r'[0-9a-f]{32}', fingerprint)
    (proposed / SEED_PATH).parent.mkdir(parents=True, exist_ok=True)
    (proposed / (SEED_PATH + '.src')).write_text(fingerprint + '\n')
    copy(proposed / (SEED_PATH + '.src'), stage / (SEED_PATH + '.src'))
    assert len([p for p in proposed.rglob('*') if p.is_file()]) == 30
    save(OUT / 'seed-fingerprint' / 'result.json', dict(
        passed=True, command=command, fingerprint=fingerprint,
        generated_qsf_sha256=sha(scratch_qsf), effective_seed=2,
        inherited_frozen_seed=2, changes_production_seed=False,
        source=f'{SEED_PATH}.src',
        parent_normal_manifest_sha256=sha(PARENT_NORMAL / 'manifest.json')))
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
    save(OUT / 'prepared.json', dict(scope=__doc__, sole_extra_instance_assignment=FANOUT, saved_seed_number_unchanged=True, selected_prepared_sha256=sha(SELECTED / 'prepared.json'),
        cpu_manifest_sha256=sha(CPU / 'manifest.json'), inventory=inventory, overrides=overrides,
        parent_normal_manifest_sha256=sha(PARENT_NORMAL / 'manifest.json'),
        selected_effective_seed=2, inherited_frozen_seed=2, seed_metadata_proposed=True,
        proposed_sha256={str(p.relative_to(proposed)): sha(p) for p in sorted(proposed.rglob('*')) if p.is_file()},
        stage_sha256={str(p.relative_to(stage)): sha(p) for p in sorted(stage.rglob('*')) if p.is_file()},
        recipe_sha256=sha(Path(__file__)), deployment_ready=False, quartus_invoked=False))
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


def seed_fingerprint(tree, qsf):
    extras = [tree / TARGET / 'variants/os30.qsf',
              tree / TARGET / 'variants/os30.sources']
    extras.extend(sorted((tree / TARGET / 'variants').glob('os30.sdc')))
    command = ['bash', '-c', '. "$1"; shift; netlist_hash "$@"', 'seed-check',
               str(tree / 'tools/netlist_hash.sh'), str(qsf), *map(str, extras)]
    result = subprocess.run(command, cwd=tree / TARGET, text=True, capture_output=True)
    assert result.returncode == 0 and not result.stderr, result.stderr
    return result.stdout.strip(), command


def check_seed_pair(tree, qsf, output):
    fingerprint, command = seed_fingerprint(tree, qsf)
    saved = (tree / (SEED_PATH + '.src')).read_text().strip()
    assert fingerprint == saved, 'Ordinary Makefile stale-netlist guard would warn'
    assert (tree / SEED_PATH).read_text() == '2\n'
    save(output, dict(passed=True, effective_seed=2, fingerprint=fingerprint,
                     saved_fingerprint=saved, stale_warning=False, command=command,
                     saved_seed_sha256=sha(tree / SEED_PATH),
                     saved_fingerprint_sha256=sha(tree / (SEED_PATH + '.src'))))


def check():
    prep = json.loads((OUT / 'prepared.json').read_text())
    assert prep['recipe_sha256'] == sha(Path(__file__))
    stage = OUT / 'stage'
    for name, digest in prep['stage_sha256'].items():
        assert sha(stage / name) == digest, name
    fit_tree = SELECTED / 'fit-seed2/job'
    fit_qsf = fit_tree / TARGET / 'ap_core.qsf'
    assert fit_qsf.is_file(), 'Wait for the actual selected job QSF; never invoke a fit here.'
    inherited = P / 'normal-build-integration-v11'
    inherited_manifest = json.loads((inherited / 'manifest.json').read_text())
    for name, digest in inherited_manifest.items():
        assert sha(inherited / name) == digest, name
    old_prep = json.loads((inherited / 'prepared.json').read_text())
    old_result = json.loads((inherited / 'result.json').read_text())
    assert old_result['passed'] and not old_result['quartus_invoked']
    seed_metadata = {SEED_PATH, SEED_PATH + '.src'}
    old_sources = {n:h for n,h in old_prep['proposed_sha256'].items() if n not in seed_metadata}
    new_sources = {n:h for n,h in prep['proposed_sha256'].items() if n not in seed_metadata}
    assert len(new_sources) == 29
    assert set(old_sources) == set(new_sources)
    assert [n for n in new_sources if new_sources[n] != old_sources[n]] == [VARIANT_QSF]
    assert (stage / VARIANT_QSF).read_text() == (inherited / 'proposed' / VARIANT_QSF).read_text() + '\n' + FANOUT + '\n'
    assert SEED_PATH not in prep['proposed_sha256']
    assert json.loads((OUT / 'production-before.json').read_text()) == json.loads((inherited / 'production-before.json').read_text())
    assert old_prep['overrides'] == prep['overrides'] and len(prep['overrides']) == 6
    assert len(prep['proposed_sha256']) == 30
    checks = OUT / 'checks'
    checks.mkdir(exist_ok=False)
    save(checks / 'inherited-checks-binding.json', dict(
        manifest_path=str((inherited / 'manifest.json').relative_to(ROOT)),
        manifest_sha256=sha(inherited / 'manifest.json'),
        result_sha256=sha(inherited / 'result.json'),
        verified_manifest_files=len(inherited_manifest), unchanged_proposed_files=28, sole_changed_proposed_setting=FANOUT,
        production_input_inventory_identical=True,
        inherited_checks=['os20/os25 normal/frozen source closure',
            'missing/duplicate/malformed source-map rejection',
            'variant timing, CPU freshness and atomic CPU publication'],
        rerun_checks=['os30 seed/source/macro/setting closure',
            'ordinary clean QSF regeneration', 'normal/frozen generator agreement',
            'saved seed and source fingerprint']))
    reverse = {row['proposed_path']:row['selected_path'] for row in prep['inventory'] if 'proposed_path' in row}
    selected_prep = json.loads((SELECTED / 'prepared.json').read_text())
    assert selected_prep['seed'] == selected_prep['saved_seed'] == 2
    assert selected_prep['effective_job_qsf_addition'] == FANOUT
    assert selected_prep['saved_seed'] == 2 and selected_prep['rtl_byte_identical_to_parent']
    expected = qsf_summary(fit_qsf, fit_tree)
    assert [r for r in expected['settings'] if r[2] == 'SEED'] == [
        ['set_global_assignment', '-name', 'SEED', '2']]
    save(checks / 'selected-binding.json', dict(
        selected_prepared_sha256=sha(SELECTED / 'prepared.json'),
        actual_job_qsf_sha256=sha(fit_qsf), effective_seed=2,
        inherited_frozen_seed=2, fitter_invoked_by_this_helper=False))
    assert ['set_instance_assignment', '-name', 'MAX_FANOUT', '4', '-to', 'ic|cpu|cpu|execute_ctrl3_up_LANE_SEL_lane0'] in expected['settings']
    map_qsf = SELECTED / 'map-core/job' / TARGET / 'ap_core.qsf'
    assert fit_qsf.read_bytes() == map_qsf.read_bytes()
    save(checks / 'mapped-job-binding.json', dict(passed=True, mapped_qsf_sha256=sha(map_qsf), actual_job_qsf_sha256=sha(fit_qsf), map_manifest_sha256=sha(SELECTED/'manifest.json')))
    save(checks / 'selected-job-closure.json', expected)
    qsf = run_make(stage, 'os30', 'os30', checks / 'os30-make.log')
    got = qsf_summary(qsf, stage, reverse)
    assert got == expected, 'os30 generated closure differs from selected job'
    save(checks / 'os30-normal-closure.json', got)
    check_seed_pair(stage, qsf, checks / 'os30-seed-pair.json')
    before = qsf.read_bytes()
    qsf.unlink()
    again = run_make(stage, 'os30', 'os30', checks / 'os30-regenerate.log')
    assert again.read_bytes() == before
    # Run only the seed-affected frozen os30 project. The identical generator
    # and unchanged other-variant/negative inputs inherit their sealed checks.
    sys.path.insert(0, str(stage / 'tools'))
    spec = importlib.util.spec_from_file_location('private_sweep', stage / 'tools/sweep_target_matrix.py')
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)
    freeze = checks / 'input'
    shutil.copytree(stage, freeze, ignore=shutil.ignore_patterns('bld', '__pycache__'))
    dest = checks / 'frozen-os30'
    directory, project = sweep.prepare(freeze, dest,
        dict(target='pocket', variant='os30', defs=' '.join(got['macros'])), 2, 4)
    frozen_got = qsf_summary(directory / (project + '.qsf'), dest, reverse)
    assert frozen_got == got
    save(checks / 'os30-frozen-closure.json', frozen_got)
    check_seed_pair(dest, directory / (project + '.qsf'), checks / 'os30-frozen-seed-pair.json')
    for name, digest in json.loads((OUT / 'production-before.json').read_text()).items():
        assert sha(ROOT / name) == digest, name
    outputs = {name:dict(record, inherited=True) for name,record in old_result['variants'].items() if name != 'os30'}
    outputs['os30'] = dict(exact_match=True, sources=len(got['sources']), macros=len(got['macros']), missing_generated_cpu=False, inherited=False)
    save(OUT / 'result.json', dict(passed=True, quartus_invoked=False, deployment_ready=False,
        selected_job_source_exact=True, effective_seed=2, inherited_frozen_seed=2,
        proposed_files=30, overrides=6, stale_seed_warning=False, other_variants_unchanged=True,
        variants=outputs, normal_and_frozen_generators_agree=True,
        negative_map_tests=old_result['negative_map_tests'], existing_tests=old_result['existing_tests'],
        inherited_check_manifest_sha256=sha(inherited / 'manifest.json'),
        inherited_checks_not_rerun=True, production_inputs_unchanged=True,
        prepared_sha256=sha(OUT / 'prepared.json'),
        limits=['Only QSF/fingerprint-affected os30 checks rerun; identical generator, source-map and other-variant checks inherit normal-v11 evidence.',
            'QSF generation bypasses CPU generation; separately bound actual Scala proof remains repro5/regenerate5.',
            'Exact source/defines/settings/SDC/QIP/MIF closure matches the actual mapped and fitting seed2 job; filesystem paths differ.',
            'Saved seed2 is unchanged; only the os30 instance setting and its matching source fingerprint differ from normal-v4.',
            'Conditional closure only: fit, both slow corners above90 MHz and positive hold remain external gates.']))
    print('PASS: MAX_FANOUT4 seed2 ordinary/frozen closure and fingerprint; unchanged checks inherited')


def seal():
    result = json.loads((OUT / 'result.json').read_text())
    assert result['passed'] and not result['quartus_invoked']
    binding = json.loads((OUT / 'checks/selected-binding.json').read_text())
    actual_qsf = SELECTED / 'fit-seed2/job' / TARGET / 'ap_core.qsf'
    assert sha(actual_qsf) == binding['actual_job_qsf_sha256']
    regeneration = P / 'cpu-os30-regenerate-v5/manifest.json'
    for name, digest in json.loads(regeneration.read_text()).items():
        assert sha(regeneration.parent / name) == digest, name
    save(OUT / 'cpu-regeneration-binding.json', dict(manifest_path=str(regeneration.relative_to(ROOT)),
        manifest_sha256=sha(regeneration), selected_cpu_sha256=sha(SELECTED / 'frozen' / CPU_PATH),
        scope='Actual private Scala generation and complete helper chain; see the bound regeneration manifest.'))
    (OUT / 'REPORT.txt').write_text(
        'Private normal-build integration of compact v7 with MAX_FANOUT4, seed2 unchanged\n\n'
        'PASS: clean os30 QSF generation resolves to the exact selected seed2 fanout job source content, defines, settings, '
        'constraints and initialization images. Normal and frozen-project generators agree. os20/os25 source '
        'closures and settings are unchanged. No production files or Quartus outputs were modified.\n\n'
        'The selected tree had 19 differences: 18 RTL/netlist files and the experimental master QSF. '
        'The normal-v4 proposal retains all28 other file bytes; its os30.qsf adds only MAX_FANOUT4 on the selected launch register. '
        'A fresh os30.seed.src is the30th proposed file. Saved os30.seed remains2 and is not a proposed mutation. '
        'The six changed shared/top modules are selected through variants/os30.sources; their exact snapshots '
        'including the os30-only io_sdram snapshot and the eleven new geometry files live in os30 directories. The CPU remains its existing per-variant path. '
        'These duplicate modules preserve the reviewed candidate and are not a new general architecture. '
        'The master QSF is unchanged; new sources and AREA settings are in variants/os30.qsf, and span lookahead '
        'is in os30.mk. The previously qualified CPU generator/config/freshness proposal is composed separately.\n\n'
        'Validation reruns only QSF/fingerprint-affected clean os30 ordinary and frozen QSF generation, exact selected '
        'source/macro/setting closure and fresh fingerprint checks. The unchanged28 normal proposal files, '
        'generator and production input inventory are byte-checked against sealed normal-v11. Its other-variant, '
        'missing/duplicate/malformed source-map and three existing build tests are rehashed and inherited, not rerun. '
        'CPU generation is bypassed for QSF tests; actual Scala regeneration-v5 separately reproduces the exact selected CPU.\n\n'
        'No fitter or deployment is authorized by this integration report. The physical clock requirement remains a '
        'separate gate; a source-equivalent QSF is not timing closure.\n')
    evidence = {str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*'))
                if p.is_file() and '__pycache__' not in p.parts and p.name != 'manifest.json'}
    save(OUT / 'manifest.json', evidence)
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    assert sys.argv[1:] in (['prepare'], ['check'], ['seal'])
    {'prepare':prepare, 'check':check, 'seal':seal}[sys.argv[1]]()
