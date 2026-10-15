#!/usr/bin/env python3
"""Assemble a fresh AJ03 source/evidence directory; never fit, deploy or archive it."""
from pathlib import Path
import argparse, difflib, hashlib, json, re, shutil

HERE = Path(__file__).resolve().parent
C = HERE.parent
P = C.parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
load = lambda p: json.loads(Path(p).read_text())
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--native', type=Path, default=C/'native-v2')
p.add_argument('--app-build', type=Path, default=C/'work-app/evidence/app-build-v1/build-result.json')
mode = p.add_mutually_exclusive_group(required=True)
mode.add_argument('--draft', action='store_true')
mode.add_argument('--selection', type=Path, help='Final explicit outcome, role-labelled strict receipts, fixtures and report')
p.add_argument('--physical', type=Path, help='Optional prepared-only physical overlay; inputs.json must bind selected native')
p.add_argument('--extra-copies', type=Path, help='Explicit bundle-relative destination -> source map')
p.add_argument('--app-revision', action='append', default=[], metavar='LABEL=BUILD_RESULT',
               help='Preserved earlier app revision: cumulative patches, focused test sources and receipts')
a = p.parse_args()
N, OUT, app_receipt = a.native.resolve(), a.output.resolve(), a.app_build.resolve()
assert not OUT.exists(), 'Refuse to alter an existing bundle'
app = load(app_receipt); APP = Path(app['elf']).parents[2]
prepared = load(N/'prepared.json'); native_build = load(N/'build.json')
assert native_build['returncode'] == 0 and native_build['prepared_sha256'] == sha(N/'prepared.json')
assert app['status'] == 'PASS_BUILD_NOT_HARDWARE_QUALIFIED'
assert sha(app['elf']) == app['elf_sha256']
for name, digest in prepared['source_sha256'].items(): assert sha(N/name) == digest, name
for name, digest in app['source_sha256'].items(): assert sha(APP/name) == digest, name
for name, digest in app['artifact_sha256'].items(): assert sha(app_receipt.parent/name) == digest, name
selection = load(a.selection) if a.selection else None
if selection:
    assert selection['outcome'] in ('MEASURED_GAIN', 'NO_GAIN', 'CORRECTNESS_NOT_QUALIFIED')
    assert selection['strict_receipts'] and selection['fixtures'] and selection['report'] and selection['conclusion']
    assert selection.get('physical_fit', False) is False and selection.get('deployed', False) is False
OUT.mkdir(parents=True)
BASE = P/'cached-cpu-v1/native-sdram-v2'
PRIOR = P/'priority-mesh-v1/native-v2'
m = dict(status='DRAFT_PENDING_NATIVE_VERDICT' if a.draft else selection['outcome'],
         native_revision=str(N), native_baseline=str(BASE), prior_native=str(PRIOR),
         native_source_sha256=prepared['source_sha256'], selected_app_elf_sha256=app['elf_sha256'],
         patches={}, copied_inputs={}, external_evidence_sha256={}, receipt_input_relocations=[],
         strict_receipts={}, fixtures=[], physical_fit=False, deployed=False)
forbidden = {'.elf','.bin','.rbf','.rbf_r','.mif','.z64','.n64','.v64','.o','.a','.qdb','.png'}
def relative(name):
    q = Path(name)
    assert not q.is_absolute() and '..' not in q.parts
    return q
def pin(source):
    source = Path(source).resolve()
    m['external_evidence_sha256'][str(source)] = sha(source)
def put(source, name):
    source = Path(source).resolve(); target = OUT/relative(name)
    assert source.suffix not in forbidden, source
    assert not target.exists(), name
    target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
    m['copied_inputs'][str(name)] = dict(source=str(source), sha256=sha(source))
def patch(name, base, pairs):
    text = ''; records = {}
    for rel, post in sorted(pairs.items()):
        relative(rel); pre = base/rel
        before = pre.read_text() if pre.is_file() else ''; after = post.read_text()
        if before == after: continue
        text += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                    fromfile='a/'+rel if pre.exists() else '/dev/null', tofile='b/'+rel))
        records[rel] = dict(before_sha256=sha(pre) if pre.exists() else None,
                           after_sha256=sha(post), postimage_source=str(post))
        pin(post)
    assert records, name
    target = OUT/relative(name); target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)
    m['patches'][name] = dict(base=str(base), files=records)
    return records

# Compute cumulative diffs against the actual baseline, not the previous revision's delta list.
native_pairs = {name:N/name for name in prepared['source_sha256']
                if not (BASE/name).is_file() or sha(BASE/name) != prepared['source_sha256'][name]}
native_changes = patch('patches/native-from-qualified.patch', BASE, native_pairs)
patch('patches/native-from-aj02.patch', PRIOR, {name:N/name for name in prepared['source_sha256']
      if not (PRIOR/name).is_file() or sha(PRIOR/name) != prepared['source_sha256'][name]})
