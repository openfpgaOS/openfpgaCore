#!/usr/bin/env python3
"""Private scalar load-sign factoring over the exact selected CPU; no Quartus."""
from pathlib import Path
import difflib, hashlib, json, re, shutil, subprocess, sys

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
B = P / 'cpu-load-forward-v1/VexiiRiscv.v'
O = P / 'cpu-load-sign-v1'
D = O / 'literal'
PARENT = '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
OLD = "  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (execute_ctrl4_up_LANE_SEL_lane0 ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);\n"
NEW = '''  // Alignment and sign selection are linear in each data source. Precompute
  // only the scalar extension bits, then apply the late cache-way hit mask.
  // Existing raw data, SC low byte, FP loads and all write controls stay intact.
  function os30_load_byte_sign;
    input [31:0] data;
    input [1:0] address;
    begin
      case (address)
        2'b00: os30_load_byte_sign = data[7];
        2'b01: os30_load_byte_sign = data[15];
        2'b10: os30_load_byte_sign = data[23];
        default: os30_load_byte_sign = data[31];
      endcase
    end
  endfunction
  function os30_load_half_sign;
    input [31:0] data;
    input address;
    begin
      os30_load_half_sign = address ? data[31] : data[15];
    end
  endfunction
  wire os30_load_nonload_bit7 =
      (early0_MulPlugin_logic_formatBus_valid && early0_MulPlugin_logic_formatBus_payload[7]) ||
      (FpuF2iPlugin_logic_iwb_valid && FpuF2iPlugin_logic_iwb_payload[7]);
  wire os30_load_nonload_bit15 =
      (early0_MulPlugin_logic_formatBus_valid && early0_MulPlugin_logic_formatBus_payload[15]) ||
      (FpuF2iPlugin_logic_iwb_valid && FpuF2iPlugin_logic_iwb_payload[15]);
  (* keep *) wire os30_load_sign_byte_way0 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
      LsuPlugin_logic_iwb_valid && !when_LsuPlugin_l1025 && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0,
          execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]);
  (* keep *) wire os30_load_sign_byte_way1 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
      LsuPlugin_logic_iwb_valid && !when_LsuPlugin_l1025 && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1,
          execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]);
  (* keep *) wire os30_load_sign_upper_way0 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      LsuPlugin_logic_iwb_valid && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      (((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
        !when_LsuPlugin_l1025 && os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0])) ||
       ((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b01) &&
        os30_load_half_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1])));
  (* keep *) wire os30_load_sign_upper_way1 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      LsuPlugin_logic_iwb_valid && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      (((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
        !when_LsuPlugin_l1025 && os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0])) ||
       ((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b01) &&
        os30_load_half_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1])));
  (* keep *) wire os30_load_sign_byte_other =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
      (os30_load_nonload_bit7 || (LsuPlugin_logic_iwb_valid && !when_LsuPlugin_l1025 &&
       LsuPlugin_logic_onCtrl_io_cmdSent && os30_load_byte_sign(LsuPlugin_logic_onCtrl_io_rsp_payload_data,
           execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0])));
  (* keep *) wire os30_load_sign_upper_other = os30_load_sign_byte_other ||
      (execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
       (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b01) &&
       (os30_load_nonload_bit15 || (LsuPlugin_logic_iwb_valid && LsuPlugin_logic_onCtrl_io_cmdSent &&
        os30_load_half_sign(LsuPlugin_logic_onCtrl_io_rsp_payload_data,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1]))));
  (* keep *) wire os30_load_sign_byte =
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[0] && os30_load_sign_byte_way0) ||
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[1] && os30_load_sign_byte_way1) ||
      os30_load_sign_byte_other;
  (* keep *) wire os30_load_sign_upper =
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[0] && os30_load_sign_upper_way0) ||
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[1] && os30_load_sign_upper_way1) ||
      os30_load_sign_upper_other;
  (* keep *) wire os30_load_copy_byte = execute_ctrl4_up_LANE_SEL_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 != 2'b00);
  (* keep *) wire os30_load_copy_upper = execute_ctrl4_up_LANE_SEL_lane0 &&
      execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0[1];
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = {
      (({16{os30_load_copy_upper}} & lane0_IntFormatPlugin_logic_stages_2_raw[31:16]) |
       {16{os30_load_sign_upper}}),
      (({8{os30_load_copy_byte}} & lane0_IntFormatPlugin_logic_stages_2_raw[15:8]) |
       {8{os30_load_sign_byte}}),
      ({8{execute_ctrl4_up_LANE_SEL_lane0}} & lane0_IntFormatPlugin_logic_stages_2_raw[7:0])};
'''
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def put(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True); f.write('\n')

