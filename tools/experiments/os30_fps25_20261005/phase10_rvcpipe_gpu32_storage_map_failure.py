#!/usr/bin/env python3
"""Preserve the rejected inline-genvar parsing attempt; no resource conclusions."""
import json,pathlib,re
import phase10_rvcpipe_gpu32_storage_map as m
R,O,sha,put=m.R,m.O,m.sha,m.put
d=m.checked();completion=json.loads((O/'map-core/completion.json').read_text())
assert completion['returncode']==3 and not completion['passed']
log=(O/'map-core/map.log').read_text(errors='replace')
errors=[l for l in log.splitlines() if re.match(r'Error \(\d+\):',l)]
assert len(errors)==2 and errors[0].startswith('Error (10170):') and errors[1].startswith('Error (10112):')
assert 'gpu_color_depth_cache.sv(143)' in errors[0] and '"genvar"' in errors[0]
result=dict(passed_map=False,rejected_phase='Verilog parsing before synthesis',returncode=3,seconds=completion['seconds'],errors=errors,source_cpu_sha256=d['component_cpu_sha256'],source_cache_sha256=d['component_render_cache_sha256'],qsf_unchanged=True,resource_estimates_available=False,physical_m10ks=None,fitted_alms=None,fmax_mhz=None,fitter_invoked=False,production_modified=False,correctness_proofs_invalidated=False,diagnosis='Installed Quartus project file treatment rejects inline for(genvar b=...). A separately declared genvar matches the pre-existing generated tag-bank style; any correction requires a fresh source binding and private map attempt.')
put(O/'result.json',result)
files=[pathlib.Path(__file__),pathlib.Path(m.__file__),O/'prepared.json',O/'candidate.patch',O/'recipe.py',O/'native-map-gate.json',O/'result.json',O/'map-core/prepared.json',O/'map-core/completion.json',O/'map-core/map.log']+[O/'frozen'/x for x in d['source_sha256']]
files += list((O/'map-core/job').rglob('ap_core.qsf'))+list((O/'map-core/job').rglob('ap_core.map.rpt'))
put(O/'manifest.json',dict(passed_map=False,fitter_invoked=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}))
print(json.dumps(result,indent=2));print('MANIFEST',sha(O/'manifest.json'))
