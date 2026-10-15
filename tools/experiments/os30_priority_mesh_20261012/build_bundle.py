#!/usr/bin/env python3
"""Assemble immutable AJ02 source/evidence bundle. Never builds/deploys a core."""
from pathlib import Path
import argparse,difflib,hashlib,json,re,shutil
ROOT=next(p for p in Path(__file__).resolve().parents if (p/'src/fpga').is_dir())
P=ROOT/'docs/measurements/os30-psram32-20261009';A=P/'priority-mesh-v1';HERE=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--native',type=Path,default=A/'native-v2');p.add_argument('--app-build',type=Path,default=A/'work-app-v2/evidence/app-build-v2/build-result.json');p.add_argument('--strict',type=Path,action='append',default=[]);p.add_argument('--fixture',type=Path,action='append',default=[]);p.add_argument('--app-revision',action='append',default=[],metavar='LABEL=BUILD_RESULT');p.add_argument('--extra-patches',type=Path);p.add_argument('--extra-copies',type=Path);p.add_argument('--physical',type=Path,default=A/'physical/prepared-v2');p.add_argument('--report',type=Path);p.add_argument('--draft',action='store_true');args=p.parse_args()
N=args.native.resolve();app_receipt=args.app_build.resolve();build=json.loads(app_receipt.read_text());APP=Path(build['elf']).parents[2];OUT=args.output.resolve();assert not OUT.exists();assert args.draft or len(args.strict)>=2,'Final bundle requires strict paired receipts';assert args.draft or args.fixture,'Final bundle requires explicit fixture binding';assert args.draft or args.report,'Final bundle requires explicit current result report';OUT.mkdir(parents=True)
BASE=P/'cached-cpu-v1/native-sdram-v2';PHYSICAL=P/'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1';prepared=json.loads((N/'prepared.json').read_text())
for name,h in prepared['source_sha256'].items():assert sha(N/name)==h,name
assert len(prepared['source_sha256'])==181
manifest={'status':'DRAFT_PENDING_FINAL_REPORT'if args.draft else'EXPERIMENTAL_NATIVE_REVIEW_NOT_FITTED_OR_DEPLOYED','native_revision':str(N),'native_baseline':str(BASE),'physical_baseline':str(PHYSICAL),'app_revision':str(APP),'native_source_sha256':prepared['source_sha256'],'patches':{},'copied_inputs':{},'external_evidence_sha256':{},'receipt_input_relocations':[],'strict_receipts':[]}
def pin(source):
 source=Path(source).resolve();manifest['external_evidence_sha256'][str(source)]=sha(source)
def put(source,name):
 source=Path(source).resolve();target=OUT/name;assert not target.exists(),name;assert source.suffix not in {'.elf','.bin','.rbf','.rbf_r','.mif','.z64','.n64','.v64','.o','.a','.qdb'};target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);manifest['copied_inputs'][name]={'source':str(source),'sha256':sha(source)}
def patch(name,base,pairs):
 text='';records={}
 for relative,post in sorted(pairs.items()):
  pre=base/relative;before=pre.read_text()if pre.exists()else'';after=post.read_text()
  if before==after:continue
  text+=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+relative if pre.exists()else'/dev/null',tofile='b/'+relative))
  records[relative]={'before_sha256':sha(pre)if pre.exists()else None,'after_sha256':sha(post),'postimage_source':str(post)};pin(post)
 (OUT/name).parent.mkdir(parents=True,exist_ok=True);(OUT/name).write_text(text);manifest['patches'][name]={'base':str(base),'files':records}
patch('native.patch',BASE,{name:N/name for name in prepared['changed_inputs']})
physical=args.physical.resolve();phys=json.loads((physical/'inputs.json').read_text());assert phys['native_prepared_sha256']==sha(N/'prepared.json')
patch('core.patch',PHYSICAL/'frozen',{name:physical/'frozen'/name for name in phys['changed_rtl']})
for name,h in build['source_sha256'].items():assert sha(APP/name)==h,(APP,name);pin(APP/name)
manifest['selected_app_elf_sha256']=build['elf_sha256']
app_names=list(build['source_sha256']);patch('sm64.patch',Path(build['baseline']),{name:APP/name for name in app_names});patch('sm64-from-predecessor.patch',Path(build['predecessor']),{name:APP/name for name in app_names});pin(Path(build['elf']))
# Copy active/new RTL plus unchanged dependencies used by portable focused tests.
rtl_names=set(json.loads((A/'work-core-v2/tests/sources.json').read_text()))|{Path(n).name for n in prepared['changed_inputs']if '/src/fpga/common/'in n}
for name in sorted(rtl_names):put(N/'frozen/src/fpga/common'/name,'rtl/'+name)
copy_map={'design/abi-v2.json': 'abi.json', 'design/ports.vh': 'ports.vh', 'design/scope-v2.json': 'scope.json', 'build_native_v2.py': 'recipes/build_native.py', 'physical/prepare_v2.py': 'recipes/prepare_physical.py', 'physical/review_v2.py': 'recipes/review_physical.py', 'physical/assessment-v2.txt': 'reviews/physical-assessment.txt', 'physical/review-v2.json': 'reviews/physical-review.json', 'work-core-v2/new-sources.json': 'recipes/new-sources.json', 'work-core-v2/tests/sources.json': 'recipes/wrapper-sources.json', 'work-core-v2/rtl-ready-v1.json': 'reviews/rtl-ready.json', 'work-core-v2/tests/wrapper-v3/manifest.json': 'receipts/tests/wrapper.json', 'work-core-v2/tests/test_async_wrapper.cpp': 'tests/wrapper/test_async_wrapper.cpp', 'work-core-v2/tests/run_priority_wrapper.py': 'recipes/tests/run_priority_wrapper.py', 'work-core-v2/tests/combined-v1/manifest.json': 'receipts/tests/combined.json', 'work-core-v2/tests/combined/tb_async_combined.v': 'tests/combined/tb_async_combined.v', 'work-core-v2/tests/combined/test_async_combined.cpp': 'tests/combined/test_async_combined.cpp', 'work-core-v2/tests/combined/run_combined.py': 'recipes/tests/run_combined.py'}
for rel,name in copy_map.items():put(A/rel,name)
put(physical/'inputs.json','receipts/physical-prepared-only.json')
if args.report:put(args.report,'REPORT.txt')
for path,name in [(N/'prepared.json','receipts/native/prepared.json'),(N/'build.json','receipts/native/build.json'),(app_receipt,'receipts/app-build.json'),(app_receipt.parent/'build_app.py','recipes/build_app.py')]:put(path,name)
for name in ['verify.py','run_tests.py']:put(HERE/name,name)
put(Path(__file__).resolve(),'build_bundle.py')
for n,path in enumerate(args.strict):
 path=path.resolve();receipt=json.loads(path.read_text());assert str(receipt['status']).startswith('PASS'),path;target=f'receipts/native/strict-{n}.json';put(path,target);manifest['strict_receipts'].append(target)
