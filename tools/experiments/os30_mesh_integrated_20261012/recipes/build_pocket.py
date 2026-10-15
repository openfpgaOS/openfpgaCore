#!/usr/bin/env python3
"""Freeze a measured mesh native revision into the qualified os30 project.

Only the native revision's changed common RTL and extra macros are applied.
The physical memory controller, firmware, pins, clocks and constraints retain
their qualified inputs. This creates one seed-3 project; it never publishes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
P = HERE.parent
ROOT = P.parents[2]
BASE = P / 'cached-cpu-v1/native-sdram-v2'
PHYSICAL = P / 'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compile', action='store_true')
    args = parser.parse_args()
    native, out = args.native.resolve(), args.output.resolve()
    assert out.is_relative_to(HERE) and not out.exists(), out
    prepared = json.loads((native / 'prepared.json').read_text())
    baseline = json.loads((BASE / 'prepared.json').read_text())
    for name, digest in prepared['source_sha256'].items():
        assert sha(native / name) == digest, name
    command = prepared['command']
    assert not (set(x for x in baseline['command'] if x.startswith('-D')) -
                set(x for x in command if x.startswith('-D'))), 'Removed baseline macros'
    extra_defines = sorted(set(x for x in command if x.startswith('-D')) -
                           set(x for x in baseline['command'] if x.startswith('-D')))
    assert extra_defines and all('MESH' in x for x in extra_defines), extra_defines
    frozen = out / 'frozen'
    shutil.copytree(PHYSICAL / 'frozen', frozen)
    build = out / 'build'
    build.mkdir()
    shutil.copy2(PHYSICAL / 'build/ap_core.qpf', build / 'ap_core.qpf')
    shutil.copy2(PHYSICAL / 'os.bin', out / 'os.bin')
    qsf = (PHYSICAL / 'build/ap_core.qsf').read_text().replace(
        str(PHYSICAL / 'frozen'), str(frozen))
    top_source = HERE / 'physical-top/core_top.v'
    top_target = frozen / 'src/fpga/targets/pocket/os30/core_top.v'
    top_previous_sha = sha(top_target)
    shutil.copy2(top_source, top_target)
    common = native / 'frozen/src/fpga/common'
    base_common = BASE / 'frozen/src/fpga/common'
    changed = {str(top_target.relative_to(frozen)): {
        'native_source': str(top_source), 'sha256': sha(top_source),
        'previous_sha256': top_previous_sha,
    }}
    for source in sorted(common.iterdir()):
        if not source.is_file() or source.suffix not in ('.v', '.sv', '.vh'):
            continue
        old = base_common / source.name
        if old.exists() and sha(source) == sha(old):
            continue
        # os30's qualified modules are isolated from the generic variants.
        target = frozen / 'src/fpga/common/os30' / source.name
        if not target.exists() and old.exists():
            target = frozen / 'src/fpga/common' / source.name
        was_present = target.exists()
        previous_sha = sha(target) if was_present else None
        shutil.copy2(source, target)
        changed[str(target.relative_to(frozen))] = {
            'native_source': str(source), 'sha256': sha(source),
            'previous_sha256': previous_sha,
        }
        if not was_present and source.suffix != '.vh':
            language = 'SYSTEMVERILOG_FILE' if source.suffix == '.sv' else 'VERILOG_FILE'
            qsf += f'\nset_global_assignment -name {language} {target}\n'
    assert any('phase10_load_alias.v' in name for name in changed), changed
    assert any('gpu_core.v' in name for name in changed), changed
    for define in extra_defines:
        qsf += f'\nset_global_assignment -name VERILOG_MACRO {define[2:]}\n'
    qsf += f'\nset_global_assignment -name SEARCH_PATH {frozen / "src/fpga/common/os30"}\n'
    # Every active source is local to this frozen candidate. Require physical
    # constraints to retain byte-identical content, without timing exceptions.
    for line in qsf.splitlines():
        match = re.match(r'set_global_assignment -name (\w+_FILE) (/.+)$', line)
        if not match:
            continue
        path = Path(match.group(2)).resolve()
        assert path.is_relative_to(frozen) and path.is_file(), (line, path)
    constraints = {}
    for source in (PHYSICAL / 'frozen').rglob('*.sdc'):
        relative = source.relative_to(PHYSICAL / 'frozen')
        assert sha(source) == sha(frozen / relative)
        constraints[str(relative)] = sha(source)
    (build / 'ap_core.qsf').write_text(qsf)
    record = {
        'native': str(native), 'native_prepared_sha256': sha(native / 'prepared.json'),
        'physical_baseline': str(PHYSICAL),
        'physical_baseline_inputs_sha256': sha(PHYSICAL / 'inputs.json'),
        'configured_mhz': 90, 'seed': 3, 'placement_effort': 3.0,
        'changed_rtl': changed, 'extra_defines': extra_defines,
        'constraints_sha256': constraints,
        'qsf_sha256': sha(build / 'ap_core.qsf'),
        'sources': {str(f.relative_to(frozen)): sha(f)
                    for f in sorted(frozen.rglob('*')) if f.is_file()},
        'os_sha256': sha(out / 'os.bin'),
        'recipe_sha256': sha(Path(__file__)),
        'limits': ['Unqualified until fit and timing review', 'No seed sweep',
                   'No automatic deployment'],
    }
    (out / 'inputs.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'build': str(build), 'changed_rtl': list(changed),
                      'extra_defines': extra_defines}), flush=True)
    if args.compile:
        start = time.monotonic()
        with (out / 'compile.log').open('x') as log:
            result = subprocess.run(['bash', str(ROOT / 'tools/quartus-container.sh'),
                                     str(build)], stdout=log, stderr=subprocess.STDOUT)
        (out / 'compile.json').write_text(json.dumps({
            'returncode': result.returncode, 'seconds': time.monotonic() - start,
            'inputs_sha256': sha(out / 'inputs.json'),
        }, indent=2) + '\n')
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
