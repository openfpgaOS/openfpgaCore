#!/usr/bin/env python3
"""Prepare an isolated exact-baseline os30 QGF1 queued front fit. Never launch Quartus."""
from pathlib import Path
import difflib,hashlib,json,re,shutil,subprocess
I=Path(__file__).resolve().parent
P=I.parent
R=P.parents[2]
OLD=P/'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1'
N=I/'native-v1'
B=I/'physical-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,o):p.write_text(json.dumps(o,indent=2)+'\n')
base=json.loads((OLD/'inputs.json').read_text())
qual=json.loads((OLD/'timing-qualification.json').read_text())
assert sha(OLD/'build/ap_core.qsf')==base['qsf_sha256']
assert sha(OLD/'os.bin')==base['os_sha256']
for rel,h in base['sources'].items():assert sha(OLD/'frozen'/rel)==h,rel
np=json.loads((N/'prepared.json').read_text())
for rel,h in np['source_sha256'].items():assert sha(N/rel)==h,rel
B.mkdir(exist_ok=False)
shutil.copytree(OLD/'frozen',B/'frozen')
(B/'build').mkdir();(B/'tools').mkdir()
shutil.copyfile(OLD/'build/ap_core.qpf',B/'build/ap_core.qpf')
shutil.copyfile(OLD/'os.bin',B/'os.bin')
common='src/fpga/common/os30/'
native_copies={}
for n in ('gpu_core.v','phase10_load_alias.v','geometry_raw_load.v','geometry_import_convert.v','geometry_import_view.v','geometry_float_cull_mem.v','gpu_queued_front.v'):
 src=N/'frozen/src/fpga/common'/n;dst=B/'frozen'/common/n
 shutil.copyfile(src,dst);native_copies[common+n]={'source':str(src),'sha256':sha(src)}
# Selected native GPU/LOAD baseline must match the qualified physical selection.
for n in ('gpu_core.v','phase10_load_alias.v','geometry_raw_load.v'):
 ref=P/'cached-cpu-v1/native-sdram-v2/frozen/src/fpga/common'/n
 assert sha(OLD/'frozen'/common/n)==sha(ref),n
rel='src/fpga/targets/pocket/os30/core_top.v'
top=B/'frozen'/rel;s=top.read_text();old=s
def replace(a,b):
 global s
 assert s.count(a)==1,(a[:70],s.count(a));s=s.replace(a,b)
replace('    // No result DMA and no GPU command ingress. CPU copies completed results.','    // CPU alias and optional ordered GPU import share the completed result BRAM.')
replace('    wire [5:0] geometry_view_count;','''    wire [5:0] geometry_view_count;
`ifdef INCLUDE_GPU_BRAM_IMPORT
    wire geometry_import_req, geometry_import_grant, geometry_import_error;
    wire geometry_import_read_done, geometry_import_release, geometry_import_fault, geometry_import_cancel;
    wire [31:0] geometry_import_token, geometry_import_data, geometry_import_release_token;
    wire [4:0] geometry_import_src;
    wire [7:0] geometry_import_addr;
`endif''')
ports='''`ifdef INCLUDE_GPU_BRAM_IMPORT
        .import_req(geometry_import_req), .import_token(geometry_import_token),
        .import_src(geometry_import_src), .import_addr(geometry_import_addr),
        .import_grant(geometry_import_grant), .import_error(geometry_import_error),
        .import_data(geometry_import_data), .import_read_done(geometry_import_read_done),
        .import_release(geometry_import_release), .import_release_token(geometry_import_release_token),
        .import_fault(geometry_import_fault),
'''
replace('    geometry_load_alias geometry_load (\n','''    geometry_load_alias geometry_load (
'''+ports+'''        // Both consumer reset replicas cancel local ownership only; raw DMA drains.
        .import_reset(!reset_n_cpu_core || !reset_n_cpu_media || geometry_import_cancel),
`endif
''')
replace(') gpu (\n',') gpu (\n'+ports+'''        .import_cancel(geometry_import_cancel),
`endif
''')
# The same point-to-point math/repair interface used by native-v1.
port_widths={
 'math_req':1,'math_grant':1,'math_release':1,
 'math_fma_valid':1,'math_fma_a':32,'math_fma_b':32,'math_fma_c':32,
 'math_fma_multiply':1,'math_fma_result_valid':1,'math_fma_result':32,'math_fma_unsupported':1,
 'math_scratch_read':1,'math_scratch_write':1,'math_scratch_read_addr':3,'math_scratch_write_addr':3,
 'math_scratch_data':32,'math_scratch_write_data':32,
 'queue_reg_wr':1,'queue_reg_addr':10,'queue_reg_wdata':32,'queue_reg_wstrb':4,'queue_reg_rdata':32}
