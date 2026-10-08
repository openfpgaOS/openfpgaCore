#!/usr/bin/env python3
"""Private GPU lookahead ownership and PSS DSP-A mux factoring."""
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'gpu-recip-write-v1/gpu_core.v'
OUT = P / 'gpu-control-owner-v1'
BASE_SHA = '238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def once(s, old, new):
    assert s.count(old) == 1, (old, s.count(old))
    return s.replace(old, new)


DATA = '''// Select the PSS operand independently of the main-FSM ownership bit.
// All original write conditions remain in the enable. The final owner select
// overrides the existing every-cycle DSP-A default without passing state
// through every arm of the operand mux. Illegal PSS codes never write.
wire pss_dsp_a_write =
       ((persp_pss == PSS_ADV_ISSUE) && !pss_full_advance)
    || ((persp_pss == PSS_ADV_TAIL_ST_CAPTURE)
        && (!PRUNE_DISABLED_SETUP || EFF_Q29))
    || (persp_pss == PSS_NR_MUL_X)
    || (persp_pss == PSS_NR_MUL_Y)
    || (persp_pss == PSS_MUL)
    || ((persp_pss == PSS_FINAL)
        && (`ifdef INCLUDE_PSS_SPECULATIVE_STEP 1'b1 `else pss_constz_go_r `endif))
    || ((persp_pss == PSS_SLOPE_PREP)
        && (pss_effective_divisor != 5'd1)
        && (pss_effective_divisor != 5'd2)
        && (pss_effective_divisor != 5'd4)
        && (pss_effective_divisor != 5'd8)
        && (pss_effective_divisor < 5'd16))
    || ((persp_pss == PSS_SLOPE_DIV_COMMIT)
        && (!PRUNE_DISABLED_SETUP || EFF_Q29));
reg signed [31:0] pss_dsp_a_data;
always @* begin
    pss_dsp_a_data = 32'sd0;
    case (persp_pss)
        PSS_ADV_ISSUE, PSS_FINAL: pss_dsp_a_data = sp_sZstep;
        PSS_ADV_TAIL_ST_CAPTURE: pss_dsp_a_data = sp_zinv_step;
        PSS_NR_MUL_X: pss_dsp_a_data = $signed(persp_zinv_abs_r);
        PSS_NR_MUL_Y: pss_dsp_a_data = recip_q16_r;
        PSS_MUL: pss_dsp_a_data = sp_sZ;
        PSS_SLOPE_PREP: pss_dsp_a_data = pss_slope_s_delta[31]
            ? (32'd0 - pss_slope_s_delta[31:0]) : pss_slope_s_delta[31:0];
        PSS_SLOPE_DIV_COMMIT: pss_dsp_a_data = $signed(dsp_p[63:32]);
        default: ;
    endcase
end
'''

# Exact original site spellings; all enclosing state/condition branches stay.
REMOVED = [
    '                        dsp_a  <= sp_sZstep;\n',
    '\t                    dsp_a  <= sp_zinv_step;\n',
    '                    dsp_a <= $signed(persp_zinv_abs_r);\n',
    '                    dsp_a <= recip_q16_r;\n',
    '                    dsp_a     <= sp_sZ;\n',
    '                        dsp_a  <= $signed(s_mag);\n',
    '                    dsp_a  <= $signed(dsp_p[63:32]);\n',
]


