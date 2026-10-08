#!/usr/bin/env python3
"""Validate observation noninterference and size exact attribute reuse."""
from common import *
import argparse
from collections import Counter, OrderedDict
import csv
import subprocess


def layout_proof():
    dest = OUT / 'attribute-layout-proof'
    dest.mkdir(exist_ok=True)
    pc = ROOT / 'docs/measurements/os30-simple-20261005/simple-v4/gfx_pc.c'
    header = ROOT / 'docs/measurements/os30-fps25-20261005/SM64/src/sm64/sm64/include/PR/gbi.h'
    source = pc.read_text()
    light = header.read_text()
    light = light[light.index('typedef struct {\n  unsigned char\tcol[3]'):]
    light = light[:light.index('} Light_t;') + len('} Light_t;')]
    parts = []
    for name in ('RGBA', 'LoadedVertex'):
        p = source[source.index('struct ' + name + ' {'):]
        parts.append(p[:p.index('\n};') + 3])
    rsp = source[source.index('static struct RSP {'):]
    rsp = rsp[:rsp.index('\n} rsp;') + len('\n} rsp;')]
    c = '''#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>
#define MAX_LIGHTS 2
#define MAX_VERTICES 64
typedef float rspv_t;
''' + light + '\n' + '\n'.join(parts) + '\n' + rsp + '''
_Static_assert(sizeof(float)==4 && sizeof(Light_t)==12 && sizeof(struct LoadedVertex)==36, "scalar layout");
_Static_assert(sizeof(struct RSP)==3388, "RSP size");
int main(void) {
#define FIELD(x) printf(#x "=%zu\\n",offsetof(struct RSP,x))
FIELD(current_lights);FIELD(current_lights_coeffs);FIELD(current_num_lights);
FIELD(geometry_mode);FIELD(texture_scaling_factor);FIELD(loaded_vertices);
return 0;
}
'''
    path = dest / 'layout.c'
    path.write_text(c)
    exe = dest / 'layout'
    cmd = ['cc', '-std=c11', '-O2', str(path), '-o', str(exe)]
    subprocess.run(cmd, check=True)
    lines = subprocess.check_output([str(exe)], text=True)
    fields = dict((k, int(v)) for k, v in (line.split('=') for line in lines.splitlines()))
    assert fields == dict(current_lights=837, current_lights_coeffs=876, current_num_lights=924,
                          geometry_mode=928, texture_scaling_factor=936, loaded_vertices=940), fields
    record = {'passed': True, 'fields': fields, 'source_sha256': sha(pc), 'gbi_sha256': sha(header),
              'harness_sha256': sha(path), 'exe_sha256': sha(exe), 'command': cmd,
              'scope': 'All fields are fixed-width scalar/array types without pointers; same4-byte float/int and byte alignment on host/RV32. Exact ELF symbol size3388 separately guards target.'}
    (dest / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout-only', action='store_true')
    parser.add_argument('--label', default='attributes-observed-v2-normal')
    args = parser.parse_args()
    layout = layout_proof()
    if args.layout_only:
        print(json.dumps(layout, indent=2)); return
    baseline = BASELINES['baseline-normal']
    observed = capture_path(args.label)
    check_capture(baseline); check_capture(observed)
    assert sha(baseline / 'sm64.elf') == sha(observed / 'sm64.elf')
    exact = {}
    normalized_profiles = {}
    for p in baseline.rglob('*'):
        if not p.is_file(): continue
        rel = p.relative_to(baseline)
        if p.name.startswith('profile') and p.suffix == '.txt':
            def without_location(path):
                return [line for line in path.read_text().splitlines(True) if not line.startswith('elf: ')]
            assert without_location(p) == without_location(observed / rel), str(rel)
            normalized_profiles[str(rel)] = {'baseline_sha256': sha(p), 'observed_sha256': sha(observed / rel),
                'normalization': 'Omit only the elf: filesystem-location line; all numeric/profile content exact.'}
        elif p.name in ('frames.csv', 'scene.csv', 'geometry.csv', 'culling.csv') or \
           (p.name.startswith('profile') and p.suffix == '.tsv') or p.suffix == '.vec':
            assert sha(p) == sha(observed / rel), str(rel)
            exact[str(rel)] = sha(p)
    rows = list(csv.DictReader((observed / 'attributes.csv').open()))
    for r in rows:
        for name in ('frame', 'count', 'dest', 'address', 'eligible', 'mode', 'scale', 'lights', 'epoch'):
            r[name] = int(r[name])
    def key(r):
        lit = bool(r['mode'] & 0x20000)
        return (r['address'], r['count'], r['scale'], lit,
                r['lights'] if lit else 0, r['light_key'] if lit else '')
    def eligible(r):
        return r['eligible'] and not (r['mode'] & 0x40000)
    def summarize(selected):
        total_vertices = sum(r['count'] for r in selected)
        allowed = [r for r in selected if eligible(r)]
        groups = Counter((bool(r['mode'] & 0x20000), bool(r['mode'] & 0x40000), bool(r['eligible'])) for r in selected for _ in range(r['count']))
        descriptors = {(r['address'], r['count']) for r in allowed}
        states = {(r['lights'], r['light_key']) for r in allowed if r['mode'] & 0x20000}
        infinite = {}
        mismatch = 0; hits = 0
        for r in allowed:
            k = key(r)
            if k in infinite:
                hits += r['count']
                if infinite[k] != r['attributes']: mismatch += 1
            infinite[k] = r['attributes']
        lrus = {}
        for capacity in (16, 32, 64, 128, 256, 512, 1024):
            entries = OrderedDict(); hits_lru = 0; batches = 0
            for r in allowed:
                k = key(r)
                if k in entries:
                    hits_lru += r['count']; batches += 1; entries.move_to_end(k)
                else:
                    entries[k] = None
                    if len(entries) > capacity: entries.popitem(last=False)
            lrus[str(capacity)] = {'vertices': hits_lru, 'batches': batches,
                                  'percent_all_vertices': 100 * hits_lru / total_vertices}
        return {'batches': len(selected), 'vertices': total_vertices,
                'eligible_batches': len(allowed), 'eligible_vertices': sum(r['count'] for r in allowed),
                'groups_by_lighting_texgen_static': {str(k): v for k, v in groups.items()},
                'static_descriptors': len(descriptors), 'distinct_light_states': len(states),
                'distinct_batch_keys': len(infinite), 'same_key_output_mismatches': mismatch,
                'unbounded_hit_vertices': hits, 'unbounded_percent_all_vertices': 100 * hits / total_vertices,
                'lru': lrus}
    gameplay = [r for r in rows if 360 <= r['frame'] <= 720]
    result = {'passed_noninterference': True, 'baseline_elf_sha256': sha(observed / 'sm64.elf'),
              'observer_manifest_sha256': sha(OUT / 'qsim-attributes-observed-v2-manifest.json'),
              'observation_sha256': sha(observed / 'attributes.csv'), 'script_sha256': sha(Path(__file__)),
              'layout': layout, 'unchanged_guest_artifacts': exact, 'location_normalized_profiles': normalized_profiles,
              'all_frames': summarize(rows), 'gameplay': summarize(gameplay),
              'scope': 'Read-only exact finalized UV/RGB reuse keyed by immutable source descriptor, scale and actual light state. Excludes texture generation; fog alpha intentionally excluded and must remain eager/snapshot independently. LRU is a reach estimate, not proposed implementation or measured speedup.'}
    assert result['gameplay']['same_key_output_mismatches'] == 0, result['gameplay']
    (OUT / 'attribute-observer-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['gameplay'], indent=2))


if __name__ == '__main__': main()
