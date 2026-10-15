#!/usr/bin/env python3
"""Strict same-ELF native AJ02 A/B checks; no synthesized timing credits."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

V = Path(__file__).resolve().parent
P = V.parent.parent
Q = P/'queued-front-v1'
spec = importlib.util.spec_from_file_location('queued_reference', Q/'check_replays.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
ref = prior.ref
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())


def mesh_preparation(run, cfg, fixture):
    initial = (fixture/'capture/run-reference/snapshot/sdram.bin').read_bytes()
    address = int(cfg['mesh_probe_address'], 0)
    before = struct.unpack_from('<I', initial, address-0x10000000)[0]
    assert before in (0, 2), 'Functional reference must not advertise MTF1'
    expected = {'probe_address': address, 'probe_before': before, 'probe_after': 0}
    assert read(run/'prepared-mesh.json') == expected
    return expected


def async_preparation(run, cfg, fixture):
    initial = (fixture/'capture/run-reference/snapshot/sdram.bin').read_bytes()
    address = int(cfg['async_probe_address'], 0)
    before = struct.unpack_from('<I', initial, address-0x10000000)[0]
    assert before in (0, 2), 'Functional reference must not advertise AJ02'
    expected = {'probe_address': address, 'probe_before': before, 'probe_after': 0}
    assert read(run/'prepared-async.json') == expected
    return expected


def same_elf_pair(run, baseline, fixture):
    a = read(run.parent/'build.json')
    b = read(baseline.parent/'build.json')
    manifest = read(fixture/'prepared.json')
    assert sha(Path(a['elf'])) == sha(Path(b['elf'])) == manifest['elf_sha256']
    assert (run/'input.cfg').read_bytes() == (baseline/'input.cfg').read_bytes()
    for name in ('prepared-memory.json', 'prepared-front.json', 'prepared-mesh.json', 'prepared-async.json'):
        assert (run/name).read_bytes() == (baseline/name).read_bytes(), name
    return {'elf_sha256': manifest['elf_sha256'], 'identical_prepared_inputs': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=('baseline', 'async'))
    parser.add_argument('run', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--hook', type=Path,
                        help='Independent task lifetime/MMIO checker for final integrated ABI')
    args = parser.parse_args()
    assert not args.report.exists(), 'Preserve prior correctness evidence'
    run = args.run.resolve()
    fixture = args.fixture.resolve()
    cfg = dict(line.split('=', 1) for line in (fixture/'input.cfg').read_text().splitlines() if '=' in line)
    ref.N = fixture
    ref.CFG = cfg
    ref.prior.health = prior.health
    result, ingress, golden = ref.validate(run)
    result['front_preparation'] = ref.front_preparation(run)
    result['mesh_preparation'] = mesh_preparation(run, cfg, fixture)
    result['async_preparation'] = async_preparation(run, cfg, fixture)
    if args.kind == 'baseline':
        assert [f['commands'] for f in ingress] == [f['commands'] for f in golden]
        result['status'] = 'PASS_SAME_ELF_CAPABILITY_OFF_NATIVE_ORACLE'
    else:
        assert args.baseline and args.hook, 'Candidate requires baseline and bound task lifetime checker'
        baseline = args.baseline.resolve()
        result['pair'] = same_elf_pair(run, baseline, fixture)
        ref.prior.bind(baseline)
        original, _ = ref.commands(baseline/'commands.bin')
        output, _ = ref.commands(run/'worker-commands.bin')
        original_semantics, _ = prior.resolved_semantics(original)
        candidate_semantics, detail = prior.resolved_semantics(output)
        assert candidate_semantics == original_semantics, 'Draw payload/state/order or other side effect mismatch'
        result['semantics'] = {'indexed_draws': detail['indexed_draws'],
                               'all_effective_draw_payloads_and_state_exact': True}
        hook_spec = importlib.util.spec_from_file_location('mesh_lifetime_check', args.hook.resolve())
        hook = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(hook)
        result['async_lifetime'] = hook.check(run, baseline, fixture, ingress, output)
        abi=result['async_lifetime'].get('abi','AJ02')
        assert abi == 'AJ02'
        result['status'] = 'PASS_'+abi+'_SAME_ELF_FULL_NATIVE_FUNCTIONAL_AND_ORDERED_SEMANTICS'
    result['fixture'] = str(fixture)
    result['fixture_manifest_sha256'] = sha(fixture/'prepared.json')
    result['evidence_sha256'] = {str(p): sha(p) for p in run.iterdir() if p.is_file()}
    sources = [Path(__file__).resolve(), Q/'check_replays.py',
               P/'triangle-front-v1/tests/check_replays.py', ref.OLD]
    if args.hook:
        sources.append(args.hook.resolve())
    result['checker_sources_sha256'] = {str(p): sha(p) for p in sources}
    result['normalization'] = (
        'Only known framebuffer bases/flip indices, the one prepared zero-clear, '
        'known s5 timestamp at later frame entries and redundant sticky material/vertex preparation. '
        'Every actual indexed draw resolves exact vertex payload and material/object state; '
        'other side effects retain exact order. Registers/FCSR otherwise remain strict.')
    result['limitations'] = [
        'Two prepared, GPU-drained full game/render/audio intervals on actual native CPU and raster RTL.',
        'Fixed synthetic display/audio arrivals and bounded service implementations; not sustained Pocket FPS or full OS/PCM/interrupt qualification.',
        'No ideal producer, instruction-cost subtraction or assumed overlap is credited as a frame-time gain.',
        'Physical area, timing and hardware behavior require separate qualification after measured benefit.'
    ]
    args.report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'evidence_sha256'}, indent=2))


if __name__ == '__main__':
    main()
