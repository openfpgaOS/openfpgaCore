#!/usr/bin/env python3
"""Private, immutable app/source handoff; never writes a sibling or SD card."""
import difflib,json,shutil,subprocess,sys,zipfile
from pathlib import Path
from phase10_experiment import ROOT,CAMP,INPUTS,sha,write
P=CAMP/'phase10';BASE=CAMP/'phase9/checks-state-f11';APP=P/'lod5-recycle-driver-v1'
OUT=P/'lod5-recycle-delivery-v1';REBUILD=P/'lod5-recycle-rebuild-v1'
QUAL=P/'lod5-recycle-coupled-v1/manifest.json'

def prepare():
 assert sha(QUAL)=='20681e2b770a99b046df791eb0cf873a6c3431fe1ba2939ec6efab033d59ec28'
 build=json.loads((APP/'build-normal.json').read_text())
 assert sha(APP/'app.elf')==build['elf_sha256']=='bb41f2088130935dc637d55a4a1dbd0afddb68f94794440ddb5a67e504d4a2c5'
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
 print(OUT);print(REBUILD)

def seal():
 expected=json.loads((OUT/'prepared.json').read_text())['elf_sha256']
 assert sha(REBUILD/'app.elf')==expected,'Rebuild differs; preserve evidence and report before sealing'
 assert json.loads((REBUILD/'build-normal.json').read_text())['returncode']==0
 readme='''Private SM64 LOD5 recycler app handoff

payload/Assets/sm64/common/sm64.elf is the exact qualified normal app.
ELF SHA256: bb41f2088130935dc637d55a4a1dbd0afddb68f94794440ddb5a67e504d4a2c5
No FPGA bitstream, kernel, loader, sound bank or existing installation metadata
is replaced by this payload. Pair with the separately qualified matching
LOD5 core (MMIO ID0x4c4f4405). Without that device ID, the app retains its CPU
fallback; acceleration is not implied merely by copying this ELF.

Source integration, from an existing F11 SM64 checkout:
1. Check every before_sha256 in sources.json against the named relative file.
2. From that checkout, run:
   git apply --check /absolute/path/to/integration.patch
   git apply /absolute/path/to/integration.patch
3. Build the exact normal configuration, explicitly disabling diagnostics:
   make -C src/sm64 -j4 PROFILE=0 PERF_DIAG=0 "$(pwd)/.obj/sm64/app.elf"

The patch changes only gfx_pc.c, gfx_gpu.c and gfx_gpu.h. All eight pinned
build inputs are also included verbatim in sources/. No default Makefile,
SDK, renderer option or asset changes are needed. The normal build still
requires the existing SDK toolchain and the user's existing extracted assets.
The private validation applied this patch to pinned F11 sources, rebuilt
through the original normal harness and obtained the identical ELF SHA256.
This handoff was not applied to a sibling checkout or device.

Qualification: unchanged actual cache-v2 model, all16 warm/cold color/depth
buffers and scene/culling records exact. Warm34.78922125→34.988370ms and
cold49.01864875→49.227475ms; bounded recycling/layout cost, not a speedup.
CPU/cache timing is modeled, not a physical Pocket FPS measurement.
Existing normal/fault/absent/drained-timeout/full-pool functional and actual-C
multigeneration/cooldown/wrap tests remain sealed and referenced in manifest.
Recycling occurs on capacity misses only, at most once per120 rendered frames
after its first use; hits continue during cooldown, probe failures fall back.

The app-only ZIP contains the payload/ contents with SD-relative paths.
It is local delivery material, not a publication or deployment action.
'''
 (OUT/'README.txt').write_text(readme)
 rebuild=dict(passed=True,elf_identical=True,elf_sha256=expected,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in [REBUILD/'app.elf',REBUILD/'build-normal.json',REBUILD/'build-normal-invocation.json',REBUILD/'build-normal.log',REBUILD/'harness-stage.json']})
 write(OUT/'rebuild-proof.json',rebuild)
 archive=OUT/'sm64-lod5-recycler-app.zip'
 with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9)as z:
  for p in sorted((OUT/'payload').rglob('*')):
   if p.is_file():
    info=zipfile.ZipInfo(str(p.relative_to(OUT/'payload')),(2026,10,7,0,0,0));info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_DEFLATED
    z.writestr(info,p.read_bytes())
 with zipfile.ZipFile(archive)as z:
  assert z.namelist()==['Assets/sm64/common/sm64.elf']
  assert z.read(z.namelist()[0])==(APP/'app.elf').read_bytes()
 files={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()and'patch-check'not in p.parts}
 proofs=[QUAL,P/'lod5-recycle-functional-v1/manifest.json',P/'lod5-recycle-review-v1/manifest.json',APP/'build-normal.json']
 write(OUT/'manifest.json',dict(private_only=True,deployed=False,elf_sha256=expected,
  files=files,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in proofs},
  helper_sha256=sha(Path(__file__)),rebuild_elf_identical=True))
 print(OUT/'manifest.json');print(sha(OUT/'manifest.json'));print('ZIP',sha(archive))

if __name__=='__main__':globals()[sys.argv[1]]()
