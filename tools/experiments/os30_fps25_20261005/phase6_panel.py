#!/usr/bin/env python3
"""Replay the F8 overlay through retained-A RTL; numbers are simulated."""
import importlib.util
import struct
import subprocess
from pathlib import Path

spec = importlib.util.spec_from_file_location('f8_validate', Path(__file__).with_name('phase6_validate.py'))
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
destination = v.OUT / 'panel'
destination.mkdir(exist_ok=False)
capture = v.OUT / 'capture-f8-panel'
v.verify_capture(capture, v.sha(v.OUT / 'candidate/diagnostic.elf'), panel=True)
snapshots = v.snapshot_checks(capture, panel=True)
for name in ('scene.csv', 'geometry.csv', 'culling.csv'):
    assert v.sha(capture / name) == v.sha(v.PRIOR / 'capture-f7' / name), name
source, vector = capture / 'gpuvec/frame_00640.vec', destination / 'frame_00640.vec'
adapter = v.module('f8_panel_adapter', Path(__file__).with_name('phase4_combined_validate.py'))
adapter.with_depth(source, vector)
exe = v.RTL / 'obj/Vtb_gpu'
assert v.sha(exe) == v.read(v.RTL / 'build.json')['simulator_sha256']
command = [str(exe), str(vector), str(destination / 'frame.bin'), '+memory_traffic=2', '+tagged_tex=1']
with (destination / 'replay.log').open('x') as log:
    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
from PIL import Image
raw = (destination / 'frame.bin').read_bytes()
assert len(raw) == 307212
pixels = struct.unpack_from('<76800H', raw)
image = Image.new('RGB', (320, 240))
image.putdata([(((pixel >> 11) & 31) * 255 // 31, ((pixel >> 5) & 63) * 255 // 63,
                (pixel & 31) * 255 // 31) for pixel in pixels])
image.save(destination / 'panel.png')
v.write(destination / 'result.json', dict(
    snapshot_checks=snapshots, capture_inputs_sha256=v.sha(capture / 'inputs.json'),
    simulator_sha256=v.sha(exe), vector_sha256=v.sha(vector),
    output_sha256=v.sha(destination / 'frame.bin'), image_sha256=v.sha(destination / 'panel.png'),
    command=command, scope='Retained-A RTL rendering of F8 overlay; displayed counters are simulated, not hardware performance.'))
print(destination / 'panel.png')
