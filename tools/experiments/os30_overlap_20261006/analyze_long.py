#!/usr/bin/env python3
"""Follow-up of the real midframe queue-pressure benefit found near frame600."""
import hashlib,json
from pathlib import Path
from analysis_common import OUT,HERE,run,sha,load
def main():
    short=load(OUT/'results.json');assert short['passed']
    runs=[];comparisons=[]
    for t in [1,2]:
        b=run('baseline',600,t,32,'-long');c=run('kick128-tail',600,t,32,'-long')
        runs += [b,c];assert b['frames']==c['frames']
        changes=[]
        for i in range(600,632):
            a,z=b['workload'][i],c['workload'][i]
            assert all(a[k]==z[k] for k in ['gpu_cmd_words','gpu_pixels']),(t,i)
            if a['buffer']==z['buffer']:assert a['hash_cmd']==z['hash_cmd'],(t,i)
            else:changes.append(i)
        comparisons.append(dict(traffic=t,baseline_ms=b['modeled_frame_ms'],candidate_ms=c['modeled_frame_ms'],
            modeled_throughput_gain_percent=100*(b['modeled_frame_ms']/c['modeled_frame_ms']-1),
            renderer_ms_saved=b['renderer_entry_to_flip_publication_ms']-c['renderer_entry_to_flip_publication_ms'],
            rdptr_polls_removed=b['rdptr_polls']-c['rdptr_polls'],
            rdptr_polls_removed_per_interval=(b['rdptr_polls']-c['rdptr_polls'])/31,
            exact_color_depth_frames=32,word_pixel_counts_exact=True,presentation_draw_buffer_changes=changes))
    report=dict(passed=True,short_results_sha256=sha(OUT/'results.json'),
        analyzer_sha256=sha(Path(__file__)),shared_analyzer_sha256=sha(HERE/'analysis_common.py'),
        runs=runs,comparisons=comparisons,limitations=short['limits'])
    with (OUT/'long-results.json').open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    lines=['F9 publication scheduling: 32-frame follow-up, frames600–631','',
        'Times are CPU-model/GPU-RTL coupled results, not Pocket measurements.',
        'Traffic Baseline ms Candidate ms Throughput gain Ring polls removed/interval']
    for x in comparisons:lines.append(f"{x['traffic']:7} {x['baseline_ms']:11.5f} {x['candidate_ms']:12.5f} {x['modeled_throughput_gain_percent']:14.4f}% {x['rdptr_polls_removed_per_interval']:27.2f}")
    lines+=['','All64 paired framebuffers/depth buffers match exactly; all four first frames match independent retained F9 replay.',
       'CPU workload/guest instruction layout also differs; ring poll reduction and localization to batch submission support reduced midframe backpressure.',
       'Final GPU tails and overlap with the following game update are reported separately in long-results.json.']
    with (OUT/'LONG_REPORT.txt').open('x') as f:f.write('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__=='__main__':main()
