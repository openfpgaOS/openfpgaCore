#!/usr/bin/env python3
"""Checked line-anchored full-writer shadow; fixes the retained first draft."""
import json,re,shutil,subprocess
from pathlib import Path
import phase10_gpu_span_step_enable as c
R,P,O=c.R,c.P,c.O
PROBE=c.MONITOR.replace('  if((gpu.span_step_advance_early && !gpu.src_done)\n     !==(gpu.frag_pipe_blk.load_p0a && gpu.persp_active && (gpu.sp_seg_left!=0)))\n   $fatal(1,"running-step original/new enables differ");\n', '')


def instrument_shadow(source):
    assert source.count(c.NEW_WRITES)==1 and source.count(c.EARLY)==1
    pattern=r'^([ \t]*)(sp_[st]step)\s*<=\s*([^;\n]+);'
    matches=list(re.finditer(pattern,source,re.M))
    counts={r:sum(m.group(2)==r for m in matches)for r in ('sp_sstep','sp_tstep')}
    assert counts=={'sp_sstep':9,'sp_tstep':9},counts
    def mirror(m):
        indent,reg,rhs=m.groups()
        if rhs.strip() in ('sp_sstep + sp_scc','sp_tstep + sp_tcc'):return m.group(0)
        return m.group(0)+' proof_old_'+reg+' <= '+rhs+';'
    result=re.sub(pattern,mirror,source,flags=re.M)
    result=c.once(result,'reg signed [31:0] sp_sstep, sp_tstep;',
        'reg signed [31:0] sp_sstep, sp_tstep;\nreg signed [31:0] proof_old_sp_sstep, proof_old_sp_tstep;')
    anchor="                        sp_seg_left <= sp_seg_left - 4'd1;\n"
    result=c.once(result,anchor,'''                        proof_old_sp_sstep <= proof_old_sp_sstep + sp_scc;
                        proof_old_sp_tstep <= proof_old_sp_tstep + sp_tcc;
'''+anchor)
    for reg in counts:
        assert len(re.findall(r'\bproof_old_'+reg+r'\s*<=',result))==counts[reg]
    return result


def main():
    d=O/'shadow-v3';d.mkdir(exist_ok=False)
    shutil.copy2(__file__,d/'recipe.py')
    (d/'instrumented.v').write_text(instrument_shadow((O/'gpu_core.v').read_text()))
    # Selected parent lint fixture carries the existing passive proofs; retain
    # them, add only this factor/shadow and its passive TB monitor.
    parent=P/'gpu-parallel-control-v1/lint'
    src=c.transform((parent/'gpu_core.v').read_text())
    (d/'gpu_core.v').write_text(instrument_shadow(src))
    cmd=json.loads((parent/'command.json').read_text())
    tb=next(Path(x)for x in cmd if x.endswith('/tb_gpu.v'))
    s=tb.read_text();assert s.count('endmodule')==1
    (d/'tb_gpu.v').write_text(s.replace('endmodule',PROBE+'\nendmodule'))
    cmd=[str(d/'gpu_core.v')if x==str(parent/'gpu_core.v')else str(d/'tb_gpu.v')if x==str(tb)else str(d/'obj')if x==str(parent/'obj')else x for x in cmd]
    c.put(d/'command.json',cmd)
    with (d/'lint.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    c.put(d/'result.json',dict(passed_lint=True,simulation_run=False,literal_gate_comparison_separate=True,matched_real_writers_per_register=9,shadow_writers_per_register=9,line_anchored_assignments_only=True,
        retained_invalid_draft=str((O/'instrumented-reference-shadow.v').relative_to(R)),
        draft_issue='Initial unanchored extraction matched a comment, preserved without use. Shadow-v2 line anchoring fixed that, but its optional qualification monitor used an inaccessible block-local scope and failed lint. Shadow-v3 drops only that redundant monitor; exhaustive literal predicate proof is separate and every original full-writer shadow comparison remains.',
        source_sha256=c.sha(O/'gpu_core.v'),instrumented_sha256=c.sha(d/'instrumented.v')))
    print('SHADOW_LINT_PASS',c.sha(d/'instrumented.v'))


if __name__=='__main__':main()
