#!/usr/bin/env python3
"""Seal the measured counter cleanup as unselected; preserve both controls."""
import json, shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write
P=CAMP/'phase10';OUT=P/'backend-wire-counters-coupled-v1';APP=P/'backend-wire-counters-v1';FUNC=P/'backend-wire-functional-v1';CONTROL=P/'load-submit-coupled-v1'
def main():
 prep=json.loads((OUT/'prepared.json').read_text());elf=sha(APP/'app.elf')
 assert prep['elf_sha256']==elf
 for n,h in prep['source_sha256'].items():assert sha(OUT/n)==h,n
 assert sha(OUT/'model/obj/Vtb_gpu')==sha(CONTROL/'model/obj/Vtb_gpu')==prep['simulator_sha256']
 assert sha(FUNC/'manifest.json')=='6bc68536b43b469eeb65d62e0c96d790b0d6748abf97045719bb44afcf99d99e'
 functional=json.loads((FUNC/'manifest.json').read_text())
 assert functional['passed'] and functional['elf_sha256']==elf
 binding=json.loads((OUT/'functional-directory-binding.json').read_text())
 assert binding['elf_sha256']==elf and binding['normal_proof_sha256']==sha(FUNC/'result-normal.json')
 for key,name in [('staged_helper_sha256','phase10_backend_wire_coupled.py'),('runner_sha256','phase10_backend_wire_coupled_run.py')]:assert binding[key]==sha(Path(__file__).with_name(name))
 windows={}
 for window in ('warm','cold'):
  r=json.loads((OUT/f'result-{window}.json').read_text());old=json.loads((CONTROL/f'result-{window}.json').read_text())
  assert r['passed'] and r['all8_actual_color_depth_exact'] and r['all_histories_exact'] and r['simulator_byte_identical_to_control']
  assert r['prior_static_result_sha256']==sha(CONTROL/f'result-{window}.json')
  before=old['timing']['overlap']['frame_ms'];after=r['timing']['overlap']['frame_ms']
  assert before==r['prior_static_overlap_ms'] and after>before
  assert r['timing']['overlap']['uploaded_words']==old['timing']['overlap']['uploaded_words']
  run=OUT/f'run-{window}';inv=json.loads((run/'inputs.json').read_text());assert inv['success'] and sha(run/'sm64.elf')==elf
  for n,h in inv['results_sha256'].items():assert sha(run/n)==h,n
  assert 'FATAL'not in(run/'run.log').read_text()
  windows[window]=dict(control_ms=before,candidate_ms=after,delta_ms=after-before,delta_percent=100*(after/before-1),actual_color_depth_and_histories_exact=True,uploaded_words_exact=True)
 result=dict(passed=True,selected=False,decision='Retain load-submit-overlap-v1; no layout/padding follow-up.',windows=windows,elf_sha256=elf,qualified_for_deployment=False,rtl_unchanged=True,limits='Actual GPU/LOAD/cache/memory RTL coupled to modeled CPU instruction, dependency, branch and cache timing. Not Pocket FPS or a whole-native-CPU run.')
 write(OUT/'result.json',result)
 report='Backend wire-counter cleanup: unselected\n\n'
 for n,r in windows.items():report+=f"{n}: original overlap {r['control_ms']:.8f} -> cleanup {r['candidate_ms']:.8f} ms/frame; +{r['delta_ms']:.8f} ms (+{r['delta_percent']:.4f}%).\n"
 report+='\nAll 16 actual color/depth buffers, scene/culling histories and command-word counts match. Hardware simulator binary is identical to the original control. The existing per-window result files also compare to the older pre-overlap recycler baseline; the selection here uses the immediate load-submit-overlap-v1 parent, so its already-earned overlap gain is not attributed to counter removal.\n\nAll five functional modes and enabled/disabled counter code-generation proofs pass. Removing dead diagnostic work saves about 12,846 modeled instructions per gameplay frame, but ELF-layout cache/branch effects offset it in both coupled windows. Retain the original overlap client. No layout/padding experiment, production change or deployment follows.\n\nTiming is from an actual-RTL GPU/LOAD/memory and modeled-CPU coupling. This does not establish Pocket FPS, timing closure or the 25FPS goal.\n'
 (OUT/'REPORT.txt').write_text(report);shutil.copy2(Path(__file__),OUT/'seal-recipe.py')
 paths=[p for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts and p.name!='manifest.json']
 paths += [OUT/'model/obj/Vtb_gpu',FUNC/'manifest.json',CONTROL/'manifest.json',CONTROL/'result-warm.json',CONTROL/'result-cold.json',APP/'source.json',APP/'build-normal.json']
 paths += [Path(__file__).with_name(n)for n in ('phase10_backend_wire_coupled.py','phase10_backend_wire_coupled_run.py','phase10_backend_wire_coupled_seal.py')]
 write(OUT/'manifest.json',dict(passed=True,selected=False,elf_sha256=elf,decision=result['decision'],evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(paths))}))
 print(report);print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
