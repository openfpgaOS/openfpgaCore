#!/usr/bin/env python3
"""Private os30-only I-fetch target simplification with PMA/slot proof.

This is intentionally tied to one generated CPU's fetch PMA. It is not a
generic replacement for cpu_system's decoder with arbitrary CPU netlists.
"""
from pathlib import Path
import argparse,difflib,hashlib,itertools,json,re,shutil,subprocess
ROOT=Path(__file__).resolve().parents[3];ART=ROOT/'docs/measurements/os30-dual-20261004'
CPU=ART/'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
BASE=ART/'posted-write-factor-v1/cpu_system.v'
SLICE=ROOT/'src/fpga/common/axi_register_slice.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=ART/'iar-target-pma-v1');out=ap.parse_args().output
    assert sha(CPU)=='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
    assert sha(BASE)=='2eb4017111babe0b40651535025847092444930adbfa98e007eea97b2e35c214'
    c=CPU.read_text();old=BASE.read_text();sl=SLICE.read_text();contracts={}
    def guard(name,s):
        assert s in c,name
        contracts[name]=s
        return s
    def assign(name):
        s=re.search(r'^  assign '+re.escape(name)+r' = [^;]+;',c,re.M)[0]
        contracts[name]=s;return s
    def block(name):
        a=[s for s in re.findall(r'^  always @\(\*\) begin\n.*?^  end\n',c,re.M|re.S) if re.search(r'\b'+name+r'\s*=',s)]
        assert len(a)==1;contracts[name]=a[0];return a[0]
    before='assign i_ar_target_cpu = decode_target(i_araddr_cpu);'
    after="assign i_ar_target_cpu = {~i_araddr_cpu[28], 1'b0};"
    assert old.count(before)==1
    new=old.replace(before,after,1);assert new.replace(after,before,1)==old
    # Exact physical address identity through the only fetch-refill producer.
    aliases={
      'FetchL1Plugin_logic_ctrl_pmaPort_cmd_address':'fetch_logic_ctrls_3_down_MMU_TRANSLATED',
      'FetchL1Plugin_pmaBuilder_addressBits':'FetchL1Plugin_logic_ctrl_pmaPort_cmd_address',
      'FetchL1Plugin_logic_refill_start_address':'fetch_logic_ctrls_3_down_MMU_TRANSLATED',
      'FetchL1Plugin_logic_bus_resizer_ret_cmd_valid':'FetchL1Plugin_logic_bus_cmd_valid',
      'FetchL1Plugin_logic_bus_resizer_ret_cmd_payload_address':'FetchL1Plugin_logic_bus_cmd_payload_address',
      'FetchL1Axi4Plugin_logic_axi_ar_valid':'FetchL1Plugin_logic_bus_resizer_ret_cmd_valid',
      'FetchL1Axi4Plugin_logic_axi_ar_payload_addr':'FetchL1Plugin_logic_bus_resizer_ret_cmd_payload_address'}
    for lhs,rhs in aliases.items():assert assign(lhs)==f'  assign {lhs} = {rhs};'
    for signal,port in [('i_arvalid_cpu','FetchL1Axi4Plugin_logic_axi_ar_valid'),('i_araddr_cpu','FetchL1Axi4Plugin_logic_axi_ar_payload_addr')]:
        assert re.search(r'\.'+port+r'\s*\('+signal+r'\)',old)
    assert len(re.findall(r'\bFetchL1Plugin_logic_refill_start_valid\s*=',c))==2
    # Actual allocation blocks share precisely the same per-slot enable for
    # address capture and setting valid. Auxiliary counters/priority are
    # retained in source contracts but irrelevant to the safety abstraction.
    p='FetchL1Plugin_logic_refill_'
    reset='\n'.join(f'      {p}slots_{i}_{field} <= 1\'b{v};' for i in range(2) for field,v in [('valid',0),('cmdSent',1)])
    guard('slot_reset',reset);guard('lock_reset',f"      {p}onCmd_locked <= 1'b0;")
    alloc=c[c.index('      if(when_FetchL1Plugin_l256) begin'):c.index('      if(FetchL1Plugin_logic_bus_cmd_valid) begin')]
    contracts['allocation_valid']=alloc
    assert re.findall(r'if\(([^)]+)\)',alloc)==['when_FetchL1Plugin_l256','_zz_33','_zz_when_1']
    for i,g in enumerate(['_zz_33','_zz_when_1']):
        assert f"if({g}) begin\n          {p}slots_{i}_valid <= 1'b1;\n          {p}slots_{i}_cmdSent <= 1'b0;" in alloc
        cap=re.search(r'    if\(when_FetchL1Plugin_l256\) begin\n      if\(_zz_33\) begin\n.*?^    end\n',c,re.M|re.S)[0]
        contracts['allocation_address']=cap
        assert f'if({g}) begin\n        {p}slots_{i}_address <= {p}start_address;' in cap
        assert re.findall(r'\b'+p+f'slots_{i}_address\s*<=\s*([^;]+);',c)==[p+'start_address']
        assert re.findall(r'\b'+p+f'slots_{i}_valid\s*<=\s*([^;]+);',c)==["1'b0","1'b1","1'b0"]
    lockblock=c[c.index('      if(FetchL1Plugin_logic_bus_cmd_valid) begin'):c.index('      if(FetchL1Plugin_logic_bus_rsp_fire) begin')]
    contracts['lock_and_command_sent']=lockblock
    assert lockblock.startswith(f"      if(FetchL1Plugin_logic_bus_cmd_valid) begin\n        {p}onCmd_locked <= 1'b1;\n      end\n      if(FetchL1Plugin_logic_bus_cmd_ready) begin\n        {p}onCmd_locked <= 1'b0;\n      end\n")
    guard('locked_selector_capture',f'    if(when_FetchL1Plugin_l277) begin\n      {p}onCmd_lockedOh <= {p}onCmd_propoedOh;\n    end')
    assert assign('when_FetchL1Plugin_l277')==f'  assign when_FetchL1Plugin_l277 = (! {p}onCmd_locked);'
    for i in range(2):
        assert assign(p+f'slots_{i}_askCmd')==f'  assign {p}slots_{i}_askCmd = ({p}slots_{i}_valid && (! {p}slots_{i}_cmdSent));'
    proposal=assign(p+'onCmd_propoedOh')
    assert proposal==f'  assign {p}onCmd_propoedOh = {{({p}slots_1_askCmd && (&((! {p}slots_0_askCmd) || (! {p}slots_0_priority[1])))),({p}slots_0_askCmd && (&((! {p}slots_1_askCmd) || (! {p}slots_1_priority[0]))))}};'
    assert assign(p+'onCmd_oh')==f'  assign {p}onCmd_oh = ({p}onCmd_locked ? {p}onCmd_lockedOh : {p}onCmd_propoedOh);'
    assert assign('FetchL1Plugin_logic_bus_cmd_valid')=='  assign FetchL1Plugin_logic_bus_cmd_valid = (|FetchL1Plugin_logic_refill_onCmd_oh);'
    # A safe slot address means membership in PMA's union, irrespective of
    # whether its valid bit was since cleared by a response. This invariant
    # therefore covers held/locked commands even under arbitrary clear timing.
    states=transitions=0
    for bits in itertools.product(range(2),repeat=11):
        v0,v1,sent0,sent1,locked,oh0,oh1,prio0,prio1,safe0,safe1=bits
        if v0 and not safe0 or v1 and not safe1 or locked and ((oh0 and not safe0) or (oh1 and not safe1)):continue
        a0=v0 and not sent0;a1=v1 and not sent1
        prop0=a0 and (not a1 or not prio1);prop1=a1 and (not a0 or not prio0)
        sel0,sel1=(oh0,oh1) if locked else (prop0,prop1)
        assert not sel0 or safe0;assert not sel1 or safe1
        states+=1
        # Both allocation gates and both response clears are unrestricted:
        # stronger than the actual priority allocator/one-ID response bus.
        for alloc0,alloc1,clear0,clear1,ready,reset in itertools.product(range(2),repeat=6):
            nsafe0=safe0 or alloc0;nsafe1=safe1 or alloc1
            nv0=0 if reset else (v0 or alloc0) and not clear0
            nv1=0 if reset else (v1 or alloc1) and not clear1
            nl=0 if reset or ready else locked or sel0 or sel1
            no0,no1=(oh0,oh1) if locked else (prop0,prop1)
            assert not nv0 or nsafe0;assert not nv1 or nsafe1
            assert not nl or ((not no0 or nsafe0) and (not no1 or nsafe1))
            transitions+=1
    # Queue capture is the only use of the possibly changed upstream target.
    assert len(re.findall(r'\bi_ar_target_cpu\b',old))==3
    instance=old[old.index('axi_register_slice #(.W(43), .FIFO_OUT(1)) i_ar_slice'):old.index('//   R payload =')]
    assert '.s_valid (i_arvalid_cpu)' in instance and '.s_payload({i_ar_target_cpu, i_araddr_cpu, i_arid_cpu, i_arlen_cpu})' in instance
    fifo=sl[sl.index('generate if (FIFO_OUT)'):sl.index('end else begin : g_skid')]
    assert 'wire upstream_xfer   = s_valid && (count != 2\'d2);' in fifo
    assert 'if (upstream_xfer) begin\n                if (wr_sel) slot1 <= s_payload;\n                else        slot0 <= s_payload;' in fifo
    assert "slot0  <= {W{1'b0}};\n            slot1  <= {W{1'b0}};" in fifo
    contracts['i_ar_slice_instance']=instance;contracts['i_ar_fifo']=fifo
    # Extract actual PMA/allowRefill/start/command address cones, without a
    # handwritten surrogate for their Verilog expressions.
    cone='\n'.join([assign('FetchL1Plugin_logic_ctrl_pmaPort_rsp_fault'),assign('when_FetchL1Plugin_l488'),block('FetchL1Plugin_logic_ctrl_allowRefill'),block('FetchL1Plugin_logic_refill_start_valid'),assign('when_FetchL1Plugin_l545'),assign('when_FetchL1Plugin_l256'),assign('FetchL1Plugin_logic_bus_cmd_payload_address')])
    names={'FetchL1Plugin_pmaBuilder_addressBits':'addr','FetchL1Plugin_pmaBuilder_onTransfers_0_hit':'flags[0]',
      'fetch_logic_ctrls_3_down_FetchL1Plugin_logic_WAYS_HIT':'flags[1]','fetch_logic_ctrls_3_down_FetchL1Plugin_logic_HAZARD':'flags[2]',
      'FetchL1Plugin_logic_ctrl_dataAccessFault':'flags[3]','fetch_logic_ctrls_3_down_FetchL1Plugin_logic_pmpPort_ACCESS_FAULT':'flags[4]',
      'fetch_logic_ctrls_3_down_MMU_PAGE_FAULT':'flags[5]','fetch_logic_ctrls_3_down_MMU_ACCESS_FAULT':'flags[6]',
      'fetch_logic_ctrls_3_down_MMU_REFILL':'flags[7]','fetch_logic_ctrls_3_down_MMU_HAZARD':'flags[8]',
      'fetch_logic_ctrls_3_down_Fetch_PC_FAULT':'flags[9]','FetchL1Plugin_logic_ctrl_trapSent':'flags[10]',
      'fetch_logic_ctrls_3_up_isValid':'flags[11]','FetchL1Plugin_logic_invalidate_done':'flags[12]','FetchL1Plugin_logic_refill_hazard':'flags[13]',
      'FetchL1Plugin_logic_ctrl_pmaPort_rsp_fault':'fault','when_FetchL1Plugin_l488':'accessFault',
      'FetchL1Plugin_logic_ctrl_allowRefill':'allowRefill','FetchL1Plugin_logic_refill_start_valid':'startValid',
      'when_FetchL1Plugin_l545':'fetchInvalid','when_FetchL1Plugin_l256':'allocate',
      '_zz_FetchL1Plugin_logic_bus_cmd_payload_address':'sel[0]','_zz_FetchL1Plugin_logic_bus_cmd_payload_id':'sel[1]',
      'FetchL1Plugin_logic_refill_slots_0_address':'addr0','FetchL1Plugin_logic_refill_slots_1_address':'addr1','FetchL1Plugin_logic_bus_cmd_payload_address':'busaddr'}
    mapped=re.sub(r'\b[A-Za-z_]\w*\b',lambda m:names.get(m[0],m[0]),cone)
    assert not re.search(r'FetchL1|fetch_logic',mapped)
    func=re.search(r'function \[2:0\] decode_target;.*?endfunction',old,re.S)[0]
    proof='''module tb(input [31:0] addr,addr0,addr1,input [1:0] sel,input[13:0]flags,
output wire fault,allocate,output reg allowRefill,startValid,output wire[31:0]busaddr,
output wire[1:0]targetref,targetnew);
wire accessFault,fetchInvalid;
'''+func+'\n'+mapped+'\n'+before.replace('i_ar_target_cpu','targetref').replace('i_araddr_cpu','busaddr')+'\n'+after.replace('i_ar_target_cpu','targetnew').replace('i_araddr_cpu','busaddr')+'\nendmodule\n'
    out.mkdir(parents=True,exist_ok=False)
    (out/'cpu_system.v').write_text(new);(out/'reference.v').write_text(old)
    (out/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/cpu_system.v',tofile='b/cpu_system.v')))
    (out/'source-contracts.json').write_text(json.dumps(contracts,indent=2)+'\n')
    (out/'actual-cones.v').write_text(cone);(out/'proof.v').write_text(proof)
    (out/'proof.cpp').write_text(r'''#include "Vtb.h"
#include <verilated.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>
bool safe(uint32_t a){return (a&0xfc000000)==0x10000000 || (a&0xffff8000)==0;}
int main(){Vtb t;uint64_t addrCases=0,controlCases=0,orCases=0;std::vector<uint32_t>allowed;
for(uint32_t upper=0;upper<(1<<17);upper++){
 for(uint32_t low:{0u,0x7fffu}){t.addr=(upper<<15)|low;t.addr0=t.addr;t.addr1=0;t.sel=1;t.flags=1|(1<<11)|(1<<12);t.eval();
 assert(bool(t.fault)==!safe(t.addr));assert(bool(t.allocate)==safe(t.addr));
 assert(t.busaddr==(t.addr&~63u));if(safe(t.addr)){assert(safe(t.busaddr));assert(t.targetref==t.targetnew);}addrCases++;}
 if(safe(upper<<15))allowed.push_back(upper<<15);
}
for(uint32_t a:{0x7fffu,0x13ffffffu,0x30000000u})for(unsigned f=0;f<(1<<14);f++){
 t.addr=a;t.flags=f;t.eval();if(t.allocate)assert(safe(a)&&!t.fault);controlCases++;
}
for(uint32_t a:allowed)for(uint32_t b:allowed){t.addr0=a|0x7fff;t.addr1=b|0x7fff;t.sel=3;t.eval();assert(safe(t.busaddr));assert(t.targetref==t.targetnew);orCases++;}
// Out-of-profile invalid addresses intentionally differ. They cannot be
// queued from this CPU after reset; no universal cpu_system claim is made.
t.addr0=0x30000000;t.sel=1;t.eval();assert(t.targetref!=t.targetnew);
printf("PASS actual-address-prefix cases=%llu actual-control cases=%llu safe-OR pairs=%llu\n",(unsigned long long)addrCases,(unsigned long long)controlCases,(unsigned long long)orCases);
}
''')
    with (out/'build.log').open('w') as log:
        subprocess.run(['verilator','--cc','--exe','--build','-j','2','--top-module','tb','-Wno-fatal','--Mdir',str(out/'obj'),str(out/'proof.v'),str(out/'proof.cpp')],stdout=log,stderr=subprocess.STDOUT,check=True)
    with (out/'proof.log').open('w') as log:subprocess.run([str(out/'obj/Vtb')],stdout=log,stderr=subprocess.STDOUT,check=True)
    shutil.copy2(__file__,out/Path(__file__).name)
    manifest=dict(passed=True,candidate_sha256=sha(out/'cpu_system.v'),reference_sha256=sha(BASE),bound_cpu_sha256=sha(CPU),queue_sha256=sha(SLICE),
      exact_one_assignment_only=True,exact_reverse=True,new_registers=0,new_cycles=0,
      pma_and_32bit_address_abstraction='Both PMA membership and target depend only on address[31:15]; all 131072 prefixes and low-boundaries checked. Low6 alignment cannot change membership or routing.',
      actual_address_prefix_cases=262144,actual_refill_control_cases=49152,safe_address_or_pairs=4198401,
      slot_lock_inductive_states=states,slot_lock_inductive_transitions=transitions,
      control_abstraction='Arbitrary current priorities, both allocation gates and both response clears; slot address is safe once written, valid/locked selects imply safe. Allocation validity and address use the same enable. Reset clears valid and locked; uninitialized addresses may remain only unselected.',
      instruction_prefetch_scope='Every emitted FetchL1 refill uses the one guarded refill_start_valid producer; speculative fetches use the same PMA gate. No separate instruction prefetch allocation writer exists.',
      queue_noninterference='Upstream target has only one consumer: valid-transfer-only FIFO payload capture. All other inputs, payload resets, queue controls and outputs remain identical.',
      scope='Private specialization for the bound generated os30 fetch PMA, from reset with binary protocol inputs. Not equivalent for arbitrary CPU/invalid standalone i_ar addresses; such payload differences are never captured by this bound CPU.',
      cpu_system_per_and_mem_routing_unchanged=True,physical_fit='Not run; unselected proposal.',whole_system_runtime='Not run; source-contract/extracted-cone/inductive proof only.',independent_review='Pending',
      evidence_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
