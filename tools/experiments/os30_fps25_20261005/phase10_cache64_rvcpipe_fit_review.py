#!/usr/bin/env python3
"""Independent read-only binding and one-fit gate; never invokes CAD or tests."""
import ast, hashlib, json, pathlib, re
R=pathlib.Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'; T=R/'tools/experiments/os30_fps25_20261005'
C=P/'cache64-rvcpipe-map-v1'; F=P/'cache64-rvcpipe-fit-v1'; O=P/'cache64-rvcpipe-fit-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read(p): return json.loads(p.read_text())
def put(p,d):
 with p.open('x') as f: json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def inv(p): return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
evidence={};checks=0
def bind(p):
 global checks
 evidence[str(p.relative_to(R))]=sha(p)
 return read(p)
def manifest(p):
 global checks
 d=bind(p)
 for n,h in d.get('evidence_sha256',{}).items():
  assert sha(R/n)==h,n
  checks+=1
 return d
for n in ['cache64-rvcpipe-map-v1','cpu-cache64-rvcpipe-v1','cpu-cache64-rvcpipe-source-review-v1','cpu-cache64-rvcpipe-directed-v1','native-rvcpipe-comparison-v1']:
 manifest(P/n/'manifest.json')
prepared=bind(F/'prepared.json'); mp=bind(C/'prepared.json'); mr=bind(C/'result.json'); nr=bind(P/'native-rvcpipe-comparison-v1/result.json'); dr=bind(P/'cpu-cache64-rvcpipe-directed-v1/result.json')
assert mr['passed_map'] and nr['passed'] and dr['passed']
assert prepared['recipe_sha256']==sha(T/'phase10_cache64_rvcpipe_fit.py')
assert prepared['component_manifest_sha256']==sha(C/'manifest.json')
assert inv(C/'map-core/job')==prepared['mapped_job_sha256']==inv(F/'job')
changed=[n for n,h in mp['source_sha256'].items() if mp['parent_source_sha256'].get(n)!=h]
assert changed==['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'],changed
assert mp['source_sha256'].keys()==mp['parent_source_sha256'].keys()
for n,h in mp['source_sha256'].items():assert sha(C/'frozen'/n)==h,n
assert mp['component_cpu_sha256']==mr['candidate_cpu_sha256']=='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
qrel=pathlib.Path('src/fpga/targets/pocket/ap_core.qsf');q=F/'job'/qrel
assert q.read_bytes()==(P/'cache64-timing-compose-v2/map-core/job'/qrel).read_bytes()
qs=q.read_text(); assert re.findall(r'^set_global_assignment -name SEED (\d+)$',qs,re.M)==['2']
locs=[l for l in qs.splitlines() if l.startswith('set_location_assignment ')];assert len(locs)==224 and all('PIN_' in l for l in locs)
assert not list((F/'job').rglob('*.fit.rpt')) and not list((F/'job').rglob('*.sta.rpt')) and not (q.parent/'timing').exists()
# Mechanics unchanged from the already reviewed exact-copy/fresh-corner runner.
def funcs(p):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef)}
old=funcs(T/'phase10_cache64_timing_fit.py');new=funcs(T/'phase10_cache64_rvcpipe_fit.py')
for name in ['inv','put','component','checked','run']:assert old[name]==new[name],name
assert prepared['fit_count']==1 and prepared['seed']==2 and not prepared['seed_sweep']
assert mr['entity_delta']['VexiiRiscv']['registers']==115 and mr['entity_delta']['gpu_core']['registers']==-1
assert mr['total_delta']['registers']==114
reports=[P/'cache64-timing-compose-v2/map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt',C/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt']
texts=[p.read_text(errors='replace') for p in reports]
needle='core_top:ic|gpu_core:gpu|spanprod_header_supported'
assert needle not in texts[0]
removed=[(i,l) for i,l in enumerate(texts[1].splitlines(),1) if needle in l]
assert len(removed)==1 and 'Lost fanout' in removed[0][1]
for p in reports+[q,T/'phase10_cache64_rvcpipe_fit.py',T/'phase10_cache64_timing_fit.py',pathlib.Path(__file__)]:evidence[str(p.relative_to(R))]=sha(p)
O.mkdir(exist_ok=False)
result=dict(passed=True,checked_proof_bindings=checks,changed_source=changed,source_files=len(mp['source_sha256']),candidate_cpu_sha256=mp['component_cpu_sha256'],mapped_job_files=len(prepared['mapped_job_sha256']),map_delta=mr['total_delta'],mapped_register_attribution={'CPU':115,'unchanged_GPU':-1,'GPU_removal':'spanprod_header_supported: Lost fanout','candidate_report_line':removed[0][0],'scope':'The added CPU frontend stage changes RTL state; the GPU difference is synthesis pruning of unchanged RTL, not a GPU architecture change.'},runner_mechanics_identical_to_prior=True,seed=2,original_pins=224,no_internal_location_constraints=True,no_new_tests_or_CAD=True,production_modified=False,deployment_ready=False,limitations=['Synthesis estimated ALMs are not fitted placement; physical trial remains necessary.','One added frontend stage is not cycle equivalent: native cold call +1.089636% and two intervals +0.585162% versus I64/D64 control; exact buffers and commands pass.','Directed quiet/scan frontend and stress tests pass IRQ, ECALL, redirects, compressed line boundaries, FPU and atomics.','No sustained FPS or native operating-system/audio claim.'])
put(O/'result.json',result);evidence[str((O/'result.json').relative_to(R))]=sha(O/'result.json')
(O/'REPORT.txt').write_text('PASS for exactly one ordinary seed-2 fit. Only CPU source differs from the preceding I64/D64 timing composition; GPU8, audio, all constraints, clocks, and original 224 pin assignments remain exact. No placement backannotation or seed sweep.\n\nMap changes: CPU -40 ALUT / +115 registers; whole core -67 ALUT / +114 registers, RAM/DSP unchanged, estimated ALMs +65 to 18,011. The unchanged GPU contributes -1 register: candidate map line 4322 removes spanprod_header_supported for Lost fanout; the prior map has no such removal. This is CAD pruning, not a new GPU source/state change.\n\nNative renderer buffers/commands and directed IRQ/frontend/stress tests are bound and pass. The pipeline costs 1.089636% in the cold renderer call and 0.585162% across the two measured intervals versus I64/D64 control; the latter remains 1.270622% faster than original I32/D128. No cycle-equivalence, sustained FPS, native OS/audio, or physical-success claim.\n\nThe fit runner uses the reviewed exact-map-copy mechanics, exclusive output creation, fit/assembly/STA followed by fresh four-corner 20-row checks, exact permitted timing-failure handling and post-run source hashing. Root authorized this one trial; this review launches nothing.\n')
evidence[str((O/'REPORT.txt').relative_to(R))]=sha(O/'REPORT.txt')
put(O/'manifest.json',dict(passed=True,deployment_ready=False,evidence_sha256=evidence))
evidence[str((O/'manifest.json').relative_to(R))]=sha(O/'manifest.json')
put(F/'independent-review.json',dict(passed_for_one_fit=True,prepared_sha256=sha(F/'prepared.json'),deployment_ready=False,exactly_one_fit=True,seed=2,seed_sweep=False,component_and_native_proofs_complete=True,source_and_map_binding_passed=True,evidence_sha256=evidence))
print('REVIEW',sha(O/'manifest.json'));print('GATE',sha(F/'independent-review.json'));print('BINDINGS',checks)
