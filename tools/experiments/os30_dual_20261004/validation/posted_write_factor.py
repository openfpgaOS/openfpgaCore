#!/usr/bin/env python3
"""Freeze a private exact-combinational posted-write length/WLAST factoring."""
import argparse,difflib,hashlib,json,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
REF=ROOT/'src/fpga/common/cpu_system.v'
REFERENCE_SHA='2960fe6b493b0de3de53b04f841a6667205c5141ce86a05b1b228cda9ab86833'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

OLD_PREFIX="""wire match01 = coalesce_ok
            && (wr_count >= 3'd2)
            && wr_matches_prev[head1];
wire match02 = match01 && (wr_count >= 3'd3)
            && wr_matches_prev[head2];
wire match03 = match02 && (wr_count >= 3'd4)
            && wr_matches_prev[head3];

wire [WR_PTR_W-1:0] burst_awlen_calc = match03 ? 2'd3
                                     : match02 ? 2'd2
                                     : match01 ? 2'd1
                                     : 2'd0;"""
NEW_PREFIX="""// Compute the equal-address/mask run independently of the late target decode.
wire local_match01 = (wr_count >= 3'd2) && wr_matches_prev[head1];
wire local_match02 = local_match01 && (wr_count >= 3'd3)
                  && wr_matches_prev[head2];
wire local_match03 = local_match02 && (wr_count >= 3'd4)
                  && wr_matches_prev[head3];
wire [WR_PTR_W-1:0] local_awlen_calc =
    {local_match02, (local_match01 && (!local_match02 || local_match03))};
wire [WR_PTR_W-1:0] burst_awlen_calc = {WR_PTR_W{coalesce_ok}} & local_awlen_calc;"""
OLD_LAST='wire                w_is_last = (burst_w_idx == eff_burst_awlen);'
NEW_LAST="""// Compare in parallel, then select Boolean results with the original priority.
wire accepted_w_is_last = (burst_w_idx == burst_awlen);
wire held_w_is_last = (burst_w_idx == held_awlen);
wire live_w_is_last = ((!coalesce_ok && (burst_w_idx == 2'd0)) ||
                      (coalesce_ok && (burst_w_idx == local_awlen_calc)));
wire w_is_last = (lsu_aw_sent && accepted_w_is_last) ||
                 (!lsu_aw_sent && awlen_held && held_w_is_last) ||
                 (!lsu_aw_sent && !awlen_held && live_w_is_last);"""

def between(text,start,end):
    a=text.index(start);return text[a:text.index(end,a)]

def module(name,source):
    heads=between(source,'wire [WR_PTR_W-1:0] head1 =','// Same-address FIXED-burst')
    prefix=NEW_PREFIX if NEW_PREFIX in source else OLD_PREFIX
    assert source.count(prefix)==1
    presented=between(source,'wire [WR_PTR_W-1:0] presented_awlen =','// W-beat data routing:')
    effective=between(source,'wire [WR_PTR_W-1:0] eff_burst_awlen =','// Write FIFO push/pop')
    aw=between(source,'assign per_awlen_cpu   =','// W channel')
    return f'''module {name}(
input wire [17:0] controls, output wire [16:0] result);
localparam WR_PTR_W=2;
wire coalesce_ok=controls[0];
wire [2:0] wr_count=controls[3:1];
wire [3:0] wr_matches_prev=controls[7:4];
wire [1:0] wr_head=controls[9:8];
wire lsu_aw_sent=controls[10],awlen_held=controls[11];
wire [1:0] burst_awlen=controls[13:12],held_awlen=controls[15:14],burst_w_idx=controls[17:16];
wire [7:0] per_awlen_cpu;
wire [1:0] per_awburst_cpu;
{heads}{prefix}
{presented}{effective}{aw}
assign result={{burst_awlen_calc,presented_awlen,eff_burst_awlen,w_is_last,per_awlen_cpu,per_awburst_cpu}};
endmodule
'''

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    assert sha(REF)==REFERENCE_SHA
    reference=REF.read_text();candidate=reference
    for old,new in [(OLD_PREFIX,NEW_PREFIX),(OLD_LAST,NEW_LAST)]:
        assert candidate.count(old)==1;candidate=candidate.replace(old,new)
    reversed_source=candidate.replace(NEW_PREFIX,OLD_PREFIX).replace(NEW_LAST,OLD_LAST)
    assert reversed_source==reference
    (out/'cpu_system.v').write_text(candidate);(out/'reference_cpu_system.v').write_text(reference)
    patch=''.join(difflib.unified_diff(reference.splitlines(True),candidate.splitlines(True),fromfile='cpu_system.v',tofile='cpu_system.v'))
    (out/'posted-write-factor.patch').write_text(patch)
    shutil.copyfile(__file__,out/'posted_write_factor.py')
    tb=module('reference_cone',reference)+module('candidate_cone',candidate)+'''
module tb;
reg [17:0] controls;wire [16:0] before_result,after_result;
reference_cone a(controls,before_result);candidate_cone b(controls,after_result);
initial begin
  for(integer n=0;n<262144;n=n+1) begin
    controls=n[17:0];#1;
    if(before_result!==after_result)$fatal(1,"control=%h before=%h after=%h",controls,before_result,after_result);
  end
  $display("PASS: 262144 actual coalescer/WLAST control combinations; accepted/held/live priority, both qualifiers, all queue counts and index values");
  $finish;
end
endmodule
'''
    (out/'tb.sv').write_text(tb)
    wrapper=(ROOT/'src/fpga/test/lsu_axi_shim.v').read_text().split('\n);\n')[0]+'\n);\n'
    for key,source in [('reference',reference),('candidate',candidate)]:
        body=between(source,'// Read tracker — 1-deep.','\nVexiiRiscv cpu (')
        (out/(key+'_shim.v')).write_text(wrapper+'\n'+body+'\nendmodule\n`default_nettype wire\n')
    manifest={'reference':str(REF.relative_to(ROOT)),'reference_sha256':sha(REF),'candidate_sha256':sha(out/'cpu_system.v'),
        'exact_reverse_restores_reference':True,'all_sequential_source_unchanged':True,'extra_registers':0,'extra_cycles':0,
        'scope':'Only two exact combinational source replacements: LOCAL run encoding and comparison before length selection.',
        'files':{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'candidate_sha256':manifest['candidate_sha256'],'output':str(out)}))

if __name__=='__main__':main()
