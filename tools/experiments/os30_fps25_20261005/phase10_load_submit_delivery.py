#!/usr/bin/env python3
"""Private, immutable app/source handoff; never writes a sibling or SD card."""
import difflib,json,shutil,subprocess,sys,time,zipfile
import phase10_experiment as experiment
from pathlib import Path
from phase10_experiment import ROOT,CAMP,INPUTS,sha,write
P=CAMP/'phase10';BASE=CAMP/'phase9/checks-state-f11';APP=P/'load-submit-overlap-v1'
OUT=P/'load-submit-delivery-v1';REBUILD=P/'load-submit-rebuild-v1'
QUAL=P/'load-submit-coupled-v1/manifest.json'
EXPECTED='bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
PROOFS={
 'load-submit-coupled-v1/manifest.json':'7c8fbe0056c708bcc31275a2b3dfad3d728f7f9a2c7bfacfd24c8bf0592432e6',
 'load-submit-functional-v1/manifest.json':'58e45ea2a0ccdaa10394080539a34404f0872dc9f2f736cf7d7e7e3d60d573be',
 'load-submit-overlap-audit-v1/manifest.json':'c03c9de866b6808eb9e5487f4b7de2972f8ab0f983804964752497c465aecd1b',
 'lod5-recycle-review-v1/manifest.json':'4349ccc4c27846d65515c2762b5f7d2d3c614e84a7b73ae4a45d33f27e39b570',
 'lod5-live-native-v2/manifest.json':'5830b0fd13b8f943a013b8d263601d3b3aac012779a359ec28cfc50b10e60591',
 'lod5-live-compact-v3/manifest.json':'65c7770eb823648386f1921a2c44c2e55fc93fbc9f7403ac631718821148abd3'}

def qualified():
 for name,digest in PROOFS.items():assert sha(P/name)==digest,name
 functional=json.loads((P/'load-submit-functional-v1/result.json').read_text())
 coupled=json.loads((P/'load-submit-coupled-v1/result.json').read_text())
 assert functional['candidate_elf_sha256']==coupled['app_elf_sha256']==EXPECTED
 assert functional['passed'] and functional['all_five_modes_721_records_and_eight_streams_exact']
 assert functional['all_passive_ownership_guards_pass']
 assert set(functional['modes'])=={'normal','faults','absent','timeout','fullpool'}
 assert coupled['passed'] and set(coupled['windows'])=={'warm','cold'}
 for v in coupled['windows'].values():assert v['all8_actual_color_depth_exact'] and v['all_histories_exact']
 return dict(selected_elf_sha256=EXPECTED,exact_app_functional_and_coupled_passed=True,
  functional_modes=list(functional['modes']),
  inherited_native_scope='Original LOD5 client and unchanged arithmetic/CRAM/alias/cache path; not the new submission-overlap scheduling itself.',
  proof_manifests_sha256={str((P/n).relative_to(ROOT)):h for n,h in PROOFS.items()},
  exact_app_results_sha256={str(p.relative_to(ROOT)):sha(p) for p in [P/'load-submit-functional-v1/result.json',P/'load-submit-coupled-v1/result.json']},
  hardware_timing_pending=True,deployment_ready=False)

