#!/usr/bin/env python3
"""Isolated 21/22-bit hard-DSP modular-adder feasibility; never a full core fit."""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase10/dsp-modadd-component-v3'
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as sweep

MODULE = '''module phase10_dsp_modadd #(parameter WIDTH=21)(
 input wire [WIDTH-1:0] a,b, output wire [WIDTH-1:0] sum
);
 wire [53:0] product;
 cyclonev_mac #(
  .ax_width(27),.ay_scan_in_width(26),.az_width(26),
  .bx_width(1),.by_width(1),.bz_width(1),
  .result_a_width(54),.result_b_width(1),.scan_out_width(1),
  .operation_mode("m27x27"),
  .operand_source_max("coef"),.operand_source_may("preadder"),
  .operand_source_mbx("input"),.operand_source_mby("input"),
  .preadder_subtract_a("false"),.preadder_subtract_b("false"),
  .signed_max("true"),.signed_may("false"),
  .signed_mbx("false"),.signed_mby("false"),
  .coef_a_0(27'sd1),.coef_a_1(27'sd1),.coef_a_2(27'sd1),.coef_a_3(27'sd1),
  .coef_a_4(27'sd1),.coef_a_5(27'sd1),.coef_a_6(27'sd1),.coef_a_7(27'sd1),
  .ay_use_scan_in("false"),.by_use_scan_in("false"),
  .delay_scan_out_ay("false"),.delay_scan_out_by("false"),
  .use_chainadder("false"),.enable_double_accum("false"),
  .ax_clock("none"),.ay_scan_in_clock("none"),.az_clock("none"),
  .bx_clock("none"),.by_clock("none"),.bz_clock("none"),
  .coef_sel_a_clock("none"),.coef_sel_b_clock("none"),
  .sub_clock("none"),.negate_clock("none"),
  .accumulate_clock("none"),.load_const_clock("none"),.output_clock("none")
 ) hard_add(
  .ax(),.ay({{(26-WIDTH){1'b0}},a}),.az({{(26-WIDTH){1'b0}},b}),
  .bx(),.by(),.bz(),.coefsela(3'd0),.coefselb(),
  .scanin(),.chainin(),.loadconst(1'b0),.accumulate(1'b0),
  .negate(1'b0),.sub(1'b0),.clk(3'b000),.ena(3'b111),.aclr(2'b00),
  .resulta(product),.resultb(),.scanout(),.chainout(),.dftout()
 );
 assign sum=product[WIDTH-1:0];
endmodule
'''

def save(p, value):
    p.write_text(json.dumps(value, indent=2) + '\n')

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def prepare():
    OUT.mkdir(exist_ok=False)
    (OUT/'phase10_dsp_modadd.v').write_text(MODULE)
    for variant in ['fabric','dsp']:
        d=OUT/variant; d.mkdir()
        sums = 'assign sum21=q21+step21;\n assign sum22=q22+step22;' if variant=='fabric' else '''phase10_dsp_modadd #(21) a21(q21,step21,sum21);
 phase10_dsp_modadd #(22) a22(q22,step22,sum22);'''
        (d/'ap_core.v').write_text('''module ap_core(input wire clk,load,advance,
 input wire [42:0] initial_value,step_value,output wire [42:0] result);
 (* preserve *) reg [20:0] q21,step21;
 (* preserve *) reg [21:0] q22,step22;
 wire [20:0] sum21;wire [21:0] sum22;
 '''+sums+'''
 always @(posedge clk) begin
  if(load) begin
   q21<=initial_value[20:0];q22<=initial_value[42:21];
   step21<=step_value[20:0];step22<=step_value[42:21];
  end else if(advance) begin q21<=sum21;q22<=sum22;end
 end
 assign result={q22,q21};
endmodule
''')
        (d/'ap_core.qpf').write_text('PROJECT_REVISION = "ap_core"\n')
        qsf='''set_global_assignment -name FAMILY "Cyclone V"
set_global_assignment -name DEVICE 5CEBA4F23C8
set_global_assignment -name TOP_LEVEL_ENTITY ap_core
set_global_assignment -name VERILOG_FILE ap_core.v
set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files
set_global_assignment -name SDC_FILE timing.sdc
set_global_assignment -name NUM_PARALLEL_PROCESSORS 2
set_global_assignment -name SEED 2
set_global_assignment -name OPTIMIZATION_MODE BALANCED
set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA
set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC OFF
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING OFF
set_global_assignment -name ROUTER_TIMING_OPTIMIZATION_LEVEL MAXIMUM
'''
        if variant=='dsp':qsf+='set_global_assignment -name VERILOG_FILE ../phase10_dsp_modadd.v\n'
        (d/'ap_core.qsf').write_text(qsf)
        (d/'timing.sdc').write_text('create_clock -name core -period 10 [get_ports clk]\nderive_clock_uncertainty\n')
    save(OUT/'prepared.json',{'scope':__doc__,'source_sha256':{str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()},'helper_sha256':sha(Path(__file__)),'no_full_core':True})
    print(OUT)

def run(variant,stage):
    assert variant in ['fabric','dsp'] and stage in ['map','fit','sta']
    prep=json.loads((OUT/'prepared.json').read_text())
    # Quartus may append its version to QSF; preserve and check the original
    # settings explicitly after compilation rather than claiming byte equality.
    for n,h in prep['source_sha256'].items():
        if not n.endswith('.qsf'):assert sha(OUT/n)==h,n
    d=OUT/variant
    cmd=sweep.docker(OUT,'pocket',d,['quartus_'+stage,'ap_core'])
    save(d/(stage+'-invocation.json'),{'command':cmd,'no_full_core':True,'prepared_sha256':sha(OUT/'prepared.json')})
    start=time.monotonic()
    with (d/(stage+'.log')).open('x') as f:
        proc=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
    save(d/(stage+'-exit.json'),{'returncode':proc.returncode,'elapsed_seconds':time.monotonic()-start})
    print(variant,stage,proc.returncode)
    raise SystemExit(proc.returncode)

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    else:run(sys.argv[1],sys.argv[2])
