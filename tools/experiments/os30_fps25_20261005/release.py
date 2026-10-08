#!/usr/bin/env python3
"""Qualify and publish the selected SM64 application on retained single-issue A."""
import argparse, hashlib, json, os, re, shutil, stat, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005'
CAND=OUT/'render-v6'
VAL=OUT/'validation'
DEST=ROOT.parent/'SM64'
CARD=Path('/run/media/alberto/POCKETDEV')
CORE='d3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f'
OS='054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
OLD_FPS='94d8a7e30f5ce9615722f54f71018eb2f520e5214cc06c550477b44c7c255168'
OLD_NORMAL='e11905966c8d5c3c8a8d086e3269e5c97a64042eb77c21c1e15d9964b68770a4'
PREVIOUS=ROOT/'docs/measurements/os30-simple-20261005/validation'
FRAMES={40,120,200,360,400,500,600,720}
REPLAY_KEYS={(mode,frame) for mode in (1,2) for frame in FRAMES}
CAPTURES={'baseline-normal':PREVIOUS/'capture-simple-v4-normal',
 'baseline-diagnostic':PREVIOUS/'capture-simple-v4-diagnostic',
 'render-v5-normal':VAL/'capture-render-v5-normal',
 'render-v6-fps':VAL/'capture-render-v6-fps'}
SOURCES={'gfx_pc.c':'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
 'gfx_gpu.c':'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
 'perf_pocket.h':'src/sm64/pocket/perf_pocket.h',
 'perf_pocket.c':'src/sm64/pocket/perf_pocket.c',
 'Makefile.sm64':'src/sm64/Makefile'}
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
def write_json(p,obj):p.write_text(json.dumps(obj,indent=2)+'\n')
def core_pins():
 for rel in ('runtime/pocket/os30.rbf_r','build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r'):
  assert sha(DEST/rel)==CORE,rel

def source_inventory(directory):
 # Mirror prepare.py exactly; build outputs outside src/tools are unrelated.
 paths={Path('Makefile'),Path('Mario64.z64')}
 for top in ('src','tools'):
  for path in (directory/top).rglob('*'):
   relative=path.relative_to(directory)
   if any(x in ('.git','__pycache__') for x in relative.parts):continue
   if path.is_file():paths.add(relative)
 return {str(p) for p in paths}

def check_source_tree(directory,expected):
 assert source_inventory(directory)==set(expected),('Unexpected source paths',str(directory))
 for rel,h in expected.items():assert sha(directory/rel)==h,('Unexpected source input',str(directory/rel))

def card_precheck():
 """Read-only mount, retained core/OS and destination checks."""
 assert CARD.is_mount(),('SD card is not mounted',str(CARD))
 entries=json.loads(subprocess.check_output(['findmnt','--json','--target',str(CARD),
  '--output','TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN'],text=True))['filesystems']
 assert len(entries)==1 and entries[0]['target']==str(CARD)
 mount=entries[0];assert 'rw' in mount['options'].split(','),('SD mount is read-only',mount)
 assert not (os.statvfs(CARD).f_flag & os.ST_RDONLY),'SD filesystem is read-only'
 for relative,h in [('Cores/ThinkElastic.SM64/os30.rbf_r',CORE),('Assets/sm64/common/os.bin',OS)]:
  path=CARD/relative;assert not path.is_symlink() and sha(path)==h,relative
 app=CARD/'Assets/sm64/common/sm64.elf'
 assert not app.is_symlink() and sha(app)==OLD_FPS,'SD application differs from frozen deployed P3'
 return {'target':mount['target'],'source':mount['source'],'fstype':mount['fstype'],
  'major_minor':mount['maj:min'],'st_dev':CARD.stat().st_dev}

def card_guard(identity):
 assert CARD.is_mount() and CARD.stat().st_dev==identity['st_dev'],'SD mount identity changed'
 entries=json.loads(subprocess.check_output(['findmnt','--json','--target',str(CARD),
  '--output','TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN'],text=True))['filesystems']
 assert len(entries)==1
 mount=entries[0]
 assert (mount['target'],mount['source'],mount['fstype'],mount['maj:min'])==(
  identity['target'],identity['source'],identity['fstype'],identity['major_minor'])
 assert 'rw' in mount['options'].split(',') and not (os.statvfs(CARD).f_flag & os.ST_RDONLY)
 assert sha(CARD/'Cores/ThinkElastic.SM64/os30.rbf_r')==CORE
 assert sha(CARD/'Assets/sm64/common/os.bin')==OS