app_pairs = {name:APP/name for name in app['source_sha256']}
patch('patches/sm64-from-qualified.patch', Path(app['baseline']), app_pairs)
patch('patches/sm64-from-aj02.patch', P/'priority-mesh-v1/work-app-v2', app_pairs)
for name, source in app_pairs.items(): put(source, 'app/'+name); pin(source)
pin(app['elf'])
for name in native_changes: put(N/name, 'native-postimages/'+name)
rtl_names = set(load(C/'work-core/tests/sources.json'))
rtl_names.update(Path(n).name for n in native_changes if '/src/fpga/common/' in n)
for name in sorted(rtl_names): put(N/'frozen/src/fpga/common'/name, 'rtl/'+name)

copy_map = {
 'design/group-abi.md':'design/group-abi.md',
 'work-core/rtl-ready-v2.json':'receipts/rtl-ready.json',
 'work-core/changed-inputs-v2.json':'receipts/rtl-changes.json',
 'work-core/tests/selected-v2.json':'receipts/tests/selected.json',
 'work-core/tests/sources.json':'recipes/rtl-sources.json',
 'work-core/tests/wrapper-v3/manifest.json':'receipts/tests/wrapper.json',
 'work-core/tests/faults-v1/manifest.json':'receipts/tests/faults.json',
 'work-core/tests/combined-v2/manifest.json':'receipts/tests/combined.json',
 'work-core/tests/test_async_wrapper.cpp':'tests/wrapper/test_async_wrapper.cpp',
 'work-core/tests/test_async_faults.cpp':'tests/faults/test_async_faults.cpp',
 'work-core/tests/combined/test_async_combined.cpp':'tests/combined/test_async_combined.cpp',
 'work-core/tests/combined/tb_async_combined.v':'tests/combined/tb_async_combined.v',
 'work-core/tests/run_priority_wrapper.py':'recipes/original/run_priority_wrapper.py',
 'work-core/tests/run_faults.py':'recipes/original/run_faults.py',
 'work-core/tests/combined/run_combined.py':'recipes/original/run_combined.py',
}
for source, target in copy_map.items(): put(C/source, target)
for case, field in [('priority','focused_tests_path'),('cache','source_tests_path')]:
    receipt=Path(app[field]); assert sha(receipt)==app[field.replace('_path','_sha256')]
    test=load(receipt); assert test['status']=='PASS'
    put(receipt, 'receipts/tests/app-'+case+'.json')
    for source,digest in test['inputs_sha256'].items(): assert sha(source)==digest,source
    generated=[Path(s) for s in test['inputs_sha256'] if Path(s).name=='test.c']
    recipes=[Path(s) for s in test['inputs_sha256'] if Path(s).suffix=='.py']
    assert len(generated)==1 and len(recipes)==1
    put(generated[0], 'tests/app/'+case+'.c'); put(recipes[0], 'recipes/original/'+recipes[0].name)
if (APP/'evidence/readiness-v1.json').exists():
    put(APP/'evidence/readiness-v1.json', 'receipts/app-readiness.json')

for source in sorted((C/'work-core/tests/history').iterdir()):
    if source.is_file(): put(source, 'history/rtl/'+source.name)
for rel in ['group-cache-tests-v1/reproducer.py','group-cache-tests-v1/test.c',
            'group-cache-tests-v1/run.log','app-build-v1/build_app-attempt1.py','app-build-v1/attempt1-failure.txt']:
    put(C/'work-app/evidence'/rel, 'history/app/'+rel)
for name in ['prepared.json','build.json']: put(N/name, 'receipts/native/'+name)
put(app_receipt, 'receipts/app-build.json'); put(app_receipt.parent/'build_app.py', 'recipes/original/build_app.py')
for source in [BASE/'prepared.json', PRIOR/'prepared.json', app_receipt]: pin(source)
for name in ['verify.py','run_tests.py']: put(HERE/name, name)
put(Path(__file__), 'build_bundle.py')
selected = load(C/'work-core/tests/selected-v2.json')
assert selected['status'] == 'PASS'
for test in selected['tests']:
    assert sha(test['receipt']) == test['sha256'] and test['result']['status'] == 'PASS'
for name in ['app-priority','app-cache']:
    assert load(OUT/f'receipts/tests/{name}.json')['status'] == 'PASS'

if a.physical:
    physical = a.physical.resolve(); phys = load(physical/'inputs.json')
    assert phys['native_prepared_sha256'] == sha(N/'prepared.json')
    physical_base = P/'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1'
    patch('patches/core-from-qualified.patch', physical_base/'frozen',
          {name:physical/'frozen'/name for name in phys['changed_rtl']})
    put(physical/'inputs.json', 'receipts/physical-prepared-only.json')
    for name in ['inputs.json','timing-qualification.json']: pin(physical_base/name)
    m['physical_overlay'] = dict(path=str(physical), status='PREPARED_ONLY_NOT_FITTED')