declarations='`ifdef INCLUDE_GPU_QUEUED_FRONT\n'
connections='`ifdef INCLUDE_GPU_QUEUED_FRONT\n'
for port,width in port_widths.items():
 declarations+='    wire '+('['+str(width-1)+':0] ' if width!=1 else '')+'geometry_'+port+';\n'
 connections+='        .'+port+'(geometry_'+port+'),\n'
declarations+='`endif\n';connections+='`endif\n'
replace('    wire [5:0] geometry_view_count;','    wire [5:0] geometry_view_count;\n'+declarations)
replace('    geometry_load_alias geometry_load (\n','    geometry_load_alias geometry_load (\n'+connections)
replace(') gpu (\n',') gpu (\n'+connections)
for port in port_widths:
 assert s.count('.'+port+'(geometry_'+port+')')==2,port
top.write_text(s)
q=(OLD/'build/ap_core.qsf').read_text();qn=q.replace(str(OLD/'frozen'),str(B/'frozen'))
assert qn!=q
assert 'set_global_assignment -name SEED 3\n' in qn
assert 'set_global_assignment -name PLACEMENT_EFFORT_MULTIPLIER 3.0\n' in qn
assert 'CPU_FAIR_THRESHOLD(' not in s
append='\n# Isolated os30 QGF1 queued triangle front experiment.\nset_global_assignment -name VERILOG_MACRO INCLUDE_GPU_BRAM_IMPORT\nset_global_assignment -name VERILOG_MACRO INCLUDE_GPU_QUEUED_FRONT\n'
for n in ('geometry_import_convert.v','geometry_import_view.v','geometry_float_cull_mem.v','gpu_queued_front.v'):
 append+='set_global_assignment -name VERILOG_FILE '+str(B/'frozen'/common/n)+'\n'
qn+=append;(B/'build/ap_core.qsf').write_text(qn)
assert qn[:-len(append)].replace(str(B/'frozen'),str(OLD/'frozen'))==q
patch=''
changed=[];added=[];hashes={}
for f in sorted((B/'frozen').rglob('*')):
 if not f.is_file():continue
 rr=str(f.relative_to(B/'frozen'));hashes[rr]=sha(f);src=OLD/'frozen'/rr
 if not src.exists():added.append(rr);before=''
 elif sha(src)!=sha(f):changed.append(rr);before=src.read_text()
 else:continue
 patch+=''.join(difflib.unified_diff(before.splitlines(True),f.read_text().splitlines(True),fromfile='a/'+rr,tofile='b/'+rr))
assert changed==[common+'geometry_raw_load.v',common+'gpu_core.v',common+'phase10_load_alias.v',rel],changed
assert len(added)==4,added
(B/'physical-queued-front.patch').write_text(patch)
# The already reviewed helper mounts the root repo, including this sibling job.
F=P/'first-word-v1/fit-fair1'
for n in ('quartus-container.sh','oci.sh'):
 shutil.copyfile(F/'tools'/n,B/'tools'/n);(B/'tools'/n).chmod(0o755)
 subprocess.run(['bash','-n',str(B/'tools'/n)],check=True)
assert 'REPO='+str(R) in (B/'tools/quartus-container.sh').read_text()
runner=(F/'run_candidate.py').read_text()
runner=runner.replace("ap=argparse.ArgumentParser();ap.add_argument('--verify-only',action='store_true');a=ap.parse_args()\n if a.verify_only:print(json.dumps(verify(),indent=2))\n else:run()", "ap=argparse.ArgumentParser();ap.add_argument('--run',action='store_true');ap.add_argument('--verify-only',action='store_true');a=ap.parse_args()\n if a.run:run()\n else:print(json.dumps(verify(),indent=2))")
assert '--run' in runner
(B/'run_candidate.py').write_text(runner)
selected={}
for line in qn.splitlines():
 m=re.match(r'^set_global_assignment -name (?:QIP_FILE|VERILOG_FILE|SYSTEMVERILOG_FILE|MIF_FILE|SDC_FILE) (.+)$',line)
 if m:
  f=Path(m.group(1).strip('"')).resolve();assert f.is_file() and f.is_relative_to(B/'frozen'),f
  selected[str(f)]=sha(f)