def qualify():
 evidence={}
 def remember(p,expected=None):
  p=Path(p);assert p.resolve().is_relative_to(ROOT),p
  digest=sha(p)
  if expected is not None:assert digest==expected,p
  relative=str(p.relative_to(ROOT))
  assert relative not in evidence or evidence[relative]==digest,('Evidence changed while reading',p)
  evidence[relative]=digest
  return digest
 def bind(p):
  remember(p)
  return read(p)
 remember(Path(__file__))
 for name in ('analyze.py','common.py','replay.py'):remember(Path(__file__).parent/'validation'/name)
 normal=bind(VAL/'results-render-v5-normal.json')
 fps=bind(VAL/'results-render-v6-fps.json')
 capture_records={}
 for label,cap in CAPTURES.items():
  m=bind(cap/'inputs.json')
  assert m['success'] and not m['panel'] and not m['geometry_capability'] and not m['command_dma'] and m['audio']
  assert m['hw_features']=='0x2b136753'
  for path,h in m['sha256'].items():remember(Path(path),h)
  for rel,h in m['results_sha256'].items():remember(cap/rel,h)
  capture_records[label]=m
 baseline_hashes={'baseline-normal':OLD_NORMAL,'baseline-diagnostic':OLD_FPS}
 for label,h in baseline_hashes.items():assert sha(CAPTURES[label]/'sm64.elf')==h
 for label,r,elf in [('normal',normal,CAND/'app.elf'),('fps',fps,CAND/'diagnostic.elf')]:
  expected_label='render-v5-normal' if label=='normal' else 'render-v6-fps'
  base_label='baseline-normal' if label=='normal' else 'baseline-diagnostic'
  assert r['candidate']==expected_label and r['baseline']==base_label
  assert r['baseline_elf_sha256']==baseline_hashes[base_label]
  assert r['exact_inputs_except_app'] and r['scene_identical']
  assert r['candidate_elf_sha256']==sha(elf)
  comparisons=r['gpu_comparisons']
  assert len(comparisons)==16 and {(x['mode'],x['frame']) for x in comparisons}==REPLAY_KEYS
  assert r['all_replayed_pixels_depth_exact']
  assert all(x['color']['different_pixels']==x['depth']['different_pixels']==0 for x in comparisons)
  assert (r['gameplay']['first_frame'],r['gameplay']['last_frame'],r['gameplay']['frames'])==(360,720,361)
  assert r['gameplay']['metrics']['gpu_cmd_words']['saving_percent']==0
  assert [(w['first_frame'],w['last_frame']) for w in r['windows']]==[(f,min(720,f+59)) for f in range(360,721,60)]
  assert all(w['metrics']['est_cycles']['saving_percent']>0 for w in r['windows'])
  assert r['gameplay']['metrics']['est_cycles']['saving_percent']>0
  assert r['gameplay']['metrics']['insns']['saving_percent']>0
  for rel,h in r['evidence_sha256'].items():remember(ROOT/rel,h)
  # The normal executable is byte-identical to v5; its replay remains exact.
  assert sha(CAPTURES[expected_label]/'sm64.elf')==sha(elf)
  def non_app_inputs(m):return {Path(p).name:h for p,h in m['sha256'].items() if Path(p).name!='sm64.elf'}
  assert non_app_inputs(capture_records[base_label])==non_app_inputs(capture_records[expected_label])
  assert (CAPTURES[base_label]/'scene.csv').read_bytes()==(CAPTURES[expected_label]/'scene.csv').read_bytes()

 # Freeze and check actual GPU output bytes, traffic keys, logs and vectors.
 replay_root=PREVIOUS/'replay-single-A'
 replay_manifest=bind(replay_root/'manifest.json')
 model_hash=remember(replay_root/'obj/Vtb_gpu',replay_manifest['simulator_sha256'])
 for rel,h in replay_manifest['sources'].items():remember(replay_root/rel,h)
 records=[]
 for filename in ('replay-results-baseline.json','replay-results-render-v5-normal.json','replay-results-render-v6-fps.json'):
  records+=bind(VAL/filename)
 indexed={}
 for row in records:
  key=(row['label'],row['mode'],row['frame']);assert key not in indexed
  assert row['label'] in CAPTURES and (row['mode'],row['frame']) in REPLAY_KEYS
  assert row['hardware_sha256']==model_hash
  binary=ROOT/row['binary'];remember(binary)
  data=binary.read_bytes();assert len(data)==307212
  assert hashlib.sha256(data[:153600]).hexdigest()==row['framebuffer_sha256']
  assert hashlib.sha256(data[153612:]).hexdigest()==row['depth_sha256']
  log=binary.with_suffix('.log');remember(log,row['log_sha256'])
  assert {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log.read_text())}==row['stats']
  remember(binary.with_suffix('.vec'))
  remember(CAPTURES[row['label']]/'gpuvec'/f"frame_{row['frame']:05d}.vec",row['vector_sha256'])
  indexed[key]=row
 assert set(indexed)=={(label,mode,frame) for label in CAPTURES for mode,frame in REPLAY_KEYS}
 for r in (normal,fps):
  for x in r['gpu_comparisons']:
   before=indexed[(r['baseline'],x['mode'],x['frame'])]
   after=indexed[(r['candidate'],x['mode'],x['frame'])]
   assert before['framebuffer_sha256']==after['framebuffer_sha256']
   assert before['depth_sha256']==after['depth_sha256']
   assert x['baseline_gpu_cycles']==before['stats']['cycles'] and x['candidate_gpu_cycles']==after['stats']['cycles']
   assert x['baseline_commands']==before['commands'] and x['candidate_commands']==after['commands']

 review=bind(OUT/'independent-review/render-v6-source-abi.json')
 assert review['source_review_passed'] and review['candidate']=='render-v6'
 for key in ('all_sdk_inputs_unchanged','all_compiler_dependencies_in_closed_tree'):
  assert review['stage_input_closure'][key]
 assert not review['stage_input_closure']['missing_inputs'] and not review['stage_input_closure']['unexpected_source_files']
 stage_manifest_path=OUT/'independent-review/render-v6-stage-inputs.json'
 remember(stage_manifest_path,review['stage_input_closure']['manifest_sha256'])
 stage_manifest=read(stage_manifest_path)
 expected_stage={**stage_manifest['files'],**stage_manifest['allowed_generated_outputs']}
 stage=OUT/'SM64'
 assert {str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()}==set(expected_stage)
 for rel,h in expected_stage.items():assert sha(stage/rel)==h,rel
 for mode in ('normal','diagnostic'):
  build_path=CAND/('build-'+mode+'.json')
  m=bind(build_path);assert m['returncode']==0 and m['diagnostic']==(mode=='diagnostic')
  reviewed=review['build_bindings'][mode]
  assert sha(build_path)==reviewed['record_sha256']
  remember(CAND/('build-'+mode+'.log'),reviewed['log_sha256'])
  assert m['elf_sha256']==sha(CAND/('app.elf' if mode=='normal' else 'diagnostic.elf'))
  assert m['elf_sha256']==reviewed['elf_sha256']
  assert set(m['inputs'])==set(SOURCES)|{'gfx_gpu.h','gfx_geometry.h'}
  for name,info in m['inputs'].items():
   assert info['sha256']==reviewed['all7_input_hashes'][name]
   remember(Path(info['source']),info['sha256'])
 for name in SOURCES:assert sha(CAND/name)==review['source_proof'][name]['source_sha256']
 bind(CAND/'manifest.json')
 v3=bind(OUT/'vc-packet-v3/independent-review.json')
 assert v3['source_review_passed'] and v3['focused_proof_passed'] and v3['combined_writer_body_exact']
 remember(OUT/v3['proof']['path'],v3['proof']['sha256'])
 v4=bind(OUT/'vc-packet-v4/independent-review.json')
 assert v4['source_review_passed'] and v4['actual_A_component_proof_passed'] and v4['combined_render_v3_writer_exact']
 tune=bind(OUT/'vc-packet-v4/cpu-quantize-kernel-sifive5/tuning-comparison.json');assert tune['passed']
 conditional=bind(OUT/'fps-panel-v1/conditional-check/result.json')
 for key in ('source_review_passed','snapshot_definition_exact','details0_hot_hooks_runtime_noop','details0_has_no_scope_count_functions','fps_title_and_labels_present'):
  assert conditional[key]
 assert len(conditional['preprocessor_cases'])==3 and all(x['passed_token_equivalence'] for x in conditional['preprocessor_cases'])
 panel=bind(VAL/'panel-smoke-v6.json')
 assert panel['passed_abi'] and panel['visual_layout_pass']
 assert panel['snapshot_version']==4 and panel['detail_samples']==0
 assert panel['elf_sha256']==sha(CAND/'diagnostic.elf')
 remember(ROOT/panel['image'],panel['image_sha256'])
 baseline=bind(OUT/'source-baseline.json')
 assert sha(OUT/'source-baseline.json')==stage_manifest['source_baseline_sha256']
 changes={SOURCES[name]:sha(CAND/name) for name in SOURCES}
 check_source_tree(stage,{rel:changes.get(rel,h) for rel,h in baseline['files'].items()})
 core_pins()
 diffs=[100*(r['candidate_gpu_cycles']/r['baseline_gpu_cycles']-1) for v in (normal,fps) for r in v['gpu_comparisons']]
 result={'selected':'render-v6','software_qualified':True,'hardware_25fps_observed':False,
  'last_observed_device_fps':20.3,'target_fps':25,'retained_core_sha256':CORE,
  'normal_elf_sha256':sha(CAND/'app.elf'),'fps_elf_sha256':sha(CAND/'diagnostic.elf'),
  'normal_vs_normal':normal['gameplay'],'fps_vs_deployed_P3':fps['gameplay'],
  'gpu_color_depth_exact_comparisons':32,'gpu_component_cycle_change_percent_range':[min(diffs),max(diffs)],
  'source_sha256':{name:sha(CAND/name) for name in SOURCES},'evidence_sha256':evidence,
  'normal_evidence_reuse':'Final normal ELF is byte-identical to render-v5; validated by direct SHA-256 equality against its capture and replay analysis.',
  'scope':'Actual guest instructions and modeled CPU cycles on a matched 721-frame route, 361 gameplay frames; separate GPU RTL replays with synthetic traffic. Actual A CPU quantizer component validated independently. No physical-device FPS claim, no seed sweep. Experimental late-ALU hardware is excluded.'}
 return result

