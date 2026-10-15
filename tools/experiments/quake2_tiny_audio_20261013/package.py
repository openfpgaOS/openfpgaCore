#!/usr/bin/env python3
"""Verify this portable experiment or stage its pinned sources; never deploy."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import sys
import tarfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def cpu_combo_scope(text):
    """Accept only the reviewed CPU instance, with simple Tcl token quoting."""
    assignments=[]
    expected=['set_instance_assignment','-name','PHYSICAL_SYNTHESIS_COMBO_LOGIC','ON','-to','ic|cpu|cpu']
    for raw in text.splitlines():
        line=raw.strip()
        if not re.match(r'^set_instance_assignment\s',line)or 'PHYSICAL_SYNTHESIS_COMBO_LOGIC'not in line:
            continue
        words=re.findall(r'"[^"\n]*"|\{[^{}\n]*\}|\S+',line)
        if len(words)==6:
            for index in [2,3,5]:
                word=words[index]
                if (word.startswith('"')and word.endswith('"'))or(word.startswith('{')and word.endswith('}')):
                    words[index]=word[1:-1]
        if words!=expected:raise ValueError('Unsupported instance physical synthesis scope: '+line)
        assignments.append(dict(target='ic|cpu|cpu',value='ON'))
    return dict(present=bool(assignments),target='ic|cpu|cpu'if assignments else None,
                effective='ON'if assignments else None,assignments=assignments)


def optimization_settings(text):
    """Record effective global synthesis choice without losing its history."""
    values=[];instances=[]
    for line in text.splitlines():
        line=line.strip()
        if re.match(r'^set_global_assignment\s+-name\s+OPTIMIZATION_TECHNIQUE(?:\s|$)',line):
            match=re.fullmatch(r'set_global_assignment -name OPTIMIZATION_TECHNIQUE (AREA|SPEED|BALANCED)',line)
            if not match:raise ValueError('Unsupported synthesis optimization assignment')
            values.append(match[1])
        if line.startswith('set_instance_assignment -name OPTIMIZATION_TECHNIQUE '):instances.append(line)
    physical={}
    for name,choices in [('PHYSICAL_SYNTHESIS_COMBO_LOGIC',('ON','OFF')),
                         ('PHYSICAL_SYNTHESIS_EFFORT',('NORMAL','FAST','EXTRA'))]:
        history=[]
        for line in text.splitlines():
            line=line.strip()
            if re.match(r'^set_global_assignment\s+-name\s+'+name+r'(?:\s|$)',line):
                match=re.fullmatch('set_global_assignment -name '+name+' ('+'|'.join(choices)+')',line)
                if not match:raise ValueError('Unsupported physical synthesis assignment: '+name)
                history.append(match[1])
        physical[name]=dict(effective=history[-1]if history else None,assignments=history)
    return dict(global_setting=dict(effective=values[-1]if values else 'BALANCED',assignments=values),
                instance_assignments=instances,physical_synthesis=physical,
                cpu_combo_scope=cpu_combo_scope(text))


def functional_qsf(text):
    """Strip only reviewed global fitter choices; preserve every other setting."""
    optimization_settings(text)
    ignored=re.compile(r'set_global_assignment -name (?:OPTIMIZATION_TECHNIQUE (?:AREA|SPEED|BALANCED)|PHYSICAL_SYNTHESIS_COMBO_LOGIC (?:ON|OFF)|PHYSICAL_SYNTHESIS_EFFORT (?:NORMAL|FAST|EXTRA))')
    return [line for raw in text.splitlines()if(line:=raw.strip())and not line.startswith('#')and not ignored.fullmatch(line)and not cpu_combo_scope(line)['present']]


def safe_name(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or '\0' in name or p.is_absolute() or str(p) != name or any(x in ('', '.', '..') for x in p.parts):
        raise ValueError('Unsafe relative path: ' + repr(name))
    return p


def unlinked(path):
    path = Path(os.path.abspath(path))
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise ValueError('Symlink refused: ' + str(item))
    return path


def sha(path):
    path = unlinked(path)
    if not path.is_file():
        raise ValueError('Missing regular file: ' + str(path))
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    return json.loads(unlinked(path).read_text())


def inventory(root):
    root = unlinked(root)
    answer = {}
    for p in sorted(root.rglob('*')):
        unlinked(p)
        if p.is_file():
            name = str(p.relative_to(root)); safe_name(name)
            answer[name] = sha(p)
        elif not p.is_dir():
            raise ValueError('Nonregular package entry: ' + str(p))
    return answer


def checked_files(root, pins, manifest_name='manifest.json'):
    actual = inventory(root)
    actual.pop(manifest_name, None)
    if actual != pins:
        changed = sorted(n for n in set(actual) | set(pins) if actual.get(n) != pins.get(n))
        raise ValueError('Package inventory/hash mismatch: ' + str(changed[:12]))


def archive_members(path, pins):
    seen = set()
    with tarfile.open(unlinked(path), 'r:gz') as archive:
        for member in archive:
            name = member.name; safe_name(name)
            if name in seen or not member.isfile() or member.linkname or name not in pins:
                raise ValueError('Duplicate, linked, nonregular or unlisted member: ' + name)
            if member.mode & ~0o777:
                raise ValueError('Special archive mode: ' + name)
            data = archive.extractfile(member).read()
            expected = pins[name]
            digest = expected['sha256'] if isinstance(expected, dict) else expected
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Archive bytes differ: ' + name)
            if isinstance(expected, dict) and member.mode != expected['mode']:
                raise ValueError('Archive mode differs: ' + name)
            seen.add(name)
            yield name, data, member.mode
    if seen != set(pins):
        raise ValueError('Archive inventory incomplete')


def verify_component(component, root=HERE):
    if component not in ('app', 'core', 'core-source'):
        raise ValueError('Expected app, core or core-source')
    directory = unlinked(root / component)
    manifest = load(directory / 'manifest.json')
    if manifest['format'] != 1 or manifest['component'] != component:
        raise ValueError('Unexpected component format')
    checked_files(directory, manifest['files'])
    safe_name(manifest['archive'])
    settings=None
    for name,data,mode in archive_members(directory / manifest['archive'], manifest['source']):
        if component!='app' and name=='src/fpga/targets/pocket/ap_core.qsf':settings=optimization_settings(data.decode())
    if component!='app' and settings!=manifest['synthesis_settings']:
        raise ValueError('Recorded synthesis settings differ from archived QSF')
    if component=='core-source':
        check_unqualified(manifest, manifest['files'])
        if any(Path(n).suffix.lower() in ('.rbf', '.rbf_r', '.sof') for n in manifest['source']):
            raise ValueError('FPGA binary inside source-only archive')
        return manifest
    artifact, key = ('app.elf', 'expected_elf_sha256') if component == 'app' else ('os30.rbf_r', 'expected_artifact_sha256')
    if sha(directory / artifact) != manifest[key]:
        raise ValueError('Component artifact differs from expected build')
    return manifest


def check_unqualified(manifest, files):
    if manifest.get('qualified') is not False or 'selected_seed' not in manifest or manifest['selected_seed'] is not None:
        raise ValueError('Source-only metadata must remain unqualified with null selection')
    if 'expected_artifact_sha256' in manifest:
        raise ValueError('Source-only export cannot declare a selected artifact')
    if manifest.get('timing_closed_at_configured_100_mhz') is not False:
        raise ValueError('Source-only export cannot claim configured timing closure')
    if any(Path(n).suffix.lower() in ('.rbf', '.rbf_r', '.sof') for n in files):
        raise ValueError('FPGA binary forbidden in source-only export')


def verify(root=HERE):
    manifest = load(root / 'manifest.json')
    if manifest['format'] != 1 or manifest['state'] not in ('assembled', 'source-only-unqualified'):
        raise ValueError('Final experiment is not assembled')
    source_only=manifest['state']=='source-only-unqualified'
    checked_files(root, manifest['files'])
    if source_only:
        check_unqualified(manifest, manifest['files'])
        if (root/'core').exists():raise ValueError('Release core directory forbidden in source-only export')
    core_component='core-source' if source_only else 'core'
    for component in ('app', core_component):
        verify_component(component, root)
    core=load(root/core_component/'manifest.json'); sweep=load(root/'sweep/manifest.json')
    if {n:v['sha256']for n,v in core['source'].items()}!=sweep['source_sha256'] or core['selected_seed']!=manifest['selected_seed']:
        raise ValueError('Core source/seed differs from selected sweep')
    # The independent parser rechecks raw reports and all30 attempt receipts.
    import audit_sweep
    audit_sweep.audit(root / 'sweep', manifest['selected_seed'], source_only=source_only)
    return manifest


def check_stage(directory, component, root=HERE, exact=True):
    manifest = verify_component(component, root)
    pins = {name: item['sha256'] for name, item in manifest['source'].items()}
    actual = inventory(directory)
    if any(actual.get(name) != digest for name, digest in pins.items()) or (exact and actual != pins):
        raise ValueError('Staged source inventory/hash differs')
    return manifest


def stage(component, destination, root=HERE):
    manifest = verify_component(component, root)
    destination = unlinked(destination)
    if destination.exists():
        raise ValueError('Destination must be new: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    unlinked(destination.parent)
    destination.mkdir()  # Exclusive; never overwrite an existing source tree.
    for name, data, mode in archive_members(root / component / manifest['archive'], manifest['source']):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        unlinked(path)
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(mode)
    check_stage(destination, component, root)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='action', required=True)
    v = commands.add_parser('verify'); v.add_argument('component', nargs='?', choices=['app', 'core', 'core-source'])
    for action in ('stage', 'check-stage'):
        s = commands.add_parser(action); s.add_argument('component', choices=['app', 'core', 'core-source'])
        s.add_argument('directory', type=Path)
    a = p.parse_args()
    if a.action == 'verify':
        verify_component(a.component) if a.component else verify()
    elif a.action == 'stage':
        stage(a.component, a.directory)
    else:
        check_stage(a.directory, a.component)
    print('PASS:', a.action)


if __name__ == '__main__':
    main()
