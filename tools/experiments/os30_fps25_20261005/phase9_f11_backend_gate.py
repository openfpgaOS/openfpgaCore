#!/usr/bin/env python3
"""Bind completed private F11 backend checks without qualifying publication."""
import json
from pathlib import Path
from phase9_f11_validate import ROOT,CAMP,DEST,HERE,MARKER,ready
from phase9_experiment import flat_inputs,sha


def main():
    target=DEST/'backend-validation.json'
    assert not target.exists(), 'Preserve completed validation evidence'
    normal=ready('normal');diagnostic=ready('diagnostic')
    assert MARKER in (DEST/'perf_pocket.c').read_text()
    perf=json.loads((DEST/'perf-tests.json').read_text());assert perf['passed']
    for name,digest in perf['source_sha256'].items():assert sha(DEST/name)==digest
    invocation=json.loads((DEST/'perf-invocation.json').read_text())
    assert invocation['result_sha256']==sha(DEST/'perf-tests.json')
    assert invocation['recipe_sha256']==sha(HERE/'phase9_f11_validate.py')
    assert invocation['accounting_helper_sha256']==sha(HERE/'phase6_perf_tests.py')
    panel=json.loads((DEST/'panel/result.json').read_text());assert panel['passed']
    visual=json.loads((DEST/'panel/visual-review.json').read_text());assert visual['passed'] and not visual['physical_measurement']
    assert visual['image_sha256']==panel['image_sha256']==sha(DEST/'panel/panel.png')
    assert panel['candidate_elf_sha256']==sha(diagnostic)
    assert panel['output_sha256']==sha(DEST/'panel/frame.bin')
    assert panel['recipe_sha256']==sha(HERE/'phase9_f11_validate.py')
    cases=[]
    for mode,elf in [('normal',normal),('diagnostic',diagnostic)]:
        rows=json.loads((DEST/f'replay-{mode}.json').read_text())
        assert len(rows)==6 and {(r['traffic'],r['frame']) for r in rows}=={(t,f) for t in (1,2) for f in (360,600,720)}
        for r in rows:
            assert r['color_exact'] and r['depth_exact'] and r['fence_exact']
            assert r['gpu_cycle_change_percent']==0
            assert r['candidate_elf_sha256']==sha(elf)
            assert r['recipe_sha256']==sha(HERE/'phase9_f11_validate.py')
            stem=DEST/'replays'/mode/str(r['traffic'])/f"frame_{r['frame']:05d}"
            for suffix,key in [('.bin','output_sha256'),('.vec','adapted_vector_sha256'),('.log','log_sha256')]:
                assert sha(stem.with_suffix(suffix))==r[key]
            assert sha(ROOT/r['reference_path'])==r['reference_output_sha256']
        cases.extend(dict(mode=mode,frame=r['frame'],traffic=r['traffic']) for r in rows)
    focused=CAMP/'phase9/direct-state-v1/state-tests'
    proof=json.loads((focused/'RESULT.json').read_text());assert proof['passed']
    origin=CAMP/'phase9/direct-state-v1/gfx_gpu.c'
    assert sha(origin)==sha(DEST/'gfx_gpu.c')==proof['source_sha256'][str(origin.relative_to(ROOT))]
    for relative,digest in proof['source_sha256'].items():assert sha(ROOT/relative)==digest
    for run in proof['runs'].values():
        assert run['result']['passed'] and run['result']['cases']==8192
        assert sha(Path(run['command'][-1]))==run['executable_sha256']
    assert all(r['detected'] and r['returncode']!=0 for r in proof['negative_controls'].values())
    files=[DEST/n for n in ['perf-tests.json','perf-invocation.json','replay-normal.json','replay-diagnostic.json',
        'build-normal.json','build-diagnostic.json','analysis-normal.json','analysis-diagnostic.json','composition.json']]
    for directory in [DEST/'panel',DEST/'capture-diagnostic-panel',DEST/'replays',focused]:
        files += [p for p in directory.rglob('*') if p.is_file()]
    files += [HERE/n for n in ['phase9_f11_validate.py','phase9_f11_backend_gate.py','phase6_perf_tests.py',
        'phase9_experiment.py','phase7_experiment.py','phase8_experiment.py','phase6_validate.py','phase4_combined_validate.py',
        'phase9_direct_state_tests.py','phase7_residency_tests.py']]
    evidence={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(files))}
    result=dict(passed=True,source_sha256=flat_inputs(DEST),normal_elf_sha256=sha(normal),
        diagnostic_elf_sha256=sha(diagnostic),evidence_sha256=evidence,
        checks=dict(perf_accounting=True,rendered_panel_and_visual_review=True,retained_A_replay_cases=cases,
            exact_color_depth_fence=True,gpu_cycle_change_percent=0.0,
            complete_direct_renderer_state_and_packet_cases_per_mode=8192,
            focused_modes=['sanitized','fast-math'],focused_negative_controls_detected=True),
        qualified_for_publication=False,
        limits='Backend validation only. CPU finite-W guard/codegen, coupled performance and publication review have separate owners. Simulated timings are not physical Pocket FPS.')
    target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=True,path=str(target),sha256=sha(target),bound_evidence_files=len(evidence)),indent=2))


if __name__=='__main__':main()