def plans():
 changes=[(CAND/name,DEST/rel,sha(OUT/'baseline'/name)) for name,rel in SOURCES.items()]
 changes += [(CAND/'app.elf',DEST/'.obj/sm64/app.elf',OLD_NORMAL),
  (CAND/'diagnostic.elf',DEST/'.obj/sm64-perfdiag/app.elf',OLD_FPS),
  (CAND/'diagnostic.elf',DEST/'build/pocket/sm64/Assets/sm64/common/sm64.elf',OLD_FPS)]
 return changes

def frozen_selection():
 r=read(OUT/'REPORT.json');assert r['software_qualified'] and not r['hardware_25fps_observed']
 assert r['selected']==CAND.name and r['retained_core_sha256']==CORE
 assert set(r['source_sha256'])==set(SOURCES)
 for rel,h in r['evidence_sha256'].items():assert sha(ROOT/rel)==h,rel
 for name,h in r['source_sha256'].items():assert sha(CAND/name)==h
 assert sha(CAND/'app.elf')==r['normal_elf_sha256']
 assert sha(CAND/'diagnostic.elf')==r['fps_elf_sha256']
 core_pins();return r

def atomic_copy(src,dst,before,after,guard=None):
 assert not dst.is_symlink() and stat.S_ISREG(dst.stat().st_mode),dst
 data=src.read_bytes();assert hashlib.sha256(data).hexdigest()==after,src
 if guard:guard()
 assert sha(dst)==before,('Destination changed before copy',str(dst))
 fd,tmp=tempfile.mkstemp(prefix='.'+dst.name+'.fps25-',dir=dst.parent)
 try:
  with os.fdopen(fd,'wb') as f:
   f.write(data);f.flush();os.fsync(f.fileno())
  shutil.copymode(dst,tmp)
  assert sha(tmp)==after
  if guard:guard()
  assert sha(dst)==before,('Destination changed before replace',str(dst))
  os.replace(tmp,dst)
 finally:
  if Path(tmp).exists():Path(tmp).unlink()
 if guard:guard()
 assert sha(dst)==after

