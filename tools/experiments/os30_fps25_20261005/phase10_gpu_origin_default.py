#!/usr/bin/env python3
"""Private selected-configuration unused DSP-A default simplification."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP,sha,write
P=CAMP/'phase10';BASE=P/'gpu-attribute-v13/gpu_core.v';OUT=P/'gpu-origin-default-v14'
def main():
 assert sha(BASE)=='61f4776cada85fb3d209204b82444027054f18ced59eb46e970458c783110888'
 OUT.mkdir(exist_ok=False);old=BASE.read_text()
 a='''`elsif INCLUDE_SPAN_DEDICATED_MULT
            dsp_a <= 32'sd0;
            dsp2_a <= 32'sd0;
            dsp_b <= 32'sd0;'''
 b='''`elsif INCLUDE_SPAN_DEDICATED_MULT
            // The origin launch already selects dv_du. Every other live
            // launch overrides A and B together; default B=0 keeps unused
            // products identical while removing origin-state gating of A.
            dsp_a <= dv_du;
            dsp2_a <= 32'sd0;
            dsp_b <= 32'sd0;'''
 assert old.count(a)==1;t=old.replace(a,b);(OUT/'gpu_core.v').write_text(t)
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),t.splitlines(True),fromfile='gpu-attribute-v13/gpu_core.v',tofile='gpu-origin-default-v14/gpu_core.v')))
 write(OUT/'prepared.json',dict(baseline_sha256=sha(BASE),candidate_sha256=sha(OUT/'gpu_core.v'),helper_sha256=sha(Path(__file__)),changed_registers=['dsp_a default only'],added_flip_flops=0,added_states=0,added_cycles=0,scope='Only INCLUDE_SPAN_DEDICATED_MULT when INCLUDE_SPAN_OPERAND_DEFAULT is not selected. Other clients, dsp2_a, B defaults and all reset/launch assignments unchanged.',deployment_ready=False))
 print(sha(OUT/'gpu_core.v'))
if __name__=='__main__':main()