def prepare():
    assert sha(BASE) == BASE_SHA
    old = BASE.read_text()
    new = once(old,
        'wire span_la_eligible = cmd_is_tri_walker && spanprod_active && tex_route\n',
        '// A live lookahead owner was armed only by the walker-qualified EMIT.\n'
        '// Command flags cannot change until it retires or reset clears span_la.\n'
        'wire span_la_eligible = spanprod_active && tex_route\n')
    anchor = '// Cheap "<<2 magnitude shrink" check:\n'
    new = once(new, anchor, DATA + '\n' + anchor)
    start = new.index('            if (!persp_active) begin\n                persp_pss <= PSS_IDLE;')
    stop = new.index('            if (persp_active && ((persp_pss == PSS_ADV_TAIL_COMMIT)', start)
    body = new[start:stop]
    assert len(re.findall(r'\bdsp_a\s*<=', body)) == 8
    for text in REMOVED:
        expected = 2 if 'sp_sZstep' in text else 1
        assert body.count(text) == expected, (text, body.count(text))
        body = body.replace(text, '')
    assert not re.search(r'\bdsp_a\s*<=', body)
    new = new[:start] + body + new[stop:]
    anchor = '            dsp2_b <= 32\'sd0;\n            // m_wr inflight counter'
    new = once(new, anchor,
        '            dsp2_b <= 32\'sd0;\n'
        '            if (state == S_FRAG_PIPE && persp_active && pss_dsp_a_write)\n'
        '                dsp_a <= pss_dsp_a_data;\n'
        '            // m_wr inflight counter')
    assert len(re.findall(r'\bdsp_a\s*<=', old)) - len(re.findall(r'\bdsp_a\s*<=', new)) == 7
    OUT.mkdir(exist_ok=False)
    (OUT / 'baseline.v').write_text(old)
    (OUT / 'gpu_core.v').write_text(new)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='gpu238f/gpu_core.v',
        tofile='gpu-control-owner-v1/gpu_core.v')))
    shutil.copy2(__file__, OUT / 'recipe.py')
    save(OUT / 'prepared.json', dict(parent_sha256=sha(BASE), candidate_sha256=sha(OUT / 'gpu_core.v'),
        added_registers=0, added_cycles=0, pss_original_sites=8,
        scope='Remove only the redundant tri-owner lookahead qualifier; factor exactly the eight DSP-A PSS writes into a data mux plus original enables at the same final register. All original defaults/reset/soft-reset and other writers remain.',
        pending=['Inductive ownership review', 'Literal exhaustive controls/data basis', 'Broad GPU per-edge ownership and DSP-A assertions', 'Matched native renderer replay', 'Resource and timing qualification']))
    print('SOURCE', sha(OUT / 'gpu_core.v'))


REFERENCE = '''case (persp_pss)
 PSS_ADV_ISSUE: if (!pss_full_advance) begin old_write=1; old_data=sp_sZstep; end
 PSS_ADV_TAIL_ST_CAPTURE: if (!PRUNE_DISABLED_SETUP || EFF_Q29) begin old_write=1; old_data=sp_zinv_step; end
 PSS_NR_MUL_X: begin old_write=1; old_data=$signed(persp_zinv_abs_r); end
 PSS_NR_MUL_Y: begin old_write=1; old_data=recip_q16_r; end
 PSS_MUL: begin old_write=1; old_data=sp_sZ; end
 PSS_FINAL: if (`ifdef INCLUDE_PSS_SPECULATIVE_STEP 1'b1 `else pss_constz_go_r `endif) begin old_write=1; old_data=sp_sZstep; end
 PSS_SLOPE_PREP: begin
  if (pss_effective_divisor == 5'd1 || pss_effective_divisor == 5'd2
      || pss_effective_divisor == 5'd4 || pss_effective_divisor == 5'd8) begin end
  else if (pss_effective_divisor < 5'd16) begin
   s_mag=pss_slope_s_delta[31] ? (32'd0-pss_slope_s_delta[31:0]) : pss_slope_s_delta[31:0];
   old_write=1; old_data=$signed(s_mag);
  end
 end
 PSS_SLOPE_DIV_COMMIT: if (!PRUNE_DISABLED_SETUP || EFF_Q29) begin old_write=1; old_data=$signed(dsp_p[63:32]); end
 default: ;
endcase
'''


