#!/usr/bin/env python3
"""Capacity-bound QGF event attribution. Does not infer removable frame cost."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import re

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
STATES = dict(enumerate(('header_prime', 'header', 'passthrough_payload',
    'context', 'wait_jobs', 'view_request', 'view_prime', 'job_prime',
    'job_capture', 'math_request', 'cull_start', 'cull_wait', 'repair',
    'next_job', 'vertex_select', 'vertex_prime', 'vertex_capture',
    'vertex_emit', 'draw_select', 'draw_prime', 'draw_capture', 'draw_header',
    'draw_word', 'run_finish', 'release_token', 'fault')))


def rows(path):
    with path.open() as stream:
        result = [{k: int(v) for k, v in r.items()}
                  for r in csv.DictReader(stream, delimiter='\t')]
    assert result and all(a['cycle'] <= b['cycle']
                          for a, b in zip(result, result[1:])), path
    return result


def intervals(events, begin, end):
    assert events[0]['cycle'] <= begin
    for i, event in enumerate(events):
        next_cycle = events[i + 1]['cycle'] if i + 1 < len(events) else end
        duration = max(0, min(next_cycle, end) - max(event['cycle'], begin))
        if duration:
            yield event, duration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, model = args.run.resolve(), args.model.resolve()
    assert not args.out.exists(), args.out
    prepared = json.loads((model / 'prepared.json').read_text())
    for rel, expected in prepared['source_sha256'].items():
        assert sha(model / rel) == expected, rel
    core = model / 'frozen/src/fpga/common/gpu_core.v'
    core_text = core.read_text()
    queue_bits = re.findall(r'gpu_queued_front #\(\.ADDR_BITS\(RING_ADDR_BITS\), \.QUEUE_BITS\((\d+)\)\)', core_text)
    assert len(queue_bits) == 1
    queue_words = 1 << int(queue_bits[0])
    # These two models retain a 4096-word ingress and 12-bit monotonic pointers.
    assert re.search(r'localparam\s+(?:integer\s+)?RING_ADDR_BITS\s*=\s*12\b', core_text)
    pointer_modulus = 4096
    assert queue_words <= pointer_modulus
    full = queue_words - 1
    boundaries = [list(map(int, line.split()))
                  for line in (run / 'boundaries.tsv').read_text().splitlines()]
    assert len(boundaries) >= 3
    begin, end = boundaries[1][1], boundaries[2][1]
    assert end > begin
    qrows, brows = rows(run / 'queue-events.tsv'), rows(run / 'bank-events.tsv')
    states, occupancy, derived = (collections.Counter() for _ in range(3))
    for event, duration in intervals(qrows, begin, end):
        state = event['state']
        used = (event['output_write'] - event['output_read']) & (pointer_modulus - 1)
        assert used <= full
        states[STATES[state]] += duration
        band = ('zero' if used == 0 else 'full' if used == full else
                'below_quarter' if used < queue_words // 4 else
                'quarter_to_half' if used < queue_words // 2 else 'half_to_full')
        occupancy[band] += duration
        derived['output_word_cycle_sum'] += used * duration
        if used == full:
            derived['fifo_full'] += duration
            if state in (17, 21, 22):
                derived['blocked_geometry_output'] += duration
            if state in (1, 2) and event['input_read'] != event['input_write']:
                derived['blocked_passthrough_output'] += duration
        if state == 9:
            derived['math_request'] += duration
            if event['raw_active'] or event['raw_pending']:
                derived['math_request_with_raw_job'] += duration
        if state in (9, 10, 11):
            derived['all_math_states'] += duration
        if event['math_owner']:
            derived['math_owned'] += duration
        if state == 1 and event['input_read'] == event['input_write']:
            derived['front_empty_idle'] += duration
        if state == 12:
            derived['repair_wait'] += duration
    banks, holds = collections.Counter(), collections.Counter()
    for event, duration in intervals(brows, begin, end):
        banks[str(event['valid'])] += duration
        for bank in (0, 1):
            if event['valid'] & (1 << bank):
                cause = ('copy' if not event['ready'] & (1 << bank) else
                         'reading' if event['owner'] and event['read_bank'] == bank
                         else 'ready_not_reading')
                holds[cause] += duration
    times = sorted({begin, end} |
                   {r['cycle'] for r in qrows if begin < r['cycle'] < end} |
                   {r['cycle'] for r in brows if begin < r['cycle'] < end})
    qi = bi = 0
    overlap = collections.Counter()
    for cycle, next_cycle in zip(times, times[1:]):
        while qi + 1 < len(qrows) and qrows[qi + 1]['cycle'] <= cycle:
            qi += 1
        while bi + 1 < len(brows) and brows[bi + 1]['cycle'] <= cycle:
            bi += 1
        q, b = qrows[qi], brows[bi]
        duration = next_cycle - cycle
        if b['valid'] == 3:
            used = (q['output_write'] - q['output_read']) & (pointer_modulus - 1)
            if used == full:
                overlap['both_banks_and_output_full'] += duration
            if q['state'] == 1 and q['input_read'] == q['input_write']:
                overlap['both_banks_and_front_empty'] += duration
            if q['state'] in (9, 10, 11):
                overlap['both_banks_and_math_states'] += duration
    assert sum(states.values()) == sum(occupancy.values()) == sum(banks.values()) == end - begin
    inputs = [run / n for n in ('queue-events.tsv', 'bank-events.tsv',
              'boundaries.tsv', 'prepared-queue.json')]
    inputs += [model / 'prepared.json', core,
               model / 'frozen/src/fpga/common/gpu_queued_front.v', Path(__file__)]
    result = dict(status='PASS_EXACT_EVENT_INTERVAL_ATTRIBUTION', run=str(run),
        model=str(model), output_queue_words=queue_words,
        pointer_modulus=pointer_modulus, warm_begin=begin, warm_end=end,
        warm_cycles=end - begin, worker_state_cycles=dict(states),
        output_occupancy_cycles=dict(occupancy), derived_cycles=dict(derived),
        bank_valid_mask_cycles=dict(banks), weighted_bank_hold_cycles=dict(holds),
        exact_overlap_cycles=dict(overlap),
        limitations=[
            'Durations overlap CPU and GPU work and are not causal speedup estimates.',
            'Weighted bank holds count each bank separately, not a frame partition.',
            'Ready-not-reading includes queued work and generation lifetime between runs.',
            'Queue-event pointers are used; legacy ring_word_sum is not used.',
            'Event streams are treated as piecewise constant at recorded event cycles; common pre/post-edge sampling convention is inherited.'
        ], inputs_sha256={str(p): sha(p) for p in inputs})
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('status', 'output_queue_words',
        'warm_cycles', 'derived_cycles', 'exact_overlap_cycles')}, indent=2))


if __name__ == '__main__':
    main()