def between(s, first, last):
    assert s.count(first) == s.count(last) == 1
    return s[s.index(first):s.index(last)]

def prepare():
    assert sha(B) == PARENT
    s = B.read_text(); assert s.count(OLD) == 1 and 'os30_load_byte_sign' not in s
    O.mkdir(exist_ok=False)
    (O/'baseline.v').write_bytes(B.read_bytes())
    (O/'VexiiRiscv.v').write_text(s.replace(OLD, NEW, 1))
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True), (O/'VexiiRiscv.v').read_text().splitlines(True), fromfile='82b7/VexiiRiscv.v', tofile='load-sign/VexiiRiscv.v')))
    put(O/'hunk.json', dict(old=OLD, new=NEW))
    shutil.copy2(__file__, O/'recipe.py')
    put(O/'prepared.json', dict(parent_sha256=PARENT, candidate_sha256=sha(O/'VexiiRiscv.v'), recipe_sha256=sha(Path(__file__)), new_state=False, new_cycles=False, scope='Only final integer stage2 DATA expression: scalar per-way sign precompute followed by exact late hit OR. All original formatter, IO/SC, raw sources, controls and current forwarding remain unchanged.', production_modified=False))
    literal_prepare(s)
    print('SOURCE', sha(O/'VexiiRiscv.v'), flush=True)

