#!/usr/bin/env python3
"""Private continuous original-app interval; immutable runner inputs, no FPS claim."""
import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
W = P / 'native-renderer-window-v1'
G = W / 'guest-warm-v3'
S = P / 'native-warm-capture-v4/run-reference/snapshot'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def main():
    global G, S
    parser = argparse.ArgumentParser()
    parser.add_argument('model')
    parser.add_argument('label')
    parser.add_argument('--guest', default='guest-warm-v3')
    parser.add_argument('--capture', default='native-warm-capture-v4')
    parser.add_argument('--max-cycles', type=int, default=18000000)
    args = parser.parse_args()
    assert '/' not in args.model and '/' not in args.label
    assert '/' not in args.guest and '/' not in args.capture
    G = W / args.guest
    S = P / args.capture / 'run-reference/snapshot'
    model = W / args.model
    runner = W / ('runner-' + args.label)
    run = W / ('run-' + args.label)
    prepared = json.loads((model / 'prepared.json').read_text())
    for name, digest in prepared['source_sha256'].items():
        assert sha(model / name) == digest, name
    assert json.loads((model / 'build.json').read_text())['returncode'] == 0
    guest = json.loads((G / 'prepared.json').read_text())
    for name, digest in guest['files'].items():
        assert sha(G / name) == digest, name
    runner.mkdir(exist_ok=False)
    run.mkdir(exist_ok=False)
    shutil.copyfile(R / 'tools/experiments/os30_fps25_20261005/native_interval_main.cpp', runner / 'main.cpp')
    shutil.copyfile(__file__, runner / 'recipe.py')
    overlay = json.loads((G / 'overlay.json').read_text())
    config = (G / 'run-config.txt').read_text()
    config += f'snapshot={S}\nsegments={len(overlay["segments"])}\n'
    inputs = [runner / 'main.cpp', runner / 'recipe.py', G / 'manifest.json',
              G / 'prepared.json', G / 'overlay.json', G / 'run-config.txt',
              model / 'prepared.json', model / 'build.json']
    inputs += [p for p in S.iterdir() if p.is_file()]
    inputs += list((G / 'goldens').iterdir())
    for index, segment in enumerate(overlay['segments']):
        path = G / segment['path']
        assert sha(path) == segment['sha256']
        inputs.append(path)
        config += f'segment{index}_address={segment["address"]}\nsegment{index}_path={path}\n'
    config += 'scan_period=6361\nscan_words=160\nscan_base_halfword=16777216\n'
    (runner / 'input.cfg').write_text(config)
    inputs.append(runner / 'input.cfg')
    obj = model / 'obj'
    cmd = ['g++', '-std=c++17', '-O2', '-I' + str(obj),
           '-I/usr/share/verilator/include', '-I/usr/share/verilator/include/vltstd',
           str(runner / 'main.cpp'), str(obj / 'Vtb_system__ALL.a'),
           str(obj / 'libverilated.a'), '-pthread', '-latomic', '-o', str(runner / 'native_interval')]
    inputs += [obj / 'Vtb_system__ALL.a', obj / 'libverilated.a']
    inventory = {str(p.relative_to(R)): sha(p) for p in inputs}
    save(runner / 'prepared.json', dict(inputs_sha256=inventory, build_command=cmd,
         scope='Original application from gfx entry 360 through entry 362; cold first renderer, game/audio application work, warm second renderer. Guest services use fixed reference logical time, simulated voice state, real GPU ownership, and actual CPU instructions. Synthetic scanout/audio memory pressure starts from first gfx entry. Not actual OS mixer cost, physical display behavior, or Pocket FPS.'))
    with (runner / 'build.log').open('x') as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    save(runner / 'build.json', dict(returncode=result.returncode))
    assert result.returncode == 0, runner / 'build.log'
    cmd = [str(runner / 'native_interval'), str(runner / 'input.cfg'), str(run), str(args.max_cycles)]
    save(run / 'command.json', cmd)
    start = time.monotonic()
    with (run / 'run.log').open('x') as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=model / 'frozen/src/fpga/test')
    save(run / 'process.json', dict(returncode=result.returncode, seconds=time.monotonic() - start))
    for name, digest in inventory.items():
        assert sha(R / name) == digest, name
    exact = {}
    refs = {'entry-regs.bin': 'entry-1.bin', 'entry-2.bin': 'entry-2.bin',
            'entry-3.bin': 'entry-3.bin', 'frame-1.bin': 'frame-1.bin',
            'frame-2.bin': 'frame-2.bin', 'commands.bin': 'commands.bin'}
    for actual, expected in refs.items():
        exact[actual] = (run / actual).exists() and (run / actual).read_bytes() == (G / 'goldens' / expected).read_bytes()
    frames = {}
    for boundary in (1, 2):
        expected = (G / 'goldens' / f'frame-{boundary}.bin').read_bytes()
        frames[str(boundary)] = [index for index in range(3)
            if (run / f'frame-{boundary}-buffer{index}.bin').exists()
            and (run / f'frame-{boundary}-buffer{index}.bin').read_bytes() == expected]
    save(run / 'verification.json', dict(process_returncode=result.returncode, exact=exact,
         matching_actual_buffers=frames, configured_clock_mhz=100, full_frame_FPS_claimed=False,
         note='Raw comparison is preserved. Address/role differences require explicit review; no automatic normalization.'))
    files = [p for folder in (runner, run) for p in folder.iterdir() if p.is_file()]
    save(run / 'manifest.json', dict(model_prepared_sha256=sha(model / 'prepared.json'),
         inputs_sha256=inventory, evidence_sha256={str(p.relative_to(R)): sha(p) for p in files}))
    print((run / 'verification.json').read_text(), flush=True)
    print('MANIFEST', sha(run / 'manifest.json'), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
