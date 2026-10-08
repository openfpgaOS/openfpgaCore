#!/usr/bin/env python3
"""Seal the measured software-only LOAD/submission overlap, without FPS claims."""
import json
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
OUT = P / 'load-submit-coupled-v1'
APP = P / 'load-submit-overlap-v1'
FUNC = P / 'load-submit-functional-v1'


def main():
    prep = json.loads((OUT / 'prepared.json').read_text())
    for n, h in prep['source_sha256'].items():
        assert sha(OUT / n) == h, n
    assert sha(APP / 'app.elf') == prep['elf_sha256']
    assert sha(FUNC / 'manifest.json') == '58e45ea2a0ccdaa10394080539a34404f0872dc9f2f736cf7d7e7e3d60d573be'
    for mode in ('normal', 'faults', 'absent', 'timeout', 'fullpool'):
        proof = json.loads((FUNC / f'result-{mode}.json').read_text())
        assert proof['passed'] and proof['elf_sha256'] == prep['elf_sha256']
    windows = {}
    for window in ('warm', 'cold'):
        result = json.loads((OUT / f'result-{window}.json').read_text())
        assert result['passed'] and result['all8_actual_color_depth_exact'] and result['all_histories_exact']
        inv = json.loads((OUT / f'run-{window}/inputs.json').read_text())
        assert inv['success'] and sha(OUT / f'run-{window}/sm64.elf') == prep['elf_sha256']
        for n, h in inv['results_sha256'].items():
            assert sha(OUT / f'run-{window}' / n) == h, n
        windows[window] = result
    write(OUT / 'result.json', dict(passed=True, windows=windows, rtl_changed=False,
        app_elf_sha256=prep['elf_sha256'], deployment_ready=False,
        limits=['CPU instruction/dependency/cache timing and background traffic are modeled; GPU, LOAD, render cache and memories execute RTL.',
                'Eight actual color/depth frames per window. Functional721 records per mode are not721 RTL-rendered frames.',
                'Modest additional gain, not proof of the25FPS goal or a substantial standalone improvement.',
                'The unchanged hardware candidate still requires satisfactory physical timing.']))
    text = 'Software-only adjacent LOAD/submission overlap\n\n'
    for name, r in windows.items():
        text += (f"{name}: {r['timing']['baseline']['frame_ms']:.8f} -> "
                 f"{r['timing']['overlap']['frame_ms']:.8f} ms/frame; "
                 f"{r['frame_saving_ms']:.8f} ms ({r['frame_saving_percent']:.4f}%) saved. "
                 'All8 actual color/depth buffers and scene/cull histories match.\n')
    text += ('\nNo additional FPGA logic, memory, clock or ISA change. Prepared payload/jobs '
             'move from stack to one static owned buffer without copying. '
             'Only an immediately adjacent G_VTX may be crossed.\n\n'
             'Five functional modes preserve721 records and8 ordered streams each; '
             '64,175 pending batches per mode retain their bytes, order and exclusive lifetime. '
             'Zero-length G_VTX has source/emitted-code review but is absent from the game corpus.\n\n'
             'Modeled timing; no PocketFPS, >90MHz closure or25FPS achievement is claimed. '
             'No production, sibling project or SD change has been made.\n')
    with (OUT / 'REPORT.txt').open('x') as f:
        f.write(text)
    files = {str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*')
             if p.is_file() and 'build' not in p.parts and 'obj' not in p.parts}
    files['model/obj/Vtb_gpu'] = sha(OUT / 'model/obj/Vtb_gpu')
    write(OUT / 'manifest.json', dict(passed=True, deployment_ready=False, files=files,
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in (
            FUNC / 'manifest.json', APP / 'source.json', APP / 'build-normal.json',
            P / 'cache-write-lod5-coupled-v2/manifest.json', Path(__file__))}))
    print(text)
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
