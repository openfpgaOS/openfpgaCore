#!/usr/bin/env python3
"""One bounded world-plane-mask candidate, with per-edge BSP containment."""
from pathlib import Path
import difflib,hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/quake2-comprehensive-20261008'
OUT=P/'components/plane-mask-v1'
GAME=Path('src/quake2/src/client/refresh')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)

GUARD='''#ifdef YQ2_OPENFPGA
/* Stored BSP bounds are signed shorts. Bound the live frustum as well so
 * support-corner dot products remain finite and monotonic under rounding.
 * Unexpected planes retain the original all-plane traversal. */
static int
R_WorldPlaneMask(void)
{
\tfor (int f = 0; f < 4; f++)
\t{
\t\tuint32_t bits;
\t\tmemcpy(&bits, &frustum[f].dist, sizeof(bits));
\t\tif ((bits & 0x7fffffffu) >= 0x7f800000u) return -1;
\t\tif (frustum[f].type >= 3)
\t\t{
\t\t\tunsigned signs = 0;
\t\t\tfor (int j = 0; j < 3; j++)
\t\t\t{
\t\t\t\tmemcpy(&bits, &frustum[f].normal[j], sizeof(bits));
\t\t\t\tunsigned magnitude = bits & 0x7fffffffu;
\t\t\t\tif (magnitude > 0x47800000u) return -1;
\t\t\t\tif ((bits >> 31) && magnitude) signs |= 1u << j;
\t\t\t}
\t\t\tif (signs != frustum[f].signbits) return -1;
\t\t}
\t}
\treturn 15;
}
#else
#define R_WorldPlaneMask() (-1)
#endif

'''

def nodes(s):
    anchor='\tMod_SetParent(*nodes, NULL); /* sets nodes and leafs */'
    add='''#ifdef YQ2_OPENFPGA
\t/* Some valid maps have child bounds outside their stored parent bounds.
\t * Only inherit plane results across edges with verified containment. */
\tfor (i = 0, out = *nodes; i < count; i++, out++)
\t{
\t\tout->clip_children = 0;
\t\tfor (int child = 0; child < 2; child++)
\t\t{
\t\t\tconst float *bounds = out->children[child]->minmaxs;
\t\t\tint contained = 1;
\t\t\tfor (int axis = 0; axis < 3; axis++)
\t\t\t\tif (out->minmaxs[axis] > out->minmaxs[axis + 3] ||
\t\t\t\t\tbounds[axis] > bounds[axis + 3] ||
\t\t\t\t\tbounds[axis] < out->minmaxs[axis] ||
\t\t\t\t\tbounds[axis + 3] > out->minmaxs[axis + 3])
\t\t\t\t\tcontained = 0;
\t\t\tif (contained) out->clip_children |= 1u << child;
\t\t}
\t}
#endif

'''
    return once(s,anchor,add+anchor)

def surf(s):
    anchor='static void\nR_RecursiveWorldNode(entity_t *currententity, mnode_t *node)'
    s=once(s,anchor,GUARD+anchor.replace('mnode_t *node)','mnode_t *node, int plane_mask)'))
    before='''\tif (r_cull->value && R_CullBox(node->minmaxs, node->minmaxs + 3, frustum))
\t{
\t\treturn;
\t}'''
    after='''\tif (r_cull->value)
\t{
#ifdef YQ2_OPENFPGA
\t\tif (plane_mask >= 0)
\t\t{
\t\t\tfor (int f = 0; f < 4; f++)
\t\t\t\tif (plane_mask & (1u << f))
\t\t\t\t{
\t\t\t\t\tint side = BOX_ON_PLANE_SIDE(node->minmaxs,
\t\t\t\t\t\tnode->minmaxs + 3, frustum + f);
\t\t\t\t\tif (side == 2) return;
\t\t\t\t\tif (side == 1) plane_mask &= ~(1u << f);
\t\t\t\t}
\t\t}
\t\telse
#endif
\t\tif (R_CullBox(node->minmaxs, node->minmaxs + 3, frustum))
\t\t\treturn;
\t}'''
    s=once(s,before,after)
    for side in ['side','!side']:
        before=f'\tR_RecursiveWorldNode(currententity, node->children[{side}]);'
        after=f'''\tR_RecursiveWorldNode(currententity, node->children[{side}],
#ifdef YQ2_OPENFPGA
\t\tplane_mask | ((node->clip_children & (1u << ({side}))) ? 0 : 15)
#else
\t\t-1
#endif
\t);'''
        s=once(s,before,after)
    return once(s,'R_RecursiveWorldNode(&ent, r_worldmodel->nodes);',
        'R_RecursiveWorldNode(&ent, r_worldmodel->nodes, R_WorldPlaneMask());')

def main():
    base=P/'work/base';work=P/'work/plane_mask'
    assert not work.exists() and not OUT.exists()
    OUT.mkdir(parents=True);shutil.copytree(base,work)
    changes={GAME/'ref_shared.h':lambda s:once(s,'\tunsigned short\tnumsurfaces;\n} mnode_t;',
        '\tunsigned short\tnumsurfaces;\n#ifdef YQ2_OPENFPGA\n\tbyte clip_children; /* contained child bounds: safe inherited frustum planes */\n#endif\n} mnode_t;'),
        GAME/'files/models.c':nodes,GAME/'gl1/gl1_surf.c':surf}
    manifest={};patch=[]
    for rel,fn in changes.items():
        old=(base/rel).read_text();new=fn(old);(work/rel).write_text(new)
        saved=OUT/'payload'/rel;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_text(new)
        manifest[str(rel)]=dict(before=sha(base/rel),after=sha(work/rel))
        patch.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=str(rel),tofile=str(rel)))
    (OUT/'change.patch').write_text(''.join(patch))
    (OUT/'manifest.json').write_text(json.dumps(dict(candidate=str(work),files=manifest,
        changes='Inherit fully accepted frustum planes only across loader-verified contained BSP edges; retain all-plane fallback otherwise.',
        cost='One byte plus struct alignment per BSP node (4 bytes/node on RV32); no generic vertex or alias path changes.',
        fp='Same BOX_ON_PLANE_SIDE arithmetic; bounded finite plane guard prevents overflow or nonfinite monotonicity assumptions.'),indent=2)+'\n')
    print((OUT/'manifest.json').read_text())
if __name__=='__main__':main()
