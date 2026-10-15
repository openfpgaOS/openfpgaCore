#!/usr/bin/env python3
"""Independent receipt/scene accounting audit; never executes or edits the model."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import struct

M = Path(__file__).resolve().parents[1]
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):
    return json.loads(Path(path).read_text())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--stream-receipt', type=Path)
    parser.add_argument('--control-receipt', type=Path)
    args = parser.parse_args()
    assert not args.out.exists(), 'Preserve prior audit receipts'
    receipts = [M / p for p in (
        'packet/test-v2/report.json', 'packet/fetch-v2/report.json',
        'work-core/tests/engine-v4/manifest.json',
        'work-core/tests/chain-v1/manifest.json',
        'work-core/tests/scene-chain-v1/manifest.json',
        'work-core/tests/scene-cull-oracle-v1/manifest.json',
        'work-app/producer-v1/result.json')]
    receipts += [p.resolve() for p in (args.stream_receipt, args.control_receipt) if p]
    verified = []
    for receipt_path in receipts:
        data = read(receipt_path)
        assert data.get('passed') is True or data.get('status', '').startswith('PASS')
        checks = []
        for key in ('sources', 'inputs'):
            for item in data.get(key, []):
                if isinstance(item, dict) and 'path' in item and 'sha256' in item:
                    checks.append((Path(item['path']), item['sha256']))
        checks += [(Path(p), h) for p, h in data.get('inputs_sha256', {}).items()]
        checks += [(receipt_path.parent / p, h) for p, h in data.get('outputs_sha256', {}).items()]
        if 'test_sha256' in data:
            tests = [Path(x) for x in data['build_command'] if x.endswith('.cpp')]
            assert len(tests) == 1
            checks.append((tests[0], data['test_sha256']))
        for path, expected in checks:
            assert sha(path) == expected, str(path)
        verified.append({'path': str(receipt_path), 'sha256': sha(receipt_path),
                         'recorded_input_output_checks': len(checks)})

    equalities = []
    for rel in ('run/task-raw-vertices.tsv', 'tasks-classified.json'):
        old = M / 'review/native-task-v2' / rel
        new = M / 'review/native-task-v3' / rel
        assert old.read_bytes() == new.read_bytes()
        equalities.append({'old': str(old), 'new': str(new), 'sha256': sha(new)})

    vector_path = M / 'work-app/native-vectors-v3/vectors.json'
    vectors = read(vector_path)['vectors']
    oracle_dir = M / 'work-core/tests/scene-cull-oracle-v1'
    inputs = read(oracle_dir / 'inputs.json')
    outputs = list(struct.iter_unpack('<11I', (oracle_dir / 'outputs.bin').read_bytes()))
    assert len(inputs['rows']) == len(inputs['mapping']) == len(outputs)
    oracle = {(entry['task'], entry['triangle']): (row, out)
              for entry, row, out in zip(inputs['mapping'], inputs['rows'], outputs)}
    assert len(oracle) == len(outputs)
    totals = collections.Counter()
    phases = collections.defaultdict(collections.Counter)
    for task in vectors:
        totals['tasks'] += 1
        totals['raw_words_compared'] += len(task['raw_expected_u32']) * 8
        totals['triangles'] += len(task['triangles'])
        failure = 0
        kept = []
        used = set()
        for index, slots in enumerate(task['triangles']):
            vertices = [task['raw_expected_u32'][s-task['destination']] for s in slots]
            operands, result = oracle[(task['native_task'], index)]
            assert [n for v in vertices for n in (v[0], v[1], v[4])] == operands[:9]
            assert [v[7] for v in vertices] == operands[10:13]
            assert task['cull_mode'] == operands[13]
            assert result[9] in (0, 1, 2)
            if result[9]:
                continue
            w = [v[3] for v in vertices]
            reason = 0
            if any(v[7] for v in vertices):
                reason = 8
            elif any(x < 0x00800000 or x >= 0x46fffe00 for x in w) or w[0] == w[1] == w[2]:
                reason = 9
            if reason:
                failure = failure or reason
            else:
                kept.append(slots)
                used.update(slots)
        phases[str(task['phase'])][str(failure)] += 1
        if failure:
            totals['fallback_tasks'] += 1
        else:
            totals['accepted_geometry_tasks'] += 1
            totals['kept_triangles'] += len(kept)
            totals['packet_words_compared'] += 9*len(used) + 2*len(kept)
    scene = read(M / 'work-core/tests/scene-chain-v1/manifest.json')
    assert all(scene['result'][key] == value for key, value in totals.items())
    if args.stream_receipt:
        stream = read(args.stream_receipt)
        assert all(stream['result'][key] == value for key, value in totals.items())

    report = {
        'status': 'PASS_INDEPENDENT_RECEIPTS_AND_GEOMETRY_ACCOUNTING',
        'reviewer_script_sha256': sha(__file__),
        'receipt_checks': verified,
        'historical_oracle_v2_v3_equalities': equalities,
        'scene_totals_independently_recomputed': dict(totals),
        'per_phase_geometry_outcomes': {k: dict(v) for k, v in phases.items()},
        'producer_review': [
            'The host producer enforces hardware count/index limits, immutable task/context keys, bounded credits, ordered completion and drain-before-generation recycle.',
            'Host allocations and detached source bytes do not prove hardware DMA leases, RV32 submission cost or a running application path.',
            'Sequence/handle exhaustion and actual PSRAM address/cache/publication binding remain integration obligations.'
        ],
        'stream_review': [
            'Final source must ignore ordinary cancel after accepted packet launch, block repeat publication and automatically own raw/list ports during packet consumption.',
            'Command prefetch must drain before fallback exposure or ACK; accepted raw DMA remains owned until raw completion.',
            'Packet error is an explicit zero-output repair outcome. Reset after publication requires coupled packet-destination and AXI-responder reset.'
        ],
        'claim_limits': [
            '488 geometry-accepted tasks do not establish real material/shader eligibility; packet comparison uses controlled material state.',
            'The two prepared intervals and component tests establish no new full-frame gain, physical fit, clock rate or hardware deployment.',
            'Only synthetic component tests are portable without private fixtures; source-bound scene/oracle/producer receipts require their external recorded inputs.'
        ],
        'inputs_sha256': {str(p): sha(p) for p in (vector_path, oracle_dir/'inputs.json', oracle_dir/'outputs.bin')}
    }
    args.out.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'scene': dict(totals),
                      'receipt_checks': sum(x['recorded_input_output_checks'] for x in verified)}, indent=2))

if __name__ == '__main__':
    main()
