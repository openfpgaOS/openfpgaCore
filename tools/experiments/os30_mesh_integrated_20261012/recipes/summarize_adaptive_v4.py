#!/usr/bin/env python3
"""Bind completed frame comparisons; never infer a gain by subtracting stalls."""
import collections
import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
I = HERE.parent
bindings = {}


def bind(path):
    path = path.resolve()
    bindings[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return path


def read(path):
    return json.loads(bind(path).read_text())


def functions(phase):
    return {r['name']: r['gap_cycles'] for r in phase['cpu']['functions']}


def credit(run, paired):
    path = run / 'mesh-events.tsv'
    rows = [{k: int(v) for k, v in r.items()}
            for r in csv.DictReader(bind(path).open(), delimiter='\t')]
    output = {}
    for phase, data in paired['candidate']['phases'].items():
        start, end = data['entry_cycle'], data['next_entry_cycle']
        counts, tasks = collections.Counter(), collections.Counter()
        for index, row in enumerate(rows):
            lo = max(start, row['cycle'])
            hi = min(end, rows[index + 1]['cycle'] if index + 1 < len(rows) else end)
            if hi <= lo:
                continue
            waiting = (row['owner'] and not row['legacy'] and row['arm_pending']
                       and row['packet_state'] == 10 and not row['publication_started'])
            if not waiting:
                continue
            if row['fill_active']:
                label = ('own_fill_header_or_alias_drain' if row['fill_serial'] == row['serial']
                         else 'prior_fill_incomplete')
            else:
                used = (row['committed_ptr'] - row['read_ptr']) & 1023
                need = 5 + 9 * row['used'].bit_count() + 2 * row['live']
                assert 0 <= used <= 512 and 5 <= need <= 421
                label = 'exact_credit_block' if 512 - used < need else 'reservation_admission_cycle'
            duration = hi - lo
            counts[label] += duration
            if label == 'exact_credit_block':
                tasks[row['serial']] += duration
                if row['gpu_state'] == 6:
                    counts['exact_credit_block_and_gpu_fragment'] += duration
                if row['bridge_state'] == 2:
                    counts['exact_credit_block_and_prior_ordinary_copy'] += duration
                if row['bridge_state'] == 5:
                    counts['exact_credit_block_and_prior_generated_stream'] += duration
        output[phase] = {
            'cycles': dict(counts),
            'blocked_tasks': len(tasks),
            'largest_task_waits': [{'serial': k, 'cycles': v} for k, v in tasks.most_common(10)],
        }
    return output


def main():
    names = {
        'forced_appv2_512': 'paired-v7.json',
        'batched_appv3_512': 'paired-v7-appv3.json',
        'adaptive128_appv4_512': 'paired-v7-appv4.json',
        'capacity_ceiling_appv2_16384': 'paired-v9.json',
    }
    pairs = {label: read(HERE / name) for label, name in names.items()}
    comparisons = {}
    for label, pair in pairs.items():
        assert pair['candidate']['elf_sha256'] == pair['control']['elf_sha256']
        control = [pair['control']['phases'][p]['elapsed_cycles'] for p in ('1', '2')]
        candidate = [pair['candidate']['phases'][p]['elapsed_cycles'] for p in ('1', '2')]
        comparisons[label] = {
            'elf_sha256': pair['candidate']['elf_sha256'],
            'model': pair['candidate']['model'],
            'control_intervals': control,
            'candidate_intervals': candidate,
            'control_total': sum(control),
            'candidate_total': sum(candidate),
            'same_elf_total_cycle_change_percent': 100 * (sum(candidate) / sum(control) - 1),
            'same_elf_warm_cycle_change_percent': 100 * (candidate[1] / control[1] - 1),
            'warm_retired': pair['candidate']['phases']['2']['cpu']['retired'],
            'warm_source_wait_gap_cycles': functions(pair['candidate']['phases']['2'])['app:gfx_gpu_mesh_wait_source'],
            'warm_cache_flush_entries': pair['candidate']['phases']['2']['gpu_metrics']['cache_flush_entries'],
            'warm_idle_cache_drain_cycles': pair['candidate']['phases']['2']['gpu_partitions']['idle_cache_drain']['cycles'],
            'warm_arm_stage_cycles': pair['candidate']['mesh']['2']['stage_cycles']['mesh_arm_wait_credit_or_reservation'],
        }
    ceiling = read(HERE / 'ceiling-comparison-v9.json')
    prior = ceiling['prior_qualified_app']
    for data in comparisons.values():
        data['different_elf_prior_qualified_total_change_percent'] = 100 * (data['candidate_total'] / sum(prior) - 1)
    current = pairs['adaptive128_appv4_512']
    pressure = credit(I / 'validation/candidate-v7-appv4/run', current)
    publication = read(HERE / 'ingress-publish-appv4.json')
    zero_credit = read(HERE / 'queue-credit-v9.json')
    old_pressure = read(HERE / 'queue-credit-v7.json')
    checks = {}
    required = {
        'control_v4': I / 'validation/control-v4/check-independent.json',
        'candidate_appv4': I / 'validation/candidate-v7-appv4/check-independent-v2.json',
        'candidate_appv3': I / 'validation/candidate-v7-appv3/check-independent-v2.json',
        'candidate_appv2': I / 'validation/candidate-v7/check-independent.json',
        'capacity_ceiling': I / 'validation/candidate-v9/check-independent.json',
    }
    for name, path in required.items():
        receipt = read(path)
        assert receipt['status'].startswith('PASS_'), (path, receipt.get('status'))
        checks[name] = {'path': str(path.resolve()), 'status': receipt['status']}
    warm_ceiling = pairs['capacity_ceiling_appv2_16384']['candidate']['phases']['2']
    selected_names = [
        'app:load_alias_vertex', 'app:gfx_run_dl', 'app:gfx_mesh_finish',
        'app:gfx_gpu_mesh_sources', 'app:gfx_gpu_mesh_packet', 'app:gfx_cpu_indexed_run',
        'app:load_alias_copy', 'app:gfx_clip_triangle.constprop.0',
        'app:gfx_push_triangle.constprop.0.isra.0', 'app:gfx_gpu_draw_direct_records',
        'app:gpu_project_clip', 'app:gfx_gpu_mesh_wait_source',
    ]
    remaining = {name: functions(warm_ceiling)[name] for name in selected_names}
    report = {
        'schema': 1,
        'decision': 'RETAIN_QUALIFIED_BASELINE_NO_PHYSICAL_FIT_OR_DEPLOYMENT',
        'scope': 'Two complete drained native frame intervals of this captured workload; no device timing claim.',
        'comparisons': comparisons,
        'prior_qualified_app_intervals': prior,
        'adaptive128_queue_pressure': pressure,
        'publication_report': str((HERE / 'ingress-publish-appv4.json').resolve()),
        'zero_credit_ceiling_report': str((HERE / 'queue-credit-v9.json').resolve()),
        'ceiling_warm_remaining_retirement_gap_cycles': remaining,
        'ceiling_warm_hardware_stages': pairs['capacity_ceiling_appv2_16384']['candidate']['mesh']['2']['stage_cycles'],
        'strict_checks': checks,
        'interpretation': [
            'Adaptive128 is slower than its same-ELF control and both earlier 512-word publication policies in actual total elapsed cycles.',
            'Small-queue capacity couples the CPU next-source poll to prior raster/command consumption. Exact queue-credit intersections establish this without treating every queue residence cycle as a CPU stall.',
            'Offload reduces CPU quantization, canonical copy and vertex-cache packet emission, but synchronous LOAD/preflight, task setup/finish, display-list/material work and fallback clipping remain.',
            'The large-queue run removes all capacity waits in this trace. Its remaining validation pass is small; retaining material and fallback ordering still requires CPU task handoffs.',
            'The 16K diagnostic measures a capacity ceiling for this implementation and trace, not a universal accelerator speed limit. It is not physically qualified and exceeds available RAM as tested.',
            'Whole-frame performance, not retired-instruction reductions or summed overlapping hardware counters, determines the recommendation.',
            'This direct-output-queue design is not justified for deployment. A separate future experiment would need to target synchronous per-task input/context setup and fallback software; a coarse asynchronous job design has not been shown to win.',
        ],
        'caveats': [
            'Per-PC gap cycles charge the preceding retirement gap to the next instruction; they are not isolated self-times or guaranteed removable cost.',
            'Busy, queue residency and GPU/CPU overlap counters are not additive savings.',
            'Different-app comparisons include code-layout, driver and fallback-path overhead. Only each same-ELF pair isolates the hardware capability.',
            'A large part of GPU idle time occurs after rendering during game/audio work; it is not all starvation caused by triangle submission.',
        ],
    }
    text_lines = [
        'Mesh integration: final measured publication-policy decision',
        '',
        'Retain the qualified baseline. No physical fit or deployment is justified by this direct-output-queue design.',
        'All rows below are complete native frame intervals with the GPU drained at both boundaries; positive change means slower.',
        '',
        'Policy / private words | cold candidate | warm candidate | total candidate | total same-ELF control | cycle change',
    ]
    for name, row in comparisons.items():
        cold, warm = row['candidate_intervals']
        text_lines.append(f"{name} | {cold:,} | {warm:,} | {row['candidate_total']:,} | {row['control_total']:,} | {row['same_elf_total_cycle_change_percent']:+.3f}%")
    text_lines += [
        '',
        f'Prior qualified app/core: {prior[0]:,} cold + {prior[1]:,} warm = {sum(prior):,} cycles.',
        'Different-ELF comparisons include driver/code-layout overhead and are practical comparisons, not isolated hardware A/B tests.',
        'Adaptive128 totals 46,090 more cycles than forced appv2 and 22,722 more than batched appv3 on the same 512-word hardware.',
        '',
        'Why direct BRAM transport did not produce the expected gain:',
        f"1. The small packet queue makes the next raw LOAD wait behind earlier raster work. Adaptive128 has {pressure['2']['cycles'].get('exact_credit_block', 0):,} exact warm credit-blocked cycles; {pressure['2']['cycles'].get('exact_credit_block_and_gpu_fragment', 0):,} coincide with the GPU fragment stage. Its CPU source-wait function is charged 552,085 cycles versus 3,738 in the control. These measurements overlap and must not be summed.",
        '2. Geometry offload does remove substantial CPU work. In the zero-credit diagnostic, warm indexed preparation falls 581,381 to 153,169 charged cycles, vertex-cache packet batching 189,914 to 38,814, and canonical readback 192,831 to 80,033.',
        '3. The remaining synchronous task handoffs are costly: LOAD/preflight is charged 472,290 warm cycles versus 283,505 control; task finish adds 98,307 and source bookkeeping 81,001. Display-list traversal is still 322,968, with CPU clipping 144,096 and fallback projection/direct draws still present. These are per-PC retirement-gap attributions, not guaranteed removable self-times.',
        '4. Numeric validation itself takes only 26,652 hardware cycles in that warm interval. Removing it alone cannot explain or repair the much larger overhead. Material preparation and fallback ordering still constrain when the CPU can advance.',
        '5. The 16,384-word diagnostic removes every measured capacity stall and improves these two intervals by 4.376% against its same-ELF control, or 3.341% against the prior qualified app. This is the measured capacity ceiling of this implementation on this trace; it is not a universal accelerator limit. That diagnostic exceeds physical memory resources as built and has no fit/timing qualification.',
        '',
        'The next architecture, if investigated separately, should target synchronous per-task input/context setup and fallback software. A coarse asynchronous job design would need its own full-frame proof; these results do not establish that it will succeed.',
        'Queue residence and CPU/GPU busy counters overlap. GPU idle after rendering includes game/audio work. No projected FPS or gain is obtained by subtracting these counters.',
        '',
        'Strict functional, ordered draw, lifetime and source checks pass for the bound candidate/control runs. Sources, ELF, fixtures and exact receipts are bound by adaptive-v4-conclusion.json and its paired analysis inputs.',
    ]
    text_out = HERE / 'adaptive-v4-conclusion.txt'
    assert not text_out.exists()
    text_out.write_text('\n'.join(text_lines) + '\n')
    bind(text_out)
    bind(Path(__file__))
    report['inputs_sha256'] = bindings
    out = HERE / 'adaptive-v4-conclusion.json'
    assert not out.exists()
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'output': str(out), 'comparisons': comparisons, 'adaptive128_credit': pressure}, indent=2))


if __name__ == '__main__':
    main()