def literal_prepare(s):
    D.mkdir(exist_ok=False)
    pieces = [between(s, '  always @(*) begin\n    case(_zz_LsuPlugin_logic_onCtrl_loadData_shifted_1)', '  always @(*) begin\n    case(_zz_LsuPlugin_logic_onCtrl_loadData_shifted_3)')]
    # The second block has only one combinational case; avoid depending on the
    # next generated temporary name.
    start = s.index('  always @(*) begin\n    case(_zz_LsuPlugin_logic_onCtrl_loadData_shifted_3)')
    pieces.append(s[start:s.index('\n  end\n', start)+len('\n  end\n')])
    pieces += [
        between(s, '  assign _zz_execute_ctrl4_down_LsuL1Plugin_logic_MUXED_DATA_lane0 =', '  always @(*) begin\n    execute_ctrl3_down_LsuL1Plugin_logic_WRITE_TO_READ_HAZARDS'),
        between(s, '  assign LsuPlugin_logic_onCtrl_loadData_input =', '  assign execute_ctrl4_down_LsuPlugin_logic_onCtrl_loadData_RESULT_lane0 =') +
          re.search(r'  assign execute_ctrl4_down_LsuPlugin_logic_onCtrl_loadData_RESULT_lane0 = [^;]+;\n', s).group(),
        between(s, '  assign LsuPlugin_logic_iwb_valid =', '  assign LsuPlugin_logic_fpwb_valid ='),
        between(s, '  assign lane0_IntFormatPlugin_logic_stages_2_hits =', '  assign FpuAddSharedPlugin_logic_inserter_portsRs1_0_mode ='),
    ]
    body = ''.join(pieces)
    # Exact original net declarations are selected by references, not retyped.
    needed = set(re.findall(r'\b[A-Za-z_]\w*\b', body+NEW))
    declarations = []
    bound = {
        'execute_ctrl4_up_LANE_SEL_lane0': 'controls[7]',
        'execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0': 'controls[3:2]',
        'execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0': 'controls[4]',
        'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0': 'controls[12:11]',
        'execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0': 'w0',
        'execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1': 'w1',
        'LsuPlugin_logic_onCtrl_io_rsp_payload_data': 'io_data',
        'LsuPlugin_logic_onCtrl_io_cmdSent': 'controls[6]',
        'execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0': "{30'd0,controls[1:0]}",
        'execute_ctrl4_down_LsuPlugin_logic_onCtrl_SC_MISS_lane0': 'sc_miss',
        'execute_ctrl4_down_AguPlugin_SEL_lane0': 'controls[8]',
        'execute_ctrl4_down_AguPlugin_FLOAT_lane0': "1'b0",
        'execute_ctrl4_down_LsuL1_ATOMIC_lane0': 'controls[5]',
        'execute_ctrl4_down_LsuL1_LOAD_lane0': "1'b0",
        'early0_MulPlugin_logic_formatBus_valid': 'controls[9]',
        'FpuF2iPlugin_logic_iwb_valid': 'controls[10]',
        'early0_MulPlugin_logic_formatBus_payload': 'mul_data',
        'FpuF2iPlugin_logic_iwb_payload': 'f2i_data',
        '_zz_LsuPlugin_logic_onCtrl_loadData_shifted_1': 'controls[1:0]',
        '_zz_LsuPlugin_logic_onCtrl_loadData_shifted_3': 'controls[1]',
        'execute_ctrl4_down_LsuL1_READ_DATA_lane0': 'execute_ctrl4_down_LsuL1Plugin_logic_MUXED_DATA_lane0',
    }
    for line in s.splitlines():
        m = re.fullmatch(r'  (wire|reg)(\s+(?:\[[^\]]+\]\s*)?)([A-Za-z_]\w*);', line)
        if m and m[3] in needed:
            if m[3] in bound:
                declarations.append('  wire'+m[2]+m[3]+' = '+bound[m[3]]+';')
            else: declarations.append(line)
    for k in bound: assert any(re.search(r'\b'+k+r'\b', x) for x in declarations), k
    assert len(declarations) == len(set(declarations))
    modules = []
    for kind in ['old','new','bad_sc','bad_priority']:
        change = OLD if kind=='old' else NEW
        if kind=='bad_sc':
            change = change.replace('!when_LsuPlugin_l1025', "1'b1")
        if kind=='bad_priority':
            change = change.replace('execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[1] && os30_load_sign_', '!execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[0] && execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[1] && os30_load_sign_')
        modules.append('module '+kind+'_dut(input [12:0] controls, input sc_miss, input [31:0] w0,w1,io_data,mul_data,f2i_data, output [31:0] result);\n'+'\n'.join(declarations)+'\n'+body+change+'\nassign result=lane0_integer_WriteBackPlugin_logic_stages_2_muxed;\nendmodule\n')
    top='module proof(input [12:0] controls,input sc_miss,input [31:0] w0,w1,io_data,mul_data,f2i_data,output [31:0] old_result,new_result,bad_sc_result,bad_priority_result);\n'
    for kind in ['old','new','bad_sc','bad_priority']:
        top+=f'{kind}_dut u_{kind}(controls,sc_miss,w0,w1,io_data,mul_data,f2i_data,{kind}_result);\n'
    (D/'proof.v').write_text(''.join(modules)+top+'endmodule\n')
    (D/'main.cpp').write_text(r'''#include "Vproof.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
int main(int argc,char **argv){
 Verilated::commandArgs(argc,argv);Vproof t;uint64_t n=0,sc=0,priority=0;uint32_t rng=0x673139ae;
 auto random=[&](){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;};
 auto check=[&](unsigned c,unsigned miss,const uint32_t *v){
  t.controls=c;t.sc_miss=miss;t.w0=v[0];t.w1=v[1];t.io_data=v[2];t.mul_data=v[3];t.f2i_data=v[4];t.eval();
  if(t.old_result!=t.new_result){fprintf(stderr,"FAIL c=%u sc=%u n=%llu old=%08x new=%08x\n",c,miss,(unsigned long long)n,t.old_result,t.new_result);exit(1);}
  sc+=t.old_result!=t.bad_sc_result;priority+=t.old_result!=t.bad_priority_result;n++;
 };
 uint32_t v[5];
 for(unsigned c=0;c<8192;c++)for(unsigned m=0;m<2;m++){
  for(auto &x:v)x=0;check(c,m,v);
  for(unsigned k=0;k<160;k++)for(unsigned inv=0;inv<2;inv++){
   for(auto &x:v)x=inv?UINT32_MAX:0;v[k/32]^=1u<<(k%32);check(c,m,v);
  }
 }
 uint64_t basis=n;
 for(unsigned k=0;k<262144;k++){for(auto &x:v)x=random();check(random()&8191,random()&1,v);}
 if(!sc||!priority)return 2;
 printf("{\"passed\":true,\"actual_extracted_RTL\":true,\"arbitrary_controls\":8192,\"sc_miss_values\":2,\"basis_complement_zero_checks\":%llu,\"random_checks\":262144,\"checks\":%llu,\"negative_missing_sc_checks\":%llu,\"negative_priority_checks\":%llu}\n",(unsigned long long)basis,(unsigned long long)n,(unsigned long long)sc,(unsigned long long)priority);
}
''')

