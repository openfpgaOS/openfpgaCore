#!/usr/bin/env python3
"""Build every FPGA target/variant from frozen sources and compare fitter seeds.

Requires freshly generated per-variant CPUs and target firmware.mif files.
Uses the existing Quartus Docker images (25.1 for Pocket, 17 for MiSTer).
Each candidate has its own project, database, temporary directory and HOME.
Never publishes artifacts or changes the configured clocks or saved seeds.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import traceback
from prepare_mister_variant import prepare as select_mister_variant

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def inventory(include_diagnostics=False):
    rows = []
    for target in sorted((ROOT / 'src/fpga/targets').iterdir()):
        if not (target / 'Makefile').is_file():
            continue
        paths = list((target / 'variants').glob('*.mk'))
        if include_diagnostics:
            paths += list((target / 'variants/diagnostic').glob('*.mk'))
        for path in sorted(paths):
            probe = '\n.PHONY: matrix-values\nmatrix-values:\n'
            for key in ('TARGET', 'VARIANT', 'DEFS', 'SEED'):
                probe += f'\t@echo MATRIX_{key}=$({key})\n'
            run = subprocess.run(['make', '-s', '--no-print-directory', '-f', 'Makefile',
                                  '-f', '-', 'VARIANT=' + path.stem, 'matrix-values'],
                                 input=probe, cwd=target, capture_output=True, text=True, check=True)
            row = {}
            for line in run.stdout.splitlines():
                if line.startswith('MATRIX_'):
                    key, value = line.removeprefix('MATRIX_').split('=', 1)
                    row[key.lower()] = value
            assert row['target'] == target.name and row['variant'] == path.stem
            row['seed'] = int(row['seed'])
            rows.append(row)
    if not rows:
        raise RuntimeError('No target variants found')
    return rows


def freeze(out, rows):
    frozen = out / 'frozen'
    frozen.mkdir()
    ignored = shutil.ignore_patterns('bld', 'db', 'incremental_db', 'output_files',
        '*.rpt', '*.summary', '*.log', '*.sof', '*.rbf', '*.rbf_r', '*.qws', '*.smsg',
        'firmware.mif.tmp')
    for name in ['common', *['targets/' + t for t in sorted({r['target'] for r in rows})]]:
        shutil.copytree(ROOT / 'src/fpga' / name, frozen / 'src/fpga' / name, ignore=ignored)
    if any(row['target'] == 'mister' for row in rows):
        # Freeze once: an overnight sweep must not change the OSD date midway
        # through its seeds merely because the host's calendar date changed.
        (frozen / 'src/fpga/targets/mister/build_id.v').write_text(
            '`define BUILD_DATE "' + datetime.date.today().strftime('%y%m%d') + '"')
    for row in rows:
        name = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_' + row['variant'] + '.v'
        source = ROOT / name
        dest = frozen / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        row['cpu_sha256'] = digest(source)
        row['boot_sha256'] = digest(frozen / 'src/fpga/targets' / row['target'] / 'firmware.mif')
    (frozen / 'tools').mkdir()
    shutil.copy2(ROOT / 'tools/report_core_timing.tcl', frozen / 'tools/report_core_timing.tcl')
    files = {str(p.relative_to(frozen)): digest(p) for p in sorted(frozen.rglob('*')) if p.is_file()}
    save(out / 'frozen-sources.json', files)
    save(out / 'inventory.json', rows)
    return frozen


def docker(out, target, cwd, command):
    identity = f'{os.getpid()}:{cwd}:{command}'.encode()
    name = 'of-target-sweep-' + hashlib.sha256(identity).hexdigest()[:20]
    prefix = ['docker', 'run', '--rm', '--name', name,
              '--label', 'openfpgaos.sweep-output=' + str(out),
              '--user', f'{os.getuid()}:{os.getgid()}',
              '-v', f'{out}:{out}', '--tmpfs', '/tmp:exec', '--tmpfs', '/qhome:exec',
              '-e', 'HOME=/qhome', '-w', str(cwd)]
    if target == 'mister':
        return [*prefix, 'openfpgaos-quartus17', *command]
    if target == 'pocket':
        altera = Path(os.environ.get('ALTERA_ROOT', '/home/alberto/altera_lite')).resolve()
        qroot = altera / '25.1std/quartus'
        return [*prefix, '-v', f'{altera}:{altera}:ro', '-e', f'QUARTUS_ROOTDIR={qroot}',
                '-e', f'PATH={qroot}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                'openfpgaos-quartus', *command]
    raise ValueError('No Quartus backend for target ' + target)



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


def prepare(frozen, dest, row, seed, processors):
    shutil.copytree(frozen, dest)
    project = 'mister' if row['target'] == 'mister' else 'ap_core'
    directory = dest / 'src/fpga/targets' / row['target']
    qsf = directory / (project + '.qsf')
    text = qsf.read_text()
    variant_qsf = directory / 'variants' / (row['variant'] + '.qsf')
    if row['target'] == 'pocket' and variant_qsf.is_file():
        text += '\n' + variant_qsf.read_text()
    if row['target'] == 'pocket':
        text = pocket_source_overrides(text, directory / 'variants' / (row['variant'] + '.sources'))
    legacy_selector = row['target'] == 'mister' and 'source cpu_select.tcl' in text
    text = re.sub(r'^set_global_assignment -name (SEED|NUM_PARALLEL_PROCESSORS) .*\n',
                  '', text, flags=re.M)
    text += f'\nset_global_assignment -name SEED {seed}\n'
    text += f'set_global_assignment -name NUM_PARALLEL_PROCESSORS {processors}\n'
    for macro in row['defs'].split():
        text += 'set_global_assignment -name VERILOG_MACRO ' + macro + '\n'
    cpu = '../../vendor/vexriscv/VexiiRiscv/VexiiRiscv_' + row['variant'] + '.v'
    if row['target'] == 'pocket':
        text += 'set_global_assignment -name VERILOG_FILE ' + cpu + '\n'
    elif legacy_selector:
        # Preserve the file order of an older frozen project when resuming it.
        (directory / 'cpu_variant.qip').write_text(
            'set_global_assignment -name VERILOG_FILE ' + cpu + '\n')
    if row.get('build_id_contents') is not None:
        (directory / 'build_id.v').write_text(row['build_id_contents'])
    qsf.write_text(text)
    if row['target'] == 'mister' and not legacy_selector:
        select_mister_variant(qsf, cpu, row['defs'].split())
    return directory, project


def parse_result(directory, project):
    with (directory / 'timing/summary.tsv').open() as stream:
        timing = list(csv.DictReader(stream, delimiter='\t'))
    checks = ('setup', 'hold', 'recovery', 'removal', 'pulse_width')
    corners = {(r['model'], r['temperature']) for r in timing}
    keys = {(r['model'], r['temperature'], r['check']) for r in timing}
    if (len(timing) != 20 or len(corners) != 4
            or keys != {(m, t, c) for m, t in corners for c in checks}):
        raise ValueError('Expected all five timing checks at four corners')
    for row in timing:
        slack = row['slack']
        if slack == 'no_paths':
            if row['check'] in ('setup', 'hold', 'pulse_width'):
                raise ValueError('Missing required timing paths: ' + row['check'])
        elif not math.isfinite(float(slack)):
            raise ValueError('Non-finite timing slack')
    worst = {}
    for check in checks:
        values = [float(r['slack']) for r in timing if r['check'] == check and r['slack'] != 'no_paths']
        if not values and check in ('setup', 'hold', 'pulse_width'):
            raise ValueError('Missing required timing paths: ' + check)
        worst[check] = min(values) if values else None
    summary = (directory / 'output_files' / (project + '.fit.summary')).read_text(errors='replace')
    match = re.search(r'Logic utilization \(in ALMs\) : ([\d,]+)', summary)
    if not match:
        raise ValueError('Missing fitter resource summary')
    alms = int(match[1].replace(',', ''))
    report = (directory / 'output_files' / (project + '.fit.rpt')).read_text(errors='replace')
    match = re.search(r';\s*M10K blocks\s*;\s*([\d,]+)\s*/\s*([\d,]+)', report)
    ram = int(match[1].replace(',', '')) if match else None
    return dict(worst=worst, all_checks_pass=all(v is None or v >= 0 for v in worst.values()),
                alms=alms, m10ks=ram, timing=timing,
                hashes={ext:digest(directory / 'output_files' / (project + '.' + ext))
                        for ext in ('rbf', 'sof')})


def collect(out):
    """Re-read completed Quartus reports without rebuilding the hardware."""
    if not (out / 'complete.json').exists():
        raise RuntimeError('Wait for the sweep driver to finish before collecting')
    results = []
    for archive in sorted((out / 'results').iterdir()):
        result = json.loads((archive / 'result.json').read_text())
        codes = result['returncodes']
        if all(codes.get(p) == 0 for p in ('map', 'fit', 'asm', 'sta')) and 'corners' in codes:
            directory = Path(result['directory'])
            project = 'mister' if result['target'] == 'mister' else 'ap_core'
            measured = parse_result(directory, project)
            if codes['corners'] and measured['all_checks_pass']:
                raise RuntimeError('Corner analysis failed despite positive reports: ' + str(archive))
            result.update(measured, valid=True, state='complete')
            result.pop('error', None)
            result.pop('traceback', None)
            shutil.copytree(directory / 'timing', archive / 'timing', dirs_exist_ok=True)
            for ext in ('fit.summary', 'map.summary', 'fit.rpt', 'sta.rpt'):
                shutil.copy2(directory / 'output_files' / (project + '.' + ext), archive)
            save(archive / 'result.json', result)
            save(archive / 'status.json', result)
        results.append(result)
    save(out / 'results.json', results)
    save(out / 'complete.json', dict(candidates=len(results), valid=sum(r['valid'] for r in results),
                                   passing=sum(r.get('all_checks_pass', False) for r in results)))


def verify_frozen(out):
    frozen = out / 'frozen'
    changed = [name for name, expected in json.loads((out / 'frozen-sources.json').read_text()).items()
               if digest(frozen / name) != expected]
    if changed:
        raise RuntimeError('Frozen sources changed: ' + repr(changed))
    return frozen


def seed_candidates(rows, offsets, seeds=None):
    if seeds is None:
        tasks = [(row, row['seed'] + offset) for offset in offsets for row in rows]
    else:
        tasks = [(row, seed) for seed in seeds for row in rows]
        # A full range must still measure the saved seed if it lies outside it.
        tasks += [(row, row['seed']) for row in rows if row['seed'] not in seeds]
    if any(seed < 1 for _, seed in tasks) or len({(r['variant'], s) for r, s in tasks}) != len(tasks):
        raise ValueError('invalid or duplicate seeds')
    return tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collect-only', action='store_true', help='Re-read reports from a completed sweep')
    parser.add_argument('--resume', action='store_true',
                        help='Resume an interrupted driver, retaining finished candidates and frozen inputs')
    parser.add_argument('--include-diagnostics', action='store_true',
                        help='Also sweep the archived SDRAM diagnostic configurations')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--processors', type=int, default=4)
    seed_selection = parser.add_mutually_exclusive_group()
    seed_selection.add_argument('--seed-offsets', nargs='+', type=int, default=[0, 1, 2])
    seed_selection.add_argument('--seeds', nargs='+', type=int,
                                help='Absolute seeds for every variant; also includes each saved seed')
    parser.add_argument('--target', action='append', help='Restrict a retry to specific target(s)')
    parser.add_argument('--variant', help='Restrict a diagnostic trial to one variant')
    parser.add_argument('--extra-macro', action='append', default=[],
                        help='Add a diagnostic macro to a single --variant; leaves production unchanged')
    args = parser.parse_args()
    if min(args.jobs, args.processors) < 1:
        parser.error('worker counts must be positive')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=args.resume or args.collect_only)
    # Keep two drivers from building the same candidate or replacing its reports.
    lockfile = (out / '.driver.lock').open('w')
    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.collect_only:
        collect(out)
        return
    if args.resume:
        if args.target or args.variant or args.extra_macro or args.include_diagnostics:
            parser.error('Resume uses the original frozen variant selection')
        frozen = verify_frozen(out)
        rows = json.loads((out / 'inventory.json').read_text())
        previous = json.loads((out / 'config.json').read_text())
        args.seed_offsets = previous['seed_offsets']
        args.seeds = previous.get('seeds')
        if args.processors != previous['processors']:
            parser.error('Resume must retain the original per-fit processor count')
        # A killed driver can leave a Docker tool running. It must not write
        # into a restarted job whose directory has been archived/recreated.
        containers = subprocess.check_output(['docker', 'ps', '-q'], text=True).split()
        for container in containers:
            metadata = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
            if any(m.get('Source') == str(out) for m in metadata['Mounts']):
                raise RuntimeError('Previous sweep container is still running: ' + container)
        # Early sweep versions stamped build_id.v while preparing each job.
        # Recover that value from the existing jobs rather than reverting to
        # the older source-tree stamp or using today's date on a resumed job.
        stamps = {p.read_text() for p in out.glob('jobs/*/src/fpga/targets/mister/build_id.v')}
        if len(stamps) > 1:
            raise RuntimeError('Existing candidates have inconsistent MiSTer build dates')
        if stamps:
            stamp = next(iter(stamps))
            for row in rows:
                if row['target'] == 'mister':
                    row['build_id_contents'] = stamp
            save(out / 'resume-inputs.json', dict(build_id_contents=stamp))
    else:
        rows = inventory(include_diagnostics=args.include_diagnostics or bool(args.variant))
    if args.target:
        known = {row['target'] for row in rows}
        if set(args.target) - known:
            parser.error('Unknown target')
        rows = [row for row in rows if row['target'] in args.target]
    if args.variant:
        rows = [row for row in rows if row['variant'] == args.variant]
        if not rows:
            parser.error('Unknown variant for selected target')
    if args.extra_macro:
        if len(rows) != 1 or any(not re.fullmatch(r'[A-Z_][A-Z_0-9]*', m) for m in args.extra_macro):
            parser.error('Diagnostic macros require one variant and uppercase identifiers')
        rows[0]['original_defs'] = rows[0]['defs']
        rows[0]['defs'] += ' ' + ' '.join(args.extra_macro)
    try:
        tasks = seed_candidates(rows, args.seed_offsets, args.seeds)
    except ValueError as error:
        parser.error(str(error))
    if not args.resume:
        frozen = freeze(out, rows)
    save(out / 'config.json', dict(jobs=args.jobs, processors=args.processors,
        seed_offsets=args.seed_offsets, seeds=args.seeds, variants=len(rows), candidates=len(tasks)))

    def run(pair):
        row, seed = pair
        name = row['target'] + '-' + row['variant'] + '-s' + str(seed)
        dest = out / 'jobs' / name
        result = dict(target=row['target'], variant=row['variant'], seed=seed,
                      start=time.time(), state='preparing', valid=False, returncodes={})
        archive = out / 'results' / name
        if args.resume and (archive / 'result.json').exists():
            previous = json.loads((archive / 'result.json').read_text())
            with LOCK:
                print('RETAIN', name, previous['state'], flush=True)
            return previous
        if args.resume:
            # Only interrupted candidates reach here. Keep their partial logs
            # and databases for diagnosis, then restart from the frozen inputs.
            interrupted = out / 'interrupted' / (name + '-' + str(time.time_ns()))
            interrupted.mkdir(parents=True)
            for source, label in [(dest, 'job'), (archive, 'reports')]:
                if source.exists():
                    shutil.move(source, interrupted / label)
        archive.mkdir(parents=True)
        try:
            directory, project = prepare(frozen, dest, row, seed, args.processors)
            result['directory'] = str(directory)
            with LOCK:
                print('START', name, flush=True)
            phases = [('map', ['quartus_map', project]), ('fit', ['quartus_fit', project]),
                      ('asm', ['quartus_asm', project]), ('sta', ['quartus_sta', project]),
                      ('corners', ['quartus_sta', '-t', str(dest / 'tools/report_core_timing.tcl'),
                                   project, 'timing'])]
            for phase, cmd in phases:
                result.update(state=phase, phase_start=time.time())
                save(archive / 'status.json', result)
                invocation = docker(out, row['target'], directory, cmd)
                with (archive / (phase + '.log')).open('w') as log:
                    try:
                        p = subprocess.run(invocation, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=2700)
                    except subprocess.TimeoutExpired:
                        container = invocation[invocation.index('--name') + 1]
                        subprocess.run(['docker', 'rm', '-f', container], stdout=log, stderr=log)
                        raise
                result['returncodes'][phase] = p.returncode
                if p.returncode and phase != 'corners':
                    raise RuntimeError(f'{phase} failed: exit {p.returncode}')
            result.update(parse_result(directory, project))
            if result['returncodes']['corners'] and result['all_checks_pass']:
                raise RuntimeError('Corner analysis failed despite positive reports')
            shutil.copytree(directory / 'timing', archive / 'timing')
            for ext in ('fit.summary', 'map.summary', 'fit.rpt', 'sta.rpt'):
                shutil.copy2(directory / 'output_files' / (project + '.' + ext), archive)
            result.update(valid=True, state='complete')
        except Exception as error:
            result.update(state='failed', error=str(error), traceback=traceback.format_exc())
        result['seconds'] = time.time() - result['start']
        save(archive / 'status.json', result)
        save(archive / 'result.json', result)
        with LOCK:
            print('DONE', name, json.dumps({k:result[k] for k in
                ('valid', 'worst', 'alms', 'm10ks', 'seconds', 'error') if k in result}), flush=True)
        return result

    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in as_completed([pool.submit(run, pair) for pair in tasks]):
            results.append(future.result())
            save(out / 'results.json', results)
    verify_frozen(out)
    save(out / 'complete.json', dict(candidates=len(results), valid=sum(r['valid'] for r in results),
                                   passing=sum(r.get('all_checks_pass', False) for r in results)))
    print('SWEEP COMPLETE', len(results), 'candidates', flush=True)


if __name__ == '__main__':
    main()
