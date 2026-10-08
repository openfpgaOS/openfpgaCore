#!/usr/bin/env python3
"""Private exact position reuse experiment; no attribute or matrix math changes."""
import argparse,difflib,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];C=ROOT/'docs/measurements/os30-simple-20261005'
EXPECTED='4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84'
sha=lambda data:hashlib.sha256(data).hexdigest()

def transform(s,entries):
 assert sha(s.encode())==EXPECTED
 assert entries>0 and entries&(entries-1)==0
 anchor='static __attribute__((noinline)) void gfx_sp_vertex('
 storage=f'''#if defined(TARGET_OPENFPGA) && defined(GBI_FLOATS)
/* Attribute seams duplicate object positions. Cache only the exact geometry
 * result; colours, UVs and fog still follow the ordinary vertex loader.
 * A changed MVP/aspect clears all keys before lookup, including epoch wrap. */
struct PositionMemo {{
    uint32_t key_x, key_y, key_z, tag;
    float x, y, depth, w, w_inv;
    uint8_t clip;
}};
static struct PositionMemo position_memo[{entries}];
static uint32_t position_memo_epoch;
static uint32_t position_memo_aspect, position_memo_tag;
#endif

'''
 assert s.count(anchor)==1;s=s.replace(anchor,storage+anchor)
 anchor='    const float m00 = rsp.MP_matrix[0][0]'
 init='''#if defined(TARGET_OPENFPGA) && defined(GBI_FLOATS)
    uint32_t position_aspect;
    memcpy(&position_aspect,&aspect_scale,sizeof(position_aspect));
    if (!position_memo_tag || position_memo_epoch != geometry_mvp_epoch || position_memo_aspect != position_aspect) {
        if (++position_memo_tag == 0) {
            for (unsigned i=0;i<sizeof(position_memo)/sizeof(position_memo[0]);i++)
                position_memo[i].tag=0;
            position_memo_tag=1;
        }
        position_memo_epoch=geometry_mvp_epoch;
        position_memo_aspect=position_aspect;
    }
#endif
'''
 assert s.count(anchor)==1;s=s.replace(anchor,init+anchor)
 old='''        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
        float x = (ox * m00 + oy * m10 + oz * m20 + m30) * aspect_scale;
        float y = ox * m01 + oy * m11 + oz * m21 + m31;
        float z = ox * m02 + oy * m12 + oz * m22 + m32;
        float w = ox * m03 + oy * m13 + oz * m23 + m33;
'''
 new=f'''        float x, y, z, w, position_w_inv;
#if defined(TARGET_OPENFPGA) && defined(GBI_FLOATS)
        uint32_t position_x, position_y, position_z;
        memcpy(&position_x,&v->ob[0],sizeof(position_x));
        memcpy(&position_y,&v->ob[1],sizeof(position_y));
        memcpy(&position_z,&v->ob[2],sizeof(position_z));
        const unsigned position_hash=((position_x^(position_y>>7)^(position_z>>13))*2654435761u) >> {32-(entries.bit_length()-1)};
        struct PositionMemo *position=&position_memo[position_hash];
        const bool position_hit=position->tag==position_memo_tag && position->key_x==position_x
            && position->key_y==position_y && position->key_z==position_z;
        if(position_hit) {{
            x=position->x;y=position->y;z=position->depth;w=position->w;
            position_w_inv=position->w_inv;
        }} else
#endif
        {{
            const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
            x = (ox * m00 + oy * m10 + oz * m20 + m30) * aspect_scale;
            y = ox * m01 + oy * m11 + oz * m21 + m31;
            z = ox * m02 + oy * m12 + oz * m22 + m32;
            w = ox * m03 + oy * m13 + oz * m23 + m33;
            position_w_inv=RSPV_RCP(w);
        }}
'''
 assert s.count(old)==1;s=s.replace(old,new)
 old='''        // trivial clip rejection
        d->clip_rej = 0;
        if (x < -w) d->clip_rej |= CLIP_LEFT;
        if (x >  w) d->clip_rej |= CLIP_RIGHT;
        if (y < -w) d->clip_rej |= CLIP_BOTTOM;
        if (y >  w) d->clip_rej |= CLIP_TOP;
        if (z < -w) d->clip_rej |= CLIP_FAR;
        if (z >  w) d->clip_rej |= CLIP_NEAR;
'''
 new='''        // trivial clip rejection
#if defined(TARGET_OPENFPGA) && defined(GBI_FLOATS)
        if(position_hit) d->clip_rej=position->clip;
        else {
#endif
        d->clip_rej = 0;
        if (x < -w) d->clip_rej |= CLIP_LEFT;
        if (x >  w) d->clip_rej |= CLIP_RIGHT;
        if (y < -w) d->clip_rej |= CLIP_BOTTOM;
        if (y >  w) d->clip_rej |= CLIP_TOP;
        if (z < -w) d->clip_rej |= CLIP_FAR;
        if (z >  w) d->clip_rej |= CLIP_NEAR;
#if defined(TARGET_OPENFPGA) && defined(GBI_FLOATS)
            position->key_x=position_x;position->key_y=position_y;position->key_z=position_z;
            position->x=x;position->y=y;position->depth=z;position->w=w;
            position->w_inv=position_w_inv;position->clip=d->clip_rej;
            position->tag=position_memo_tag;
        }
#endif
'''
 assert s.count(old)==1;s=s.replace(old,new)
 # Only the ordinary loader assignment, not clipper/generated vertices.
 old='''        d->w_inv = RSPV_RCP(w);

        if (configEnableFog'''
 assert s.count(old)==1;s=s.replace(old,old.replace('RSPV_RCP(w)','position_w_inv'))
 return s

def main():
 p=argparse.ArgumentParser();p.add_argument('--entries',type=int,default=128);p.add_argument('--output',type=Path,default=C/'position-memo-v3');a=p.parse_args()
 old=(C/'baseline/gfx_pc.c').read_text();new=transform(old,a.entries)
 a.output.mkdir(parents=True,exist_ok=True)
 dest=a.output/'gfx_pc.c';assert not dest.exists() or dest.read_text()==new
 dest.write_text(new)
 (a.output/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/gfx_pc.c',tofile='b/gfx_pc.c')))
 (a.output/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
 record={'qualified':False,'parent_sha256':EXPECTED,'candidate_sha256':sha(new.encode()),'entries':a.entries,'recipe_sha256':sha(Path(__file__).read_bytes()),'scope':'Exact transform and clip-mask reuse for identical object positions and MVP/aspect; attribute/fog/UV work unchanged. Private experiment pending performance and exact output validation.'}
 (a.output/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
if __name__=='__main__':main()
