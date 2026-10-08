#!/usr/bin/env python3
"""One private, exact-v7 physical ECO. No source/constraint edits or full fitter flow."""
import argparse, hashlib, json, pathlib, re, shutil, subprocess, zlib
R=pathlib.Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10/eco-v7-local-placement-v1'
CDB=pathlib.Path('/home/alberto/altera_lite/25.1std/quartus/bin/quartus_cdb')
TARGET='core_top:ic|cpu_system:cpu|VexiiRiscv:cpu|WideOr109~0'
OLD='LABCELL_X50_Y32_N33'
NEW='LABCELL_X44_Y34_N6'
JOB=P/'run-job'
WD=JOB/'src/fpga/targets/pocket'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
def origin_guard():
 d=json.loads((P/'prepared.json').read_text()); original=R/d['original_job']
 for f,h in d['source_db_sha256'].items():assert sha(original/f)==h,('original changed',f)
 return d,original
def run(tcl,label):
 origin_guard()
 with (P/(label+'.log')).open('x') as f:
  rc=subprocess.run([str(CDB),'-t',str(tcl)],cwd=WD,stdout=f,stderr=subprocess.STDOUT).returncode
 origin_guard();assert rc==0,(label,rc)
def export_script(label):
 return '''package require ::quartus::chip_planner
package require ::quartus::atoms
project_open ap_core
read_netlist
set nf [open {%s} w]
set rf [open {%s} w]
set count 0
set routes 0
foreach_in_collection node [get_nodes -type all] {
 puts $nf "${node}\\t[get_node_info -node $node -info name]\\t[get_node_info -node $node -info {Location String}]"
 incr count

}
close $nf
close $rf
puts [list EXPORT_NODES $count EXPORT_ROUTES $routes]
read_atom_netlist -type cmp
write_atom_netlist -file {%s}
project_close
'''%(P/(label+'-nodes.tsv'),P/(label+'-routes.tsv'),P/(label+'.atm'))
def prepare():
 d,original=origin_guard();assert not JOB.exists(),'private run exists; never overwrite'
 shutil.copytree(original,JOB)
 assert all(sha(JOB/f)==h for f,h in d['source_db_sha256'].items())
 for name in ['baseline','after']:(P/(name+'-export.tcl')).write_text(export_script(name))
 (P/'apply-one-move.tcl').write_text('''package require ::quartus::chip_planner
project_open ap_core
read_netlist
set node [get_node_by_name -name {%s}]
if {$node < 0} {error "Missing exact source atom"}
if {[get_node_info -node $node -info {Location String}] ne {%s}} {error "Unexpected original location"}
set set_ok [set_node_info -node $node -info {Location Index} {%s}]
if {$set_ok != 1} {discard_all_changes; error "Single location edit rejected"}
if {[get_node_info -node $node -info {Location String}] ne {%s}} {discard_all_changes; error "Location differs from requested site"}
puts [list REQUESTED_ONE_MOVE {%s} {%s} {%s}]
# This is the one authorized ECO fitter invocation, not a full compile.
set saved [check_netlist_and_save]
puts [list ECO_SAVE_RESULT $saved]
if {$saved != 1} {error "ECO netlist was not saved legally"}
set actual [get_node_info -node $node -info {Location String}]
puts [list ECO_ACTUAL_LOCATION $actual]
if {$actual ne {%s}} {error "ECO moved target elsewhere"}
project_close
'''%(TARGET,OLD,NEW,NEW,TARGET,OLD,NEW,NEW))
 put(P/'proposal.json',{'target':TARGET,'old':OLD,'new':NEW,'device':'5CEBA4F23C8','seed':2,'dry_legal':True,'fitter_executed':False,'run_command':'python3 tools/experiments/os30_fps25_20261005/phase10_v7_local_eco.py execute','tcl_sha256':sha(P/'apply-one-move.tcl'),'source_and_db_files':len(d['source_db_sha256']),'source_db_inventory_sha256':sha(P/'prepared.json'),'limits':['Single LUT location change; no RTL, clock, QSF or SDC change.','No route-preservation assumption.','All logical properties must compare strictly; only exact target location and export timestamp initially allowed.','Fresh assembly and all-corner STA required separately after logical/placement review.']})
 origin_guard()
