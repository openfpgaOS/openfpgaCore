#!/usr/bin/env python3
"""Independent source/binding review only; invokes no generator, map or fitter."""
import hashlib,json,pathlib,re,difflib
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';S=P/'cpu-cache64-resource-v1';O=P/'cpu-cache64-source-review-v1';B=P/'compact-lod5-core-v7';OLD=P/'cpu-os30-repro-v5/proposed/src/fpga/vendor/vexriscv';CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x')as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def main():
 O.mkdir(exist_ok=False);manifest=json.loads((S/'manifest.json').read_text());bindings=manifest['evidence_sha256']
 for n,h in bindings.items():assert sha(R/n)==h,n
 prepared=json.loads((S/'prepared.json').read_text());qual=json.loads((S/'qualification.json').read_text());res=json.loads((S/'resource-result.json').read_text());regen=json.loads((S/'regeneration.json').read_text())
 for n,h in prepared['production_guard'].items():assert sha(R/n)==h,n
 oldcfg=(OLD/'configs/os30.cfg').read_text();newcfg=(S/'vendor/configs/os30.cfg').read_text();addition='# Private resource experiment: 64 KiB instruction / 64 KiB data caches.\nICACHE_SETS=512\nDCACHE_SETS=512\n';assert newcfg.replace(addition,'')==oldcfg and newcfg.count(addition)==1
 assert newcfg.index(addition)==newcfg.index('if [ "$VARIANT" = os30 ]; then\n')+len('if [ "$VARIANT" = os30 ]; then\n')
 state=(S/'normalized-before-rs3.v').read_text();helper_checks=[]
 for item in qual['chain']:
  old=(OLD/item['helper']).read_text();new=(S/'vendor'/item['helper']).read_text();normalize=lambda x:re.sub(r"my \$(before|after)='[a-f0-9]{64}';",lambda m:'my $'+m[1]+"='<SHA>';",x)
  assert normalize(old)==normalize(new);assert sum(1 for a,b in zip(old.splitlines(),new.splitlines())if a!=b)==2
  blocks=re.findall(r"my \$old=<<'([^']+)';\n(.*?)\n\1\n\s*my \$new=<<'([^']+)';\n(.*?)\n\3\n",old,re.S);assert len(blocks)==len(item['hunks'])
  assert hashlib.sha256(state.encode()).hexdigest()==item['before_sha256']
  for ot,a,nt,b in blocks:
   if 'frontend' in item['helper']:a+='\n';b+='\n'
   assert state.count(a)==1;state=state.replace(a,b)
  assert hashlib.sha256(state.encode()).hexdigest()==item['after_sha256'];helper_checks.append(dict(helper=item['helper'],exact_unchanged_hunks=len(blocks),only_two_hash_constants_changed=True))
 candidate=(S/'VexiiRiscv.v').read_text();assert candidate==state;assert regen['passed'] and regen['actual_sha256']==sha(S/'VexiiRiscv.v')==qual['candidate_cpu_sha256']
 parent=(B/'frozen'/CPU).read_text();assert re.search(r'module VexiiRiscv\s*\(.*?\);',parent,re.S).group()==re.search(r'module VexiiRiscv\s*\(.*?\);',candidate,re.S).group()
 def arrays(s):return {n:(int(w)+1,int(d)+1)for w,n,d in re.findall(r'reg\s+\[(\d+):0\]\s+(\w+)\s+\[0:(\d+)\];',s)}
 a,b=arrays(parent),arrays(candidate);assert a.keys()==b.keys();changed={n:dict(before=a[n],after=b[n])for n in a if a[n]!=b[n]};assert len(changed)==16
 for n,v in changed.items():
  if n.startswith('LsuL1Plugin_logic_banks_'):assert v==dict(before=(8,16384),after=(8,8192))
  elif n.startswith('LsuL1Plugin_logic_ways_'):assert v==dict(before=(18,1024),after=(19,512))
  elif n=='LsuL1Plugin_logic_shared_mem':assert v==dict(before=(3,1024),after=(3,512))
  elif n.startswith('FetchL1Plugin_logic_banks_'):assert v==dict(before=(64,2048),after=(64,4096))
  elif n.startswith('FetchL1Plugin_logic_ways_'):assert v==dict(before=(20,256),after=(19,512))
  elif n=='FetchL1Plugin_logic_plru_mem':assert v==dict(before=(1,256),after=(1,512))
  else:raise AssertionError(n)
 ram_delta=sum(b[n][0]*b[n][1]-a[n][0]*a[n][1] for n in changed);assert ram_delta==-271616==res['total_delta']['block_memory_bits']
 for n,h in prepared['baseline_frozen'].items():
  assert sha(B/'frozen'/n)==h,n
  assert sha(S/'frozen'/n)==(qual['candidate_cpu_sha256']if n==CPU else h),n
 mp=json.loads((S/'map-core/prepared.json').read_text());qsf=next((S/'map-core/job').rglob('ap_core.qsf'));bq=next((B/'fit-seed2/jobs').rglob('ap_core.qsf'));assert qsf.read_bytes()==bq.read_bytes();assert sha(qsf)==mp['qsf_sha256']
 assert res['passed_map'] and res['actual_generator_flag_changes']==[{'after':'--fetch-l1-sets=512','before':'--fetch-l1-sets=256'},{'after':'--lsu-l1-sets=512','before':'--lsu-l1-sets=1024'}]
 review=dict(passed=True,source_resource_review_passed=True,deployment_ready=False,candidate_cpu_sha256=sha(S/'VexiiRiscv.v'),parent_cpu_sha256=sha(B/'frozen'/CPU),author_evidence_bindings_checked=len(bindings),frozen_files_checked=len(prepared['baseline_frozen']),only_changed_synthesis_source=CPU,exact_qsf_seed_clocks_unchanged=True,seed=2,interface_unchanged=True,helper_checks=helper_checks,changed_arrays=changed,ram_bits_delta=ram_delta,capacities_bytes={'before':{'I':32768,'D':131072},'after':{'I':65536,'D':65536}},ways=2,line_bytes=64,source_audit=['All generated changes are cache set/address/tag/counter widths, RAM depths or corresponding cache address slices. No predictor, arithmetic, ISA, forwarding or queue-capacity change.','D tags gain bit15 as sets shrink; I tags lose bit14 as sets grow. Writeback concatenation still reconstructs all address bits31:6. Initializers and CBO/flusher counters use the corresponding new index widths.','All postprocessor transformation bodies and their one/eight literal hunks are unchanged; only exact before/after SHA guards are rebound to the regenerated cache geometry.','The actual isolated generator succeeds and exactly reproduces the independently applied unchanged helper chain.'],map_delta=res['total_delta'],model_regression=res['model_windows'],limits=['Resource-only source gate; no actual native CPU validation of this cache geometry has been run.','Logical RAM-bit reduction is not a fitted M10K/LAB/timing result; estimated ALMs increase9.','The existing matched software model is slower by0.206ms warm/0.140ms cold, presentation unchanged. This is not a speed improvement.','No generator, compiler, Quartus, simulation or production action was run by this reviewer.'])
 put(O/'review.json',review);(O/'REPORT.txt').write_text('PASS for the isolated resource experiment source closure, not deployment.\n\nOnly os30 cache set counts change: I32/D128 KiB becomes I64/D64 KiB with two ways and64-byte lines. The remaining114 frozen files, actual QSF, seed2 and clocks match v7. The existing helper equations are identical; real generation reproduces the exact candidate. Cache address/tag and flush/index widths are consistent.\n\nMap reduces271616 RAM bits,49 CPU ALUTs and7 CPU registers, but total estimated ALMs rise9. Physical M10K packing/timing remains unknown. Existing modeled CPU time regresses slightly; no speed claim or actual native cache qualification.\n')
 files=[S/'manifest.json',S/'prepared.json',S/'qualification.json',S/'resource-result.json',S/'regeneration.json',S/'generated-cache-only.patch',S/'VexiiRiscv.v',O/'review.json',O/'REPORT.txt',pathlib.Path(__file__)]
 put(O/'manifest.json',dict(passed=True,deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files}));print(sha(O/'manifest.json'))
if __name__=='__main__':main()
