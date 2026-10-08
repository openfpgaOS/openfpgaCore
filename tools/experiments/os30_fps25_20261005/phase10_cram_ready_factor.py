#!/usr/bin/env python3
"""Private cycle-exact texture ARREADY reset absorption; no map or fitter."""
import argparse,difflib,json,re,shutil,subprocess
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';OUT=P/'cram-ready-factor-v1';CORE=P/'compact-lod5-core-v7';FULL=P/'cram1-share-full-v1'
REL='src/fpga/common/phase10_cram1_read_arbiter.v'
OLD=' assign s0_arready=state!=RESPONSE && !selected && m_arready && !s0_reset;'
NEW=''' // Under !s0_reset the IDLE selection can use raw s0_arvalid directly.
 // Preserve delayed acknowledgement/owner locking; only absorb reset here.
 assign s0_arready=state!=RESPONSE && m_arready && !s0_reset &&
                  (state==IDLE ? (!s1_arvalid || (s0_arvalid && !prefer_one)) : !owner);'''

def prepare():
 OUT.mkdir(exist_ok=False);old=(CORE/'frozen'/REL).read_text()
 assert sha(CORE/'frozen'/REL)=='b82f0725555682a14006e90a6a8546d320e822c71f34c25d057942aaf653c303'
 assert sha(CORE/'frozen'/REL)==sha(FULL/'frozen/phase10_cram1_read_arbiter.v')
 assert old.count(OLD)==1;new=old.replace(OLD,NEW);assert new.replace(NEW,OLD)==old
 (OUT/'geometry_cram1_read_arbiter.v').write_text(new);(OUT/'baseline.v').write_text(old)
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='compact-v7/arbiter',tofile='ready-factor/arbiter')))
 shutil.copy2(Path(__file__),OUT/'recipe.py')
 write(OUT/'prepared.json',dict(parent_prepared_sha256=sha(CORE/'prepared.json'),parent_sha256=sha(CORE/'frozen'/REL),source_sha256=sha(OUT/'geometry_cram1_read_arbiter.v'),only_changed_assignment='s0_arready',no_new_registers_states_or_cycles=True,full_protocol_parent_manifest_sha256=sha(FULL/'manifest.json'),helper_sha256=sha(Path(__file__)),deployment_ready=False))
 # Extract the actual old/new combinational definitions, rather than testing
 # independently retyped Boolean expressions.
 unit=OUT/'truth';unit.mkdir()
 oldexpr=re.search(r'assign s0_arready=(.*?);',old,re.S)[1]
 newexpr=re.search(r'assign s0_arready=(.*?);',new,re.S)[1]
 shared='\n'.join(re.findall(r' wire (?:s0_valid|selected)=[^;]+;',old))
 probe='''module ready_probe(input wire [7:0] sample,output wire old_ready,new_ready);
localparam IDLE=2'd0,ADDRESS=2'd1,RESPONSE=2'd2;
wire [1:0] state=sample[1:0];
wire owner=sample[2],s0_reset=sample[3],s0_arvalid=sample[4],s1_arvalid=sample[5],prefer_one=sample[6],m_arready=sample[7];
'''+shared+'\nassign old_ready='+oldexpr+';\nassign new_ready='+newexpr+';\nendmodule\n'
 (unit/'probe.v').write_text(probe)
 (unit/'main.cpp').write_text('''#include "Vready_probe.h"
#include "verilated.h"
#include <cassert>
#include <cstdio>
int main(){Vready_probe t;unsigned ones=0;for(unsigned i=0;i<256;i++){t.sample=i;t.eval();assert(t.old_ready==t.new_ready);ones+=t.new_ready;}printf("PASS combinations=256 ready_true=%u\\n",ones);}
''')
 # Preserve physical controller/PHY/chip and full-capacity hostile fixture.
 f=OUT/'protocol/frozen';f.mkdir(parents=True)
 for p in(FULL/'frozen').iterdir():
  if p.is_file():shutil.copy2(p,f/p.name)
 (f/'phase10_cram1_read_arbiter.v').write_text(new)
 (f/'reference_arbiter.v').write_text(old.replace('module geometry_cram1_read_arbiter (','module geometry_cram1_read_arbiter_reference ('))
 tb=f/'phase10_cram1_share_tb.v';s=tb.read_text()
 instance=re.search(r'    geometry_cram1_read_arbiter arbiter\(.*?\);',s,re.S)[0]
 reference=instance.replace('geometry_cram1_read_arbiter arbiter','geometry_cram1_read_arbiter_reference reference')
 for port,signal in [('s0_arready','ref_arready[0]'),('s1_arready','ref_arready[1]'),('s0_rvalid','ref_rvalid[0]'),('s1_rvalid','ref_rvalid[1]'),('s0_rdata','ref_rdata[0]'),('s1_rdata','ref_rdata[1]'),('s0_rlast','ref_rlast[0]'),('s1_rlast','ref_rlast[1]'),('m_arvalid','ref_arvalid_m'),('m_araddr','ref_araddr_m'),('m_arlen','ref_arlen_m')]:
  reference,n=re.subn(r'\.'+port+r'\([^)]*\)',f'.{port}({signal})',reference);assert n==1
 declarations='''
    wire [1:0] ref_arready,ref_rvalid,ref_rlast;
    wire [31:0] ref_rdata[0:1];
    wire ref_arvalid_m;wire [31:0] ref_araddr_m;wire [7:0] ref_arlen_m;
'''
 monitor='''
    integer equivalence_checks=0,sticky_checks=0,drain_checks=0;
    reg prev_held=0,prev_owner=0,prev_drain=0;
    reg [31:0] prev_addr;reg [7:0] prev_len;
    always @(posedge clk) begin
      if(!reset_n)begin prev_held<=0;prev_drain<=0;end
      else begin
        if({arready,rvalid,rlast,rdata[0],rdata[1],arvalid_m,araddr_m,arlen_m} !==
           {ref_arready,ref_rvalid,ref_rlast,ref_rdata[0],ref_rdata[1],ref_arvalid_m,ref_araddr_m,ref_arlen_m})
          $fatal(1,"old/new arbiter output mismatch");
        if({arbiter.state,arbiter.owner,arbiter.prefer_one,arbiter.discard_s0} !==
           {reference.state,reference.owner,reference.prefer_one,reference.discard_s0})
          $fatal(1,"old/new arbiter state mismatch");
        equivalence_checks<=equivalence_checks+1;
        if(prev_held && (prev_owner || !s0_reset))begin
          if(!arvalid_m || araddr_m!==prev_addr || arlen_m!==prev_len)
            $fatal(1,"request changed while stalled");
          sticky_checks<=sticky_checks+1;
        end
        if(prev_drain)begin
          if(arbiter.state!=2 || arbiter.owner!=prev_owner)
            $fatal(1,"response ownership released before last");
          drain_checks<=drain_checks+1;
        end
        prev_held<=arvalid_m&&!arready_m;
        prev_owner<=arbiter.state==0 ? arbiter.selected : arbiter.owner;
        prev_addr<=araddr_m;prev_len<=arlen_m;
        prev_drain<=arbiter.state==2 && !(rvalid_m&&rlast_m);
      end
    end
    final $display("MITER equivalence_checks=%0d sticky_checks=%0d drain_checks=%0d",equivalence_checks,sticky_checks,drain_checks);
'''
 s=s.replace(instance,declarations+instance+'\n'+reference+monitor)
 tb.write_text(s)
 write(OUT/'protocol/sources.json',{p.name:sha(p)for p in f.iterdir()if p.is_file()})

