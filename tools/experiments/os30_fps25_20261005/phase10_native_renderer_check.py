#!/usr/bin/env python3
"""Read-only input checks and new evidence for the first native renderer call."""
import bisect
import collections
import csv
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'native-renderer-check-v1'
WINDOW = P / 'native-renderer-window-v1'
RUN = WINDOW / 'run-v2'
MODEL = WINDOW / 'model-v3'
GUEST = WINDOW / 'guest-v2'
CAPTURE = P / 'native-renderer-capture-v1'
SNAP = CAPTURE / 'run-reference/snapshot'
ELF = P / 'renderer-pgo-delivery-v7/payload/Assets/sm64/common/sm64.elf'
EXPECTED_ELF = 'fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text())


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def check_table(base, table):
    for rel, digest in table.items():
        assert sha(base / rel) == digest, str(base / rel)
    return len(table)


def main():
    assert not OUT.exists()
    assert sha(ELF) == EXPECTED_ELF
    counts = {}
    counts['capture'] = check_table(CAPTURE, read(CAPTURE / 'manifest.json')['files'])
    counts['model'] = check_table(MODEL, read(MODEL / 'prepared.json')['source_sha256'])
    counts['guest'] = check_table(GUEST, read(GUEST / 'prepared.json')['files'])
    selected = read(P / 'compact-lod5-core-v7/prepared.json')['source_sha256']
    counts['selected_core_sources'] = check_table(MODEL / 'frozen', selected)
    assert read(MODEL / 'build.json')['returncode'] == 0
    assert read(RUN / 'process.json')['returncode'] == 0
    stats = read(RUN / 'result.json')
    assert stats['status'] == 0 and stats['fence'] == 362
    assert all(stats[k] == 0 for k in ['write_errors', 'scan_errors', 'cram_errors'])
    assert all(r['errors'] == 0 for r in stats['readers'].values())
    assert all(stats['readers'][k]['pending'] == 0 for k in ['I', 'MEM', 'PER', 'M1'])
    # Synthetic AUDIO may remain in flight after renderer completion; it is not
    # a CPU or GPU result owner and is explicitly reported separately.
    state = struct.unpack('<77I', (SNAP / 'cpu-state.bin').read_bytes())
    entry = struct.unpack('<66I', (RUN / 'entry-regs.bin').read_bytes())
    assert entry == state[:66]
    assert state[67:69] == (0, 0) and state[74:76] == (0, 0)
    commands = (RUN / 'commands.bin').read_bytes()
    assert commands == (SNAP / 'commands.bin').read_bytes()
    assert len(commands) == 23036 * 4 == stats['upload_words'] * 4
    frame = (RUN / 'frame.bin').read_bytes()
    assert frame == (SNAP.parent / 'frame_00360.bin').read_bytes()
    assert len(frame) == 307200
    snapshot = read(SNAP / 'state.json')
    assert snapshot['frame'] == 360 and snapshot['fence'] == 361
    assert snapshot['ring_byte_pointer'] == 5868
    assert [snapshot['globals'][x]['value'] for x in
            ['load_alias_present', 'load_alias_dirty', 'load_alias_controls_valid']] == [0, 3, 0]
    services = list(csv.DictReader((SNAP / 'services.csv').open()))
    renderer_services = [r for r in services if int(r['phase']) in (1, 3)]
    assert [(int(r['kind']), int(r['index'])) for r in renderer_services] == [(0, 12), (0, 13)]
    assert [(int(r['a0']), int(r['a1']), int(r['r0'])) for r in renderer_services] == [
        (1, 361, 0), (0, 17232, 0x10000000)]
    # The selected arbiter's explicit parameters matter: its default WQ is 8.
    core_top = (P / 'compact-lod5-core-v7/frozen/src/fpga/targets/pocket/core_top.v').read_text()
    tb_path = MODEL / 'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/tb_irq.v'
    tb = tb_path.read_text()
    for module, inst in [('axi_sdram_arbiter', 'sdram_arb'), ('gpu_core', 'gpu'), ('axi_periph_slave', 'periph')]:
        begin = core_top.index(module + ' #(\n')
        end = core_top.index(') ' + inst + ' (', begin) + len(') ' + inst + ' (')
        assert core_top[begin:end] in tb, (module, inst)
    assert 'INCLUDE_GPU_WQ16' in read(MODEL / 'prepared.json')['defs']
    assert '.REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)' in tb
    # Aggregate retired PCs by bounded function symbols, including the private
    # bridge. Time is the gap BEFORE retirement, not exclusive source cost.
    syms = []
    for elf in [ELF, GUEST / 'overlay.elf']:
        text = subprocess.check_output(['riscv64-elf-nm', '-S', '--defined-only', str(elf)], text=True)
        for line in text.splitlines():
            f = line.split()
            if len(f) == 4 and f[2] in 'tT' and int(f[1], 16):
                syms.append((int(f[0], 16), int(f[1], 16), f[3]))
    syms.sort()
    starts = [s[0] for s in syms]
    agg = collections.defaultdict(lambda: [0, 0])
    pc_rows = []
    for row in csv.DictReader((RUN / 'pc.csv').open()):
        pc = int(row['pc'])
        idx = bisect.bisect_right(starts, pc) - 1
        symbol = syms[idx][2] if idx >= 0 and pc < syms[idx][0] + syms[idx][1] else 'private_or_unsized'
        retired, gap = int(row['retired']), int(row['cycles_since_previous_retirement'])
        agg[symbol][0] += retired
        agg[symbol][1] += gap
        pc_rows.append(dict(pc=pc, symbol=symbol, retired=retired, retirement_gap_cycles=gap))
    assert sum(v[0] for v in agg.values()) == stats['retired']
    call_cycles = stats['finished_cycle'] - stats['entry_cycle']
    gap_cycles = sum(v[1] for v in agg.values())
    assert call_cycles == 2924576 and gap_cycles - call_cycles == 39
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    profile = [dict(symbol=k, retired=v[0], retirement_gap_cycles=v[1],
                    ms_at_100MHz=v[1] / 100000) for k, v in sorted(agg.items(), key=lambda item: -item[1][1])]
    save(OUT / 'profile.json', dict(symbols=profile, pcs=pc_rows,
        attribution='Cycles since previous retirement charged to the retiring PC; includes dependency/cache/bus stalls, cross-function boundary gaps and guest bridge. Not exclusive function timing.',
        total_retired=stats['retired'], total_gap_cycles=gap_cycles, call_window_cycles=call_cycles,
        boundary_delta_cycles=39))
    result = dict(passed=True, input_hash_checks=counts,
        selected_elf_sha256=EXPECTED_ELF, exact_entry_words=66,
        entry_scope='x0..31, raw f0..31, gfx_run PC and FCSR. Private mtvec/mepc/privilege bootstrap intentionally differs; captured interrupts/reservation are disabled.',
        exact_command_words=len(commands)//4, exact_color_bytes=153600, exact_depth_bytes=153600,
        native_renderer_cycles=call_cycles, native_renderer_ms_at_100MHz=call_cycles/100000,
        gpu_drain_cycles=stats['complete_cycle']-stats['finished_cycle'],
        until_drained_cycles=stats['complete_cycle']-stats['entry_cycle'],
        retired=stats['retired'], freeze_cycles=stats['freeze_cycles'], hazard_cycles=stats['hazard_cycles'],
        gpu_busy_cycles=stats['gpu_busy_cycles'], raw_counters=stats,
        limits=[
            'One captured busy frame360 gfx_run plus gfx_end_frame, cold CPU/GPU caches except guest restore footprint. Not a full native frame, warm frame rate or presentation measurement.',
            'Guest bridge emulates only captured video service calls; inline guest cache/fence instructions execute on real CPU RTL. No host cache edits after CPU release.',
            'Physical memory pins use behavioral SDRAM/CRAM models. Synthetic fixed-address scan/audio traffic is retained, not native display/audio logic or physical hardware validation.',
            'Configured 100MHz cycle conversion is informational; this selected core has not qualified 100MHz timing.',
            'Freeze/hazard/GPU-busy overlap and must not be summed. Reader statistics span CPU release through DONE rather than exactly the renderer entry/finish window.',
            'No exact I/D miss counter claimed from linefill counts; root is preparing finer passive instrumentation.',
            'Guest v1 unsupported-CSR preparation and run-v1 alias-observer failure remain preserved and are not qualification runs.'
        ])
    save(OUT / 'result.json', result)
    report = [
        'Native renderer milestone: PASS\n',
        'All 66 entry register/PC/FCSR words, 23,036 ordered command words, and full 153,600-byte color plus 153,600-byte depth buffers match the frozen qualified PGO snapshot/reference.\n',
        f'gfx_run entry through original gfx_end_frame return: {call_cycles:,} cycles, 29.24576 ms at configured 100MHz. Subsequent actual GPU drain: {result["gpu_drain_cycles"]:,} cycles, 0.21385 ms. Total through drain: 29.45961 ms. Retired instructions: 1,394,955.\n',
        'The call includes the bounded guest return trap and captured video services. Game logic, audio execution, warm cache history, vblank/presentation pacing and whole-frame FPS are not measured. Memory models and synthetic scan/audio arbitration traffic remain fixture assumptions.\n',
        'PC attribution uses the preceding retirement gap, not an exclusive function profiler; its total is 39 cycles longer than the explicit call window because of the boundary convention. Top symbols:\n',
    ]
    report += [f'  {x["symbol"]}: {x["retirement_gap_cycles"]:,} cycles ({x["ms_at_100MHz"]:.5f} ms), {x["retired"]:,} instructions\n' for x in profile[:15]]
    report += ['\nNo new simple optimization is established by these aggregate counts alone. Indexed rejection/preparation dominates; root\'s passive per-PC freeze/hazard/bus attribution is needed before selecting a change. Previously rejected duplicate-preparation, PGO-cache-size and copy-layout changes are not revived by this single sample.\n']
    (OUT / 'REPORT.txt').write_text(''.join(report))
    evidence = {}
    def bind(path):
        evidence[str(path.relative_to(ROOT))] = sha(path)
    for directory in [RUN, GUEST, WINDOW/'runner-v2']:
        for path in directory.rglob('*'):
            if path.is_file():
                bind(path)
    for rel in read(MODEL/'prepared.json')['source_sha256']:
        bind(MODEL/rel)
    for path in [MODEL/'prepared.json', MODEL/'build.json', MODEL/'build.log', MODEL/'recipe-prepare.py',
                 CAPTURE/'manifest.json', P/'compact-lod5-core-v7/prepared.json', ELF,
                 P/'native-renderer-services-v2/component-manifest.json',
                 P/'native-renderer-guest-review-v2/manifest.json', WINDOW/'IMPLEMENTATION.txt']:
        bind(path)
    for path in SNAP.iterdir():
        if path.is_file():
            bind(path)
    bind(SNAP.parent/'frame_00360.bin')
    save(OUT/'manifest.json', dict(passed=True,
        files={str(f.relative_to(OUT)): sha(f) for f in OUT.iterdir() if f.is_file()}, evidence_sha256=evidence))
    print(json.dumps({'stage': str(OUT), 'manifest_sha256': sha(OUT/'manifest.json'), 'passed': True}))


if __name__ == '__main__':
    main()
