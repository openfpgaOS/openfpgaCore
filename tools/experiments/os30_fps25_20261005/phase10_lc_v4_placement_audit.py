#!/usr/bin/env python3
"""Bounded read-only post-fit placement and original critical-cone audit."""
import hashlib,json,pathlib,shlex,shutil,subprocess,time,collections,re
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
S=P/'lc-v7-timing-placement-v4';F=S/'fit-seed2';O=P/'lc-v4-placement-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def inv(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def put(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
assert json.loads((F/'complete.json').read_text())['state']=='complete'
O.mkdir(exist_ok=False)
origin=P/'compact-lod5-core-v7/fit-seed2/jobs/pocket-os30-s2'
original_expected=json.loads((P/'eco-v7-local-placement-v1/prepared.json').read_text())['source_db_sha256']
assert inv(origin)==original_expected
before=inv(F/'job');put(O/'source-inventory.json',before)
shutil.copytree(F/'job',O/'job');assert inv(O/'job')==before
shutil.copy2(__file__,O/'recipe.py')
wd=O/'job/src/fpga/targets/pocket'
(O/'locations.tcl').write_text("""package require ::quartus::chip_planner
project_open ap_core
read_netlist
set output [open [lindex $quartus(args) 0] w]
set count 0
foreach_in_collection node [get_nodes -type all] {
 puts $output "$node\t[get_node_info -node $node -info name]\t[get_node_info -node $node -info {Location String}]"
 incr count
}
close $output
puts [list EXPORTED_NODES $count]
project_close
""")
(O/'cone.tcl').write_text("""package require ::quartus::project
package require ::quartus::sta
project_open ap_core
create_timing_netlist -model slow
read_sdc
foreach_in_collection corner [get_available_operating_conditions] {
 set model [get_operating_conditions_info $corner -model]
 set temperature [get_operating_conditions_info $corner -temperature]
 if {$model ne "slow"} {continue}
 set_operating_conditions $corner
 update_timing_netlist
 report_timing -setup -from [get_registers {*|execute_ctrl3_up_LANE_SEL_lane0}] -to [get_registers {*|AlignerPlugin_logic_buffer*}] -npaths 20 -detail full_path -file [file join [lindex $quartus(args) 0] "${model}_${temperature}_cone.rpt"]
}
delete_timing_netlist
project_close
""")
base=pathlib.Path('/home/alberto/altera_lite/25.1std/quartus/bin')
commands=[('locations',[str(base/'quartus_cdb'),'-t',str(O/'locations.tcl'),str(O/'nodes.tsv')]),('cone',[str(base/'quartus_sta'),'-t',str(O/'cone.tcl'),str(O)])]
put(O/'invocations.json',commands)
for n,cmd in commands:
 start=time.monotonic()
 with (O/(n+'.log')).open('x') as f:r=subprocess.run(cmd,cwd=wd,stdout=f,stderr=subprocess.STDOUT)
 print(n,r.returncode,round(time.monotonic()-start,3),flush=True)
 assert r.returncode==0,(n,r.returncode)
assert inv(F/'job')==before,'Completed fitted job changed'
assert inv(origin)==original_expected,'Original v7 changed'
put(O/'read-only-result.json',dict(original_v7_files_unchanged=len(original_expected),completed_fit_files_unchanged=len(before),private_copy_only=True,no_map_or_fit=True))
