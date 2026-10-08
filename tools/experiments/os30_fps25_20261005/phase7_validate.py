#!/usr/bin/env python3
"""Validate selected software triangle changes; modeled cost is not Pocket FPS."""
import argparse
import json
from pathlib import Path
import struct
import subprocess

from phase7_experiment import ROOT, CAMP, OUT, BASE, module, sha, write, verify_capture

HERE = Path(__file__).resolve().parent
v = module('f9_prior_validation', HERE / 'phase6_validate.py')


def padding(changes, before, current, stage):
    """Bound only the existing oversized star upload, after exact packet checks."""
    source_rel = 'src/sm64/sm64/actors/star/model.inc.c'
    source = stage / source_rel
    context = json.loads((CAMP / 'phase5/star-uv-review.json').read_text())
    note = json.loads((CAMP / 'phase5/tex1-padding-note.json').read_text())
    assert sha(source) == note['source_sha256'] == sha(CAMP / 'phase5/SM64' / source_rel)
    allowed = {entry['offset'] for entry in context['differences']}
    assert allowed == {140188, 140236, 140260}
    for entry in changes:
        assert {x['offset'] for x in entry['texture_changes']} <= allowed, entry
    details, assets = [], []
    for label, directory in [('baseline', before), ('candidate', current)]:
        elf = directory / 'sm64.elf'
        symbols, memory = v.elf_data(elf)
        found = [s for s in symbols if s[0] == 'star_seg3_texture_0302A6F0']
        assert len(found) == 1 and found[0][2] == 2048
        address = found[0][1]
        raw = memory(address, 4096)
        assets.append(raw[:2048])
        converted = bytearray()
        for i in range(0, 4096, 2):
            word = int.from_bytes(raw[i:i+2], 'big')
            green = ((((word >> 6) & 31) * 1053) >> 7) >> 2
            color = ((word & 0xf800) | (green << 5) | ((word >> 1) & 31)) or 1
            converted.extend(struct.pack('<H', color if word & 1 else 0))
        for entry in changes:
            if entry['frame'] < 360:
                assert not entry['texture_changes']
                continue
            info = v.vector((directory / 'gpuvec' / f"frame_{entry['frame']:05d}.vec").read_bytes())
            assert info['texture'][138048:138048+4096] == converted
        sites = []
        for offset in sorted(allowed):
            relative = offset - 138048
            covers = [name for name, start, size in symbols if start <= address+relative < start+size]
            assert 2048 <= relative < 4096 and covers
            assert all(name.startswith('mushroom_1up_seg3_dl_') for name in covers)
            sites.append(dict(offset=offset, symbols=covers, texel_y=relative//64))
        details.append(dict(build=label, elf_sha256=sha(elf), sites=sites,
                            upload_reconstructed_exactly=True))
    assert assets[0] == assets[1]
    return dict(passed=True, original_asset_exact=True, builds=details,
                prior_uv_context_sha256=sha(CAMP / 'phase5/star-uv-review.json'),
                prior_note_sha256=sha(CAMP / 'phase5/tex1-padding-note.json'),
                limit='Exact captured packets retain previous UV bounds; oversized upload extra rows contain linked pointers. This is not universal scene coverage.')


def vectors(label, mode):
    directory = OUT / label
    current = directory / ('capture-' + mode)
    before = OUT / 'control/capture-normal' if mode == 'normal' else CAMP / 'phase6/capture-f8'
    a = verify_capture(before, BASE[mode + '_elf_sha256'])
    b = verify_capture(current, sha(directory / ('app.elf' if mode == 'normal' else 'diagnostic.elf')))
    assert all(sha(before/n) == sha(current/n) for n in ('scene.csv', 'geometry.csv', 'culling.csv'))
    assert [r['gpu_cmd_words'] for r in a] == [r['gpu_cmd_words'] for r in b]
    paths = sorted((before / 'gpuvec').glob('*.vec'))
    assert len(paths) == 8
    changes = []
    for path in paths:
        target = current / 'gpuvec' / path.name
        old, new = v.vector(path.read_bytes()), v.vector(target.read_bytes())
        assert old['hardware'] == new['hardware'], (path.name, 'commands or publication order changed')
        assert all(old[k] == new[k] for k in ('depth', 'fence', 'uploads'))
        assert len(old['texture']) == len(new['texture'])
        diffs = [dict(offset=i, before=old['texture'][i:i+4].hex(), after=new['texture'][i:i+4].hex())
                 for i in range(0, len(old['texture']), 4) if old['texture'][i:i+4] != new['texture'][i:i+4]]
        changes.append(dict(frame=int(path.stem.split('_')[1]), baseline_sha256=sha(path),
                            candidate_sha256=sha(target), texture_changes=diffs))
    result = dict(passed=True, all_eight_command_mmio_order_exact=True,
                  all_721_command_word_counts_exact=True, vectors=changes,
                  padding_check=padding(changes, before, current, directory/'SM64'))
    if mode == 'diagnostic':
        result['snapshot_checks'] = v.snapshot_checks(current)
    write(directory / ('vectors-' + mode + '.json'), result)
    print(json.dumps(dict(passed=True, mode=mode, exact_command_streams=8)))


def perf(label):
    directory = OUT / label
    m = module('f9_perf_accounting', HERE / 'phase6_perf_tests.py')
    assert m.HARNESS.count('PERF F8 SEND') == 1
    m.HARNESS = m.HARNESS.replace('PERF F8 SEND', 'PERF F9 TRI')
    m.BASE, m.CAND = directory, directory
    m.main()


def panel(label):
    directory = OUT / label
    destination = directory / 'panel'
    destination.mkdir(exist_ok=False)
    capture = directory / 'capture-diagnostic-panel'
    verify_capture(capture, sha(directory / 'diagnostic.elf'))
    assert json.loads((capture/'inputs.json').read_text())['panel']
    snapshots = v.snapshot_checks(capture, panel=True)
    for name in ('scene.csv', 'geometry.csv', 'culling.csv'):
        assert sha(capture/name) == sha(CAMP/'phase6/capture-f8'/name)
    source, vector = capture/'gpuvec/frame_00640.vec', destination/'frame_00640.vec'
    adapter = module('f9_depth_adapter', HERE/'phase4_combined_validate.py')
    adapter.with_depth(source, vector)
    exe = v.RTL/'obj/Vtb_gpu'
    assert sha(exe) == json.loads((v.RTL/'build.json').read_text())['simulator_sha256']
    command = [str(exe), str(vector), str(destination/'frame.bin'), '+memory_traffic=2', '+tagged_tex=1']
    with (destination/'replay.log').open('x') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    from PIL import Image
    raw = (destination/'frame.bin').read_bytes()
    assert len(raw) == 307212
    pixels = struct.unpack_from('<76800H', raw)
    image = Image.new('RGB', (320, 240))
    image.putdata([(((p>>11)&31)*255//31, ((p>>5)&63)*255//63, (p&31)*255//31) for p in pixels])
    image.save(destination/'panel.png')
    write(destination/'result.json', dict(snapshot_checks=snapshots,
          capture_inputs_sha256=sha(capture/'inputs.json'), simulator_sha256=sha(exe),
          vector_sha256=sha(vector), output_sha256=sha(destination/'frame.bin'),
          image_sha256=sha(destination/'panel.png'), command=command,
          scope='F9 build marker with unchanged F8 snapshot ABI/accounting. All displayed counters simulated.'))
    print(destination/'panel.png')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['vectors', 'perf', 'panel'])
    p.add_argument('candidate')
    p.add_argument('--mode', default='normal', choices=['normal', 'diagnostic'])
    args = p.parse_args()
    if args.action == 'vectors':
        vectors(args.candidate, args.mode)
    elif args.action == 'perf':
        perf(args.candidate)
    else:
        panel(args.candidate)