if args.extra_copies:
 extra=json.loads(args.extra_copies.read_text())
 for target,source in extra.items():put(Path(source),target)
 pin(args.extra_copies)
# Bind explicitly selected fixtures and historical app revisions, without assets.
manifest['fixtures']=[]
for index,fixture in enumerate(args.fixture):
 fixture=fixture.resolve();entry={'root':str(fixture),'manifests':{}}
 for relative in ['capture/manifest.json','guest/manifest.json']:
  source=fixture/relative;pin(source);target=f'receipts/fixtures/{index}/'+relative;put(source,target);entry['manifests'][relative]=sha(source)
 config=fixture/'input.cfg'
 if config.exists():pin(config);entry['input_cfg_sha256']=sha(config)
 manifest['fixtures'].append(entry)
manifest['app_revisions']=[]
for specification in args.app_revision:
 label,receipt_path=specification.split('=',1);assert re.fullmatch('[a-z0-9_-]+',label),label
 receipt_path=Path(receipt_path).resolve();revision=json.loads(receipt_path.read_text());root=Path(revision['elf']).parents[2]
 for name,h in revision['source_sha256'].items():assert sha(root/name)==h,(root,name);pin(root/name)
 target=f'apps/{label}';patch(target+'/sm64.patch',Path(revision['baseline']),{name:root/name for name in revision['source_sha256']})
 put(receipt_path,target+'/build-result.json');put(receipt_path.parent/'build_app.py',target+'/build_app.py');pin(Path(revision['elf']))
 manifest['app_revisions'].append({'label':label,'root':str(root),'elf_sha256':revision['elf_sha256'],'receipt':target+'/build-result.json'})
if args.extra_patches:
 for item in json.loads(args.extra_patches.read_text()):
  name=item['name'];assert not Path(name).is_absolute()and '..'not in Path(name).parts
  patch(name,Path(item['base']),{relative:Path(post)for relative,post in item['pairs'].items()})
 pin(args.extra_patches)
for path in [BASE/'prepared.json',PHYSICAL/'inputs.json',PHYSICAL/'timing-qualification.json',app_receipt]:pin(path)
by_hash={sha(N/name):N/name for name in prepared['source_sha256']};by_hash.update({v['sha256']:Path(v['source'])for v in manifest['copied_inputs'].values()})
def bind(value,receipt):
 if isinstance(value,dict):
  for key,child in value.items():
   if isinstance(child,str)and re.fullmatch('[0-9a-f]{64}',child)and isinstance(key,str)and key.startswith('/'):
    original=Path(key);current=original
    if not current.is_file()or sha(current)!=child:
     assert child in by_hash,(receipt,key,child);current=by_hash[child];manifest['receipt_input_relocations'].append({'receipt':receipt,'historical_path':key,'sha256':child,'immutable_replacement':str(current)})
    pin(current)
   else:bind(child,receipt)
 elif isinstance(value,list):
  for child in value:bind(child,receipt)
for name in list(manifest['copied_inputs']):
 if name.endswith('.json'):bind(json.loads((OUT/name).read_text()),name)
manifest['excluded_inventory']=['manifest.json','verification.json']
(OUT/'README.txt').write_text('AJ02 snapshot priority mesh source/evidence bundle\n\nExperimental native-only implementation. No physical fit or deployment.\n'+('This is an assembly draft; final report/selection has not been sealed.\n'if args.draft else'Strict native receipt paths are listed in manifest.json. REPORT.txt contains\nthe selected current experiment outcome; no physical fit or deployment occurred.\n')+'\nverify.py --patches --evidence checks exact forward/reverse source patches,\nall181 frozen native inputs, copied receipts and external evidence.\nrun_tests.py --output /tmp/aj02-tests runs portable actual-RTL focused tests.\nNative frame/repair replay also needs the separately held game inputs and tools;\nthose are hash-bound, not distributed. Historical workspace recipes retain\noriginal paths for audit. core.patch targets the qualified physical frozen tree;\nrecipes/prepare_physical.py records the required isolated source/QSF integration.\nNo script in this bundle automatically fits or deploys a core.\n')
manifest['files']={str(p.relative_to(OUT)):sha(p)for p in sorted(OUT.rglob('*'))if p.is_file()and str(p.relative_to(OUT)) not in manifest['excluded_inventory']}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({'output':str(OUT),'files':len(manifest['files']),'native_sources':len(manifest['native_source_sha256']),'patches':len(manifest['patches']),'status':manifest['status']},indent=2))
