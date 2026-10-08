#!/usr/bin/env python3
"""Independent read-only cold native I32/D128 versus I64/D64 comparison."""
import bisect
import collections
import csv
import json
from pathlib import Path
import shutil
import struct
import subprocess
from phase10_native_renderer_check import ROOT, P, WINDOW, ELF, EXPECTED_ELF, sha, read, save, check_table

OUT = P / 'native-renderer-cache-check-v1'
CPU = 'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'


def aggregate(run):
    syms = []
    for line in subprocess.check_output(['riscv64-elf-nm', '-S', '--defined-only', str(ELF)], text=True).splitlines():
        f = line.split()
        if len(f) == 4 and f[2] in 'tT' and int(f[1], 16):
            syms.append((int(f[0], 16), int(f[1], 16), f[3]))
    syms.sort()
    starts = [s[0] for s in syms]
    agg = collections.defaultdict(lambda: [0, 0])
    for row in csv.DictReader((run/'pc.csv').open()):
        pc = int(row['pc'])
        i = bisect.bisect_right(starts, pc) - 1
        name = syms[i][2] if i >= 0 and pc < syms[i][0]+syms[i][1] else 'private_guest'
        agg[name][0] += int(row['retired'])
        agg[name][1] += int(row['cycles_since_previous_retirement'])
    return agg


