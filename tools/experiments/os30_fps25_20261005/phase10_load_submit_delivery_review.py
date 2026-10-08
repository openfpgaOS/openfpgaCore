#!/usr/bin/env python3
"""Read-only review of selected app payload/source/proof bindings."""
import hashlib,json,shutil,zipfile
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';D=P/'load-submit-delivery-v1';A=P/'load-submit-overlap-v1';OUT=P/'load-submit-delivery-review-v1'
def main():
 OUT.mkdir(exist_ok=False)
 assert sha(D/'manifest.json')=='bb3a5ecd2a5f7b75d3b3e11cd98373b36dcefcd4c72346d98cf71f76bcaf47e0'
 m=json.loads((D/'manifest.json').read_text())
 for n,h in m['files'].items():assert sha(D/n)==h,n
 for n,h in m['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 assert m['private_only'] and not m['deployed']and not m['qualified_release']and m['hardware_timing_pending']
 elf=sha(A/'app.elf');assert elf==m['elf_sha256']==sha(D/'payload/Assets/sm64/common/sm64.elf')
 with zipfile.ZipFile(D/'sm64-lod5-load-submit-app.zip')as z:
  assert z.namelist()==['Assets/sm64/common/sm64.elf']
  assert hashlib.sha256(z.read(z.namelist()[0])).hexdigest()==elf
 sources=json.loads((D/'sources.json').read_text());changed=[]
 for n,info in sources['inputs'].items():
  assert sha(D/'sources'/n)==sha(A/info['flat_name'])==info['after_sha256']
  assert sha(CAMP/'phase9/checks-state-f11'/info['flat_name'])==info['before_sha256']
  assert sha(D/'patch-check'/n)==info['after_sha256']
  if info['before_sha256']!=info['after_sha256']:changed.append(n)
 assert changed==sources['changed_files']and sorted(Path(n).name for n in changed)==['gfx_gpu.c','gfx_gpu.h','gfx_pc.c']
 rebuild=json.loads((D/'rebuild-proof.json').read_text());assert rebuild['passed']and rebuild['elf_identical']and rebuild['elf_sha256']==elf
 for n,h in rebuild['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 build=json.loads((P/'load-submit-rebuild-v1/build-normal.json').read_text());assert build['returncode']==0 and build['elf_sha256']==elf
 assert 'PROFILE=0'in build['command']and'PERF_DIAG=0'in build['command']
 q=json.loads((D/'qualification.json').read_text());assert q['selected_elf_sha256']==elf and q['exact_app_functional_and_coupled_passed']and not q['deployment_ready']
 for key in ('proof_manifests_sha256','exact_app_results_sha256'):
  for n,h in q[key].items():assert sha(ROOT/n)==h,n
 readme=(D/'README.txt').read_text()
 for phrase in ('bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405','PROFILE=0 PERF_DIAG=0','not measured\nPocket FPS','Those gates predate this software','No device installation has been performed.','This package does not contain an FPGA bitstream'):assert phrase in readme,phrase
 report='Selected LOAD/submission app delivery: independent binding review PASS\n\nThe payload and sole ZIP member are byte-identical to selected ELF bbb1c31a, not the rejected stack/counter variants. All eight shipped inputs match the selected source hashes; exactly gfx_pc.c/gfx_gpu.c/gfx_gpu.h differ from retained F11, and the existing patch-check tree has those exact final bytes. All package/evidence hashes recheck. The independent fresh normal PROFILE=0/PERF_DIAG=0 rebuild produces the identical ELF. This review does not repeat that build.\n\nREADME correctly distinguishes exact-app five-mode functional/16-frame coupled proofs from inherited native LOD5 client tests, preserves the modeled-CPU/Pocket-FPS distinction, states matching LOD5 core timing remains pending, and excludes any bitstream/deployment claim. No packaging/source change or installation was performed by this review.\n'
 (OUT/'REPORT.txt').write_text(report);shutil.copy2(Path(__file__),OUT/'recipe.py')
 write(OUT/'result.json',dict(passed=True,elf_sha256=elf,zip_only_selected_elf=True,eight_inputs_exact=True,three_changed_files=changed,normal_rebuild_exact=True,readme_scope_correct=True,deployment_ready=False))
 evidence=[D/'manifest.json',D/'README.txt',D/'qualification.json',D/'sources.json',D/'rebuild-proof.json',Path(__file__)]+list(OUT.iterdir())
 write(OUT/'manifest.json',dict(passed=True,review_only=True,elf_sha256=elf,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in evidence}))
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