def prepare():
 qualification=qualified()
 assert sha(QUAL)=='7c8fbe0056c708bcc31275a2b3dfad3d728f7f9a2c7bfacfd24c8bf0592432e6'
 build=json.loads((APP/'build-normal.json').read_text())
 assert sha(APP/'app.elf')==build['elf_sha256']=='bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
 OUT.mkdir(exist_ok=False);REBUILD.mkdir(exist_ok=False)
 target=OUT/'payload/Assets/sm64/common/sm64.elf';target.parent.mkdir(parents=True)
 shutil.copy2(APP/'app.elf',target)
 changes=[];binding={};patch=''
 for flat,relative in INPUTS.items():
  before=BASE/flat;after=APP/flat
  assert sha(after)==build['inputs'][flat]['sha256']
  p=OUT/'sources'/relative;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(after,p)
  check=OUT/'patch-check'/relative;check.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(before,check)
  binding[relative]=dict(flat_name=flat,before_sha256=sha(before),after_sha256=sha(after))
  if before.read_bytes()!=after.read_bytes():
   changes.append(relative)
   patch+=''.join(difflib.unified_diff(before.read_text().splitlines(True),after.read_text().splitlines(True),fromfile='a/'+relative,tofile='b/'+relative))
 assert changes==[INPUTS[n] for n in ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h')],changes
 (OUT/'integration.patch').write_text(patch)
 with (OUT/'patch-check.log').open('x')as f:
  subprocess.run(['patch','--batch','--forward','-p1','-i',str(OUT/'integration.patch')],cwd=OUT/'patch-check',stdout=f,stderr=subprocess.STDOUT,check=True)
 for relative,info in binding.items():
  assert sha(OUT/'patch-check'/relative)==info['after_sha256'],relative
  shutil.copy2(OUT/'patch-check'/relative,REBUILD/info['flat_name'])
 write(OUT/'sources.json',dict(inputs=binding,changed_files=changes,
  normal_build=dict(PROFILE=0,PERF_DIAG=0),source_format='Vtx24B; LoadedVertex36B',
  interface='LOD5 ID0x4c4f4405; CRAM1 raw24B pool0xfc0000..0x1000000; raw32 alias0x13f80000'))
 write(OUT/'prepared.json',dict(elf_sha256=sha(target),qualified_manifest_sha256=sha(QUAL),
  tested_build_sha256=sha(APP/'build-normal.json'),helper_sha256=sha(Path(__file__)),
  changed_files=changes,private_only=True,sibling_or_sd_written=False))
 write(OUT/'qualification.json',qualification)
 shutil.copy2(__file__,OUT/'recipe.py')
 print(OUT);print(REBUILD)

def rebuild():
 # A fresh ordinary application build: pinned F11 assets/SDK, patched source,
 # all application objects removed, and both diagnostic choices explicit.
 experiment.prepare(REBUILD.name)
 stage=REBUILD/'SM64'; binding=json.loads((REBUILD/'harness-stage.json').read_text())
 inputs={}
 for name,relative in INPUTS.items():
  source=REBUILD/name; assert sha(source)==binding['candidate_source_sha256'][name]
  shutil.copyfile(source,stage/relative)
  inputs[name]=dict(source=str(source),sha256=sha(source))
 objects=stage/'.obj/sm64'
 assert objects.parent==stage/'.obj' and not objects.is_symlink()
 if objects.exists():shutil.rmtree(objects)
 target=objects/'app.elf';cmd=['make','-j4','PROFILE=0','PERF_DIAG=0',str(target)]
 shutil.copy2(__file__,REBUILD/'build-normal-recipe.py')
 write(REBUILD/'build-normal-invocation.json',dict(command=cmd,cwd=str(stage/'src/sm64'),
  source_inputs=inputs,recipe_sha256=sha(Path(__file__)),
  stage_binding_sha256=sha(REBUILD/'harness-stage.json'),all_application_objects_removed=True,
  normal_configuration=dict(PROFILE=0,PERF_DIAG=0),production_changed=False))
 started=time.monotonic()
 with (REBUILD/'build-normal.log').open('x') as f:
  r=subprocess.run(cmd,cwd=stage/'src/sm64',stdout=f,stderr=subprocess.STDOUT)
 record=dict(command=cmd,cwd=str(stage/'src/sm64'),returncode=r.returncode,
  diagnostic=False,profile=False,elapsed_seconds=time.monotonic()-started,inputs=inputs)
 for name,relative in INPUTS.items():assert sha(stage/relative)==inputs[name]['sha256']
 if r.returncode==0:
  shutil.copy2(target,REBUILD/'app.elf');record.update(elf=str(REBUILD/'app.elf'),elf_sha256=sha(REBUILD/'app.elf'))
 write(REBUILD/'build-normal.json',record)
 assert r.returncode==0,record
 assert record['elf_sha256']==EXPECTED,record
 print(json.dumps(record,indent=2))

def seal():
 assert qualified()==json.loads((OUT/'qualification.json').read_text())
 expected=json.loads((OUT/'prepared.json').read_text())['elf_sha256']
 assert sha(REBUILD/'app.elf')==expected,'Rebuild differs; preserve evidence and report before sealing'
 assert json.loads((REBUILD/'build-normal.json').read_text())['returncode']==0
 readme='Private SM64 LOAD/submission overlap delivery\n\nStatus: software prepared and verified; matching core timing qualification is\nstill pending. This package does not contain an FPGA bitstream and is not a\nqualified app/core release. No device installation has been performed.\n\npayload/Assets/sm64/common/sm64.elf is the selected load-submit-overlap-v1 app.\nELF SHA256: bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405\nFor the accelerated path, pair it with the matching LOD5 core candidate\n(MMIO ID0x4c4f4405, reserved CRAM1 source pool0xfc0000..0xffffff and cached\nread-only result alias0x13f80000). Without the matching device ID, the existing\nCPU fallback remains active. No kernel, loader, sound bank or installation\nmetadata is supplied or changed here.\n\nSource integration from the retained F11 SM64 checkout:\n1. Verify every before_sha256 in sources.json against its named relative file.\n2. From that checkout, run:\n   git apply --check /absolute/path/to/integration.patch\n   git apply /absolute/path/to/integration.patch\n3. Build the normal configuration with diagnostics explicitly disabled:\n   make -C src/sm64 -j4 PROFILE=0 PERF_DIAG=0 "$(pwd)/.obj/sm64/app.elf"\n\nOnly gfx_pc.c, gfx_gpu.c and gfx_gpu.h change. All eight pinned build inputs\nare included verbatim in sources/. No Makefile, SDK, renderer option or asset\nchange is part of the patch. The normal build requires the existing SDK and\nthe user\'s existing extracted assets. This private check applies the exact\npatch to retained F11, deletes all application objects, runs that ordinary\nmake target with PROFILE=0 PERF_DIAG=0, and reproduces the exact ELF above.\n\nThe selected software overlaps submission of one already prepared triangle\nbatch with an immediately adjacent hardware LOAD. The pending batch owns a\nstatic buffer until submission completes. It cannot cross another producer,\nstate change or frame boundary. Existing bounded source-pool recycling and\nfailure/absent-device fallback remain. Stack-storage and diagnostic-counter\nexperiments are not included in this selected ELF.\n\nExact app qualification: five functional modes (normal, unsupported/read\nfaults, absent device, drained timeout, and full pool) each preserve721 frame\nrecords and eight ordered render streams. Each mode validates64,175 pending\nbatches without changing their bytes or ownership. Zero-count G_VTX has\nsource/emitted-code review but is absent from the game corpus.\n\nActual coupled GPU/LOAD/SDRAM/CRAM RTL preserves all16 warm/cold color/depth\nbuffers and scene/culling histories. Warm34.957095->34.650480 ms/frame and\ncold49.21498625->48.98422250 ms/frame are modeled CPU timing, not measured\nPocket FPS. No25FPS, clock closure or hardware deployment claim is made.\nThe coupled proof manifest is7c8fbe0056c708bcc31275a2b3dfad3d728f7f9a2c7bfacfd24c8bf0592432e6.\n\nInherited native LOD5 integration gates verify real CPU execution, source\nuploads, exact geometry/RGB fields, cache invalidation, alias reads, backpressure\nand selected reset/contention behavior. Those gates predate this software\nsubmission-overlap change; they establish the unchanged underlying LOAD path,\nwhile the exact selected app is bound by the functional and coupled proofs.\nEvidence paths and hashes are listed in manifest.json and qualification.json.\n\nThe app-only ZIP contains exactly Assets/sm64/common/sm64.elf. It is local\nprivate delivery material. No sibling checkout, production source or SD card\nhas been changed, and no additional manual hardware trial is requested here.\n'
 (OUT/'README.txt').write_text(readme)
 rebuild=dict(passed=True,elf_identical=True,elf_sha256=expected,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in [REBUILD/'app.elf',REBUILD/'build-normal.json',REBUILD/'build-normal-invocation.json',REBUILD/'build-normal.log',REBUILD/'harness-stage.json']})
 write(OUT/'rebuild-proof.json',rebuild)
 archive=OUT/'sm64-lod5-load-submit-app.zip'
 with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9)as z:
  for p in sorted((OUT/'payload').rglob('*')):
   if p.is_file():
    info=zipfile.ZipInfo(str(p.relative_to(OUT/'payload')),(2026,10,7,0,0,0));info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_DEFLATED
    z.writestr(info,p.read_bytes())
 with zipfile.ZipFile(archive)as z:
  assert z.namelist()==['Assets/sm64/common/sm64.elf']
  assert z.read(z.namelist()[0])==(APP/'app.elf').read_bytes()
 files={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()and'patch-check'not in p.parts}
 proofs=[QUAL,P/'load-submit-functional-v1/manifest.json',P/'load-submit-overlap-audit-v1/manifest.json',P/'lod5-recycle-review-v1/manifest.json',P/'lod5-live-native-v2/manifest.json',P/'lod5-live-compact-v3/manifest.json',APP/'build-normal.json']
 write(OUT/'manifest.json',dict(private_only=True,deployed=False,elf_sha256=expected,
  hardware_timing_pending=True,qualified_release=False,files=files,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in proofs},
  helper_sha256=sha(Path(__file__)),rebuild_elf_identical=True))
 print(OUT/'manifest.json');print(sha(OUT/'manifest.json'));print('ZIP',sha(archive))

if __name__=='__main__':globals()[sys.argv[1]]()
