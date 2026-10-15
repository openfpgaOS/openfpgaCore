"""Independent caller-aware omitted-slot proof for the exact F28 backend.

The dispatcher still executes every state command. This proof only establishes
that a queued task's omitted canonical vertex rows cannot be consumed later.
"""
NEUTRAL = frozenset((0xd7,0xfd,0xe8,0xe7,0xd9,0xfb,0xf5,0xf2,
                     0xf3,0xf4,0xf0,0xfa,0xfc,0xf8,0xe6,0xe9))


def prove(pair, start, count, continuation, step_limit=256, stack_limit=8):
    assert 0 < count <= 32
    alive = (1 << count) - 1
    address = start
    local = []
    external_used = False
    seen = set()
    crossed = []
    path = []
    reason = 'step_limit'
    for _ in range(step_limit):
        identity = (address, tuple(local), alive, external_used)
        if identity in seen:
            reason = 'cycle'
            break
        seen.add(identity)
        if address & 7:
            reason = 'unaligned_command'
            break
        words = pair(address)
        if words is None:
            reason = 'mutable_or_unknown_command'
            break
        a, b = words
        opcode = a >> 24
        path.append((address, a, b, alive))
        if opcode == 1:
            n, end = (a >> 12) & 255, (a >> 1) & 127
            if not 0 < n <= end <= 32:
                reason = 'invalid_VTX'
                break
            alive &= ~(((1 << n) - 1) << (end - n))
            if not alive:
                return {'proved': True, 'reason': 'overwritten', 'steps': len(path),
                        'external_return': external_used, 'neutral_opcodes': crossed,
                        'continuation': continuation, 'path': path}
        elif opcode in (5, 6):
            for w in ([a, b] if opcode == 6 else [a]):
                for shift in (16, 8, 0):
                    index = ((w >> shift) & 255) // 2
                    if index >= 32 or alive & (1 << index):
                        return {'proved': False, 'reason': 'live_TRI_read'}
        elif opcode in NEUTRAL:
            crossed.append(opcode)
        elif opcode == 0xde:
            if pair(b) is None:
                reason = 'mutable_or_unknown_DL_target'
                break
            if not (a & 0x10000):
                if len(local) == stack_limit:
                    reason = 'local_stack_limit'
                    break
                local.append(address + 8)
            address = b
            continue
        elif opcode == 0xdf:
            if local:
                address = local.pop()
            elif not external_used and continuation:
                address = continuation
                external_used = True
            else:
                reason = 'missing_or_second_external_return'
                break
            continue
        else:
            reason = 'barrier_0x%02x' % opcode
            break
        address += 8
    return {'proved': False, 'reason': reason}