def atm_signature(label):
 raw=(P/(label+'.atm')).read_bytes();data=zlib.decompress(raw[8:]).decode()
 assert data.endswith('}\n\n') and data.count('\nnode ')==56374,'incomplete or changed atom inventory'
 # Retain all configuration, hierarchy, constants and every named port/fanout.
 # Only documented export timestamp and the single approved placement differ.
 normalized=re.sub(r'(?m)^// date_created = .*\n','',data)
 if label=='after':
  marker='node ARRIAV_LCELL_COMB n29514 "WideOr109~0"\n'
  start=normalized.index(marker);end=normalized.index('\nnode ',start+len(marker))
  block=normalized[start:end]
  assert block.count('\tLOCATION = '+NEW+'\n')==1
  normalized=normalized[:start]+block.replace('\tLOCATION = '+NEW+'\n','\tLOCATION = '+OLD+'\n')+normalized[end:]
 return {'atom_count':56374,'decompressed_sha256':hashlib.sha256(data.encode()).hexdigest(),'strict_allowed_physical_change_sha256':hashlib.sha256(normalized.encode()).hexdigest(),'raw_sha256':sha(P/(label+'.atm'))}
def baseline():
 run(P/'baseline-export.tcl','baseline-export-v3')
 seal_baseline()
def seal_baseline():
 assert 'was successful. 0 errors' in (P/'baseline-export-v3.log').read_text()
 assert len((P/'baseline-nodes.tsv').read_text().splitlines())==56374
 put(P/'baseline-signature.json',atm_signature('baseline'))
 put(P/'baseline-private-job-sha256.json',{str(f.relative_to(JOB)):sha(f) for f in sorted(JOB.rglob('*')) if f.is_file()})
 files=[pathlib.Path(__file__).resolve(),P/'prepared.json',P/'proposal.json',P/'apply-one-move.tcl',P/'baseline-export.tcl',P/'after-export.tcl',P/'baseline-signature.json',P/'baseline-private-job-sha256.json',P/'baseline.atm',P/'baseline-nodes.tsv',P/'baseline-routes.tsv']
 put(P/'execution-bindings.json',{str(f.relative_to(R)):sha(f) for f in files})
def execute():
 # The parent must review frozen proposal and choose this explicit action.
 assert (P/'baseline-signature.json').exists(),'baseline coverage required'
 binding=json.loads((P/'execution-bindings.json').read_text())
 for f,h in binding.items():assert sha(R/f)==h,('execution artifact changed',f)
 proposal=json.loads((P/'proposal.json').read_text());assert sha(P/'apply-one-move.tcl')==proposal['tcl_sha256']
 assert not (P/'eco-execute.log').exists(),'exactly one ECO attempt'
 for f,h in json.loads((P/'baseline-private-job-sha256.json').read_text()).items():assert sha(JOB/f)==h,('private baseline DB/input changed',f)
 d,_=origin_guard()
 for f,h in d['source_db_sha256'].items():
  if pathlib.Path(f).suffix.lower() in {'.v','.sv','.vhd','.qsf','.qpf','.sdc','.qip','.mif','.hex','.tcl','.bin'} and '/db/' not in f and '/incremental_db/' not in f:
   assert sha(JOB/f)==h,('input changed',f)
 run(P/'apply-one-move.tcl','eco-execute')
 run(P/'after-export.tcl','after-export')
 a=atm_signature('after');b=json.loads((P/'baseline-signature.json').read_text());put(P/'after-signature.json',a)
 def nodes(label):
  return {line.split('\t')[1]:(line.split('\t')[0],line.split('\t')[2]) for line in (P/(label+'-nodes.tsv')).read_text().splitlines()}
 before=nodes('baseline');after=nodes('after');assert before.keys()==after.keys()
 moved={n:[before[n],after[n]] for n in before if before[n]!=after[n]}
 def routes(label):return {tuple(line.split('\t',2)[:2]):line.split('\t',2)[2] for line in (P/(label+'-routes.tsv')).read_text().splitlines()}
 rb,ra=routes('baseline'),routes('after');changed=[k for k in rb.keys()|ra.keys() if rb.get(k)!=ra.get(k)]
 same=a['strict_allowed_physical_change_sha256']==b['strict_allowed_physical_change_sha256']
 result={'strict_logic_signature_equal':same,'node_count':len(before),'moved_nodes':moved,'route_ports_before':len(rb),'route_ports_after':len(ra),'changed_routes':changed,'route_export_available':False,'route_export_limitation':'Installed routing_path API crashes, including the critical input; compare critical routes through STA.','timing_checked':False,'fresh_assembly':False,'deployment_ready':False}
 put(P/'physical-result.json',result)
 assert same,'Unallowed netlist difference; review raw exports before proceeding'
 assert list(moved)==[TARGET] and moved[TARGET]==[before[TARGET],(before[TARGET][0],NEW)],'Unexpected physical edits'
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','baseline','execute']);args=a.parse_args();globals()[args.action]()
