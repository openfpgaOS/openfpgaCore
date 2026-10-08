#!/usr/bin/env python3
"""Verify the reported overflow through raw, cached and worker geometry routes."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/gpu-edge-overflow-20261004'
BASE = ROOT / 'docs/measurements/os30-worker-20261003/replay-worker-v6/obj/Vtb_gpu'
TRIANGLE = [(-100, -400), (50, 100), (480, 600)]


def vector(route, reverse):
    state = [0,640,2,0x300000,8,0x70007,0x800010a4,0,0,0,0,0x400000,640,2,320<<16,240<<16,0]
    words = [0x4a000011, *state]
    vertices = TRIANGLE if not reverse else [TRIANGLE[0], TRIANGLE[2], TRIANGLE[1]]
    xy = [((y*16 & 65535)<<16) | (x*16 & 65535) for x,y in vertices]
    if route == 'raw':
        words += [0x4e000011, *xy, *([0]*6), *([1<<20]*3),
                  0xffffffff, 0xffff, *([1<<25]*3)]
    else:
        # Raw coordinates -> camera (x*256,y*256,w=256). Viewport scale
        # (1,-16) produces the exact requested Q4 screen endpoints.
        matrix = [1<<24,0,0,0, 0,1<<24,0,0, 0,0,0,256] + [0]*8
        words += [0x5000001a, *matrix, 0,0,1,-16,256,3]
        for slot, ((x,y), packed) in enumerate(zip(vertices,xy)):
            if route == 'projected':
                words += [0x5c000006, (0xffff<<16)|slot, packed, 1<<20, 0,0,1<<25]
            elif route == 'clip':
                words += [0x56000008, slot, x<<16, y<<16, 1<<16, 0,0,0xffff,1<<25]
            elif route == 'geometry':
                words += [0x59000005, slot, (x&65535)|((y&65535)<<16), 0xffff0000, 0,0]
            else:
                raise ValueError(route)
        words += [0x5b000001 if route=='geometry' else 0x54000001, (1<<5)|(2<<10)]

    def memory(address, data):
        return b'MEM1' + struct.pack('<II', address, len(data)) + data + bytes(-len(data)%4)
    words = [w & 0xffffffff for w in words]
    return (memory(0, bytes(320*240*2)) + memory(0x300000, b'\xff'*128) +
            b'CMDS' + struct.pack('<I',len(words)) + struct.pack('<%dI'%len(words), *words) +
            b'DUMP' + struct.pack('<II',0,320*240*2) + b'END.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='compact3')
    args = parser.parse_args()
    assert re.fullmatch(r'[a-z0-9_-]+', args.label)
    spec = importlib.util.spec_from_file_location('edge_repro', OUT/'handoff/reproduce_edge_overflow.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    expected = reference.expected_mask(TRIANGLE)
    rows = []
    for hardware, exe in [('before', BASE), *[(f'parallel{p}', OUT/f'replay-{args.label}-parallel{p}/obj/Vtb_gpu') for p in (0,1)]]:
        for route in ('raw','projected','clip','geometry'):
            for reverse in (False,True):
                dest = OUT / ('routes-'+args.label) / hardware / (route+('-reverse' if reverse else ''))
                dest.mkdir(parents=True, exist_ok=True)
                vec = dest/'input.vec'
                vec.write_bytes(vector(route,reverse))
                with (dest/'replay.log').open('w') as log:
                    subprocess.run([str(exe),str(vec),str(dest/'output.bin')],
                                   stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
                raw=(dest/'output.bin').read_bytes()
                assert len(raw)==320*240*2
                mask=[bool(v) for v in struct.unpack('<76800H',raw)]
                row=dict(hardware=hardware, route=route, reverse=reverse, pixels=sum(mask),
                         extra=sum(a and not b for a,b in zip(mask,expected)),
                         missing=sum(b and not a for a,b in zip(mask,expected)))
                rows.append(row)
                print(row,flush=True)
    (OUT/f'routes-{args.label}/results.json').write_text(json.dumps(rows,indent=2)+'\n')
    assert all(r['extra']==28416 and r['missing']==0 for r in rows if r['hardware']=='before')
    assert all(r['extra']==r['missing']==0 and r['pixels']==29457
               for r in rows if r['hardware']!='before')


if __name__=='__main__':
    main()