def audit(run, calls, binding, immutable, candidate):
    """Rebuild invocation/return stack from passive retired-PC witnesses.

    The observed app continuation is checked against this independent stack,
    never used as the authority for a queued job's liveness proof.
    """
    import csv
    import json
    from collections import Counter
    from pathlib import Path
    run = Path(run)
    with (run/'dl-events.tsv').open() as f:
        events = [{k: int(v, 0) for k, v in r.items()}
                  for r in csv.DictReader(f, delimiter='\t')]
    summary = json.loads((run/'dl-observer.json').read_text())
    assert summary['schema'] == binding['schema'] == 'f28-display-list-observer-v1'
    assert not summary['extra_fabric_edges']
    assert all(summary[k] == 0 for k in ('parent_read_failures', 'command_read_failures', 'app_read_failures'))
    assert len(calls) == summary['vtx_calls']
    assert events and all(a['cycle'] < b['cycle'] for a, b in zip(events, events[1:]))
    assert binding['command_register'] == binding['parent_command_register'] == 9
    assert binding['parent_command_addend'] == -8
    import hashlib
    for path, expected in binding['inputs_sha256'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected, path
    for site in binding['reviewed_instructions']:
        assert int.from_bytes(immutable(site['pc'], site['bytes']), 'little') == site['bits']
    returns = {r['pc']: r['bits'] for r in binding['return_instructions']}
    for pc, bits in returns.items():
        size = 4 if bits & 3 == 3 else 2
        assert int.from_bytes(immutable(pc, size), 'little') == bits
        assert bits in (0x8082, 0x00008067), 'Bound site is not a return through ra'
    bits = binding['entry_instruction']
    assert int.from_bytes(immutable(binding['entry_pc'], 4 if bits & 3 == 3 else 2), 'little') == bits
    timeline = [(e['cycle'], 0, e) for e in events] + [(c['cycle'], 1, c) for c in calls]
    assert len({x[0] for x in timeline}) == len(timeline), 'Ambiguous DL/VTX event cycle'
    stack = []
    pending_return = False
    next_id = 0
    counts = Counter()
    entry_identity = []
    invocations = {}
    for cycle, is_call, r in sorted(timeline):
        assert r['phase'] in (1, 2)
        if is_call:
            assert stack and not pending_return, 'VTX outside active DL invocation'
            active = stack[-1]
            assert (r['dl_invocation'], r['dl_depth'], r['dl_continuation']) == (
                active['invocation'], len(stack), active['continuation'])
            assert r['command_valid'] == r['app_continuation_valid'] == 1
            assert not (r['command'] & 7)
            assert r['ra'] in binding['vertex_return_pcs']
            assert r['command'] == r['s1']
            a, b = r['command_w0'], r['command_w1']
            assert a >> 24 == 1
            assert ((a >> 12) & 255, ((a >> 1) & 127) - ((a >> 12) & 255), b) == (r['a0'], r['a1'], r['a2'])
            data = immutable(r['command'], 8, allow_missing=True)
            if data is not None:
                import struct
                assert (a, b) == struct.unpack('<II', data), 'VTX snapshot differs from immutable source'
            if candidate:
                assert r['app_continuation'] == active['continuation'], 'App proof context differs from actual caller'
            counts['VTX'] += 1
            continue
        kind = r['kind']
        if kind == 1:
            assert not pending_return
            next_id += 1
            assert r['invocation'] == next_id and r['depth'] == len(stack) + 1
            assert r['pc'] == binding['entry_pc'] and r['instruction'] == binding['entry_instruction']
            assert r['cmd'] == r['a0'] and r['continuation'] == r['a1']
            if stack:
                assert r['parent_invocation'] == stack[-1]['invocation']
                assert r['ra'] in binding['recursive_return_pcs']
                assert r['parent_valid'] == 1 and not (r['parent_command'] & 7)
                a, b = r['parent_w0'], r['parent_w1']
                assert a >> 24 == 0xde and not (a & 0x10000)
                assert b == r['cmd'] and r['continuation'] == r['parent_command'] + 8
                data = immutable(r['parent_command'], 8, allow_missing=True)
                if data is not None:
                    import struct
                    assert (a, b) == struct.unpack('<II', data), 'Parent DL snapshot differs from immutable source'
            else:
                assert r['parent_invocation'] == r['continuation'] == r['parent_command'] == r['parent_valid'] == 0
                assert r['ra'] in binding['root_return_pcs']
            stack.append(r)
            invocations[r['invocation']] = r
            entry_identity.append(tuple(r[k] for k in ('phase', 'invocation', 'parent_invocation', 'cmd', 'continuation', 'ra', 'parent_command', 'parent_w0', 'parent_w1')))
            counts['entries'] += 1
        else:
            assert stack
            active = stack[-1]
            assert r['depth'] == len(stack)
            assert all(r[k] == active[k] for k in ('phase', 'invocation', 'parent_invocation', 'cmd', 'continuation', 'ra'))
            if kind == 3:
                assert not pending_return and r['pc'] in returns and r['instruction'] == returns[r['pc']]
                pending_return = True
                counts['return_retirements'] += 1
            else:
                assert kind == 2 and pending_return and r['pc'] == active['ra'] and r['instruction'] == 0
                stack.pop()
                pending_return = False
                counts['return_resumes'] += 1
    assert not stack and not pending_return
    assert counts['entries'] == counts['return_retirements'] == counts['return_resumes']
    assert all(counts[k] == summary[k] for k in ('entries', 'return_retirements', 'return_resumes'))
    return {'counts': dict(counts), 'entry_identity': entry_identity,
            'invocations': invocations, 'summary': summary}