def literal():
    assert sha(BASE) == BASE_SHA
    prepared = json.loads((OUT / 'prepared.json').read_text())
    assert sha(OUT / 'gpu_core.v') == prepared['candidate_sha256']
    d = OUT / 'literal'
    d.mkdir(exist_ok=False)
    old = BASE.read_text()
    names = re.findall(r'\bPSS_[A-Z_]+\b', DATA)
    constants = '\n'.join(re.findall(r'^localparam (?:' + '|'.join(sorted(set(names))) + r')\s*=.*?;', old, re.M))
    assert constants.count('localparam') == 8
    probe = '''module probe(input [5:0] persp_pss,input PRUNE_DISABLED_SETUP,EFF_Q29,pss_full_advance,pss_constz_go_r,
 input [4:0] pss_effective_divisor,input reset_n,soft_reset,fragment,persp_active,
 input [31:0] held,default_a,sp_sZstep,sp_zinv_step,persp_zinv_abs_r,recip_q16_r,sp_sZ,pss_slope_s_delta,
 input [63:0] dsp_p,output reg[31:0] old_value,output[31:0] new_value,
 output reg old_write,output new_write);
''' + constants + '\n' + DATA + '''
reg[31:0] old_data,s_mag;
always @*begin
 old_write=0;old_data=0;s_mag=0;
''' + REFERENCE + '''
 old_value=!reset_n?32'd0:soft_reset?held:default_a;
 if(reset_n&&!soft_reset&&fragment&&persp_active&&old_write)old_value=old_data;
end
assign new_write=pss_dsp_a_write;
assign new_value=!reset_n?32'd0:soft_reset?held:
 ((fragment&&persp_active&&pss_dsp_a_write)?pss_dsp_a_data:default_a);
endmodule
'''
    (d / 'probe.v').write_text(probe)
    (d / 'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static uint64_t n=0;static uint32_t x=0x762bd479;
static uint32_t rnd(){x^=x<<13;x^=x>>17;x^=x<<5;return x;}
static void check(Vprobe&t){t.eval();++n;if(t.old_value!=t.new_value||t.old_write!=t.new_write){printf("FAIL n=%llu pss=%u old=%08x new=%08x\\n",(unsigned long long)n,t.persp_pss,t.old_value,t.new_value);exit(1);}}
static void payload(Vprobe&t,unsigned k){uint32_t b=1u<<(k&31);t.held=~b;t.default_a=b^0xa55a55aa;t.sp_sZstep=b;t.sp_zinv_step=~b;t.persp_zinv_abs_r=b^0x12345678;t.recip_q16_r=b^0x879a3c61;t.sp_sZ=b^0xf0f05555;t.pss_slope_s_delta=k<32?b:~b;t.dsp_p=(uint64_t(b^0xaa55aa55)<<32)|~b;}
int main(){Vprobe t;
for(unsigned c=0;c<(1u<<19);c++){
 t.persp_pss=c&63;t.PRUNE_DISABLED_SETUP=(c>>6)&1;t.EFF_Q29=(c>>7)&1;t.pss_full_advance=(c>>8)&1;t.pss_constz_go_r=(c>>9)&1;t.pss_effective_divisor=(c>>10)&31;t.reset_n=(c>>15)&1;t.soft_reset=(c>>16)&1;t.fragment=(c>>17)&1;t.persp_active=(c>>18)&1;
 for(unsigned k=0;k<64;k++){payload(t,k);check(t);}}
for(unsigned i=0;i<1000000;i++){
 t.persp_pss=rnd()&63;t.PRUNE_DISABLED_SETUP=rnd()&1;t.EFF_Q29=rnd()&1;t.pss_full_advance=rnd()&1;t.pss_constz_go_r=rnd()&1;t.pss_effective_divisor=rnd()&31;t.reset_n=rnd()&1;t.soft_reset=rnd()&1;t.fragment=rnd()&1;t.persp_active=rnd()&1;
 t.held=rnd();t.default_a=rnd();t.sp_sZstep=rnd();t.sp_zinv_step=rnd();t.persp_zinv_abs_r=rnd();t.recip_q16_r=rnd();t.sp_sZ=rnd();t.pss_slope_s_delta=rnd();t.dsp_p=(uint64_t(rnd())<<32)|rnd();check(t);}
printf("DSP_OWNER_LITERAL checks=%llu PASS\\n",(unsigned long long)n);}
''')
    checks = []
    for speculative in (False, True):
        run = d / ('speculative' if speculative else 'conditional')
        run.mkdir()
        cmd = ['verilator', '--cc', '--exe', '--build', '-j', '3', '-Wno-fatal',
               '--top-module', 'probe', '--Mdir', str(run / 'obj'), '-CFLAGS', '-O2']
        if speculative:
            cmd.append('-DINCLUDE_PSS_SPECULATIVE_STEP')
        cmd += [str(d / 'probe.v'), str(d / 'main.cpp')]
        save(run / 'command.json', cmd)
        for label, command in [('build', cmd), ('run', [str(run / 'obj/Vprobe')])]:
            with (run / (label + '.log')).open('x') as f:
                subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)
        m = re.search(r'DSP_OWNER_LITERAL checks=(\d+) PASS', (run / 'run.log').read_text())
        assert m
        checks.append(int(m[1]))
    save(d / 'result.json', dict(passed=True, checks=sum(checks), variants=2,
        exhaustive_controls_per_variant=1 << 19, payload_bases_per_control=64,
        random_cases_per_variant=1000000, includes_illegal_pss_codes=True,
        includes_hard_reset_soft_reset_and_default=True,
        scope='Literal original eight conditions/expressions versus extracted candidate mux and enable; all64 PSS values, all divisors and independent prune/Q29/full-advance flags. Other main-state DSP writers remain unmodified and are checked in the full GPU replay.'))
    print('LITERAL', sum(checks))


if __name__ == '__main__':
    for action in sys.argv[1:]:
        globals()[action]()
