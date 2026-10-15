#!/usr/bin/env python3
"""Summarize a frozen integrated contention run without implying game FPS."""
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('run', type=Path)
a = p.parse_args()
path = a.run / 'results.json'
data = json.loads(path.read_text())
rows = []
for case in data['cases']:
    assert case['protocol_and_pcm_checks_passed']
    for workload in dict.fromkeys(r['workload'] for r in case['rows']):
        selected = [r for r in case['rows'] if r['workload'] == workload]
        assert len(selected) == 4
        off, on = [next(r['result'] for r in selected if r['busy'] and r['cache'] == c)
                   for c in [False, True]]
        assert all(r['pcm_common_sequence_match'] for r in selected)
        off_words = off['audio_beats'] / off['produced']
        on_words = on['audio_beats'] / on['produced']
        rows.append(dict(
            MHz=case['MHz'], latency=case['latency'], workload=workload,
            pcm_samples_compared=sum(r['pcm_samples_compared'] for r in selected),
            off_underruns=off['underruns'], on_underruns=on['underruns'],
            off_final_fifo=off['fifo_final'], on_final_fifo=on['fifo_final'],
            on_fifo_draining=on['fifo_draining'],
            off_audio_words_per_output=off_words, on_audio_words_per_output=on_words,
            word_reduction_factor=off_words/on_words,
            off_gpu_work_fraction=off['gpu_work_fraction_of_silent'],
            on_gpu_work_fraction=on['gpu_work_fraction_of_silent'],
        ))
receipt = {
    'results_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    'comparison': 'Every common produced PCM sample against cache-disabled quiet reference; underruns reported separately.',
    'scope': '400ms,32voices,real mixer/cache/selected arbiter,bounded synthetic slave,saturated synthetic CPU/GPU.',
    'limits': 'No physical SDRAM pin/timing, firmware ISR or game FPS claim; stream WPTR starts safely ahead without dynamic production.',
    'rows': rows,
}
(a.run / 'summary.json').write_text(json.dumps(receipt, indent=2)+'\n')
for r in rows:
    print(f"{r['MHz']:3} MHz latency{r['latency']:2} {r['workload']:20} "
          f"underruns {r['off_underruns']:5} -> {r['on_underruns']:5}; "
          f"FIFO {r['off_final_fifo']:3} -> {r['on_final_fifo']:3}; "
          f"words/output {r['off_audio_words_per_output']:.2f} -> {r['on_audio_words_per_output']:.2f}; "
          f"GPU bus {r['off_gpu_work_fraction']:.3f} -> {r['on_gpu_work_fraction']:.3f}")
