#!/usr/bin/env python3
"""Freeze end-to-end LOAD RTL and validate original native Vtx inputs."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
from phase10_experiment import CAMP,ROOT,sha,write
HERE=Path(__file__).resolve().parent;P=CAMP/'phase10/cpu-audit'
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--label',default='raw-load-v1');args=ap.parse_args();out=CAMP/'phase10'/args.label;out.mkdir(exist_ok=False)
    files=[HERE/'phase10_float_batch/geometry_raw_load.v',HERE/'phase10_float_batch/geometry_transform_light.v',HERE/'phase10_float_batch/geometry_float_batch_recip.v',CAMP/'phase10/fpu-feasibility/rtl/GeometryFmaProbe.v',HERE/'phase10_lighting/geometry_lighting_batch.v',HERE/'phase10_float_recip2.v',HERE/'phase10_float_fixed_pipe.v',HERE/'phase10_raw_vtx_dma.v',HERE/'phase10_raw_load_test.cpp']
    for p in files:(out/p.name).write_bytes(p.read_bytes())
    inputs=[P/'firmware/vertex-state.bin',P/'native-A-states.bin',P/'native-A-transform-vectors.json',P/'native-A-reciprocal-vectors.json',P/'lighting-native/native-vectors.json']
    transform=json.loads(inputs[2].read_text())['records'];recip=json.loads(inputs[3].read_text())['records'];light=json.loads(inputs[4].read_text())['records']
    keyed=lambda rs:{(r['job'],r['vertex']):r for r in rs}
    tr,rc,li=map(keyed,[transform,recip,light]);raw=inputs[0].read_bytes();states=inputs[1].read_bytes();o=0;payload=[]
    for job in range(20):
        h=struct.unpack_from('<10I',raw,o);n,first,base=h[3:6];source=raw[o+40+6612:o+40+6612+24*n];o+=40+2*6612+24*n+56
        rsp=states[job*6612+3176:job*6612+3176+3388];u_scale,v_scale=struct.unpack_from('<2H',rsp,936)
        t,l=tr[job,0],li[job,0];assert not l['texture_gen'];assert l['directional_lights']==1
        packrgb=lambda a:sum(x<<(8*i) for i,x in enumerate(a))
        header=[n,base,t['aspect_bits'],*t['matrix_column_major_bits'],*l['coeff_bits'],packrgb(l['ambient_rgb']),packrgb(l['diffuse_rgb'][0]),int(l['lighting']),u_scale,v_scale]
        body=[]
        for v in range(n):
            b=source[v*24:(v+1)*24];t,r,l=tr[job,v],rc[job,v],li[job,v]
            assert list(struct.unpack_from('<3I',b))==t['xyz_bits'];assert list(b[18:22])==l['source_rgba']
            xyzw=t['actual_A_xyzw_bits'];x,y,z,w=struct.unpack('<4f',struct.pack('<4I',*xyzw))
            mask=(32 if x<-w else 0)|(16 if x>w else 0)|(8 if y<-w else 0)|(4 if y>w else 0)|(2 if z<-w else 0)|(1 if z>w else 0)
            su,sv=struct.unpack_from('<2h',b,14);uv=((su*u_scale>>16)&65535)|((sv*v_scale>>16)&65535)<<16
            expected=[*xyzw,r['actual_A_w_inv_bits'],uv,packrgb(l['native_A_rgb'])|(b[21]<<24),mask]
            body.extend([*struct.unpack('<6I',b),*expected,int(not l['external_memo_hit'])])
        payload.extend(header+body)
    assert o==len(raw)
    binary=out/'native.bin';binary.write_bytes(struct.pack('<%dI'%(1+len(payload)),20,*payload))
    write(out/'manifest.json',dict(source_sha256={p.name:sha(out/p.name) for p in files},native_input_sha256={str(p.relative_to(ROOT)):sha(p) for p in inputs},binary_sha256=sha(binary),helper_sha256=sha(Path(__file__)),scope='Raw24byte input DMA + exact native transform/reciprocal/one-light RGB, signed UV and clip masks. Fog, texgen, persistent destination slots, GPU scheduling and full-core performance excluded.'))
    cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','geometry_raw_load','--Mdir',str(out/'obj'),'-CFLAGS','-O2 -std=c++17 -ffp-contract=off',*[str(out/p.name) for p in files]]
    with (out/'build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (out/'run.log').open('x') as log:p=subprocess.run([str(out/'obj/Vgeometry_raw_load'),str(binary)],stdout=log,stderr=subprocess.STDOUT)
    print((out/'run.log').read_text())
    if p.returncode:raise SystemExit(p.returncode)
    result=json.loads((out/'run.log').read_text());write(out/'result.json',dict(**result,manifest_sha256=sha(out/'manifest.json')))
if __name__=='__main__':main()
