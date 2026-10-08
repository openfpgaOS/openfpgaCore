#!/usr/bin/env python3
"""Private os30 I64/D64 resource experiment; unchanged v7 equations, no fitter."""
import copy,difflib,hashlib,json,os,pathlib,re,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'cpu-cache64-resource-v1';OLD=P/'cpu-os30-repro-v5';Q=P/'cpu-queues-v1';BASE=P/'compact-lod5-core-v7'
V=R/'src/fpga/vendor/vexriscv';REL=pathlib.Path('src/fpga/vendor/vexriscv');CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
hashtext=lambda s:hashlib.sha256(s.encode()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def sources(src):
 out={str(p.relative_to(src)):sha(p) for p in src.rglob('*.scala') if 'target' not in p.relative_to(src).parts}
 for n in ('build.sbt','project/build.properties'):out[n]=sha(src/n)
 return out
def guard():
 d=json.loads((O/'prepared.json').read_text())
 assert sources(O/'vendor/VexiiRiscv')==d['scala_sources']
 for n,h in d['production_guard'].items():assert sha(R/n)==h,n
 for n,h in d['baseline_frozen'].items():assert sha(BASE/'frozen'/n)==h,n
 return d
def prepare():
 O.mkdir(exist_ok=False);vendor=O/'vendor';vendor.mkdir();src=vendor/'VexiiRiscv'
 shutil.copytree(Q/'source',src,ignore=shutil.ignore_patterns('target','.git','VexiiRiscv*.v','vexii_active.v'))
 original=json.loads((Q/'sources.json').read_text());assert sources(src)==original
 for h in V.glob('*.pl'):shutil.copy2(h,vendor/h.name)
 proposed=OLD/'proposed'/REL
 for n in ('generate_vexii.sh','factor_os30_fp_rs3.pl','factor_os30_frontend.pl'):shutil.copy2(proposed/n,vendor/n)
 (vendor/'configs').mkdir();cfg=(proposed/'configs/os30.cfg').read_text();anchor='if [ "$VARIANT" = os30 ]; then\n';assert cfg.count(anchor)==1
 cfg=cfg.replace(anchor,anchor+'# Private resource experiment: 64 KiB instruction / 64 KiB data caches.\nICACHE_SETS=512\nDCACHE_SETS=512\n')
 (vendor/'configs/os30.cfg').write_text(cfg)
 shutil.copytree(V/'patches',vendor/'patches')
 subprocess.run(['cp','-a','--reflink=auto',str(R/'tools/.vexii-home'),str(O/'dependency-cache')],check=True)
 image=json.loads((Q/'generated.json').read_text())['docker_image']
 cmd=['docker','run','--rm','--name','phase10-cpu-cache64-resource-v1','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
      '--env','HOME=/vexiihome','--env','VEXIIRISCV_DEBUG_PARAM=0','--env','SPINALHDL_FROM_SOURCE=1',
      '--env','JAVA_TOOL_OPTIONS=-Duser.home=/vexiihome -Dsbt.boot.directory=/vexiihome/.sbt/boot -Dsbt.global.base=/vexiihome/.sbt -Dsbt.ivy.home=/vexiihome/.ivy2',
      '-v',f'{vendor}:{vendor}','-v',f'{O}/dependency-cache:/vexiihome','--tmpfs','/tmp:exec','-w',str(src),image,'bash','../generate_vexii.sh','os30']
 baseline=json.loads((BASE/'prepared.json').read_text())['source_sha256'];assert inv(BASE/'frozen')==baseline
 prod={str(p.relative_to(R)):sha(p) for p in sorted(V.rglob('*')) if p.is_file() and (p.parent==V or p.parent==V/'configs' or p.name.startswith('VexiiRiscv_') and p.suffix=='.v')}
 inputs={str(p.relative_to(vendor)):sha(p) for p in vendor.rglob('*') if p.is_file() and src not in p.parents}
 put(O/'prepared.json',dict(scope=__doc__,command=cmd,pinned_image=image,scala_sources=original,baseline_frozen=baseline,production_guard=prod,generator_inputs_sha256=inputs,parent_repro_manifest_sha256=sha(OLD/'manifest.json'),parent_regeneration_manifest_sha256=sha(P/'cpu-os30-regenerate-v5/manifest.json'),model_manifest_sha256=sha(P/'renderer-pgo-cache64-v1/manifest.json'),model_is_not_speed_improvement=True,network_disabled=True,private_dependency_cache=True,seed=2,full_fit_authorized=False))
 shutil.copy2(__file__,O/'initial-recipe.py')
 (O/'config.patch').write_text(''.join(difflib.unified_diff((proposed/'configs/os30.cfg').read_text().splitlines(True),cfg.splitlines(True),fromfile='repro-v5/os30.cfg',tofile='cache64/os30.cfg')))
 print(O,flush=True)
def capture():
 d=guard();vendor=O/'vendor';src=vendor/'VexiiRiscv'
 for n,h in d['generator_inputs_sha256'].items():assert sha(vendor/n)==h,n
 start=time.monotonic()
 with (O/'capture.log').open('x') as f:r=subprocess.run(d['command'],stdout=f,stderr=subprocess.STDOUT,timeout=900)
 log=(O/'capture.log').read_text();raw=src/'VexiiRiscv.v'
 result=dict(returncode=r.returncode,seconds=time.monotonic()-start,expected_fail_closed_hash_guard=True,raw_exists=raw.exists(),published=(src/'VexiiRiscv_os30.v').exists())
 assert r.returncode!=0 and re.search(r'Unqualified os30 CPU body \([a-f0-9]{64}\); retaining prior output',log),log[-3000:]
 assert raw.exists() and not result['published']
 shutil.copy2(raw,O/'raw-before-rs3.v');s=raw.read_text();header,body=s.split('\n\n',1)
 assert re.fullmatch(r'// Generator : [^\n]+\n// Component : VexiiRiscv(?:\n// Git hash  : [^\n]+)?',header)
 helper=(vendor/'factor_os30_fp_rs3.pl').read_text();qh=re.search(r"my \$qualified_header=<<'HEADER';\n(.*?)\nHEADER",helper,re.S).group(1)
 normalized=qh+'\n\n'+body;(O/'normalized-before-rs3.v').write_text(normalized)
 result.update(raw_sha256=sha(raw),normalized_sha256=hashtext(normalized),production_unchanged=True)
 assert result['normalized_sha256'] in log;guard();put(O/'capture-result.json',result);print(json.dumps(result,indent=2),flush=True)
def qualify():
 guard();vendor=O/'vendor';state=(O/'normalized-before-rs3.v').read_text();chain=[]
 for name,label in [('factor_os30_fp_rs3.pl','rs3'),('factor_os30_frontend.pl','frontend')]:
  original=(OLD/'proposed'/REL/name).read_text();helper=(vendor/name).read_text();assert helper==original
  before=hashtext(state);hunks=[]
  blocks=re.findall(r"my \$old=<<'([^']+)';\n(.*?)\n\1\n\s*my \$new=<<'([^']+)';\n(.*?)\n\3\n",helper,re.S)
  assert len(blocks)==(1 if label=='rs3' else 8),(name,len(blocks))
  for ot,old,nt,new in blocks:
   if label!='rs3':old+='\n';new+='\n'
   assert state.count(old)==1,(name,ot,state.count(old))
   state=state.replace(old,new);hunks.append(dict(old_token=ot,new_token=nt,count=1,old_sha256=hashtext(old),new_sha256=hashtext(new)))
  after=hashtext(state);expected=O/(label+'-expected.v');expected.write_text(state)
  oldbefore=re.search(r"my \$before='([a-f0-9]{64})';",helper).group(1);oldafter=re.search(r"my \$after='([a-f0-9]{64})';",helper).group(1)
  helper=helper.replace("my $before='"+oldbefore+"';","my $before='"+before+"';").replace("my $after='"+oldafter+"';","my $after='"+after+"';")
  (vendor/name).write_text(helper)
  # Execute the unchanged transformation/guard bodies; independently calculated hashes must agree.
  test=O/(label+'-actual.v');shutil.copy2(O/'normalized-before-rs3.v' if label=='rs3' else O/'rs3-actual.v',test)
  cmd=['perl',str(vendor/name),'os30',str(test)]
  r=subprocess.run(cmd,capture_output=True,text=True);(O/(label+'-check.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,(name,r.stderr)
  assert test.read_bytes()==expected.read_bytes();assert subprocess.run(cmd,capture_output=True).returncode==0
  bad=O/(label+'-negative.v');bad.write_bytes(test.read_bytes()+b'\n// changed unknown input\n');badsha=sha(bad)
  reject=subprocess.run(['perl',str(vendor/name),'os30',str(bad)],capture_output=True,text=True)
  assert reject.returncode!=0 and sha(bad)==badsha
  (O/(label+'-negative.log')).write_text(reject.stdout+reject.stderr)
  chain.append(dict(helper=name,before_sha256=before,after_sha256=after,hunks=hunks,original_helper_sha256=sha(OLD/'proposed'/REL/name),helper_sha256=sha(vendor/name),same_equations_only_two_hash_constants_changed=True,negative_guard_unchanged=True))
 shutil.copy2(O/'frontend-expected.v',O/'VexiiRiscv.v')
 put(O/'qualification.json',dict(candidate_cpu_sha256=sha(O/'VexiiRiscv.v'),chain=chain,old_cpu_sha256=json.loads((BASE/'prepared.json').read_text())['source_sha256'][CPU],only_config_change='os30 I-cache sets 256→512; D-cache sets 1024→512',other_variants_unchanged=True))
 # Evaluate this exact shell config for both profiles, retaining defaults for os30_worker.
 config_checks={}
 for variant in ['os30','os30_worker']:
  cmd=['bash','-c','VARIANT="$1"; source "$2"; printf "%s %s\n" "$ICACHE_SETS" "$DCACHE_SETS"','config-check',variant,str(vendor/'configs/os30.cfg')]
  values=subprocess.check_output(cmd,text=True).strip();assert values==('512 512' if variant=='os30' else '256 1024');config_checks[variant]=values
 put(O/'config-checks.json',config_checks);guard();print(json.dumps(chain,indent=2),flush=True)
def regenerate():
 d=guard();q=json.loads((O/'qualification.json').read_text());vendor=O/'vendor';src=vendor/'VexiiRiscv'
 for n,h in d['generator_inputs_sha256'].items():
  if n not in ['factor_os30_fp_rs3.pl','factor_os30_frontend.pl']:assert sha(vendor/n)==h,n
 for c in q['chain']:assert sha(vendor/c['helper'])==c['helper_sha256']
 start=time.monotonic()
 with (O/'regenerate.log').open('x') as f:r=subprocess.run(d['command'],stdout=f,stderr=subprocess.STDOUT,timeout=900)
 output=src/'VexiiRiscv_os30.v';result=dict(returncode=r.returncode,seconds=time.monotonic()-start,output_exists=output.exists(),expected_sha256=q['candidate_cpu_sha256'])
 if output.exists():result['actual_sha256']=sha(output)
 result['passed']=r.returncode==0 and result.get('actual_sha256')==result['expected_sha256'];guard();put(O/'regeneration.json',result);print(json.dumps(result,indent=2),flush=True);assert result['passed']
def map_only():
 guard();assert json.loads((O/'regeneration.json').read_text())['passed'];frozen=O/'frozen';shutil.copytree(BASE/'frozen',frozen);shutil.copy2(O/'VexiiRiscv.v',frozen/CPU)
 old=json.loads((O/'prepared.json').read_text())['baseline_frozen'];new=inv(frozen);assert sorted(n for n in old if old[n]!=new[n])==[CPU]
 m=O/'map-core';m.mkdir(exist_ok=False);sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 row=copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=new[CPU]
 job,project=sweep.prepare(frozen,m/'job',row,2,4);qsf=job/(project+'.qsf')
 originalqsf=next((BASE/'fit-seed2/jobs').rglob('ap_core.qsf'));assert qsf.read_bytes()==originalqsf.read_bytes()
 cmd=sweep.docker(m,'pocket',job,['quartus_map',project]);put(m/'prepared.json',dict(command=cmd,source_sha256=new,only_changed_file=CPU,qsf_sha256=sha(qsf),seed=2,fitter_invoked=False))
 start=time.monotonic()
 with (m/'map.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 put(m/'completion.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,passed=r.returncode==0));guard();print('MAP',r.returncode,flush=True);assert r.returncode==0
if __name__=='__main__':globals()[sys.argv[1]]()
