#!/usr/bin/env python3
"""Separate bounded record preparation from the triangle job scan."""
from pathlib import Path
import difflib
import json

ROOT=Path(__file__).resolve().parents[3]
PHASE=ROOT/'docs/measurements/os30-fps25-20261005/phase7'
BASE=PHASE/'prepared-v2'
OUT=PHASE/'prepared-v3'

def main():
    OUT.mkdir(exist_ok=False)
    for name in json.loads((PHASE/'baseline.json').read_text())['inputs']:
        (OUT/name).write_bytes((BASE/name).read_bytes())
    source=(BASE/'gfx_pc.c').read_text()
    begin=source.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(')
    lo=source.index('            for (unsigned i = 0; i < 3; ++i) {',begin)
    end=source.index('            jobs[count++]',lo)
    body_start=source.index('                const struct LoadedVertex *v = ',lo)
    body_end=source.index('                prepared |= bit;',body_start)
    body=source[body_start:body_end].replace('rsp.loaded_vertices[s[i]]','rsp.loaded_vertices[i]')
    body=body.replace('&payload[s[i]]','&payload[i]')
    body='\n'.join(line[4:] if line.startswith('    ') else line for line in body.splitlines())+'\n'
    source=source[:lo]+'''            const uint32_t used = (1u << s[0]) | (1u << s[1]) | (1u << s[2]);
            prepared |= stale & used;
'''+source[end:]
    stale='            stale &= ~((1u << s[0]) | (1u << s[1]) | (1u << s[2]));'
    assert source.count(stale)==1
    source=source.replace(stale,'            stale &= ~used;',1)
    at=source.index('    if (count) {',lo)
    ins='''    if (count) {
        /* Only triangle commands have been consumed: slots and material are
         * still unchanged. Prepare unique dirty slots away from the hot job
         * scan; jobs retain every original load/draw ordering decision. */
        for (unsigned i = 0; prepared; ++i, prepared >>= 1) {
            if (!(prepared & 1u)) continue;
'''+body+'''        }
'''
    source=source[:at]+source[at:].replace('    if (count) {\n',ins,1)
    (OUT/'gfx_pc.c').write_text(source)
    (OUT/'implementation.patch').write_text(''.join(difflib.unified_diff(
        (BASE/'gfx_pc.c').read_text().splitlines(True),source.splitlines(True),
        'prepared-v2/gfx_pc.c','prepared-v3/gfx_pc.c')))
    print(OUT)

if __name__=='__main__':main()
