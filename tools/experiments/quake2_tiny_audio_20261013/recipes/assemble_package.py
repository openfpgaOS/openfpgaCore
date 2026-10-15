#!/usr/bin/env python3
"""Freeze app now; assemble final portable evidence only after the full sweep."""
import argparse
import difflib
import gzip
import hashlib
import io
import json
import re
from pathlib import Path
import shutil
import sys
import tarfile
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'tools/experiments/quake2_tiny_audio_20261013'
PRIOR = ROOT / 'tools/experiments/quake2_gpu_draws_20261013'
sys.path.insert(0, str(OUT))
import package
import audit_sweep


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def copy(source, destination):
    source = package.unlinked(source)
    digest = package.sha(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Refusing overwrite: ' + str(destination))
    with destination.open('xb') as stream:
        stream.write(source.read_bytes())
    if package.sha(source) != digest or package.sha(destination) != digest:
        raise ValueError('Input changed while copying')


def archive(path, members):
    with path.open('xb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as output:
            for name, (data, mode) in sorted(members.items()):
                package.safe_name(name)
                entry=tarfile.TarInfo(name); entry.size=len(data); entry.mode=mode; entry.mtime=0
                output.addfile(entry, io.BytesIO(data))


def freeze_app(candidate, elf):
    directory = OUT / 'app'
    if directory.exists():
        raise ValueError('App already frozen; verify it rather than overwrite')
    old = package.load(PRIOR / 'app/manifest.json')
    package.checked_files(PRIOR / 'app', old['files'])
    base = {n:(d,m) for n,d,m in package.archive_members(PRIOR/'app'/old['baseline']['archive'], old['baseline']['sha256'])}
    for name, item in old['variants']['optimized']['changed'].items():
        image = PRIOR/'app'/item['postimage']
        if package.sha(image) != item['after_sha256']:
            raise ValueError('Native6 postimage changed')
        base[name]=(image.read_bytes(),item['mode'])
    selected={}
    for name, (_, mode) in base.items():
        path=package.unlinked(candidate/name)
        before=package.sha(path); data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=before:
            raise ValueError('App changed during snapshot')
        selected[name]=(data,mode)
    changed=[n for n in base if base[n][0]!=selected[n][0]]
    if changed != ['src/quake2/openfpga/snd_of.c']:
        raise ValueError('Unexpected native6 app changes: '+str(changed))
    clean=package.load(HERE/'app-clean-rebuild.json')
    if not clean['passed'] or package.sha(elf)!=clean['clean_elf_sha256'] or clean['clean_elf_sha256']!=clean['incremental_elf_sha256']:
        raise ValueError('App does not match clean rebuild receipt')
    if hashlib.sha256(selected[changed[0]][0]).hexdigest()!=clean['audio_source_sha256']:
        raise ValueError('Audio source differs from clean build')
    directory.mkdir()
    archive(directory/'source.tar.gz',selected)
    copy(elf,directory/'app.elf'); copy(HERE/'app-clean-rebuild.json',directory/'clean-rebuild.json')
    (directory/'source.patch').write_text(''.join(difflib.unified_diff(base[changed[0]][0].decode().splitlines(True),selected[changed[0]][0].decode().splitlines(True),fromfile='a/'+changed[0],tofile='b/'+changed[0])))
    manifest=dict(format=1,component='app',archive='source.tar.gz',
        source={n:dict(sha256=hashlib.sha256(d).hexdigest(),mode=m) for n,(d,m) in selected.items()},
        compiler=old['compiler'],source_date_epoch=old['source_date_epoch'],
        expected_elf_sha256=package.sha(elf),native6_manifest_sha256=package.sha(PRIOR/'app/manifest.json'),
        native6_elf_sha256=old['optimized_elf_sha256'],changed_from_native6=changed,
        source_scope='Full native6 source inventory including pinned SDK startup objects/static libraries; no game assets.',
        files=package.inventory(directory))
    save(directory/'manifest.json',manifest)
    package.verify_component('app',OUT)
    print('Frozen app:',len(selected),'source/build-input files, ELF',manifest['expected_elf_sha256'])


def final(sweep, evidence_plan, check_only=False, *, source_only=False):
    if any((OUT/n).exists() for n in ('manifest.json','core','core-source','sweep')):
        raise ValueError('Final output already exists; do not overwrite')
    package.verify_component('app',OUT)
    manifest=package.load(sweep/'manifest.json')
    status=package.load(sweep/'status.json')
    if status.get('state')!='complete' or status.get('seeds_completed')!=30:
        raise ValueError('Require completed full30seed sweep before final assembly')
    selection=package.load(sweep/'selection.json')
    if source_only:
        if not selection.get('all_seeds_attempted') or selection.get('selected_seed') is not None:
            raise ValueError('Source-only export requires all30 attempted and no eligible selected seed')
    elif not selection.get('all_seeds_attempted') or not selection.get('selected_seed'):
        raise ValueError('Require completed full30seed sweep and an eligible selected artifact')
    if package.inventory(sweep/'frozen')!=manifest['source_sha256']:
        raise ValueError('Final source no longer matches sweep freeze')
    if package.inventory(sweep/'recipes')!=manifest['recipe_sha256']:
        raise ValueError('Sweep recipes changed')
    # Bind logical gates to the exact final hardware, rather than carry forward
    # a passing receipt from a superseded area experiment.
    audit=package.load(HERE/'integration-tests/audit-final-ring.json')
    qsf_name='src/fpga/targets/pocket/ap_core.qsf'
    if not audit['passed'] or any(manifest['source_sha256'].get(n)!=d for n,d in audit['selected_source_pins'].items()if n!=qsf_name):
        raise ValueError('Final audio/MMIO source differs from passing integration gate')
    # Reviewed fitter choices do not change compiled RTL behavior. All other
    # instance assignments, clocks, macros and settings remain exact.
    qsf=(sweep/'frozen'/qsf_name).read_text()
    original_qsf=HERE/'contention/integrated-final-ring-v1/inputs/ap_core.qsf'
    if package.sha(original_qsf)!=audit['selected_source_pins'][qsf_name]:
        raise ValueError('Original MMIO QSF reference differs')
    if package.functional_qsf(qsf)!=package.functional_qsf(original_qsf.read_text()):
        raise ValueError('Final QSF changes more than reviewed global fitter choices')
    settings=package.optimization_settings(qsf)
    if settings['global_setting']!=manifest['synthesis_optimization_technique'] or settings['global_setting']['effective']!='AREA':
        raise ValueError('Expected explicit effective AREA sweep policy')
    if settings['physical_synthesis']!=manifest['physical_synthesis']:
        raise ValueError('Recorded physical synthesis policy differs from final QSF')
    if settings['cpu_combo_scope']!=manifest['cpu_combo_scope']:
        raise ValueError('Recorded CPU physical synthesis scope differs from final QSF')
    if 'set_instance_assignment -name OPTIMIZATION_TECHNIQUE SPEED -to ic|cpu|cpu'not in settings['instance_assignments']:
        raise ValueError('CPU SPEED override missing')
    mapping={'audio_mixer.v':'src/fpga/common/os30/audio_mixer.v',
             'audio_sample_cache.v':'src/fpga/common/os30/audio_sample_cache.v',
             'axi_sdram_arbiter.v':'src/fpga/common/axi_sdram_arbiter.v',
             'ap_core.qsf':'src/fpga/targets/pocket/ap_core.qsf',
             'core_top.v':'src/fpga/targets/pocket/os30/core_top.v'}
    for run in ['integrated-final-ring-v1','integrated-final-ring-all22-v1']:
        receipt=package.load(HERE/'contention'/run/'receipt.json')
        pins={Path(n).name:d for n,d in receipt['inputs'].items()}
        if any(pins.get(n)!=manifest['source_sha256'].get(rel)for n,rel in mapping.items()):
            raise ValueError('Final shared-bus source differs from passing contention gate: '+run)
    worker_name='src/fpga/common/gpu_geometry_worker.v'
    worker_root=HERE/'area-geometry/ring-lookahead'
    worker_gate=package.load(worker_root/'qualification.json')
    worker_portable=package.load(worker_root/'portable-proof-final-ring/receipt.json')
    worker_gpu=package.load(worker_root/'full-gpu-v1/receipt.json')
    worker_sha=manifest['source_sha256'][worker_name]
    if not worker_gate['all_functional_gates_passed'] or worker_gate['candidate_sha256']!=worker_sha:
        raise ValueError('Final worker differs from paired-cycle/width qualification')
    if not worker_portable['passed'] or worker_portable['worker_sha256']!=worker_sha:
        raise ValueError('Final worker differs from portable functional gate')
    if not worker_gpu['passed'] or worker_gpu['worker_source_sha256']!=worker_sha:
        raise ValueError('Final worker differs from full-GPU paced/unpaced gate')
    write_gate=package.load(HERE/'final-source-wrpath/final-receipt.json')
    if write_gate['status']!='PASS' or not write_gate['frozen_source_unchanged'] or write_gate['frozen_sha256']!=manifest['frozen_sha256']:
        raise ValueError('Final core differs from exact-source GPU/SDRAM write-path gate')
    selected=selection['selected_seed']; rows=package.load(sweep/'results.json')
    if len(rows)!=30 or {r['seed'] for r in rows}!=set(range(1,31)):
        raise ValueError('Missing/duplicate seed attempts')
    if source_only:
        audit_sweep.audit(sweep,selected,source_only=True)
    else:
        selected_root=sweep/'seeds'/f'seed_{selected:02}'
        selected_attempt=package.load(selected_root/'latest.json')['attempt']
        package.safe_name(selected_attempt)
        audit_sweep.audit(sweep,selected,selected_root/selected_attempt/'os30.rbf_r')
    plan=package.load(evidence_plan)
    for item in plan['files']:
        package.safe_name(item['source']);package.safe_name(item['destination'])
        source=HERE/item['source']
        if package.sha(source)!=item['sha256']:raise ValueError('Evidence plan pin changed: '+item['source'])
        if not item['destination'].startswith(('evidence/','tests/','recipes/')):raise ValueError('Evidence destination outside allowlist')
        if source.name in ('vendor_altmult_add.v','vendor_families.v','altera_mf.v'):raise ValueError('Proprietary simulation model is not a package input')
    if package.sha(OUT/'timing_results.py')!=manifest['recipe_sha256']['results.py']:
        raise ValueError('Portable timing parser differs from executed sweep')
    if check_only:
        print('PASS:', 'source-only unqualified export' if source_only else 'final assembly', 'preflight, selected seed',selected)
        return
    core_component='core-source' if source_only else 'core'
    core=OUT/core_component; core.mkdir()
    copy(sweep/'source.tar.gz',core/'source.tar.gz')
    sources={n:dict(sha256=d,mode=0o644) for n,d in manifest['source_sha256'].items()}
    for _ in package.archive_members(core/'source.tar.gz',sources): pass
    sweep_out=OUT/'sweep'; sweep_out.mkdir()
    for name in ('manifest.json','source.tar.gz','results.json','selection.json','status.json','baseline-manifest.json','map-receipt.json','map.log','run-configuration.json'):
        copy(sweep/name,sweep_out/name)
    for name in manifest['recipe_sha256']:
        copy(sweep/'recipes'/name,sweep_out/'recipes'/name)
    omissions={}
    for seed in range(1,31):
        source=sweep/'seeds'/f'seed_{seed:02}'
        dest=sweep_out/'seeds'/f'seed_{seed:02}'
        pointer=package.load(source/'latest.json')['attempt']; package.safe_name(pointer)
        if '/' in pointer or not pointer.startswith('attempt_'):
            raise ValueError('Unsafe attempt pointer')
        row=package.load(source/'result.json'); receipt=package.load(source/pointer/'receipt.json')
        if row!=receipt or row['frozen_sha256']!=manifest['frozen_sha256']:
            raise ValueError('Seed is not pinned to final source')
        pins=dict(row['evidence_sha256']); omitted={}
        for name,digest in pins.items():
            package.safe_name(name)
            if package.sha(source/pointer/name)!=digest: raise ValueError('Seed evidence changed')
            if name in ('os30.rbf_r','ap_core.rbf','ap_core.sof'):
                omitted[name]=digest
                if seed==selected and name=='os30.rbf_r': copy(source/pointer/name,core/name)
            else: copy(source/pointer/name,dest/pointer/name)
        for name in ('latest.json','result.json',pointer+'/receipt.json'): copy(source/name,dest/name)
        omissions[str(seed)]=omitted
    save(sweep_out/'bundle-omissions.json',omissions)
    chosen=next((r for r in rows if r['seed']==selected),None)
    if source_only:
        save(core/'manifest.json',dict(format=1,component=core_component,archive='source.tar.gz',source=sources,
            selected_seed=None,qualified=False,configured_mhz=100,synthesis_settings=settings,
            timing_closed_at_configured_100_mhz=False,
            reason='No seed meets the unchanged strict >90 MHz and all other timing/resource gates.',
            files=package.inventory(core)))
    else:
        save(core/'manifest.json',dict(format=1,component='core',archive='source.tar.gz',source=sources,
            selected_seed=selected,configured_mhz=100,expected_artifact_sha256=chosen['bitstream_sha256'],
            synthesis_settings=settings,
            minimum_main_restricted_fmax_mhz=chosen['minimum_main_restricted_fmax_mhz'],
            timing_closed_at_configured_100_mhz=chosen['timing_closed_at_configured_100_mhz'],
            files=package.inventory(core)))
    for item in plan['files']:
        package.safe_name(item['source']);package.safe_name(item['destination'])
        source=HERE/item['source']
        if package.sha(source)!=item['sha256']: raise ValueError('Evidence plan pin changed')
        if not item['destination'].startswith(('evidence/','tests/','recipes/')): raise ValueError('Evidence destination outside allowlist')
        copy(source,OUT/item['destination'])
    copy(evidence_plan,OUT/'evidence/assembly-plan.json')
    copy(Path(__file__),OUT/'recipes/assemble_package.py')
    save(OUT/'selection.json',selection)
    native6=dict(path='tools/experiments/quake2_gpu_draws_20261013',manifest_sha256=package.sha(PRIOR/'manifest.json'),
        note='Prior native6 contains unchanged GPU architecture, image-difference and simulator performance qualification; not duplicated here.')
    readme=OUT/'README.txt'
    prepared='Status: preparation. The application archive is verified. Core source,\nbitstream and sweep evidence have not been assembled into this bundle.'
    text=readme.read_text()
    if text.count(prepared)!=1:raise ValueError('Expected preparation status in README')
    completed=('Status: source-only, UNQUALIFIED. All 30 seeds were attempted; none passed\n'
               'the unchanged release gate. Core source, application and test/sweep evidence\n'
               'are included. There is no selected core, FPGA bitstream or deployment.') if source_only else (
               'Status: assembled. Application, core source, selected bitstream and all 30\n'
               'seed results are included. No deployment has been performed by this package.')
    readme.write_text(text.replace(prepared,completed))
    save(OUT/'manifest.json',dict(format=1,state='source-only-unqualified' if source_only else 'assembled',
        deployed=False,selected_seed=selected,qualified=not source_only,
        synthesis_settings=settings,
        configured_mhz=100,timing_closed_at_configured_100_mhz=False if source_only else chosen['timing_closed_at_configured_100_mhz'],
        frozen_sha256=manifest['frozen_sha256'],native6=native6,files=package.inventory(OUT)))
    package.verify(OUT)
    print('Final portable bundle verified:',OUT)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('app');a.add_argument('--source',type=Path,default=HERE/'app-candidate');a.add_argument('--elf',type=Path,default=HERE/'app-clean-build/app.elf')
    for mode in ('final','source-only'):
        f=sub.add_parser(mode);f.add_argument('--sweep',type=Path,required=True);f.add_argument('--evidence-plan',type=Path,required=True);f.add_argument('--check-only',action='store_true')
    args=p.parse_args()
    if args.action=='app':freeze_app(args.source,args.elf)
    else:final(args.sweep,args.evidence_plan,args.check_only,source_only=args.action=='source-only')


if __name__=='__main__':main()
