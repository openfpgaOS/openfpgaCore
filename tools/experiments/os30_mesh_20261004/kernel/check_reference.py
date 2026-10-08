#!/usr/bin/env python3
"""Capture raw inputs and independently compare all produced plane/edge words."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

from reference import decode_inputs, derive_planes, prepare_edges

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SOURCE = ROOT / 'docs/measurements/os30-parallel-20261004/edges-extract'
PRIOR = ROOT / 'docs/measurements/sm64-vertex-perf-20261004'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def replace(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new, 1)


def build(dest):
    dest.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    for relative, digest in manifest['sources'].items():
        assert sha(SOURCE / relative) == digest, relative
    shutil.copytree(SOURCE / 'frozen', dest / 'frozen')
    path = dest / 'frozen/tb_gpu.v'
    s = path.read_text()
    s = replace(s, '    output wire tap_edge_valid,',
                '    output wire tap_inputs_valid,\n    output wire [767:0] tap_inputs,\n    output wire tap_edge_valid,')
    tap = '''
assign tap_inputs_valid = gpu.state == gpu.S_TRI_DERIVE && gpu.dstate == gpu.DRV_SORT_A;
assign tap_inputs[0*32+:32] = {29'd0, gpu.spanprod_cd_combine,
    (gpu.cmd_is_draw_indexed_tri || gpu.cmd_is_draw_clip_tri || gpu.cmd_is_draw_xform_tri_rgb),
    gpu.spanprod_subpix_y};
assign tap_inputs[1*32+:32] = {gpu.tri_clip_x1,gpu.tri_clip_x0};
assign tap_inputs[2*32+:32] = {gpu.tri_clip_y1,gpu.tri_clip_y0};
'''
    for i in range(3):
        values = [f'{{gpu.tri_v{i}_y,gpu.tri_v{i}_x}}',
                  f'gpu.dv_szi[{i}]', f'gpu.dv_tzi[{i}]', f'gpu.vt_zi[{i}]', f'gpu.vt_depth[{i}]',
                  f"{{16'd0,gpu.vt_rrow[{i}],gpu.vt_lrow[{i}],gpu.vt_brow[{i}]}}",
                  f"{{16'd0,gpu.vt_Drrow[{i}],gpu.vt_Dgrow[{i}],gpu.vt_Dbrow[{i}]}}"]
        for j, value in enumerate(values):
            tap += f'assign tap_inputs[{3 + i * 7 + j}*32+:32] = {value};\n'
    offset = s.rindex('endmodule')
    path.write_text(s[:offset] + tap + s[offset:])
    path = dest / 'frozen/replay.cpp'
    s = path.read_text()
    s = replace(s, 'static FILE *converted;', 'static FILE *kernel_capture;\nstatic uint32_t kernel_input[24];\nstatic FILE *converted;')
    s = replace(s, '    if (tb->tap_edge_start) edge_seen=false;',
                '    if (tb->tap_inputs_valid) for (unsigned i=0;i<24;i++) kernel_input[i]=tb->tap_inputs[i];\n'
                '    if (tb->tap_edge_start) edge_seen=false;')
    s = replace(s, '    if (!edge_seen) return; // Original walker rejected this triangle.', '''    if (kernel_capture) {
        uint32_t header[2]={tb->tap_plane_words-3,edge_seen};
        fwrite(header,4,2,kernel_capture);
        fwrite(kernel_input,4,24,kernel_capture);
        if (edge_seen) fwrite(prepared_edges,4,9,kernel_capture);
        for (unsigned i=3;i<tb->tap_plane_words;i++) {
            uint32_t v=tb->tap_planes[i];fwrite(&v,4,1,kernel_capture);
        }
    }
    if (!edge_seen) return; // Original walker rejected this triangle.''')
    s = replace(s, '    const char *convert_path=getenv("PLANE_VECTOR_OUT");',
                '    const char *kernel_path=getenv("KERNEL_CAPTURE");\n'
                '    if(kernel_path) {kernel_capture=fopen(kernel_path,"wb");if(!kernel_capture)return 2;}\n'
                '    const char *convert_path=getenv("PLANE_VECTOR_OUT");')
    s = replace(s, '    printf("PLANES count=%llu\\n",(unsigned long long)plane_count);',
                '    if(kernel_capture)fclose(kernel_capture);\n    printf("PLANES count=%llu\\n",(unsigned long long)plane_count);')
    path.write_text(s)
    command = [x.replace(str(SOURCE), str(dest)) for x in manifest['command']]
    manifest.update(command=command, sources={str(p.relative_to(dest)): sha(p)
                    for p in (dest / 'frozen').rglob('*') if p.is_file()})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (dest / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)


def compare(path):
    raw = path.read_bytes()
    words = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    offset = count = prepared = combined = 0
    while offset < len(words):
        length, present = words[offset:offset+2]
        offset += 2
        vertices, clip, options = decode_inputs(words[offset:offset+24])
        offset += 24
        edge = list(words[offset:offset+9]) if present else None
        offset += 9 if present else 0
        planes = list(words[offset:offset+length])
        offset += length
        got_planes = derive_planes(vertices, **options)
        got_edge = prepare_edges(vertices, clip, subpixel=options['subpixel'])
        assert planes == got_planes, (path.name, count, 'planes', vertices, options,
                                      [(i, hex(a), hex(b)) for i, (a, b) in enumerate(zip(planes, got_planes)) if a != b])
        assert edge == got_edge, (path.name, count, 'edges', vertices, clip, options, edge, got_edge)
        count += 1
        prepared += present
        combined += options['combine']
    return dict(triangles=count, prepared=prepared, combined=combined, sha256=sha(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=HERE / 'results/reference')
    parser.add_argument('--reuse-build', action='store_true')
    args = parser.parse_args()
    dest = args.out.resolve()
    if not args.reuse_build:
        build(dest / 'extract')
    reference = json.loads((PRIOR / 'replay-results-final-hidden-final-panel-worker.json').read_text())
    def run(row):
        name = f"{row['label']}-m{row['mode']}-f{row['frame']}"
        result_dir = dest / name
        result_dir.mkdir(exist_ok=True)
        vector = PRIOR / 'replays-worker' / row['label'] / str(row['mode']) / f"frame_{row['frame']:05}.vec"
        env = os.environ.copy()
        env['KERNEL_CAPTURE'] = str(result_dir / 'kernel.bin')
        env['PLANE_VECTOR_OUT'] = str(result_dir / 'planes.vec')
        with (result_dir / 'run.log').open('w') as log:
            subprocess.run([str(dest / 'extract/obj/Vtb_gpu'), str(vector), str(result_dir / 'output.bin'),
                            f"+memory_traffic={row['mode']}"], env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=600)
        expected = ROOT / 'docs/measurements/os30-parallel-20261004/edges-checks' / name
        assert sha(result_dir / 'output.bin') == sha(expected / 'reference.bin')
        assert sha(result_dir / 'planes.vec') == sha(expected / 'planes.vec')
        result = dict(name=name, **compare(result_dir / 'kernel.bin'))
        print(json.dumps(result), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, reference))
    (dest / 'result.json').write_text(json.dumps(dict(passed=True, results=results,
        source_sha256={p.name:sha(p) for p in (HERE / 'reference.py', Path(__file__))}), indent=2) + '\n')


if __name__ == '__main__':
    main()