dump(B/'inputs.json',dict(kind='paired',configured_mhz=90,seed=3,placement_effort_multiplier=3.0,cpu_fair_threshold=8,base_build=str(OLD),base_inputs_sha256=sha(OLD/'inputs.json'),base_qsf_sha256=sha(OLD/'build/ap_core.qsf'),native_model=str(N),native_prepared_sha256=sha(N/'prepared.json'),native_copies=native_copies,cpu_sha256=base['cpu_sha256'],firmware_sha256=base['firmware_sha256'],os_sha256=base['os_sha256'],sources=hashes,qsf_sha256=sha(B/'build/ap_core.qsf'),qpf_sha256=sha(B/'build/ap_core.qpf'),changed_sources=changed,added_sources=added,selected_files_sha256=selected,inherited_checkpoint=False,quartus_stages=['quartus_map','quartus_fit','quartus_asm','quartus_sta']))
dump(B/'provenance.json',dict(status='PREPARED_NOT_LAUNCHED',base_build=str(OLD),baseline_evidence_sha256={n:sha(OLD/n)for n in ('inputs.json','provenance.json','timing-qualification.json')},source_file_count=len(hashes),byte_identical_baseline_sources=len(base['sources'])-len(changed),changed_sources=changed,added_sources=added,native_model=str(N),native_copies=native_copies,qsf_semantic_changes=['Enable INCLUDE_GPU_BRAM_IMPORT and INCLUDE_GPU_QUEUED_FRONT only in this isolated os30 build','Register four QGF modules; omit direct importer, TFP1 and legacy geometry worker'],qsf_path_change='Independent frozen source copy only',seed=3,placement_effort_multiplier=3.0,configured_mhz=90,clocks_constraints_pins_firmware_unchanged=True,cpu_arbiter_and_memory_policy_unchanged=True,physical_reset_binding='!reset_n_cpu_core || !reset_n_cpu_media || geometry_import_cancel; no raw DMA cancellation',container_mount=str(R),fresh_map_required=True,copied_database_or_checkpoint=False,quartus_launched=False,baseline_fmax_mhz=qual['main_clock_fmax_mhz'],baseline_setup_slack_ns=qual['timing_minima_ns']['setup'],baseline_resources=qual['resources'],nominal_remaining_resources={'ALMs':311,'M10K':4,'DSP':21},candidate_resource_estimate='Unknown ALMs until fresh map/fit. Two512x32 RAMs expect4 M10Ks total (304 to308 of308); ingress ring replaces TFP job RAM; existing FMA and scratch reused. No timing or fit claim.',required_next=['Native full-frame correctness and speed comparison for QGF1','Independent physical wiring/source preflight','Only then explicitly launch run_candidate.py --run','Fresh fit plus all-corner timing/pin/reset/CDC qualification before deployment']))
files=['build/ap_core.qsf','build/ap_core.qpf','inputs.json','provenance.json','physical-queued-front.patch','run_candidate.py','tools/quartus-container.sh','tools/oci.sh','os.bin']
audit=dict(status='STATIC_PORT_BINDINGS_PASS',port_widths=port_widths,
    connection_rule='Every QGF port connects GPU and LOAD wrapper exactly once via a dedicated same-width clk_cpu net',
    reset_rule='Fabric geometry_reset owns raw DMA; consumer import_reset includes CPU/media resets and GPU cancellation, drains accepted FMA before raw grant',
    source_bindings=native_copies,physical_top_sha256=sha(top),
    qualified_clocks_pins_constraints_firmware_unchanged=True,
    excluded_modules=['geometry_bram_import.v','geometry_triangle_preflight.v'],quartus_launched=False)
dump(B/'static-port-audit.json',audit);files.append('static-port-audit.json')
external={str(Path(__file__).resolve()):sha(Path(__file__)),str(N/'prepared.json'):sha(N/'prepared.json'),
          str(N/'build.json'):sha(N/'build.json'),str(OLD/'inputs.json'):sha(OLD/'inputs.json'),
          str(OLD/'timing-qualification.json'):sha(OLD/'timing-qualification.json')}
external.update({v['source']:v['sha256'] for v in native_copies.values()})
dump(B/'manifest.json',{'inputs_sha256':{n:sha(B/n)for n in files},'external_inputs_sha256':external})
r=subprocess.run(['python3',str(B/'run_candidate.py'),'--verify-only'],capture_output=True,text=True,check=True)
(B/'preflight.json').write_text(r.stdout)
print(r.stdout)
