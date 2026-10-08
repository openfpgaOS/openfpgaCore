#!/usr/bin/env python3
"""One isolated exact source-XYZ alias reuse candidate; generic draws unchanged."""
from pathlib import Path
import difflib,hashlib,json,shutil
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-batch-memory-20261008'
W=P/'work/alias_positions';O=P/'components/alias-positions-v1'
G=Path('src/quake2/src/client/refresh/gl1')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(s,a,b):assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)

LOOKUP='''/* Alias position identity is independent of its UV seam identity. Reuse
 * the existing position/projection memo; this small tag identifies which seam
 * owns the optional cached attributes. It is read only after a current-batch
 * geometry hit, so no extra clear or generation counter is needed. */
static uint16_t ofgl_alias_attridx[OFGL_VMEMO_N];

static ofgl_cvert_t *
ofgl_alias_cvert(int i, int xyz)
{
\tint slot = xyz & (OFGL_VMEMO_N - 1);
\tif (ofgl_vmemo_tag[slot] == ofgl_vmemo_cur && ofgl_vmemo_idx[slot] == xyz)
\t{
\t\tif (ofgl_alias_attridx[slot] != i)
\t\t{
\t\t\tofgl_vmemo[slot].attrs_valid = 0;
\t\t\tofgl_alias_attridx[slot] = i;
\t\t}
\t\treturn &ofgl_vmemo[slot];
\t}
\tofgl_fetch_position(xyz, &ofgl_vmemo[slot]);
\tif (ofgl_batch_inside)
\t\tofgl_vmemo[slot].clipcode = ofgl_vmemo[slot].viewcode = 0;
\telse
\t\tofgl_classify_cvert(&ofgl_vmemo[slot]);
\tofgl_vmemo_idx[slot] = xyz;
\tofgl_vmemo_tag[slot] = ofgl_vmemo_cur;
\tofgl_alias_attridx[slot] = i;
\treturn &ofgl_vmemo[slot];
}

'''
TRI='''static void __attribute__((noinline))
ofgl_alias_colliding_tri(int i0, int i1, int i2, int x0, int x1, int x2,
\tfloat tw, float th)
{
\tofgl_cvert_t a = *ofgl_alias_cvert(i0, x0);
\tofgl_cvert_t b = *ofgl_alias_cvert(i1, x1);
\tofgl_cvert_t c = *ofgl_alias_cvert(i2, x2);
\tofgl_cached_tri(i0, i1, i2, &a, &b, &c, tw, th);
}

static void
ofgl_alias_tri(int i0, int i1, int i2, const byte *map, int stride,
\tfloat tw, float th)
{
\tint x0 = *(const int *)(map + (size_t)i0 * stride);
\tint x1 = *(const int *)(map + (size_t)i1 * stride);
\tint x2 = *(const int *)(map + (size_t)i2 * stride);
\tif (!((x0 ^ x1) & (OFGL_VMEMO_N - 1)) ||
\t\t!((x1 ^ x2) & (OFGL_VMEMO_N - 1)) ||
\t\t!((x0 ^ x2) & (OFGL_VMEMO_N - 1)))
\t{
\t\tofgl_alias_colliding_tri(i0, i1, i2, x0, x1, x2, tw, th);
\t\treturn;
\t}
\tofgl_cvert_t *a = ofgl_alias_cvert(i0, x0);
\tofgl_cvert_t *b = ofgl_alias_cvert(i1, x1);
\tofgl_cvert_t *c = ofgl_alias_cvert(i2, x2);
\tofgl_cached_tri(i0, i1, i2, a, b, c, tw, th);
}

'''
DRAW='''/* Optional geometry-engine requests keep their original staged source ABI.
 * The setting cannot change between buffer construction and immediate apply. */
int
OFGL_AliasPositionsEnabled(void)
{
\treturn !ofgl_geometry_cv || !ofgl_geometry_cv->value;
}

void
OFGL_DrawAliasElements(const int *source_indices, int index_stride,
\tconst float *positions, const unsigned short *indices, int count)
{
\tconst ofq2_texture_t *tex;
\tfloat tw, th;
\tif (!source_indices || !positions || !indices || count < 3 ||
\t\t!ofgl_draw_enabled(OFGL_DRAW_INDEXED)) return;
\tofgl_array_t saved = ofgl_va_vertex;
\tofgl_va_vertex.ptr = (const byte *)positions;
\tofgl_va_vertex.size = 3;
\tofgl_va_vertex.stride = 4 * sizeof(float);
\tif (ofgl_begin3d(&tex, &tw, &th))
\t{
\t\tfor (int g = 0; g + 3 <= count; g += 3)
\t\t\tofgl_alias_tri(indices[g], indices[g + 1], indices[g + 2],
\t\t\t\t(const byte *)source_indices, index_stride, tw, th);
\t\tofgl_end3d();
\t}
\tofgl_va_vertex = saved;
}

'''
def gl(s):
    s=once(s,'/* Sutherland-Hodgman against one plane.',LOOKUP+'/* Sutherland-Hodgman against one plane.')
    s=once(s,'static void\nofgl_end3d(void)',TRI+'static void\nofgl_end3d(void)')
    return once(s,'/* Non-indexed 3D draw (glDrawArrays with a 3-component vertex array): sky box',DRAW+'/* Non-indexed 3D draw (glDrawArrays with a 3-component vertex array): sky box')