if selection:
    put(a.selection, 'selection.json'); put(selection['report'], 'REPORT.txt')
    put(selection['conclusion'], 'receipts/conclusion.json')
    for role, source in selection['strict_receipts'].items():
        assert re.fullmatch('[a-z0-9_-]+', role), role
        r = load(source); assert str(r.get('status','')).startswith('PASS'), source
        target = 'receipts/strict/'+role+'.json'; put(source, target); m['strict_receipts'][role] = target
    for i, source in enumerate(selection['fixtures']):
        fixture = Path(source).resolve(); entry = dict(root=str(fixture), manifests={})
        for rel in ['capture/manifest.json','guest/manifest.json']:
            target = f'receipts/fixtures/{i}/'+rel; put(fixture/rel, target); entry['manifests'][rel] = sha(fixture/rel)
        pin(fixture/'input.cfg'); entry['input_cfg_sha256'] = sha(fixture/'input.cfg'); m['fixtures'].append(entry)
if a.extra_copies:
    for target, source in load(a.extra_copies).items(): put(source, target)
    pin(a.extra_copies)
m['app_revisions'] = []
for specification in a.app_revision:
    label, receipt_path = specification.split('=', 1)
    assert re.fullmatch('[a-z0-9_-]+', label), label
    receipt_path = Path(receipt_path).resolve(); revision = load(receipt_path)
    root = Path(revision['elf']).parents[2]; prefix = 'apps/'+label
    assert sha(revision['elf']) == revision['elf_sha256']; pin(revision['elf'])
    for name, digest in revision['source_sha256'].items():
        assert sha(root/name) == digest; pin(root/name)
    for name, digest in revision['artifact_sha256'].items():
        assert sha(receipt_path.parent/name) == digest
    patch(prefix+'/sm64-from-qualified.patch', Path(revision['baseline']),
          {name:root/name for name in revision['source_sha256']})
    patch(prefix+'/sm64-from-aj02.patch', P/'priority-mesh-v1/work-app-v2',
          {name:root/name for name in revision['source_sha256']})
    put(receipt_path, prefix+'/build-result.json')
    put(receipt_path.parent/'build_app.py', prefix+'/build_app.py')
    for case, field in [('priority','focused_tests_path'),('cache','source_tests_path')]:
        test_path=Path(revision[field]); assert sha(test_path)==revision[field.replace('_path','_sha256')]
        test=load(test_path); assert test['status']=='PASS'
        put(test_path, prefix+'/tests/'+case+'-receipt.json')
        for source,digest in test['inputs_sha256'].items(): assert sha(source)==digest,source
        for source in test['inputs_sha256']:
            q=Path(source)
            if q.name=='test.c': put(q,prefix+'/tests/'+case+'.c')
            if q.suffix=='.py': put(q,prefix+'/tests/'+q.name)
    m['app_revisions'].append(dict(label=label,root=str(root),elf_sha256=revision['elf_sha256'],
                                  receipt=prefix+'/build-result.json'))

# Historical receipts may refer to a source later superseded. Bind its exact preserved bytes,
# never silently validate the new bytes against the old receipt.
by_hash = {digest:N/name for name,digest in prepared['source_sha256'].items()}
by_hash.update({v['sha256']:Path(v['source']) for v in m['copied_inputs'].values()})
def bind(value, receipt):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child,str) and re.fullmatch('[0-9a-f]{64}',child) and key.startswith('/'):
                original = Path(key); current = original
                if not current.is_file() or sha(current) != child:
                    assert child in by_hash, (receipt,key,child)
                    current = by_hash[child]
                    m['receipt_input_relocations'].append(dict(receipt=receipt,historical_path=key,
                        sha256=child,immutable_replacement=str(current)))
                pin(current)
            else: bind(child,receipt)
    elif isinstance(value,list):
        for child in value: bind(child,receipt)
for name in list(m['copied_inputs']):
    if name.endswith('.json'): bind(load(OUT/name),name)
(OUT/'README.txt').write_text('AJ03 coarse mesh source and evidence bundle\n\n'
    + ('DRAFT: native outcome is pending.\n' if a.draft else 'Outcome: '+selection['outcome']+'. Read REPORT.txt for denominators and limits.\n')
    + '\nNo physical fit, deployment, ROM, game assets or executable binaries are included.\n'
    'verify.py checks this source inventory. --patches and --evidence additionally\n'
    'require the hash-bound original workspace and verify exact patch round trips\n'
    'and external evidence. Native patches include simulator wiring; they are not\n'
    'a hardware deployment patch. An optional separately identified physical\n'
    'overlay remains prepared-only and carries no timing qualification.\n\n'
    'run_tests.py --output /tmp/aj03-tests runs portable wrapper/fault/combined RTL\n'
    'and actual extracted-C ownership/cache tests with sanitizers. It requires\n'
    'Verilator and a C/C++ compiler. These tests use synthetic inputs, not game\n'
    'assets. Full native scene replay requires the separately held, hash-bound\n'
    'fixtures and tools. Original workspace recipes preserve their audit paths.\n')
m['excluded_inventory'] = ['manifest.json']
m['files'] = {str(q.relative_to(OUT)):sha(q) for q in sorted(OUT.rglob('*')) if q.is_file()}
(OUT/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
print(json.dumps(dict(output=str(OUT),status=m['status'],files=len(m['files']),
      native_sources=len(prepared['source_sha256']),patches=len(m['patches'])),indent=2))
