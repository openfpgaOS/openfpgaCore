#!/usr/bin/env python3
"""Plan, publish, deploy or verify a separately qualified SM64 device trial.

Default action is read-only plan. This tool checks bindings, not rendering
qualification: QUALIFIED.json is the reviewed selection supplied by the root
agent. No publication result establishes actual device FPS.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase2'
QUALIFIED = OUT / 'QUALIFIED.json'
BASELINE = OUT / 'source-baseline.json'
BASELINE_SHA = 'cdf64b13dfa0fc60cb43403f7d9ca80f9720f5bab6db81f64ccffe739262b2b3'
DEST = ROOT.parent / 'SM64'
CARD = Path('/run/media/alberto/POCKETDEV')
CORE = 'd3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f'
OS = '054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
OLD_NORMAL = '6c5ce963d9ff817d69b3f76a6f2074125ee9bbd960ce28089994319a611e5e76'
OLD_FPS = 'bbc96bc2cc55d84cbb0bae09bedafe66d38536b8479ecdd62c00fddfd8a225a8'
SOURCES = {
    'gfx_pc.c': 'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
    'gfx_gpu.c': 'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
    'gfx_gpu.h': 'src/sm64/sm64/src/pc/gfx/gfx_gpu.h',
    'gfx_geometry.h': 'src/sm64/sm64/src/pc/gfx/gfx_geometry.h',
    'perf_pocket.h': 'src/sm64/pocket/perf_pocket.h',
    'perf_pocket.c': 'src/sm64/pocket/perf_pocket.c',
    'Makefile.sm64': 'src/sm64/Makefile',
}
ELF_DESTINATIONS = (
    ('app.elf', '.obj/sm64/app.elf', OLD_NORMAL, 'normal_elf_sha256'),
    ('diagnostic.elf', '.obj/sm64-perfdiag/app.elf', OLD_FPS, 'diagnostic_elf_sha256'),
    ('diagnostic.elf', 'build/pocket/sm64/Assets/sm64/common/sm64.elf', OLD_FPS, 'diagnostic_elf_sha256'),
)


def require(condition, detail):
    if not condition:
        raise RuntimeError(str(detail))


def sha(path):
    with Path(path).open('rb') as stream:
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def regular(path, anchor):
    path, anchor = Path(path), Path(anchor)
    require(path.is_relative_to(anchor), ('Path outside allowed tree', str(path)))
    check = path
    while True:
        require(not check.is_symlink(), ('Symlink is not an allowed publication path', str(check)))
        if check == anchor:
            break
        check = check.parent
    require(stat.S_ISREG(path.stat().st_mode), ('Not a regular file', str(path)))


def relative_file(base, relative):
    require(isinstance(relative, str) and relative != '', 'Invalid relative path')
    p = Path(relative)
    require(not p.is_absolute() and '..' not in p.parts and str(p) == relative,
            ('Unsafe relative path', relative))
    result = base / p
    regular(result, base)
    return result


def check_hash(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), 'Invalid SHA-256')


def local_retained():
    for rel, expected in (
        ('runtime/pocket/os30.rbf_r', CORE), ('runtime/pocket/os.bin', OS),
        ('build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r', CORE),
        ('build/pocket/sm64/Assets/sm64/common/os.bin', OS),
    ):
        require(sha(relative_file(DEST, rel)) == expected, ('Retained core/OS changed', rel))


def inventory(directory):
    paths = {'Makefile', 'Mario64.z64'}
    for top in ('src', 'tools'):
        require(not (directory / top).is_symlink(), ('Symlink source root', top))
        for p in (directory / top).rglob('*'):
            relative = p.relative_to(directory)
            if any(x in ('.git', '__pycache__') for x in relative.parts):
                continue
            require(not p.is_symlink(), ('Unexpected source symlink', str(p)))
            if p.is_file():
                paths.add(str(relative))
    return paths


def check_tree(expected):
    require(inventory(DEST) == set(expected), 'SM64 source inventory differs from frozen 6747 inputs')
    for rel, expected_hash in expected.items():
        require(sha(relative_file(DEST, rel)) == expected_hash, ('SM64 source changed', rel))


def selection():
    regular(QUALIFIED, OUT)
    qualified_hash = sha(QUALIFIED)
    r = read(QUALIFIED)
    require(r.get('qualified_for_device_trial') is True, 'Selection is not qualified for a device trial')
    require(r.get('target_verified') is False, 'Publisher must not claim the device FPS target is verified')
    candidate = r.get('candidate')
    require(isinstance(candidate, str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]*', candidate), 'Invalid candidate name')
    directory = OUT / candidate
    require(set(r['source_sha256']) == set(SOURCES), 'Selection must bind all seven source inputs')
    regular(BASELINE, OUT)
    require(sha(BASELINE) == BASELINE_SHA, 'Frozen render-v6 source baseline changed')
    baseline = read(BASELINE)
    require(baseline['parent'] == 'render-v6' and len(baseline['files']) == 6747, 'Unexpected source baseline')
    expected = dict(baseline['files'])
    for name, destination in SOURCES.items():
        h = r['source_sha256'][name]
        check_hash(h)
        require(sha(relative_file(directory, name)) == h, ('Selected source changed', name))
        expected[destination] = h
    for name, key in (('app.elf', 'normal_elf_sha256'), ('diagnostic.elf', 'diagnostic_elf_sha256')):
        check_hash(r[key])
        require(sha(relative_file(directory, name)) == r[key], ('Selected executable changed', name))
    evidence = r['evidence_sha256']
    require(isinstance(evidence, dict) and evidence, 'Missing qualification evidence hashes')
    require(evidence.get(str(BASELINE.relative_to(ROOT))) == BASELINE_SHA, 'Evidence must bind source-baseline.json')
    for rel, h in evidence.items():
        check_hash(h)
        require(sha(relative_file(ROOT, rel)) == h, ('Qualification evidence changed', rel))
    if 'source_tree_sha256' in r:
        require(r['source_tree_sha256'] == expected, 'Selected source tree is not baseline plus seven reviewed overrides')
    if 'parent' in r:
        require(r['parent'] == 'render-v6', 'Unexpected selected parent')
    require(sha(QUALIFIED) == qualified_hash, 'Qualification changed during validation')
    local_retained()
    return r, qualified_hash, directory, baseline['files'], expected


def rows_for(r, directory, before):
    rows = []
    for name, rel in SOURCES.items():
        if before[rel] != r['source_sha256'][name]:
            rows.append(dict(source=str(directory / name), destination=str(DEST / rel),
                             before_sha256=before[rel], after_sha256=r['source_sha256'][name]))
    for name, rel, old, key in ELF_DESTINATIONS:
        rows.append(dict(source=str(directory / name), destination=str(DEST / rel),
                         before_sha256=old, after_sha256=r[key]))
    return rows


def check_rows(rows, which):
    for row in rows:
        destination = Path(row['destination'])
        regular(destination, DEST)
        require(sha(destination) == row[which], ('Destination changed', str(destination)))


def card_identity(require_rw=True):
    require(not CARD.is_symlink() and CARD.is_mount(), 'Pocket SD is not mounted at the frozen path')
    found = json.loads(subprocess.check_output([
        'findmnt', '--json', '--target', str(CARD), '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN'
    ], text=True))['filesystems']
    require(len(found) == 1 and found[0]['target'] == str(CARD), 'Unexpected Pocket mount')
    mount = found[0]
    if require_rw:
        require('rw' in mount['options'].split(',') and not (os.statvfs(CARD).f_flag & os.ST_RDONLY),
                ('Pocket mount is read-only in this execution context', mount))
    for rel, h in (('Cores/ThinkElastic.SM64/os30.rbf_r', CORE), ('Assets/sm64/common/os.bin', OS)):
        require(sha(relative_file(CARD, rel)) == h, ('Pocket retained core/OS changed', rel))
    return dict(target=mount['target'], source=mount['source'], fstype=mount['fstype'],
                major_minor=mount['maj:min'], st_dev=CARD.stat().st_dev)


def card_guard(identity):
    require(card_identity() == identity, 'Pocket mount identity changed')


def write_record(path, value):
    require(not path.exists(), ('Record already exists', str(path)))
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def atomic_copy(src, dst, before, after, anchor, guard=None):
    regular(dst, anchor)
    data = Path(src).read_bytes()
    require(hashlib.sha256(data).hexdigest() == after, ('Copy source changed', str(src)))
    if guard:
        guard()
    require(sha(dst) == before, ('Destination changed before copy', str(dst)))
    fd, temporary = tempfile.mkstemp(prefix='.' + dst.name + '.phase2-', dir=dst.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copymode(dst, temporary)
        require(sha(temporary) == after, 'Temporary readback differs')
        if guard:
            guard()
        regular(dst, anchor)
        require(sha(dst) == before, ('Destination changed before replace', str(dst)))
        os.replace(temporary, dst)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    if guard:
        guard()
    regular(dst, anchor)
    require(sha(dst) == after, ('Destination readback differs', str(dst)))


def backup_rows(rows, directory, anchor):
    directory.mkdir(exist_ok=False)
    for index, row in enumerate(rows):
        destination, saved = Path(row['destination']), directory / str(index)
        regular(destination, anchor)
        require(sha(destination) == row['before_sha256'], ('Destination changed before backup', str(destination)))
        shutil.copy2(destination, saved)
        with saved.open('rb') as stream:
            os.fsync(stream.fileno())
        require(sha(saved) == row['before_sha256'] and sha(destination) == row['before_sha256'],
                ('Backup does not match frozen destination', str(destination)))
        row['backup'] = str(saved)


def restore(rows, anchor, guard=None):
    failures = []
    for row in reversed(rows):
        try:
            if guard:
                guard()
            destination = Path(row['destination'])
            regular(destination, anchor)
            current = sha(destination)
            if current == row['before_sha256']:
                continue
            require(current == row['after_sha256'], ('Preserving unknown independent edit', str(destination)))
            atomic_copy(Path(row['backup']), destination, row['after_sha256'], row['before_sha256'], anchor, guard)
        except BaseException as error:
            failures.append(dict(destination=row['destination'], backup=row['backup'], error=repr(error)))
    return failures


def plan(with_sd=False):
    r, qualification_hash, directory, before, _ = selection()
    rows = rows_for(r, directory, before)
    check_tree(before)
    check_rows(rows, 'before_sha256')
    result = dict(action='plan', read_only=True, candidate=r['candidate'],
                  qualified_sha256=qualification_hash, files=rows,
                  unchanged_source_inputs=6747 - (len(rows) - 3), target_verified=False)
    if with_sd:
        result['sd_mount'] = card_identity()
        require(sha(relative_file(CARD, 'Assets/sm64/common/sm64.elf')) == OLD_FPS,
                'Pocket application differs from frozen deployed F4')
        result['sd_app_before_sha256'] = OLD_FPS
        result['sd_app_after_sha256'] = r['diagnostic_elf_sha256']
    return result


def verify_local(r, qualification_hash, directory, before, expected, record=False):
    rows = rows_for(r, directory, before)
    check_tree(expected)
    check_rows(rows, 'after_sha256')
    local_retained()
    require(sha(QUALIFIED) == qualification_hash, 'Qualification changed during verification')
    if record:
        published = read(OUT / 'published.json')
        require(published['qualified_sha256'] == qualification_hash and published['candidate'] == r['candidate'],
                'Local publication record does not bind this selection')
        require([{k:row[k] for k in ('source', 'destination', 'before_sha256', 'after_sha256')}
                 for row in published['files']] == rows, 'Local publication record differs from this plan')
    return rows


def publish():
    r, qualified_hash, directory, before, expected = selection()
    rows = rows_for(r, directory, before)
    check_tree(before)
    check_rows(rows, 'before_sha256')
    for name in ('publication-plan.json', 'published.json', 'publication-failure.json'):
        require(not (OUT / name).exists(), ('Publication record already exists', name))
    backup_rows(rows, OUT / 'publication-backup', DEST)
    record = dict(candidate=r['candidate'], qualified_sha256=qualified_hash,
                  publisher_sha256=sha(Path(__file__)), files=rows, target_verified=False)
    write_record(OUT / 'publication-plan.json', record)
    done = []
    try:
        for row in rows:
            done.append(row)  # Includes a copy whose post-replace guard/readback fails.
            atomic_copy(Path(row['source']), Path(row['destination']),
                        row['before_sha256'], row['after_sha256'], DEST)
        verify_local(r, qualified_hash, directory, before, expected)
        os.sync()
        write_record(OUT / 'published.json', {**record, 'core_sha256':CORE, 'os_sha256':OS})
    except BaseException as error:
        failures = restore(done, DEST)
        os.sync()
        write_record(OUT / 'publication-failure.json', dict(error=repr(error), rollback_failures=failures,
                     qualified_sha256=qualified_hash, attempted_destinations=[row['destination'] for row in done]))
        if failures:
            raise RuntimeError('Publication failed; consult publication-failure.json and verified backups for manual recovery') from error
        raise
    return dict(action='publish', candidate=r['candidate'], published_files=len(rows),
                core_and_os_unchanged=True, target_verified=False)


def deploy_sd():
    r, qualified_hash, directory, before, expected = selection()
    verify_local(r, qualified_hash, directory, before, expected, record=True)
    identity = card_identity()
    destination = relative_file(CARD, 'Assets/sm64/common/sm64.elf')
    require(sha(destination) == OLD_FPS, 'Pocket application differs from frozen deployed F4')
    for name in ('sd-plan.json', 'sd-deployed.json', 'sd-failure.json'):
        require(not (OUT / name).exists(), ('SD deployment record already exists', name))
    rows = [dict(source=str(directory/'diagnostic.elf'), destination=str(destination),
                 before_sha256=OLD_FPS, after_sha256=r['diagnostic_elf_sha256'])]
    card_guard(identity)
    backup_rows(rows, OUT / 'sd-backup', CARD)
    record = dict(candidate=r['candidate'], qualified_sha256=qualified_hash,
                  publisher_sha256=sha(Path(__file__)), mount=identity, file=rows[0], target_verified=False)
    write_record(OUT/'sd-plan.json', record)
    guard = lambda: card_guard(identity)
    try:
        row = rows[0]
        atomic_copy(Path(row['source']), destination, row['before_sha256'], row['after_sha256'], CARD, guard)
        os.sync()
        guard()
        require(sha(destination) == row['after_sha256'], 'Pocket application readback differs')
        require(sha(QUALIFIED) == qualified_hash, 'Qualification changed during SD deployment')
        write_record(OUT/'sd-deployed.json', {**record, 'core_sha256':CORE, 'os_sha256':OS})
    except BaseException as error:
        failures = restore(rows, CARD, guard)
        os.sync()
        write_record(OUT/'sd-failure.json', dict(error=repr(error), rollback_failures=failures, **record))
        if failures:
            raise RuntimeError('SD deployment failed; consult sd-failure.json and restore verified backup only on the same card') from error
        raise
    return dict(action='sd', sd_app_sha256=r['diagnostic_elf_sha256'], core_and_os_unchanged=True,
                target_verified=False)


def verify(with_sd=False):
    args = selection()
    r, qualified_hash, directory, before, expected = args
    verify_local(*args, record=True)
    result = dict(action='verify', candidate=r['candidate'], source_inputs=6747,
                  executable_destinations=3, core_and_os_unchanged=True, target_verified=False)
    if with_sd:
        identity = card_identity(require_rw=False)
        deployed = read(OUT/'sd-deployed.json')
        require(deployed['qualified_sha256'] == qualified_hash and deployed['mount'] == identity,
                'SD deployment record or mount differs from selected release')
        require(sha(relative_file(CARD, 'Assets/sm64/common/sm64.elf')) == r['diagnostic_elf_sha256'],
                'Pocket application differs from selected diagnostic')
        result['sd_application_verified'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='plan', choices=('plan', 'publish', 'sd', 'verify'))
    parser.add_argument('--sd', action='store_true', help='Include read-only SD checks in plan or verify')
    a = parser.parse_args()
    require(not a.sd or a.action in ('plan', 'verify'), '--sd is only valid with plan or verify')
    if a.action == 'plan': result = plan(a.sd)
    elif a.action == 'publish': result = publish()
    elif a.action == 'sd': result = deploy_sd()
    else: result = verify(a.sd)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