def buffer(s):
    s=once(s,'static const of_alias_mesh_t *alias_direct;','static const of_alias_mesh_t *alias_direct;\nstatic const float *alias_positions;')
    s=s.replace('alias_direct = NULL;','alias_direct = NULL;\n\talias_positions = NULL;')
    anchor='void\nR_BufferAliasIndices(const of_alias_mesh_t *mesh)'
    add='''int
R_BufferAliasPositions(const float *positions)
{
\tif (!alias_direct || !OFGL_AliasPositionsEnabled()) return 0;
\talias_positions = positions;
\treturn positions != NULL;
}

'''
    s=once(s,anchor,add+anchor)
    anchor='''\t\tworld_count = 0;
\t}
\telse
#endif
\tglDrawElements'''
    after='''\t\tworld_count = 0;
\t}
\telse if (alias_direct && alias_positions)
\t{
\t\tOFGL_DrawAliasElements(&alias_direct->vertices[0].xyz,
\t\t\tsizeof(alias_direct->vertices[0]), alias_positions,
\t\t\t(const unsigned short *)(alias_direct->vertices + alias_direct->numverts), idx_ptr);
\t}
\telse
#endif
\tglDrawElements'''
    return once(s,anchor,after)
def mesh(s):
    s=once(s,'\t\tif (!direct) R_BufferAliasIndices(mesh);','\t\tif (!direct) R_BufferAliasIndices(mesh);\n\t\tint source_positions = direct && R_BufferAliasPositions(s_lerped[0]);')
    return once(s,'\t\t\tmemcpy(positions, s_lerped[index_xyz], 3 * sizeof(float));\n\t\t\tpositions += 3;',
        '\t\t\tif (!source_positions)\n\t\t\t{\n\t\t\t\tmemcpy(positions, s_lerped[index_xyz], 3 * sizeof(float));\n\t\t\t\tpositions += 3;\n\t\t\t}')
def unit(s):
    s=once(s,'static int direct_enabled;','static int direct_enabled, source_positions_enabled;\nstatic const float *direct_positions;')
    s=once(s,'void R_BufferAliasIndices(const of_alias_mesh_t *mesh)',
        '''int R_BufferAliasPositions(const float *positions)
{
\tif (!source_positions_enabled) return 0;
\tassert(direct_mesh && positions);
\tdirect_positions = positions;
\treturn 1;
}
void R_BufferAliasIndices(const of_alias_mesh_t *mesh)''')
    s=once(s,'\tdirect_mesh = NULL;','\tdirect_mesh = NULL;\n\tdirect_positions = NULL;')
    s=once(s,'\t\tdirect_enabled = iteration & 1;','\t\tdirect_enabled = iteration & 1;\n\t\tsource_positions_enabled = iteration & 2;')
    s=once(s,'\tfor (unsigned i = 0; i < gl_buf.vt; i++)\n\t\tassert(gl_buf.vtx[i] >= draw_mins[i % 3] && gl_buf.vtx[i] <= draw_maxs[i % 3]);',
        '''\tfor (unsigned i = 0; i < gl_buf.vt; i++)
\t{
\t\tfloat value = direct_positions ? direct_positions[4 * direct_mesh->vertices[i / 3].xyz + i % 3] : gl_buf.vtx[i];
\t\tassert(value >= draw_mins[i % 3] && value <= draw_maxs[i % 3]);
\t}''')
    return once(s,'\t\t\tassert(!memcmp(positions + 3 * k, gl_buf.vtx + 3 * index, 3 * sizeof(float)));',
        '''\t\t\tconst float *position = direct_positions ? direct_positions + 4 * direct_mesh->vertices[index].xyz : gl_buf.vtx + 3 * index;
\t\t\tassert(!memcmp(positions + 3 * k, position, 3 * sizeof(float)));''')
def main():
    base=P/'work/base';assert not W.exists() and not O.exists();O.mkdir(parents=True);shutil.copytree(base,W)
    changes={Path('src/quake2/openfpga/of_gl1.c'):gl,G/'gl1_buffer.c':buffer,G/'gl1_mesh.c':mesh,
        G/'header/local.h':lambda s:once(s,'int R_BufferAliasDirect(const of_alias_mesh_t *mesh);','int R_BufferAliasDirect(const of_alias_mesh_t *mesh);\nint R_BufferAliasPositions(const float *positions);'),
        Path('src/quake2/openfpga/of_world.h'):lambda s:once(s,'#endif','''/* Source indices identify tightly packed XYZW interpolation entries; UV and
 * color arrays keep their seam indices. Only standalone immediate alias draws
 * may borrow this storage. Geometry-engine requests retain ordinary staging. */
int OFGL_AliasPositionsEnabled(void);
void OFGL_DrawAliasElements(const int *source_indices, int index_stride,
    const float *positions, const unsigned short *indices, int count);

#endif'''),
        Path('tools/tests/quake2_aliasmesh_test.c'):unit}
    files={};patch=[]
    for rel,fn in changes.items():
        old=(base/rel).read_text();new=fn(old);(W/rel).write_text(new)
        dst=O/'payload'/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_text(new)
        files[str(rel)]=dict(before=sha(base/rel),after=sha(W/rel));patch.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=str(rel),tofile=str(rel)))
    (O/'change.patch').write_text(''.join(patch));(O/'manifest.json').write_text(json.dumps(dict(candidate=str(W),files=files,changes='Alias-only source-XYZ memo and direct XYZW positions; distinct seam attributes, existing exact arithmetic, generic paths unchanged.',memory='Existing256-entry cvert memo reused;512-byte seam tags plus borrowed position pointer.'),indent=2)+'\n');print((O/'manifest.json').read_text())
if __name__=='__main__':main()