def truth():
 d=OUT/'truth';cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','ready_probe','--Mdir',str(d/'obj'),str(d/'probe.v'),str(d/'main.cpp')]
 with(d/'build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 output=subprocess.check_output([str(d/'obj/Vready_probe')],text=True);(d/'run.log').write_text(output)
 assert'PASS combinations=256'in output
 write(d/'result.json',dict(passed=True,combinations=256,all_state_owner_reset_valid_preference_ready_patterns=True,probe_sha256=sha(d/'probe.v'),simulator_sha256=sha(d/'obj/Vready_probe')))
 print(output)

def protocol(refresh):
 f=OUT/'protocol/frozen';d=OUT/f'protocol/refresh{refresh}';d.mkdir(exist_ok=False)
 cmd=['verilator','--binary','--timing','--assert','-j','3','-Wno-fatal','-Wno-WIDTH','-Wno-UNOPTFLAT','-Wno-CASEINCOMPLETE','--top-module','tb_geometry_cram1_share','--Mdir',str(d/'obj'),f'-GEXTRA_REFRESH_CYCLES={refresh}']+[str(p)for p in f.iterdir()if p.suffix in('.v','.sv')]
 write(d/'build-command.json',cmd)
 with(d/'build.log').open('x')as log:subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
 with(d/'run.log').open('x')as log:r=subprocess.run([str(d/'obj/Vtb_geometry_cram1_share')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
 output=(d/'run.log').read_text();stats=dict(re.findall(r'(\w+)=(\d+)',output))
 assert r.returncode==0 and'RESULT PASS'in output,output[-2000:]
 old=json.loads((FULL/f'refresh{refresh}/result.json').read_text())['stats']
 assert{k:stats[k]for k in old}==old
 for k in ('equivalence_checks','sticky_checks','drain_checks'):assert int(stats[k])>0
 write(d/'result.json',dict(passed=True,stats=stats,all_parent_protocol_counts_and_cycles_exact=True,simulator_sha256=sha(d/'obj/Vtb_geometry_cram1_share')))
 print(output[-1200:])

def seal():
 prep=json.loads((OUT/'prepared.json').read_text());assert prep['source_sha256']==sha(OUT/'geometry_cram1_read_arbiter.v')
 assert prep['helper_sha256']==sha(OUT/'recipe.py')
 shutil.copy2(Path(__file__),OUT/'seal-recipe.py')
 assert json.loads((OUT/'truth/result.json').read_text())['passed']
 results={r:json.loads((OUT/f'protocol/refresh{r}/result.json').read_text())for r in (0,3)}
 assert all(x['passed']for x in results.values())
 report='Private CRAM texture ARREADY factor\n\nPASS. Only s0_arready changes from the exact compact-v7 arbiter. Under the existing outer !s0_reset, s0_valid equals s0_arvalid; De Morgan expansion removes reset from the IDLE selected cone. Outside IDLE, selected is owner. RESPONSE still suppresses READY. Invalid state3 retains the original owner-based readiness; it is included in the proof. Actual old/new extracted expressions match all 256 combinations of the eight input bits (two state bits plus owner/reset/two valids/preference/master ready). This is a two-state Boolean proof.\n\nNo selected, master-address/valid, geometry-ready, response, reset or sequential assignment changes. No registers, states, RAM, or cycles added. The retained ADDRESS delayed-ack priority and response discard/drain semantics remain literal-identical. This targets a measured cross-module reset/grant path; synthesis may reconstruct sharing, so no area/Fmax improvement is claimed.\n\nActual adapter/controller/PHY/full-16MiB chip protocol tests pass at refresh-stretch 0 and3. Each preserves parent totals:113 texture bursts/446 words,154 geometry bursts/1678 words,10 media resets,301 discarded response beats,6 physical uploads; total cycles12550/13372 exactly. A parallel original arbiter compares all output fields and state every active cycle, with separate stalled-request stability and response-owner drain assertions. High-pool/die/end boundaries, concurrent clients/upload, delayed acknowledgement, texture resets at capture/acceptance/mid-response and shared fabric reset are retained. No CPU instruction or whole-frame performance measurement is claimed.\n\nPrivate fallback candidate only: no map, composition, fitter, production, sibling or SD changes. The running v7 routed result determines whether this candidate is needed.\n'
 (OUT/'REPORT.txt').write_text(report)
 write(OUT/'result.json',dict(passed=True,source_sha256=prep['source_sha256'],truth_combinations=256,physical_protocol=results,no_new_state_or_cycles=True,area_timing_unmeasured=True,selected=False))
 paths=[p for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts and p.name!='manifest.json']+[OUT/'truth/obj/Vready_probe']+[OUT/f'protocol/refresh{r}/obj/Vtb_geometry_cram1_share'for r in (0,3)]
 paths += [FULL/'manifest.json',CORE/'prepared.json',Path(__file__)]
 write(OUT/'manifest.json',dict(passed=True,selected=False,source_sha256=prep['source_sha256'],evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(paths))}))
 print(sha(OUT/'manifest.json'))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','truth','protocol','seal']);p.add_argument('--refresh',type=int,choices=[0,3],default=0);a=p.parse_args();protocol(a.refresh)if a.action=='protocol'else globals()[a.action]()
