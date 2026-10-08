#!/usr/bin/env python3
"""Compare new phase9 normal runs with hash-bound sealed F9 coupled baselines."""
import argparse
import json
from pathlib import Path

from phase9_experiment import CAMP, HERE, OUT, PARENT, ROOT, directory, module, sha, write

SEALED = ROOT/'docs/measurements/os30-overlap-20261006/coupled'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate')
    p.add_argument('--mode',choices=['normal','diagnostic'],default='normal')
    p.add_argument('--start',type=int,default=360)
    p.add_argument('--frames',type=int,default=8)
    p.add_argument('--suffix',default='')
    a=p.parse_args()
    assert a.frames==8 and a.start in (360,500,600), 'Only existing exact paired baselines are supported'
    dest=directory(a.candidate)
    baseline_out = SEALED if a.mode == 'normal' else CAMP/'phase8/diagnostic-coupled'
    helper=ROOT/'tools/experiments/os30_overlap_20261006/analysis_common.py' if a.mode == 'normal' else baseline_out/'analysis_common_diagnostic.py'
    baseline_manifest=baseline_out/'final-manifest.json'
    if a.mode == 'diagnostic':
        assert a.start in (360,600), 'Only existing exact diagnostic baselines are supported'
        sealed=json.loads((baseline_out/'inputs.json').read_text())
        assert sha(helper)==sealed['diagnostic_analysis_helper_sha256']
        assert sha(PARENT/'diagnostic.elf')==sealed['elf_sha256']['baseline']
    common=module('phase9_coupled_analysis',helper)
    # The frozen diagnostic helper lives in an evidence directory; restore the
    # repository constants exactly as its original caller did, without edits.
    common.ROOT,common.HERE,common.CAMP=ROOT,ROOT/'tools/experiments/os30_overlap_20261006',CAMP
    comparisons=[]
    all_runs=[]
    for traffic in (1,2):
        common.OUT=baseline_out
        baseline=common.run('baseline',a.start,traffic)
        common.OUT=OUT/'coupled'
        candidate=common.run(a.candidate+'-'+a.mode,a.start,traffic,suffix=('-'+a.suffix if a.suffix else ''))
        run_path=common.OUT/('run-'+common.label(a.candidate+'-'+a.mode,a.start,traffic)+('-'+a.suffix if a.suffix else ''))
        invocation=json.loads((run_path/'phase9-invocation.json').read_text())
        assert invocation['mode']==a.mode
        assert invocation['source_elf_sha256']==sha(dest/('app.elf' if a.mode=='normal' else 'diagnostic.elf'))
        assert invocation['output_inputs_sha256']==sha(run_path/'inputs.json')
        assert baseline['frames']==candidate['frames'], 'Paired color/depth mismatch'
        differences=[]
        for frame in range(a.start,a.start+a.frames):
            old,new=baseline['workload'][frame],candidate['workload'][frame]
            assert all(old[k]==new[k] for k in ('gpu_cmd_words','gpu_pixels')),(frame,traffic)
            if old['buffer']==new['buffer']:
                assert old['hash_cmd']==new['hash_cmd'],(frame,traffic,'command hash')
            else:
                differences.append(frame)
        comparisons.append(dict(traffic=traffic,start=a.start,frames=a.frames,
            baseline_ms=baseline['modeled_frame_ms'],candidate_ms=candidate['modeled_frame_ms'],
            throughput_change_percent=100*(baseline['modeled_frame_ms']/candidate['modeled_frame_ms']-1),
            frame_time_delta_ms=candidate['modeled_frame_ms']-baseline['modeled_frame_ms'],
            renderer_delta_ms=candidate['renderer_entry_to_flip_publication_ms']-baseline['renderer_entry_to_flip_publication_ms'],
            exact_color_depth_frames=a.frames,exact_command_words_pixels=True,
            command_hash_exact_when_buffer_matches=True,changed_draw_buffers=differences,
            baseline_input_sha256=baseline['inputs_sha256'],candidate_input_sha256=candidate['inputs_sha256']))
        all_runs += [baseline,candidate]
    result=dict(passed=True,candidate=a.candidate,mode=a.mode,start=a.start,frames=a.frames,
        comparisons=comparisons,runs=all_runs,analyzer_sha256=sha(Path(__file__)),
        retained_analysis_helper_sha256=sha(helper),baseline_campaign_manifest_sha256=sha(baseline_manifest),
        no_baseline_rerun=True,qualified_for_deployment=False,
        scope='Coupled modeled CPU/cache timing and retained-A GPU/memory RTL. Synthetic traffic; actual CPU memory contention and kernel mixing/interrupt costs are absent. This is not observed Pocket FPS.')
    output=dest/f'coupled-{a.mode}-{a.start}{"-"+a.suffix if a.suffix else ""}.json'
    with output.with_suffix('.recipe.py').open('xb') as recipe:
        recipe.write(Path(__file__).read_bytes())
    write(output,result)
    print(json.dumps(dict(passed=True,candidate=a.candidate,comparisons=comparisons),indent=2))


if __name__=='__main__':main()
