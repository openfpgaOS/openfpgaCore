#!/usr/bin/env python3
"""Replay the F7 panel for layout inspection; its counters are simulated."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT = CAMP / 'phase5'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location('f7_panel_adapter', Path(__file__).with_name('phase4_combined_validate.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
dest = OUT / 'panel'
dest.mkdir(exist_ok=False)
capture = OUT / 'capture-f7-panel'
manifest = json.loads((capture / 'inputs.json').read_text())
assert manifest['success'] and manifest['panel']
for rel, digest in manifest['results_sha256'].items():
    assert sha(capture / rel) == digest, rel
source = capture / 'gpuvec/frame_00640.vec'
vector = dest / 'frame_00640.vec'
m.with_depth(source, vector)
model = CAMP / 'phase4/texmem/extended/model'
exe = model / 'obj/Vtb_gpu'
assert sha(exe) == json.loads((model / 'build.json').read_text())['simulator_sha256']
command = [str(exe), str(vector), str(dest / 'frame.bin'), '+memory_traffic=2', '+tagged_tex=1']
with (dest / 'replay.log').open('w') as f:
    subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=600)
from PIL import Image
raw = (dest / 'frame.bin').read_bytes()
assert len(raw) == 307212
pixels = struct.unpack_from('<76800H', raw)
image = Image.new('RGB', (320, 240))
image.putdata([(((p >> 11) & 31)*255//31, ((p >> 5) & 63)*255//63, (p & 31)*255//31) for p in pixels])
image.save(dest / 'panel.png')
(dest / 'result.json').write_text(json.dumps(dict(
    capture_inputs_sha256=sha(capture / 'inputs.json'), simulator_sha256=sha(exe),
    vector_sha256=sha(vector), output_sha256=sha(dest / 'frame.bin'),
    image_sha256=sha(dest / 'panel.png'), command=command,
    scope='Actual-A RTL rendering of captured F7 overlay; displayed numbers are simulated, not device performance.'
), indent=2) + '\n')
print(dest / 'panel.png')