def save_backup(dst,saved,expected):
 assert not dst.is_symlink() and stat.S_ISREG(dst.stat().st_mode),dst
 shutil.copy2(dst,saved)
 with saved.open('rb') as stream:os.fsync(stream.fileno())
 assert sha(saved)==expected and sha(dst)==expected,dst

def restore_rows(rows,guard=None):
 """Attempt every rollback, including a row whose post-replace check failed."""
 failures=[]
 for row in reversed(rows):
  try:
   if guard:guard()
   destination=Path(row['destination']);current=sha(destination)
   if current==row['before_sha256']:continue
   # Preserve independent edits; an unknown state requires manual recovery.
   assert current==row['after_sha256'],('Unexpected destination during rollback',str(destination))
   atomic_copy(Path(row['backup']),destination,row['after_sha256'],row['before_sha256'],guard)
  except BaseException as error:
   failures.append({'destination':row['destination'],'backup':row['backup'],'error':repr(error)})
 return failures

def publish():
 r=frozen_selection();changes=plans()
 for src,dst,old in changes:assert sha(dst)==old,('Destination changed since freeze',str(dst))
 # Check every frozen source, including files that this release does not edit.
 baseline=read(OUT/'source-baseline.json')
 check_source_tree(DEST,baseline['files'])
 backup=OUT/'publication-backup';backup.mkdir(exist_ok=False)
 rows=[]
 for i,(src,dst,old) in enumerate(changes):
  saved=backup/str(i);save_backup(dst,saved,old)
  rows.append({'source':str(src),'destination':str(dst),'before_sha256':old,'after_sha256':sha(src),'backup':str(saved)})
 write_json(OUT/'publication-plan.json',rows)
 done=[]
 try:
  for row in rows:
   done.append(row)
   atomic_copy(Path(row['source']),Path(row['destination']),row['before_sha256'],row['after_sha256'])
  expected=dict(baseline['files'])
  for name,rel in SOURCES.items():expected[rel]=r['source_sha256'][name]
  check_source_tree(DEST,expected);core_pins()
  for row in rows:assert sha(row['destination'])==row['after_sha256']
  write_json(OUT/'published.json',{'report_sha256':sha(OUT/'REPORT.json'),'files':rows,'core_sha256':CORE})
 except BaseException as error:
  failures=restore_rows(done)
  write_json(OUT/'publication-failure.json',{'error':repr(error),'rollback_failures':failures,
   'attempted_destinations':[row['destination'] for row in done]})
  if failures:raise RuntimeError('Publication failed; some rollback destinations need recovery from publication-plan.json') from error
  raise
 print(json.dumps({'published_files':len(rows),'core_unchanged':True}))

