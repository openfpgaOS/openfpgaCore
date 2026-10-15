#!/usr/bin/env python3
"""Read-only source/QSF preparation audit; never invokes Quartus."""
from pathlib import Path
import hashlib,json,re
HERE=Path(__file__).resolve().parent; B=HERE.parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
candidate=HERE/'prepared-v2'; rec=json.loads((candidate/'inputs.json').read_text())
base=Path(rec['physical_baseline']); native=Path(rec['native']); prep=json.loads((native/'prepared.json').read_text()); qual=json.loads((base/'timing-qualification.json').read_text())
assert rec['status']=='PREPARED_ONLY_NOT_COMPILED'
for n,h in rec['source_sha256'].items():assert sha(candidate/'frozen'/n)==h,n
for n,h in prep['source_sha256'].items():assert sha(native/n)==h,n
basefiles={str(p.relative_to(base/'frozen')):sha(p)for p in (base/'frozen').rglob('*')if p.is_file()}
changes={n for n,h in rec['source_sha256'].items()if basefiles.get(n)!=h}
assert changes==set(rec['changed_rtl'])
qbase=(base/'build/ap_core.qsf').read_text();qnew=(candidate/'build/ap_core.qsf').read_text();normalized=qnew.replace(str(candidate/'frozen'),str(base/'frozen'))
assert normalized.startswith(qbase),'An original QSF setting changed'
extra=normalized[len(qbase):]
for line in extra.splitlines():
 if not line:continue
 assert re.match(r'set_global_assignment -name (VERILOG_MACRO INCLUDE_GPU_ASYNC_MESH|VERILOG_FILE .+/src/fpga/common/os30/[^ /]+\.v|SEARCH_PATH .+/src/fpga/common/os30)$',line),line
for n,h in rec['constraints_sha256'].items():assert sha(base/'frozen'/n)==sha(candidate/'frozen'/n)==h
assert sha(base/'os.bin')==sha(candidate/'os.bin')==rec['os_sha256']
assert sha(base/'build/ap_core.qpf')==sha(candidate/'build/ap_core.qpf')
for dirname in ['db','incremental_db','output_files']:assert not(candidate/'build'/dirname).exists()
assert qual['configured_mhz']==rec['configured_mhz']==90
assert qual['fitter_effective_settings']['seed']==rec['seed']==3
assert qual['fitter_effective_settings']['placement_effort_multiplier']==rec['placement_effort']==3
for name,entry in rec['changed_rtl'].items():
 if 'native_source'in entry:assert sha(Path(entry['native_source']))==sha(candidate/'frozen'/name)
assert len(changes)==14
files=[candidate/'inputs.json',HERE/'prepare_v2.py',HERE/'assessment-v2.txt',Path(__file__),native/'prepared.json',base/'timing-qualification.json',B/'design/abi-v2.json',B/'design/ports.vh']
result={'status':'PREPARED_ONLY_NO_FIT','bindings':{str(p):sha(p)for p in files},'source_inventory_count':len(rec['source_sha256']),'native_inventory_count':len(prep['source_sha256']),'changed_inputs':len(changes),'original_qsf_prefix_unchanged':True,'clocks_constraints_firmware_unchanged':True,'no_database_or_outputs':True,'qualified_baseline_resources':qual['resources'],'qualified_baseline_fmax_mhz':qual['main_clock_fmax_mhz'],'qualified_baseline_worst_setup_ns':qual['timing_minima_ns']['setup']}
(HERE/'review-v2.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
