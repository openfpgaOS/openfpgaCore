#!/usr/bin/env python3
"""Build original-RTL plane extraction and the replacement rasterizer replay."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-parallel-20261004'
SOURCE=ROOT/'docs/measurements/os30-worker-20261003/replay-worker-v6'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def rep(s,old,new):
    assert s.count(old)==1,(old[:100],s.count(old))
    return s.replace(old,new,1)


def main():
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['extract','backend'])
    p.add_argument('--edges',action='store_true');a=p.parse_args()
    dest=OUT/(('edges-' if a.edges else 'planes-')+a.kind);dest.mkdir(exist_ok=False)
    manifest=json.loads((SOURCE/'manifest.json').read_text())
    for rel,digest in manifest['sources'].items(): assert sha(SOURCE/rel)==digest,rel
    shutil.copytree(SOURCE/'frozen',dest/'frozen')
    rtl=OUT/('edges-rtl' if a.edges and a.kind=='backend' else 'planes-rtl')
    for p in rtl.glob('*.v'):
        shutil.copy2(p,dest/'frozen/common'/p.name)
    cmd=[x.replace(str(SOURCE),str(dest)) for x in manifest['command']]
    if a.edges and a.kind=='backend':
        cmd+=[str(dest/'frozen/common/gpu_span_walker.v')]
    cmd+=['+define+INCLUDE_GPU_VERTEX_ATTRIBUTES']
    if a.kind=='backend':
        for key in ['INCLUDE_VERT_TRI','INCLUDE_XFORM_RGB','INCLUDE_VTX_CACHE']:
            old=f'-G{key}=1';assert old in cmd
            cmd[cmd.index(old)]=f'-G{key}=0'
        remove={'INCLUDE_VERT_TRI','INCLUDE_XFORM','INCLUDE_GPU_SM64_GEOMETRY',
                'INCLUDE_GPU_GEOMETRY_WORKER','INCLUDE_XFORM_VERTEX_RAM','INCLUDE_XFORM_RATIO_PIPE',
                'INCLUDE_GPU_VERTEX_ATTRIBUTES'}
        cmd=[x for x in cmd if x not in {'+define+'+s for s in remove}]
        cmd+=['+define+INCLUDE_GPU_PLANE_INPUT']
    else:
        p=dest/'frozen/tb_gpu.v'
        s=rep(p.read_text(),'    output wire        busy,','''    output wire tap_header_valid, tap_payload_valid, tap_planes_valid, tap_rgb,
    output wire [31:0] tap_header, tap_payload,
    output wire [5:0] tap_plane_words,
    output wire [1055:0] tap_planes,
    output wire        busy,''')
        tap=(HERE/'plane_tap.v').read_text()
        for i,row in enumerate([1,2,3,4,8,9,10,11,12,13]):
            for col,field in enumerate(['origin','du','dv']):
                tap+=f'assign tap_planes[{3+i*3+col}*32+:32] = gpu.spanprod_pl_{field}[{row}];\n'
        if a.edges:
            s=rep(s,'    output wire tap_header_valid,',
                  '    output wire tap_edge_valid, tap_edge_start,\n'
                  '    output wire [287:0] tap_edges,\n    output wire tap_header_valid,')
            path='gpu.g_tri_walker.tri_walker.'
            tap+=f'assign tap_edge_valid = {path}state == {path}S_WALK_INIT;\n'
            tap+='assign tap_edge_start = gpu.tri_start;\n'
            words=[path+x for x in ['xl','xr','slope_long[31:0]','slope_top[31:0]','slope_bot[31:0]']]
            words += [path+'x1_q16 + '+path+'bot_mid_off',
                      '{'+path+'y_mid,'+path+'y_cur}',
                      "{13'd0,gpu.spanprod_subpix_y,"+path+'long_left,'+path+'in_bottom_half,'+path+'y_end}',
                      '{'+path+'clip_x1,'+path+'clip_x0}']
            for i,w in enumerate(words):tap+=f'assign tap_edges[{i}*32+:32] = {w};\n'
        i=s.rindex('endmodule');p.write_text(s[:i]+tap+s[i:])
        p=dest/'frozen/replay.cpp';s=p.read_text()
        tap=(HERE/'plane_tap.cpp').read_text()
        if a.edges:
            tap=rep(tap,'static FILE *converted;',
                    'static uint32_t prepared_edges[9];\nstatic bool edge_seen;\nstatic FILE *converted;')
            tap=rep(tap,'    if (tb->tap_header_valid) {',
                    '''    if (tb->tap_edge_start) edge_seen=false;
    if (tb->tap_edge_valid) {
        for (unsigned i=0;i<9;i++) prepared_edges[i]=tb->tap_edges[i];
        edge_seen=true;
    }
    if (tb->tap_header_valid) {''')
            tap=rep(tap,'    unsigned n=tb->tap_plane_words;',
                    '    if (!edge_seen) return; // Original walker rejected this triangle.\n'
                    '    unsigned n=tb->tap_plane_words;')
            tap=rep(tap,'    plane_commands.push_back(0x60000000u|n);\n'
                        '    for (unsigned i=0;i<n;i++) plane_commands.push_back(tb->tap_planes[i]);',
                    '    plane_commands.push_back(0x61000000u|(n+6));\n'
                    '    for (unsigned i=0;i<9;i++) plane_commands.push_back(prepared_edges[i]);\n'
                    '    for (unsigned i=3;i<n;i++) plane_commands.push_back(tb->tap_planes[i]);')
        s=rep(s,'static uint64_t cycles, busy_cycles;','static uint64_t cycles, busy_cycles;\n'+tap)
        s=rep(s,'        tb->clk = 1; tb->eval();',
                '        tap_before(); bool planes=tb->tap_planes_valid;\n'
                '        tb->clk = 1; tb->eval(); if (planes) tap_after();')
        s=rep(s,'    const uint64_t start_cycles = cycles, start_busy = busy_cycles;',
                '''    const char *convert_path=getenv("PLANE_VECTOR_OUT");
    if (convert_path) { converted=fopen(convert_path,"wb"); if (!converted) return 2; }
    const uint64_t start_cycles = cycles, start_busy = busy_cycles;''')
        s=rep(s,'        uint32_t tag = rd32();','        size_t tag_start=pos;\n        uint32_t tag = rd32();')
        s=rep(s,'        } else if (tag == GV_TAG_DUMP) {','            tap_flush();\n        } else if (tag == GV_TAG_DUMP) {')
        s=rep(s,'            ended = true;','            if (converted) fwrite(buf.data()+tag_start,1,pos-tag_start,converted);\n            ended = true;')
        s=rep(s,'    if (!ended || pos != buf.size()) rc = 2;',
                '''    if (converted) { fclose(converted);converted=nullptr; }
    printf("PLANES count=%llu\\n",(unsigned long long)plane_count);
    if (!ended || pos != buf.size()) rc = 2;''')
        needle='            rc = 2; break;\n        }\n    }\n'
        s=rep(s,needle,'''            rc = 2; break;
        }
        if (converted && tag!=GV_TAG_CMDS) fwrite(buf.data()+tag_start,1,pos-tag_start,converted);
    }
''')
        p.write_text(s)
    manifest.update(command=cmd,scope=__doc__,kind=a.kind,
                    sources={str(p.relative_to(dest)):sha(p) for p in (dest/'frozen').rglob('*') if p.is_file()})
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with (dest/'build.log').open('w') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    print(dest/'obj/Vtb_gpu',flush=True)


if __name__=='__main__': main()
