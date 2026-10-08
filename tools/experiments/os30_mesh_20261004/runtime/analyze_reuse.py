#!/usr/bin/env python3
"""Measure exact raw-job reuse and all-inside projection opportunities."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'dma'))
sys.path.insert(0, str(HERE.parent / 'client'))
from check_gpu_replay import vector_records
from check_whole_gpu import packets
from mesh_transform import transform_vertex, clip_code

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--capture', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
global_shader = set()
global_geometry = set()
rows = []
for path in sorted(a.capture.glob('frame_*.vec')):
    memory = bytearray(1 << 26)
    counts = collections.Counter()
    frame_shader = set()
    frame_geometry = set()
    def read(addr, n):
        return list(struct.unpack_from('<%dI' % n, memory, addr & 0x3ffffff))
    for tag, record in vector_records(path.read_bytes()):
        if tag == b'MEM1':
            addr, length = struct.unpack_from('<II', record, 4)
            addr &= 0x3ffffff
            memory[addr:addr + length] = record[12:12 + length]
        if tag != b'CMDS':
            continue
        n = struct.unpack_from('<I', record, 4)[0]
        for packet in packets(struct.unpack_from('<%dI' % n, record, 8)):
            if packet[0] >> 24 != 0x62 or packet[7] != 0:
                continue
            _, va, vw, ia, im, sa, sw, _ = packet
            raw, state, indices = read(va, vw), read(sa, sw), read(ia, im & 65535)
            nv, nt = vw // 4, im >> 16
            counts.update(jobs=1, vertices=nv, triangles=nt)
            # Framebuffer/depth/texture addresses cannot affect prepared geometry.
            shader_key = tuple(raw + state[:24])
            geometry_key = shader_key + tuple(indices + state[41:52])
            for name, key, local, global_ in [('shader', shader_key, frame_shader, global_shader),
                                             ('geometry', geometry_key, frame_geometry, global_geometry)]:
                if key in local:
                    counts[name + '_repeat_jobs_in_frame'] += 1
                    counts[name + '_repeat_vertices_in_frame'] += nv
                if key in global_:
                    counts[name + '_repeat_jobs_prior_frames'] += 1
                    counts[name + '_repeat_vertices_prior_frames'] += nv
                local.add(key)
            transformed = [transform_vertex(raw[4*i:4*i+4], state) for i in range(nv)]
            codes = [clip_code(v) for v in transformed]
            if not any(codes):
                counts['all_inside_jobs'] += 1
                counts['all_inside_vertices'] += nv
                counts['all_inside_triangles'] += nt
                counts['all_inside_current_projection_calls'] += 3 * nt
            else:
                counts['partially_outside_jobs'] += 1
    global_shader.update(frame_shader)
    global_geometry.update(frame_geometry)
    rows.append(dict(frame=path.stem, counts=dict(counts), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
totals = collections.Counter()
for row in rows:
    totals.update(row['counts'])
report = dict(scope=__doc__, frames=rows, totals=dict(totals),
              note='Exact bitwise keys. Geometry reuse assumes re-emission of current material and is not implemented. All-inside projection counts include culled faces, because current winding follows projection.')
a.out.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(totals, indent=2))