def main():
    assert not OUT.exists()
    assert sha(ELF) == EXPECTED_ELF
    baseline_check = P/'native-renderer-check-v1'
    check_table(baseline_check, read(baseline_check/'manifest.json')['files'])
    check_table(ROOT, read(baseline_check/'manifest.json')['evidence_sha256'])
    base, cand = WINDOW/'model-v3', WINDOW/'model-cache64-v1'
    bp, cp = read(base/'prepared.json'), read(cand/'prepared.json')
    assert set(bp['source_sha256']) == set(cp['source_sha256'])
    assert [k for k in bp['source_sha256'] if bp['source_sha256'][k] != cp['source_sha256'][k]] == [CPU]
    assert bp['defs'] == cp['defs']
    assert bp['selected_qsf_sha256'] == cp['selected_qsf_sha256']
    assert cp['cpu_sha256'] == 'ab3205dff8e38211a223234e237a4ee083b55ecd0a02cb915c58850a34ce322e'
    check_table(cand, cp['source_sha256'])
    assert read(cand/'build.json')['returncode'] == 0
    assert (WINDOW/'runner-v2/main.cpp').read_bytes() == (WINDOW/'runner-cache64-v1/main.cpp').read_bytes()
    assert (WINDOW/'runner-v2/input.cfg').read_bytes() == (WINDOW/'runner-cache64-v1/input.cfg').read_bytes()
    br, cr = WINDOW/'run-v2', WINDOW/'run-cache64-v1'
    b, c = read(br/'result.json'), read(cr/'result.json')
    assert read(cr/'process.json')['returncode'] == 0 and c['status'] == 0
    assert c['fence'] == b['fence'] == 362
    for name, size in [('entry-regs.bin', 66*4), ('commands.bin', 23036*4), ('frame.bin', 307200)]:
        assert (br/name).read_bytes() == (cr/name).read_bytes()
        assert (cr/name).stat().st_size == size
    assert all(c[k] == 0 for k in ['write_errors', 'scan_errors', 'cram_errors'])
    assert all(x['errors'] == 0 and x['pending'] == 0 for x in c['readers'].values())
    timing = {}
    for name, r in [('baseline_i32_d128', b), ('candidate_i64_d64', c)]:
        timing[name] = dict(renderer_cycles=r['finished_cycle']-r['entry_cycle'],
                           renderer_and_drain_cycles=r['complete_cycle']-r['entry_cycle'],
                           drain_cycles=r['complete_cycle']-r['finished_cycle'],
                           retired=r['retired'])
    bt, ct = timing.values()
    assert bt['renderer_cycles'] == 2924576 and ct['renderer_cycles'] == 2786449
    assert bt['renderer_and_drain_cycles'] == 2945961 and ct['renderer_and_drain_cycles'] == 2804826
    change = dict(renderer_cycles_saved=bt['renderer_cycles']-ct['renderer_cycles'],
                  renderer_ms_saved_at_100MHz=(bt['renderer_cycles']-ct['renderer_cycles'])/100000,
                  renderer_time_reduction_percent=100*(1-ct['renderer_cycles']/bt['renderer_cycles']),
                  renderer_and_drain_cycles_saved=bt['renderer_and_drain_cycles']-ct['renderer_and_drain_cycles'],
                  renderer_and_drain_time_reduction_percent=100*(1-ct['renderer_and_drain_cycles']/bt['renderer_and_drain_cycles']))
    a, z = aggregate(br), aggregate(cr)
    profile = [dict(symbol=k, baseline_retired=a[k][0], candidate_retired=z[k][0],
                    baseline_gap_cycles=a[k][1], candidate_gap_cycles=z[k][1],
                    gap_cycles_saved=a[k][1]-z[k][1]) for k in set(a)|set(z)]
    profile.sort(key=lambda v: -v['gap_cycles_saved'])
    analytic = read(P/'renderer-pgo-cache64-v1/result.json')
    analytic_summary = {w['window']:dict(cpu_saving_ms=w['cpu_saving_ms'],
                                      cpu_saving_percent=w['cpu_saving_percent'],
                                      presentation_saving_ms=w['presentation_saving_ms'],
                                      exact_buffers=w['exact_buffers']) for w in analytic['windows']}
    OUT.mkdir()
    shutil.copyfile(__file__, OUT/'recipe.py')
    save(OUT/'profile-delta.json', dict(attribution='Preceding retirement gaps charged to PC symbols; cross-function boundaries and overlapping stalls prevent causal/exclusive interpretation.', symbols=profile))
    result = dict(passed=True, only_changed_model_source=CPU,
                  identical_fixture_source_files=len(cp['source_sha256'])-1,
                  runner_source_exact=True, runtime_config_exact=True, elf_sha256=EXPECTED_ELF,
                  exact_entry_words=66, exact_command_words=23036, exact_frame_bytes=307200,
                  timing=timing, native_cold_change=change,
                  native_readers={'baseline': b['readers'], 'candidate': c['readers']},
                  analytic_prior=analytic_summary, deployment_ready=False,
                  limitations=[
                      'One cold native renderer call. No warm native frame, full-game timing, presentation speedup or hardware FPS claim.',
                      'The analytic warm/cold multi-frame CPU model remains slower with I64/D64 and its presentation windows are unchanged. Actual native cold-call evidence is different; neither result is rewritten.',
                      'Instruction reads 3254→1305 and data reads 3756→3811 are accepted linefill transactions, not exact retired miss instructions. Reader counters include restore and final drain.',
                      'Native retired instructions increase because poll counts are schedule dependent; output/command/entry equality is the functional comparison.',
                      'This report verifies source substitution and outcomes; it does not establish fitted resource or timing qualification.'
                  ])
    save(OUT/'result.json', result)
    text = '''Cold native renderer cache comparison: PASS

Only the CPU RTL changes: selected I32/D128 (8fca2871) versus generated I64/D64 (ab3205df). All other model sources, macro settings, runner source, runtime config, guest overlay, captured state and qualified PGO ELF are identical.
Both runs exactly match all 66 entry words, 23,036 ordered command words, 153,600 color bytes and 153,600 depth bytes.

Renderer call: 2,924,576 → 2,786,449 cycles, saving 138,127 cycles (1.38127 ms at configured 100MHz, 4.723%). Through final GPU drain: 2,945,961 → 2,804,826, saving 141,135 cycles (4.791%).
I linefill transactions: 3,254 → 1,305; D linefills: 3,756 → 3,811. Retired instructions: 1,394,955 → 1,428,403, including schedule-dependent polling. Linefill counters cover CPU release through DONE rather than only the call boundaries.

The previous analytic model remains negative: +0.205854 ms warm and +0.140213 ms cold CPU progression, identical presentation windows. The positive result here is one actual cold native renderer invocation; no warm/native full-frame or physical FPS conclusion follows.
'''
    (OUT/'REPORT.txt').write_text(text)
    evidence = {}
    def bind(f):
        evidence[str(f.relative_to(ROOT))] = sha(f)
    for directory in [cr, WINDOW/'runner-cache64-v1']:
        for f in directory.rglob('*'):
            if f.is_file(): bind(f)
    for rel in cp['source_sha256']:
        bind(cand/rel)
    for f in [cand/'prepared.json', cand/'build.json', cand/'build.log', cand/'recipe-prepare.py',
              baseline_check/'manifest.json', baseline_check/'result.json', baseline_check/'profile.json',
              P/'native-renderer-method-review-v1/manifest.json',
              P/'cpu-cache64-source-review-v1/manifest.json', P/'cpu-cache64-source-review-v1/review.json',
              P/'cpu-cache64-resource-v1/manifest.json',
              P/'renderer-pgo-cache64-v1/manifest.json', P/'renderer-pgo-cache64-v1/result.json']:
        bind(f)
    save(OUT/'manifest.json',dict(passed=True, files={f.name:sha(f) for f in OUT.iterdir() if f.is_file()}, evidence_sha256=evidence))
    print(json.dumps(dict(stage=str(OUT), manifest_sha256=sha(OUT/'manifest.json'), change=change)))


if __name__ == '__main__':
    main()
