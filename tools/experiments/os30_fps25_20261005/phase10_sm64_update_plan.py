#!/usr/bin/env python3
"""Prepare a private ten-destination SM64 plan; expose no sibling mutation command."""
import argparse,difflib,hashlib,importlib.util,json,os,re,shutil,stat
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
SIB=R.parent/'SM64';S=P/'renderer-pgo-delivery-v7';D=P/'final-candidate-delivery-v2';O=P/'sm64-update-plan-v1'
H=R/'tools/experiments/os30_fps25_20261005/phase10_final_candidate_delivery_v2.py'
READINESS=P/'final-candidate-delivery-readiness-v1'
ELF='fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b'
DIAG='648b7c4e36daa8e4e7e132c947199c0c2ef51a652dbcdc1639b4aaa4026c3b85'
HELPER='4545f7ff875f49fe332902c659e2b7f11871d355e3508a6325d39459ae6cf8ae'
SOFTWARE_MANIFEST='d923e397b89aab6f94e00405be78cc017dccd9b18a7a615854f15c3782c7e218'
def require(ok,why):
 if not ok:raise RuntimeError(why)
def safe(base,name):
 name=Path(name);require(not name.is_absolute() and name.parts and not any(x in ('.','..','.git') for x in name.parts),'Unsafe relative path')
 path=base/name
 for part in reversed([path,*path.parents]):
  if os.path.lexists(part):
   mode=part.lstat().st_mode
   require(not stat.S_ISLNK(mode),'Symlink: '+str(part))
   require(stat.S_ISREG(mode) if part==path else stat.S_ISDIR(mode),'Special path: '+str(part))
 return path
def digest(data):return hashlib.sha256(data).hexdigest()
def sha(path):return digest(path.read_bytes())
def state(path):
 if not os.path.lexists(path):return None
 require(stat.S_ISREG(path.lstat().st_mode),'Not regular: '+str(path))
 return dict(sha256=sha(path),mode=stat.S_IMODE(path.stat().st_mode))
