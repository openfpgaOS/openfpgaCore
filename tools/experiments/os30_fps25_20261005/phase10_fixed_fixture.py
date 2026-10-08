#!/usr/bin/env python3
"""Export retained-A conversion results to the standalone RTL test protocol."""
from pathlib import Path
import hashlib
import json
import math
import struct

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
SOURCE = BASE / 'cpu-audit/convert-native/result.json'
OUT = BASE / 'fpu-feasibility'
data = json.loads(SOURCE.read_text())
assert data['passed'] and data['byte_exact_across_traffic']
lines = []
for record in data['records']:
    for mode in [0, 1]:
        for lane, bits in enumerate(record['bits']):
            if lane == 3 and mode:
                continue
            value = struct.unpack('<f', struct.pack('<I', bits))[0]
            scale = 30 if lane == 3 else 8
            scaled = math.ldexp(value, scale)
            if lane == 3:
                scaled = max(1, min(2147483520, scaled))
            rounded = round(scaled) if mode == 0 else math.trunc(scaled)
            bad = not (-2147483648 <= rounded <= 2147483647)
            expected = record['actual_A_output_by_frm'][mode][lane]
            native = record['kind'] == 'native-A'
            lines.append(f'{bits:08x} {expected:08x} {scale} {mode} '
                         f'{int(lane == 3)} {int(bad)} {int(native)} {mode}')
(OUT / 'native-fixed-vectors.txt').write_text('\n'.join(lines) + '\n')
(OUT / 'native-fixed-inputs.json').write_text(json.dumps({
    'source': str(SOURCE.relative_to(ROOT)),
    'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    'count': len(lines),
    'scope': ('Actual retained-A CPU separately compiled arithmetic probe; '
              'RNE clip/depth and RTZ clip. Invalid conversions require '
              'unsupported flag; result bits are not compared for invalid conversions.')
}, indent=2) + '\n')