def prove():
    assert sha(O/'VexiiRiscv.v') == json.loads((O/'prepared.json').read_text())['candidate_sha256']
    lint=['verilator','--lint-only','-Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')]
    if not (O/'lint-command.json').exists():
        put(O/'lint-command.json',lint)
        with (O/'lint.log').open('x') as f:subprocess.run(lint,stdout=f,stderr=subprocess.STDOUT,check=True)
    else:
        assert json.loads((O/'lint-command.json').read_text()) == lint
        assert '%Error' not in (O/'lint.log').read_text()
    cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','proof','--Mdir',str(D/'obj'),'-CFLAGS','-O2',str(D/'proof.v'),str(D/'main.cpp')]
    put(D/'command.json',cmd)
    with (D/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (D/'result.json').open('x') as f:subprocess.run([str(D/'obj/Vproof')],stdout=f,stderr=subprocess.STDOUT,check=True)
    print((D/'result.json').read_text(),flush=True)

def seal():
    p=json.loads((O/'prepared.json').read_text());s=B.read_text()
    assert sha(B)==PARENT and s.replace(OLD,NEW,1)==(O/'VexiiRiscv.v').read_text()
    assert sha(O/'VexiiRiscv.v')==p['candidate_sha256']
    result=json.loads((D/'result.json').read_text());assert result['passed'] and result['negative_missing_sc_checks'] and result['negative_priority_checks']
    put(O/'result.json',dict(passed=True,passed_for_native_screen=True,passed_for_map_or_fit=False,parent_sha256=PARENT,candidate_sha256=p['candidate_sha256'],new_registers=0,new_cycles=0,original_raw_formatter_and_all_controls_unchanged=True,arbitrary_non_onehot_hits_preserved=True,literal=result,production_modified=False,deployment_ready=False))
    (O/'REPORT.txt').write_text('PASS: scalar per-way load-sign precompute followed by late hit masking.\n\nThe sole final stage2 DATA assignment changes over selected CPU82b7. Byte sign selects bits7/15/23/31 by both address bits. Halfword sign selects15/31 by address bit1, preserving the original unusual non-full-shift behavior even for misaligned inputs. SC zeros only the low byte extension source; its result bit0 and all raw copy bits remain on the original path. IO overrides both cache ways. MUL/F2I/LSU hits and cache hits retain bitwise-OR behavior for arbitrary overlapping selectors. Width3 copies raw data exactly; zero/unsigned and inactive-lane cases remain exact.\n\nExisting full32-bit raw source/alignment/formatter, FP loads, atomic payload, all writeback valid/commit/cancel/control/registers and current RS1/RS2 forwarding are byte-identical. Ten kept scalar combinational nets and two small combinational selection functions add no state, RAM or cycle. This differs from prior writeback-plane-v10: that moved lane/width decode only, while this moves per-way scalar sign selection before the late WAYS_HITS merge.\n\nLiteral actual extracted old/new RTL is checked across all8192 control values, both SC result bits, zero and all160 input-bit bases/complements, plus262144 random full-width values. Negative controls remove SC exclusion or impose false priority on the two hit bits. Full CPU lint passes.\n\nThe measured parent hit-to-SRC1 path has7 LUTs and11.214ns hot/11.237ns cold. This factor targets its two byte-selection LUTs and later sign/lane qualification. The intended late-hit cone is scalar masked-OR, DATA copy/extension OR, then existing forwarding; actual mapped levels/area and all native results remain unproven. No CAD, new CPU generation or physical fit was performed.\n')
    files=[p for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts]+[D/'obj/Vproof',B,Path(__file__)]
    put(O/'manifest.json',dict(passed=True,passed_for_native_screen=True,passed_for_map_or_fit=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}))
    print('MANIFEST',sha(O/'manifest.json'),flush=True)

if __name__=='__main__':
    assert len(sys.argv)==2 and sys.argv[1] in ['prepare','prove','seal']
    globals()[sys.argv[1]]()
