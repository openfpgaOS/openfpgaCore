#!/usr/bin/env python3
"""Freeze an OS30 candidate, map once, then fit seeds 1–30 in isolated databases.

Preparation does not launch Quartus. Run only after the owner freezes and
qualifies the final source; --frozen-sha binds that decision to exact inputs.
Never changes the original source, saved seed, clock constraints, or SD card.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback

# Running the portable recipe must not mutate its own pinned directory.
sys.dont_write_bytecode = True
import results

HERE = Path(__file__).resolve().parent
PROJECT = Path('src/fpga/targets/pocket')
QSF = PROJECT / 'ap_core.qsf'
VERSION = 'Version 25.1std.0 Build 1129'
SEEDS = list(range(1, 31))
MAX_FIT_JOBS = 4
MAX_PROCESSORS = 4
MIN_AVAILABLE_MEMORY_GIB = 20
LOCK = threading.Lock()
CHILDREN = set()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def inventory(directory):
    answer = {}
    for path in sorted(Path(directory).rglob('*')):
        if path.is_symlink():
            raise ValueError('No symlinks in frozen inputs: ' + str(path))
        if path.is_file():
            answer[str(path.relative_to(directory))] = sha(path)
    return answer


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def clone(source, destination):
    if destination.exists():
        raise ValueError('Clone destination already exists: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reflinks are copy-on-write; never hardlink mutable Quartus databases.
    subprocess.run(['cp', '-a', '--reflink=auto', '--', str(source), str(destination)], check=True)


def qsf_seed(text, seed):
    changed, count = re.subn(r'^set_global_assignment -name SEED \d+[ \t]*$',
                            f'set_global_assignment -name SEED {seed}', text, flags=re.M)
    if count != 1:
        raise ValueError('Expected one explicit saved seed')
    return changed


def optimization_technique(text):
    """Validate the narrowly permitted global synthesis choice and its history."""
    values = []
    prefix = re.compile(r'^set_global_assignment\s+-name\s+OPTIMIZATION_TECHNIQUE(?:\s|$)')
    exact = re.compile(r'set_global_assignment -name OPTIMIZATION_TECHNIQUE (AREA|SPEED|BALANCED)')
    for line in text.splitlines():
        line = line.strip()
        if prefix.match(line):
            match = exact.fullmatch(line)
            if not match:
                raise ValueError('Unsupported global OPTIMIZATION_TECHNIQUE assignment: ' + line)
            values.append(match[1])
    return dict(effective=values[-1] if values else 'BALANCED', assignments=values)


def physical_synthesis(text):
    """Only the two reviewed global fitter choices; retain override history."""
    result = {}
    for name, choices in [('PHYSICAL_SYNTHESIS_COMBO_LOGIC', ('ON', 'OFF')),
                          ('PHYSICAL_SYNTHESIS_EFFORT', ('NORMAL', 'FAST', 'EXTRA'))]:
        values = []
        prefix = re.compile(r'^set_global_assignment\s+-name\s+' + name + r'(?:\s|$)')
        exact = re.compile('set_global_assignment -name ' + name + ' (' + '|'.join(choices) + ')')
        for line in text.splitlines():
            line = line.strip()
            if prefix.match(line):
                match = exact.fullmatch(line)
                if not match:
                    raise ValueError('Unsupported global ' + name + ' assignment: ' + line)
                values.append(match[1])
        result[name] = dict(effective=values[-1] if values else None, assignments=values)
    return result


def cpu_combo_scope(text):
    """The one reviewed CPU-only instance override; never accept wildcards."""
    token = lambda value: '(?:' + re.escape(value) + '|"' + re.escape(value) + '"|\\{' + re.escape(value) + '\\})'
    exact = re.compile(r'set_instance_assignment[ \t]+-name[ \t]+' + token('PHYSICAL_SYNTHESIS_COMBO_LOGIC')
                       + r'[ \t]+' + token('ON') + r'[ \t]+-to[ \t]+' + token('ic|cpu|cpu'))
    assignments = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r'^set_instance_assignment\s', line) and 'PHYSICAL_SYNTHESIS_COMBO_LOGIC' in line:
            if not exact.fullmatch(line):
                raise ValueError('Unsupported instance physical synthesis scope: ' + line)
            assignments.append(dict(target='ic|cpu|cpu', value='ON'))
    return dict(present=bool(assignments), target='ic|cpu|cpu' if assignments else None,
                effective='ON' if assignments else None, assignments=assignments)


def protected_qsf(text):
    optimization_technique(text)
    physical_synthesis(text)
    cpu_combo_scope(text)
    allowed = re.compile(r'^set_global_assignment -name (?:SEED|VERILOG_MACRO|VERILOG_FILE|SYSTEMVERILOG_FILE|NUM_PARALLEL_PROCESSORS|OPTIMIZATION_TECHNIQUE|PHYSICAL_SYNTHESIS_COMBO_LOGIC|PHYSICAL_SYNTHESIS_EFFORT) ')
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#') and not allowed.match(line)
            and not cpu_combo_scope(line)['present']]


def archive(path, root, files):
    with path.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as tar:
            for file in sorted(files):
                data = file.read_bytes()
                member = tarfile.TarInfo(str(file.relative_to(root)))
                member.size = len(data); member.mode = 0o644; member.mtime = 0
                tar.addfile(member, io.BytesIO(data))


def prepare(source, output, baseline):
    source, output, baseline = source.resolve(), output.resolve(), baseline.resolve()
    if output.exists() or source == output or source in output.parents:
        raise ValueError('Output must be new and outside the source tree')
    files = inventory(source)
    if any(set(Path(name).parts) & {'db', 'incremental_db', 'output_files', '.git'} for name in files):
        raise ValueError('Input must be a clean source snapshot, not a build directory')
    base = load(baseline)
    protected = {name: digest for name, digest in base['source_sha256'].items()
                 if name.endswith(('.sdc', '.seed')) or '/megafunctions/' in name
                 or name.endswith('/VexiiRiscv_os30.v')}
    if not protected:
        raise ValueError('Empty protected input manifest')
    for name, digest in protected.items():
        if files.get(name) != digest:
            raise ValueError('Protected CPU/clock/constraint/seed changed: ' + name)
    text = (source / QSF).read_text()
    if qsf_seed(text, 2) != text:
        raise ValueError('Frozen source must retain saved seed 2')
    if 'VERILOG_MACRO VEXII_CPU_OS30' not in text or 'VexiiRiscv_os30.v' not in text:
        raise ValueError('OS30-only source required')
    if re.search(r'VERILOG_FILE .*VexiiRiscv_(?!os30\.v)', text):
        raise ValueError('Unexpected additional CPU variant')
    baseline_source = baseline.parent / 'source' / QSF
    if baseline_source.exists():
        original_qsf = baseline_source.read_text()
    else:
        with tarfile.open(baseline.parent / 'source.tar.gz') as tar:
            original_qsf = tar.extractfile(str(QSF)).read().decode()
    if hashlib.sha256(original_qsf.encode()).hexdigest() != base['source_sha256'][str(QSF)]:
        raise ValueError('Baseline QSF does not match its pinned manifest')
    if protected_qsf(text) != protected_qsf(original_qsf):
        raise ValueError('Protected QSF device/constraint settings changed')
    output.mkdir(parents=True)
    clone(source, output / 'frozen')
    if inventory(output / 'frozen') != files or inventory(source) != files:
        raise ValueError('Source changed during snapshot')
    recipes = output / 'recipes'; recipes.mkdir()
    for name in ('sweep.py', 'results.py', 'report_sweep_timing.tcl'):
        shutil.copy2(HERE / name, recipes / name)
    shutil.copy2(baseline, output / 'baseline-manifest.json')
    (output / 'work').mkdir(); (output / 'seeds').mkdir()
    manifest = dict(format=1, target='pocket', variant='os30', configured_mhz=100,
                    seeds=SEEDS, saved_seed=2, source_sha256=files,
                    synthesis_optimization_technique=optimization_technique(text),
                    physical_synthesis=physical_synthesis(text),
                    cpu_combo_scope=cpu_combo_scope(text),
                    frozen_sha256=fingerprint(files), protected_sha256=protected,
                    baseline_manifest_sha256=sha(baseline), recipe_sha256=inventory(recipes),
                    original_source=str(source), created_unix=time.time(), deployed=False)
    archive(output / 'source.tar.gz', output / 'frozen', [output / 'frozen' / name for name in files])
    manifest['source_archive_sha256'] = sha(output / 'source.tar.gz')
    save(output / 'manifest.json', manifest)
    save(output / 'status.json', dict(state='prepared', map_completed=False, seeds_completed=0))
    print(json.dumps(dict(state='prepared', output=str(output), frozen_sha256=manifest['frozen_sha256'],
                          seeds=SEEDS, saved_seed=2, quartus_launched=False)), flush=True)


def verify(output):
    manifest = load(output / 'manifest.json')
    if manifest['seeds'] != SEEDS or manifest['variant'] != 'os30' or manifest['saved_seed'] != 2:
        raise ValueError('Unexpected sweep scope')
    if inventory(output / 'frozen') != manifest['source_sha256']:
        raise ValueError('Frozen source changed')
    if optimization_technique((output / 'frozen' / QSF).read_text()) != manifest['synthesis_optimization_technique']:
        raise ValueError('Frozen synthesis technique differs from the manifest')
    if physical_synthesis((output / 'frozen' / QSF).read_text()) != manifest['physical_synthesis']:
        raise ValueError('Frozen physical synthesis settings differ from the manifest')
    if cpu_combo_scope((output / 'frozen' / QSF).read_text()) != manifest['cpu_combo_scope']:
        raise ValueError('Frozen CPU physical synthesis scope differs from the manifest')
    if inventory(output / 'recipes') != manifest['recipe_sha256']:
        raise ValueError('Frozen recipe changed')
    if sha(output / 'baseline-manifest.json') != manifest['baseline_manifest_sha256']:
        raise ValueError('Protected baseline manifest changed')
    if sha(output / 'source.tar.gz') != manifest['source_archive_sha256']:
        raise ValueError('Portable source archive changed')
    return manifest


def event(output, **fields):
    fields['unix_time'] = time.time()
    with LOCK:
        with (output / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(fields) + '\n')
        print(json.dumps(fields), flush=True)


def proc_start(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def phase(output, receipt_path, receipt, name, command, cwd, temporary):
    temporary.mkdir(exist_ok=True)
    env = dict(os.environ, TMPDIR=str(temporary))
    receipt['state'] = 'running'; receipt['phase'] = name
    entry = dict(phase=name, argv=command, cwd=str(cwd), started_unix=time.time())
    receipt['commands'].append(entry); save(receipt_path, receipt)
    event(output, seed=receipt.get('seed'), phase=name, state='starting')
    log_path = receipt_path.parent / (name + '.log')
    with log_path.open('w') as log:
        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        with LOCK:
            CHILDREN.add(child.pid)
        receipt['active_process'] = dict(pid=child.pid, start=proc_start(child.pid))
        save(receipt_path, receipt)
        try:
            code = child.wait()
        finally:
            with LOCK:
                CHILDREN.discard(child.pid)
    receipt.pop('active_process', None)
    entry['elapsed_seconds'] = time.time() - entry['started_unix']; entry['returncode'] = code
    receipt['returncodes'][name] = code; save(receipt_path, receipt)
    event(output, seed=receipt.get('seed'), phase=name, state='finished', returncode=code)
    return code


def source_changes(directory, manifest, seed=None):
    changed = []
    for name, digest in manifest['source_sha256'].items():
        path = directory / name
        if seed is not None and name == str(QSF):
            expected = qsf_seed((directory.parents[1] / 'frozen' / QSF).read_text(), seed)
            if path.read_text() != expected:
                changed.append(name)
        elif not path.is_file() or sha(path) != digest:
            changed.append(name)
    return changed


def mapped_inventory(master):
    return {str(p.relative_to(master)): sha(p) for folder in ('db', 'incremental_db')
            for p in (master / PROJECT / folder).rglob('*') if p.is_file()}


def map_once(output, manifest, quartus, processors):
    master = output / 'work/master'
    receipt_path = output / 'map-receipt.json'
    if receipt_path.exists():
        receipt = load(receipt_path)
        if receipt.get('state') != 'complete':
            raise RuntimeError('Incomplete map receipt; preserve failed evidence and prepare a new sweep root')
        if source_changes(master, manifest) or mapped_inventory(master) != receipt['mapped_database_sha256']:
            raise ValueError('Pristine mapped source/database changed')
        return master
    clone(output / 'frozen', master)
    receipt = dict(state='prepared', commands=[], returncodes={}, frozen_sha256=manifest['frozen_sha256'])
    command = [str(quartus / 'quartus_map'), 'ap_core', '--write_settings_files=off', f'--parallel={processors}']
    code = phase(output, receipt_path, receipt, 'map', command, master / PROJECT, master / 'temporary')
    receipt['post_map_source_changes'] = source_changes(master, manifest)
    receipt['mapped_database_sha256'] = mapped_inventory(master)
    if code or receipt['post_map_source_changes'] or not receipt['mapped_database_sha256']:
        receipt['state'] = 'failed'; save(receipt_path, receipt)
        raise RuntimeError('Mapping failed or changed source; no fits launched')
    receipt['state'] = 'complete'; save(receipt_path, receipt)
    return master


def resources_available():
    memory = {k: int(v.split()[0]) * 1024 for k, v in
              (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())}
    fit_pids = []
    for p in Path('/proc').glob('[0-9]*/comm'):
        try:
            if p.read_text().strip() == 'quartus_fit':
                fit_pids.append(int(p.parent.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    return memory['MemAvailable'], fit_pids


def validate_resource_limits(jobs, processors):
    if not 1 <= jobs <= MAX_FIT_JOBS:
        raise ValueError('Use 1–4 concurrent seed jobs')
    if not 1 <= processors <= MAX_PROCESSORS:
        raise ValueError('Use 1–4 processors per Quartus process')


def can_start_seed(jobs, active_seed_jobs, available_memory, fit_pids, owned_pids):
    # Every active seed reserves a slot, including clone/assembly/STA phases.
    # Its fitter PID must not be counted again as an external process.
    outside_fits = len(set(fit_pids) - set(owned_pids))
    return (active_seed_jobs < jobs
            and available_memory >= MIN_AVAILABLE_MEMORY_GIB * 1024**3
            and outside_fits + active_seed_jobs < MAX_FIT_JOBS)


def attempt_seed(output, manifest, master, seed, quartus, processors, keep_work):
    root = output / 'seeds' / f'seed_{seed:02}'
    root.mkdir(exist_ok=True)
    serial = len(list(root.glob('attempt_*'))) + 1
    attempt = root / f'attempt_{serial:02}'; attempt.mkdir()
    work = output / 'work' / f'seed_{seed:02}_attempt_{serial:02}'
    receipt_path = attempt / 'receipt.json'
    receipt = dict(seed=seed, attempt=serial, state='prepared', commands=[], returncodes={},
                   frozen_sha256=manifest['frozen_sha256'], valid_reports=False, work=str(work))
    save(receipt_path, receipt)
    save(root / 'latest.json', dict(attempt=attempt.name))
    try:
        map_receipt = load(output / 'map-receipt.json')
        if mapped_inventory(master) != map_receipt['mapped_database_sha256']:
            raise ValueError('Pristine mapped database changed')
        clone(master, work)
        (work / QSF).write_text(qsf_seed((output / 'frozen' / QSF).read_text(), seed))
        receipt['qsf_sha256'] = sha(work / QSF)
        receipt['map_receipt_sha256'] = sha(output / 'map-receipt.json')
        cwd = work / PROJECT
        for name, command in [
            ('fit', [str(quartus / 'quartus_fit'), 'ap_core', '--recompile=off', '--write_settings_files=off', f'--seed={seed}', f'--parallel={processors}']),
            ('asm', [str(quartus / 'quartus_asm'), 'ap_core']),
            ('sta', [str(quartus / 'quartus_sta'), 'ap_core'])]:
            if phase(output, receipt_path, receipt, name, command, cwd, work / 'temporary'):
                raise RuntimeError('Quartus phase failed: ' + name)
        timing = attempt / 'timing'
        code = phase(output, receipt_path, receipt, 'corners',
                     [str(quartus / 'quartus_sta'), '-t', str(output / 'recipes/report_sweep_timing.tcl'), 'ap_core', str(timing)],
                     cwd, work / 'temporary')
        measured = results.parse(cwd, timing)
        # Negative setup intentionally returns failure from the timing script.
        # Other nonzero exits must never be rescued by stale positive reports.
        if code and (code != 3 or measured['timing_closed_at_configured_100_mhz']
                     or 'Whole-design timing failed; see ' not in (attempt / 'corners.log').read_text()):
            raise ValueError('Unexpected timing process failure')
        if source_changes(work, manifest, seed):
            raise ValueError('Quartus changed frozen inputs')
        raw = cwd / 'output_files/ap_core.rbf'
        reverse = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
        artifact = attempt / 'os30.rbf_r'; artifact.write_bytes(raw.read_bytes().translate(reverse))
        for ext in ('rbf', 'sof'):
            shutil.copy2(cwd / ('output_files/ap_core.' + ext), attempt / ('ap_core.' + ext))
        receipt.update(measured, state='complete', valid_reports=True, bitstream_sha256=sha(artifact),
                       raw_rbf_sha256=sha(raw), sof_sha256=sha(attempt / 'ap_core.sof'))
    except Exception as error:
        receipt.update(state='failed', error=str(error), traceback=traceback.format_exc())
    finally:
        if work.exists():
            files = [p for p in (work / PROJECT / 'output_files').glob('*')
                     if p.is_file() and p.suffix in ('.rpt', '.summary', '.smsg')]
            archive(attempt / 'quartus-reports.tar.gz', work, files)
            receipt['quartus_report_sha256'] = {str(p.relative_to(work)): sha(p) for p in files}
            if not keep_work:
                shutil.rmtree(work)
                receipt['scratch_database_removed'] = True
        receipt['evidence_sha256'] = {str(p.relative_to(attempt)): sha(p)
                                      for p in attempt.rglob('*') if p.is_file() and p != receipt_path}
        save(receipt_path, receipt)
        save(root / 'result.json', receipt)
        event(output, seed=seed, state=receipt['state'], eligible=receipt.get('eligible_above_90_mhz_estimate', False),
              fmax=receipt.get('minimum_main_restricted_fmax_mhz'))
    return receipt


def verified_result(output, seed, manifest):
    root = output / 'seeds' / f'seed_{seed:02}'
    pointer = load(root / 'latest.json')['attempt']
    if not re.fullmatch(r'attempt_\d+', pointer):
        raise ValueError('Invalid attempt pointer')
    attempt = root / pointer
    row = load(root / 'result.json')
    if row != load(attempt / 'receipt.json') or row['frozen_sha256'] != manifest['frozen_sha256']:
        raise ValueError('Result does not match its exact-source receipt')
    actual = {str(p.relative_to(attempt)): sha(p) for p in attempt.rglob('*')
              if p.is_file() and p.name != 'receipt.json'}
    if actual != row['evidence_sha256']:
        raise ValueError('Seed evidence changed: ' + str(seed))
    if row['valid_reports']:
        with tempfile.TemporaryDirectory(prefix='reparse-', dir=output / 'work') as temporary:
            directory = Path(temporary)
            with tarfile.open(attempt / 'quartus-reports.tar.gz') as tar:
                for name in ('ap_core.fit.rpt', 'ap_core.fit.summary'):
                    path = directory / 'output_files' / name
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(tar.extractfile(str(PROJECT / 'output_files' / name)).read())
            measured = results.parse(directory, attempt / 'timing')
        if any(row[key] != value for key, value in measured.items()):
            raise ValueError('Stored result differs from report reparse: ' + str(seed))
        if sha(attempt / 'os30.rbf_r') != row['bitstream_sha256']:
            raise ValueError('Bitstream does not match its receipt')
    return row


def collect(output):
    manifest = verify(output)
    rows = [verified_result(output, seed, manifest) for seed in SEEDS]
    selected = results.select(rows)
    save(output / 'results.json', rows); save(output / 'selection.json', selected)
    save(output / 'status.json', dict(state='complete', seeds_completed=30, selection=selected))
    print(json.dumps(selected, indent=2), flush=True)


def run(output, frozen_sha, quartus, jobs, processors, resume, retry_failed, keep_work):
    output = output.resolve(); manifest = verify(output)
    for name in ('sweep.py', 'results.py'):
        if sha(HERE / name) != manifest['recipe_sha256'][name]:
            raise ValueError('Executing recipe differs from frozen recipe: ' + name)
    if frozen_sha != manifest['frozen_sha256']:
        raise ValueError('Explicit final frozen-source fingerprint does not match')
    validate_resource_limits(jobs, processors)
    if shutil.disk_usage(output).free < 8 * 1024**3:
        raise ValueError('Less than 8 GiB free on work filesystem')
    with (output / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = list((output / 'seeds').glob('seed_*/latest.json'))
        if existing and not resume:
            raise ValueError('Existing seed attempts require --resume')
        for pointer in existing:
            entry = pointer.parent / load(pointer)['attempt'] / 'receipt.json'
            process = load(entry).get('active_process')
            current_start = proc_start(process['pid']) if process else None
            if current_start is not None and current_start == process['start']:
                raise RuntimeError(f'Previous Quartus process still active: {process}')
        version = subprocess.check_output([str(quartus / 'quartus_sh'), '--version'], text=True)
        if VERSION not in version:
            raise ValueError('Unexpected Quartus version: ' + version)
        configuration = dict(quartus_version=version, processors=processors,
             minimum_available_memory_gib=MIN_AVAILABLE_MEMORY_GIB,
             maximum_global_fit_processes=MAX_FIT_JOBS,
             maximum_processors_per_process=MAX_PROCESSORS, source_sha256=frozen_sha)
        configuration_path = output / 'run-configuration.json'
        if configuration_path.exists() and load(configuration_path) != configuration:
            raise ValueError('Resume must use the same compiler/processors/source configuration')
        if not configuration_path.exists():
            save(configuration_path, configuration)
        event(output, state='runner_configuration', concurrent_seed_jobs=jobs, processors=processors)
        master = map_once(output, manifest, quartus, processors)
        pending = []
        for seed in SEEDS:
            result_path = output / 'seeds' / f'seed_{seed:02}/result.json'
            if not result_path.exists() or (retry_failed and load(result_path)['state'] == 'failed'):
                pending.append(seed)
        futures = {}; last_wait = 0
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            while pending or futures:
                available, external = resources_available()
                with LOCK:
                    owned_pids = set(CHILDREN)
                if pending and can_start_seed(jobs, len(futures), available, external, owned_pids):
                    seed = pending.pop(0)
                    futures[pool.submit(attempt_seed, output, manifest, master, seed, quartus, processors, keep_work)] = seed
                elif pending and time.monotonic() - last_wait > 30:
                    event(output, state='waiting_resources', available_memory_gib=round(available / 1024**3, 1), active_fit_pids=external)
                    last_wait = time.monotonic()
                if futures:
                    done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result(); del futures[future]
                else:
                    time.sleep(2)
                finished = sum((output / 'seeds' / f'seed_{seed:02}/result.json').exists() for seed in SEEDS)
                save(output / 'status.json', dict(state='running', seeds_completed=finished, pending=pending, active=list(futures.values())))
        collect(output)


def main():
    def stop_children(signum, frame):
        for pid in list(CHILDREN):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, stop_children)
    signal.signal(signal.SIGTERM, stop_children)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('source', type=Path); p.add_argument('output', type=Path); p.add_argument('--baseline-manifest', required=True, type=Path)
    p = sub.add_parser('run'); p.add_argument('output', type=Path); p.add_argument('--frozen-sha', required=True)
    p.add_argument('--quartus-bin', type=Path, default=Path('/home/alberto/altera_lite/25.1std/quartus/bin'))
    p.add_argument('--jobs', type=int, default=4); p.add_argument('--processors', type=int, default=4)
    p.add_argument('--resume', action='store_true'); p.add_argument('--retry-failed', action='store_true'); p.add_argument('--keep-work', action='store_true')
    p = sub.add_parser('collect'); p.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.source, args.output, args.baseline_manifest)
    elif args.command == 'collect':
        collect(args.output.resolve())
    else:
        run(args.output, args.frozen_sha, args.quartus_bin, args.jobs, args.processors, args.resume, args.retry_failed, args.keep_work)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        with LOCK:
            for pid in list(CHILDREN):
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        raise