def put(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
def copy_bytes(path,data,mode=0o644):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('xb') as f:f.write(data)
 path.chmod(mode)
def bound_inputs():
 require(sha(H)==HELPER,'Changed delivery helper')
 require(sha(S/'manifest.json')==SOFTWARE_MANIFEST,'Changed software selection')
 sm=json.loads((S/'manifest.json').read_text())
 for n,h in sm['files'].items():require(sha(safe(S,n))==h,'Software drift: '+n)
 old=json.loads((READINESS/'manifest.json').read_text())
 require(old['passed'],'Readiness failed')
 for n,h in old['files'].items():require(sha(safe(READINESS,n))==h,'Readiness inventory drift')
 return json.loads((S/'prepared.json').read_text()),json.loads((READINESS/'destination-inventory.json').read_text())
def prepare():
 p,old=bound_inputs();require(not O.exists(),'Preserve existing private plan')
 guards={};data={}
 def guard(name,expected):
  path=safe(SIB,name);st=state(path);require(st is not None and st['sha256']==expected,'Preimage drift: '+name)
  guards[name]=st;data[name]=path.read_bytes();require(digest(data[name])==expected,'Read race: '+name)
 for name,row in p['sources'].items():guard(name,row['before_sha256'])
 guard('.obj/sm64-perfdiag/app.elf',DIAG)
 source_rows=[r for r in old['source_entries'] if r['destination_root']=='SM64_sibling']
 require(len(source_rows)==6 and len(p['source_changes'])==4 and len(p['profiles'])==2,'Wrong source scope')
 require({r['relative_path'] for r in source_rows}==set(p['source_changes'])|set(p['profiles']),'Wrong software inventory')
 entries=[]
 for row in source_rows:
  name=row['relative_path'];before=state(safe(SIB,name))
  expected=row['before_sha256'];require((None if before is None else before['sha256'])==expected,'Software preimage mismatch')
  src=safe(S/'sources',name);require(sha(src)==row['after_sha256'],'Software payload mismatch')
  entries.append(dict(path=name,kind=row['category'],before=before,after_sha256=sha(src),payload='payload/'+name,mode=before['mode'] if before else 0o644))
 for row in old['artifact_entries']:
  name=row['relative_path'];guard(name,row['before_sha256']);kind=row['category']
  require(kind in ('app','core') and row['destination_root']=='SM64_sibling','Unexpected artifact scope')
  entries.append(dict(path=name,kind=kind,before=guards[name],after_sha256=ELF if kind=='app' else None,
   payload='payload/'+name if kind=='app' else None,
   deferred_delivery_member='payload/Cores/ThinkElastic.SM64/os30.rbf_r' if kind=='core' else None,mode=guards[name]['mode']))
 require(len(entries)==10 and len({x['path'] for x in entries})==10,'Wrong destination count')
 require('.obj/sm64-perfdiag/app.elf' not in {x['path'] for x in entries},'Diagnostic is guard-only')
 patch=(S/'integration.patch').read_text();patch_paths=set(re.findall(r'^\+\+\+ b/(.+)$',patch,re.M))
 require(patch_paths==set(p['source_changes']),'Patch touches unexpected paths')
 O.mkdir();diffs=''
 for n,contents in data.items():copy_bytes(O/'preimages'/n,contents,guards[n]['mode'])
 for row in entries:
  if row['kind']=='core':continue
  source=S/'payload/Assets/sm64/common/sm64.elf' if row['kind']=='app' else S/'sources'/row['path']
  contents=source.read_bytes();require(digest(contents)==row['after_sha256'],'Copy source changed')
  copy_bytes(O/row['payload'],contents,row['mode'])
  if row['kind']=='sm64_text':diffs+=''.join(difflib.unified_diff(data[row['path']].decode().splitlines(True),contents.decode().splitlines(True),fromfile='a/'+row['path'],tofile='b/'+row['path']))
 copy_bytes(O/'integration.patch',(S/'integration.patch').read_bytes())
 copy_bytes(O/'preimage-to-payload.diff',diffs.encode())
 for n in ('README.txt','prepared.json','rebuild-proof.json','manifest.json'):copy_bytes(O/'software-evidence'/n,(S/n).read_bytes())
 copy_bytes(O/'recipe.py',Path(__file__).read_bytes(),0o755)
 plan=dict(private_plan_only=True,execution_ready=False,sibling_root=str(SIB),destination_count=10,entries=entries,guard_inputs=guards,
  diagnostic_guard='.obj/sm64-perfdiag/app.elf',diagnostic_sha256=DIAG,software_changes=6,normal_app_copies=2,core_copies=2,
  deferred_core_requires='New passing control-cones-fit-v1 plus exact reviewed final-candidate-delivery-v2 manifest. Resolve both copies to its independently reversed payload only.',
  required_execution_gates=['Explicit root selection and any filesystem approval for this sibling.',
   'verify-ready with exact reviewed plan and delivery manifest hashes; all current preimages/modes and both new-profile absences must still match.',
   'Capture durable backups/absent records before first mutation; recheck each destination immediately before same-directory fsync/atomic replacement.',
   'Journal intent/done and verify all ten outputs plus guard-only inputs; rollback only exact recorded postimages/modes, never force-overwrite later edits.'],
  normal_build_command='make -C src/sm64 -j4 PROFILE=0 PERF_DIAG=0 "$(pwd)/.obj/sm64/app.elf"',
  normal_build_requires='Checkout root; fresh empty .obj/sm64 with prior contents preserved elsewhere; pinned SDK/compiler. Broad make clean deletes release trees and is not part of this plan.',
  expected_normal_elf_sha256=ELF,no_SD_Quake2_OS_assets_or_diagnostic_writes=True,hardware_repository_scope_excluded=True,
  inputs_sha256={str(x.relative_to(R)):sha(x) for x in [H,S/'manifest.json',S/'prepared.json',S/'rebuild-proof.json',READINESS/'manifest.json',READINESS/'destination-inventory.json',P/'final-candidate-delivery-source-review-v2/manifest.json']})
 put(O/'plan.json',plan)
 copy_bytes(O/'README.txt',('PRIVATE PLAN ONLY: ten SM64 destinations, no sibling mutation command. Four text files + two raw profiles preserve future normal build reproduction; two normal-app paths receive exactfff; two core paths await the qualified delivery-v2 bit-reversed RBF. Dedicated .obj/sm64-perfdiag/app.elf remains648b and is guard-only. The old release-folder diagnostic copy is saved in preimages before any future replacement.\n\nRun verify-ready only after physical acceptance and delivery-v2 creation, passing both exact reviewed manifest hashes. This command is read-only and does not install. Any future transaction must implement plan.json gates and remain restricted to these ten relative paths. No SD/Quake2/hardware-repository writes are included.\n\nNormal rebuild, from the SM64 root and a fresh empty .obj/sm64 after preserving prior contents:\n  '+plan['normal_build_command']+'\nExpected ELF '+ELF+'\nDo not use broad make clean: it also removes release trees. The package-local gcda directory structure and opaque original GCC key must remain exact. Only two renderer TUs use PGO, and PROFILE/diagnostic modes retain their original flags. See software-evidence for the exact ordinary clean rebuild proof; no rebuild is performed here.\n').encode())
 for n,st in guards.items():require(state(safe(SIB,n))==st,'Preimage changed during plan')
 put(O/'manifest.json',dict(plan_only=True,files={str(x.relative_to(O)):sha(x) for x in sorted(O.rglob('*')) if x.is_file()},sibling_modified=False,entrypoint_supports_mutation=False))
 print('PLAN',sha(O/'manifest.json'))
def verify_ready(plan_hash,delivery_hash):
 require(re.fullmatch('[0-9a-f]{64}',plan_hash or '') is not None and sha(O/'manifest.json')==plan_hash,'Exact reviewed plan hash required')
 m=json.loads((O/'manifest.json').read_text())
 for n,h in m['files'].items():require(sha(safe(O,n))==h,'Plan drift: '+n)
 p=json.loads((O/'plan.json').read_text());require(p['sibling_root']==str(SIB) and p['destination_count']==10,'Wrong plan scope')
 for n,h in p['inputs_sha256'].items():require(sha(safe(R,n))==h,'Plan input changed: '+n)
 for n,st in p['guard_inputs'].items():require(state(safe(SIB,n))==st,'Sibling preimage changed: '+n)
 for row in p['entries']:require(state(safe(SIB,row['path']))==row['before'],'Destination or absent profile changed')
 spec=importlib.util.spec_from_file_location('qualified_delivery_v2',H);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 delivery=module.payload(delivery_hash);result,raw=module.qualification()
 require(result['comparative_physical_gate_pass'],'New fit does not pass')
 core=safe(D,'payload/Cores/ThinkElastic.SM64/os30.rbf_r');app=safe(D,'payload/Assets/sm64/common/sm64.elf')
 require(sha(core)==delivery['reversed_sha256'] and sha(app)==delivery['elf_sha256']==ELF,'Wrong delivery payload')
 table=bytes(int(f'{i:08b}'[::-1],2) for i in range(256));require(core.read_bytes()==raw.read_bytes().translate(table),'Core reversal mismatch')
 print(json.dumps(dict(read_only_ready_check=True,destination_count=10,core_sha256=sha(core),elf_sha256=ELF,execution_performed=False),indent=2))
if __name__=='__main__':
 a=argparse.ArgumentParser(description=__doc__);a.add_argument('action',choices=['prepare','verify-ready']);a.add_argument('--plan-manifest-sha256');a.add_argument('--delivery-manifest-sha256');v=a.parse_args()
 if v.action=='prepare':require(not v.plan_manifest_sha256 and not v.delivery_manifest_sha256,'No hashes for plan creation');prepare()
 else:verify_ready(v.plan_manifest_sha256,v.delivery_manifest_sha256)
