#!/usr/bin/env python3
"""Private exact packed-UV canonical vertex experiment over F11."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP, parent_inputs, write, sha

HERE=Path(__file__).resolve().parent
BASE=CAMP/'phase9/checks-state-f11'
OUT=CAMP/'phase10/packed-uv-v1'

def main():
    parent=parent_inputs();OUT.mkdir(exist_ok=False)
    for name in parent['inputs']:(OUT/name).write_bytes((BASE/name).read_bytes())
    p=OUT/'gfx_pc.c';original=p.read_text();s=original
    old='''    rspv_t w_inv;
    rspv_t u, v;
    struct RGBA color;
    uint8_t clip_rej;
};'''
    new='''    rspv_t w_inv;
    /* G_VTX UV is always signed16, including texgen's final short cast.
     * Clipped and rectangle scratch vertices retain floating UV externally. */
    union {
        struct { int16_t u, v; };
        const rspv_t *wide_uv;
    };
    struct RGBA color;
    uint8_t clip_rej;
    uint8_t uv_wide;
};
#if defined(__riscv) && defined(TARGET_OPENFPGA)
_Static_assert(sizeof(struct LoadedVertex)==32, "packed canonical vertex");
#endif
static inline rspv_t gfx_vertex_u(const struct LoadedVertex *v) {
    return v->uv_wide ? v->wide_uv[0] : RSPV_FROM_INT(v->u);
}
static inline rspv_t gfx_vertex_v(const struct LoadedVertex *v) {
    return v->uv_wide ? v->wide_uv[1] : RSPV_FROM_INT(v->v);
}
static rspv_t rectangle_uv[4][2];'''
    assert s.count(old)==1;s=s.replace(old,new)
    # Ordinary and retained-but-disabled worker G_VTX attribute writes.
    old='''        d->u = RSPV_FROM_INT(U);
        d->v = RSPV_FROM_INT(V);'''
    assert s.count(old)==1;s=s.replace(old,'''        d->u = U;
        d->v = V;
        d->uv_wide = 0;''')
    old='d->u=(short)(v->tc[0]*scale_s>>16);d->v=(short)(v->tc[1]*scale_t>>16);'
    assert s.count(old)==1;s=s.replace(old,old+'d->uv_wide=0;')
    # Generic consumers must retain fractional clip/large rectangle UV.
    for expr in ('v->u','v->v','v_arr[i]->u','v_arr[i]->v'):
        axis=expr[-1];obj=expr[:-3]
        s=s.replace('RSPV_TO_FLOAT('+expr+')','RSPV_TO_FLOAT(gfx_vertex_'+axis+'('+obj+'))')
    # Indexed original slots are always canonical packed integers. Derive Q16
    # with exact power-of-two integer arithmetic, preserving all rounding.
    old='''                                       float u, float tex_v, uint16_t rgb) {'''
    assert s.count(old)==1;s=s.replace(old,'''                                       int32_t qu, int32_t qv, uint16_t rgb) {''')
    old='''    int32_t qu = (int32_t)lrintf(u * 65536.0f);
    int32_t qv = (int32_t)lrintf(tex_v * 65536.0f);
'''
    assert s.count(old)==1;s=s.replace(old,'')
    old='''    float uofs = 0.0f, vofs = 0.0f, bias = 0.0f;'''
    assert s.count(old)==1;s=s.replace(old,'''    int32_t uofs = 0, vofs = 0, uv_bias = 0;''')
    old='''            uofs = textured ? (float)(rdp.texture_tile.uls * 8) : 0.0f;
            vofs = textured ? (float)(rdp.texture_tile.ult * 8) : 0.0f;
            bias = (textured &&
                (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT) ? 0.5f : 0.0f;
            gfx_vc_texture_state(comb, textured, bias);'''
    assert s.count(old)==1;s=s.replace(old,'''            uofs = textured ? (int32_t)rdp.texture_tile.uls * 8 : 0;
            vofs = textured ? (int32_t)rdp.texture_tile.ult * 8 : 0;
            uv_bias = (textured &&
                (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT) ? 32768 : 0;
            gfx_vc_texture_state(comb, textured, uv_bias ? 0.5f : 0.0f);''')
    begin=s.index('            float u = 0.0f, tex_v = 0.0f;',s.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run'))
    end=s.index('            uint16_t rgb = 0xffff;',begin)
    s=s[:begin]+'''            int32_t qu = 0, qv = 0;
            if (textured) {
                /* No signed-left-shift UB: all differences times2048 plus
                 * optional32768 fit signed32 for the full uint16 tile range. */
                qu = ((int32_t)v->u - uofs) * 2048 + uv_bias;
                qv = ((int32_t)v->v - vofs) * 2048 + uv_bias;
            }
'''+s[end:]
    old='gfx_cpu_prepare_clip(&payload[i], v, u, tex_v, rgb);'
    assert s.count(old)==1;s=s.replace(old,'gfx_cpu_prepare_clip(&payload[i], v, qu, qv, rgb);')
    # Clip copies must own their wide coordinates: a pointer into the opposite
    # ping-pong buffer would become stale when that buffer is reused.
    old='''    struct LoadedVertex v_buf[2][12];
    v_buf[0][0] = *v1; v_buf[0][1] = *v2; v_buf[0][2] = *v3;'''
    assert s.count(old)==1;s=s.replace(old,'''    struct LoadedVertex v_buf[2][12];
    rspv_t uv_buf[2][12][2];
    v_buf[0][0] = *v1; v_buf[0][1] = *v2; v_buf[0][2] = *v3;
    for (unsigned k=0;k<3;++k) if (v_buf[0][k].uv_wide) {
        uv_buf[0][k][0]=gfx_vertex_u(&v_buf[0][k]);
        uv_buf[0][k][1]=gfx_vertex_v(&v_buf[0][k]);
        v_buf[0][k].wide_uv=uv_buf[0][k];
    }''')
    old='''            if (this_in) v_out[v_num[outidx]++] = *vthis;
            if (this_in ^ next_in) {
                struct LoadedVertex *xv = &v_out[v_num[outidx]++];
                xv->clip_rej = 0; /* Scratch vertices go directly to submission. */'''
    assert s.count(old)==1;s=s.replace(old,'''            if (this_in) {
                const unsigned out=v_num[outidx]++;
                v_out[out] = *vthis;
                if (vthis->uv_wide) {
                    uv_buf[outidx][out][0]=gfx_vertex_u(vthis);
                    uv_buf[outidx][out][1]=gfx_vertex_v(vthis);
                    v_out[out].wide_uv=uv_buf[outidx][out];
                }
            }
            if (this_in ^ next_in) {
                const unsigned out=v_num[outidx]++;
                struct LoadedVertex *xv = &v_out[out];
                rspv_t *uv=uv_buf[outidx][out];
                xv->wide_uv=uv;
                xv->uv_wide=1;
                xv->clip_rej = 0; /* Scratch vertices go directly to submission. */''')
    for a,b in (('vthis','vnext'),('vnext','vthis')):
        for i,axis in enumerate(('u','v')):
            old=f'xv->{axis} = rspv_lerp({a}->{axis}, {b}->{axis}, t);'
            new=f'uv[{i}] = rspv_lerp(gfx_vertex_{axis}({a}), gfx_vertex_{axis}({b}), t);'
            assert s.count(old)==1;s=s.replace(old,new)
    # Rectangle UV is not signed16 after edge extrapolation. Preserve original
    # rspv_t conversions in a separate stable four-pair scratch array.
    begin=s.index('static void gfx_dp_texture_rectangle(');end=s.index('static void gfx_dp_fill_rectangle(',begin)
    t=s[begin:end]
    for slot,name in enumerate(('ul','ll','lr','ur')):
        for axisnum,axis in enumerate(('u','v')):
            t=t.replace(name+'->'+axis+' =',f'rectangle_uv[{slot}][{axisnum}] =')
    s=s[:begin]+t+s[end:]
    old='''        rsp.loaded_vertices[i].clip_rej = 0;
    }

    // The coordinates for texture rectangle'''
    assert s.count(old)==1;s=s.replace(old,'''        rsp.loaded_vertices[i].clip_rej = 0;
        rsp.loaded_vertices[i].uv_wide = 1;
        rsp.loaded_vertices[i].wide_uv = rectangle_uv[i-MAX_VERTICES];
    }

    // The coordinates for texture rectangle''')
    p.write_text(s)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='F11/gfx_pc.c',tofile='packed-uv-v1/gfx_pc.c')))
    (OUT/'prepare.py').write_bytes(Path(__file__).read_bytes())
    write(OUT/'preparation.json',dict(private_only=True,production_changed=False,
      parent_elf_sha256=parent['elf_sha256'],recipe_sha256=sha(Path(__file__)),
      source_sha256=sha(p),native_and_frame_qualification_pending=True,
      scope='32-byte canonical signed16UV record; tagged external floatUV for clipping/rectangles; exact integer indexed UV conversion'))
    print(OUT)

if __name__=='__main__':main()