def deploy_sd():
 r=frozen_selection();identity=card_precheck()
 src=CAND/'diagnostic.elf';dst=CARD/'Assets/sm64/common/sm64.elf'
 assert sha(DEST/'build/pocket/sm64/Assets/sm64/common/sm64.elf')==sha(src)
 backup=OUT/'sd-backup';backup.mkdir(exist_ok=False);saved=backup/'sm64.elf'
 card_guard(identity);save_backup(dst,saved,OLD_FPS)
 row={'source':str(src),'destination':str(dst),'before_sha256':OLD_FPS,
  'after_sha256':r['fps_elf_sha256'],'backup':str(saved)}
 write_json(OUT/'sd-plan.json',{'file':row,'mount':identity,'report_sha256':sha(OUT/'REPORT.json')})
 guard=lambda:card_guard(identity)
 try:
  atomic_copy(src,dst,row['before_sha256'],row['after_sha256'],guard)
  os.sync();guard();assert sha(dst)==row['after_sha256']
  write_json(OUT/'sd-deployed.json',{**row,'core_sha256':CORE,'os_sha256':OS,
   'mount':identity,'report_sha256':sha(OUT/'REPORT.json')})
 except BaseException as error:
  failures=restore_rows([row],guard)
  if not failures:os.sync()
  write_json(OUT/'sd-failure.json',{'error':repr(error),'rollback_failures':failures,'file':row,'mount':identity})
  if failures:raise RuntimeError('SD deployment failed; restore the verified sd-backup after the same card is mounted') from error
  raise
 print(json.dumps({'sd_application_sha256':sha(dst),'core_and_os_unchanged':True}))

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['qualify','publish','sd','sd-check','verify']);a=p.parse_args()
 if a.action=='qualify':
  assert not (OUT/'REPORT.json').exists();r=qualify();write_json(OUT/'REPORT.json',r)
  print(json.dumps({k:r[k] for k in ['selected','software_qualified','hardware_25fps_observed','normal_elf_sha256','fps_elf_sha256']},indent=2))
 elif a.action=='publish':publish()
 elif a.action=='sd':deploy_sd()
 elif a.action=='sd-check':print(json.dumps({'mount':card_precheck(),'read_only_precheck_passed':True},indent=2))
 else:
  frozen_selection()
  for src,dst,_ in plans():assert sha(src)==sha(dst),str(dst)
  expected=dict(read(OUT/'source-baseline.json')['files'])
  for n,rel in SOURCES.items():expected[rel]=sha(CAND/n)
  check_source_tree(DEST,expected)
  print('Published sources, both executables, package and retained A core verified.')
if __name__=='__main__':main()
