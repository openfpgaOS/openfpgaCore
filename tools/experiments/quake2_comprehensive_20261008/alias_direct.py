#!/usr/bin/env python3
"""Prepare immutable MD2 index/UV views, preserving the buffered fallback."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/quake2-comprehensive-20261008'
OUT=P/'components/alias-direct-v1'
GAME=Path('src/quake2/src/client/refresh/gl1')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b)

def buffer(s):
    s=once(s,'static int world_count;','static int world_count;\nstatic const of_alias_mesh_t *alias_direct;')
    anchor='void\nR_BufferAliasIndices(const of_alias_mesh_t *mesh)\n{'
    direct='''/* Cached alias draws are flushed by R_DrawAliasModel before their model
 * or staging storage can change. Keep immutable indices and UVs in that model
 * when no earlier buffered geometry needs an index offset. */
int
R_BufferAliasDirect(const of_alias_mesh_t *mesh)
{
\tif (!mesh || gl_buf.type != buf_alias || world_count || vtx_ptr || idx_ptr ||
\t\tmesh->numverts < 1 || mesh->numverts >= MAX_VERTICES ||
\t\tmesh->numindices < 1 || mesh->numindices >= MAX_INDICES)
\t\treturn 0;
\talias_direct = mesh;
\tvtx_ptr = mesh->numverts;
\tidx_ptr = mesh->numindices;
\treturn 1;
}

'''
    s=once(s,anchor,direct+anchor)
    s=once(s,'if (world_count || vtx_ptr + mesh->numverts >= MAX_VERTICES ||',
        'if (alias_direct || world_count || vtx_ptr + mesh->numverts >= MAX_VERTICES ||')
    s=once(s,'#ifdef YQ2_OPENFPGA\n\tworld_count = 0;\n#endif',
        '#ifdef YQ2_OPENFPGA\n\tworld_count = 0;\n\talias_direct = NULL;\n#endif')
    s=once(s,'\t\tglTexCoordPointer(2, GL_FLOAT, 0, gl_buf.tex[0]);',
        '''#ifdef YQ2_OPENFPGA
\t\tif (alias_direct)
\t\t\tglTexCoordPointer(2, GL_FLOAT, sizeof(alias_direct->vertices[0]),
\t\t\t\t&alias_direct->vertices[0].s);
\t\telse
#endif
\t\tglTexCoordPointer(2, GL_FLOAT, 0, gl_buf.tex[0]);''')
    s=once(s,'\tglDrawElements(GL_TRIANGLES, idx_ptr, GL_UNSIGNED_SHORT, gl_buf.idx);',
        '''\tglDrawElements(GL_TRIANGLES, idx_ptr, GL_UNSIGNED_SHORT,
#ifdef YQ2_OPENFPGA
\t\talias_direct ? (const unsigned short *)(alias_direct->vertices + alias_direct->numverts) :
#endif
\t\tgl_buf.idx);''')
    s=once(s,'\tGLBUFFER_RESET\n}\n\n/*\n * Sets current state',
        '\tGLBUFFER_RESET\n#ifdef YQ2_OPENFPGA\n\talias_direct = NULL;\n#endif\n}\n\n/*\n * Sets current state')
    # A future append must first finish a direct view; the existing ordinary
    # append behavior remains unchanged for every other batch.
    s=once(s,'\tif (idx_ptr > idx_max || vtx_ptr > vtx_max)',
        '#ifdef YQ2_OPENFPGA\n\tif (alias_direct) R_ApplyGLBuffer();\n#endif\n\tif (idx_ptr > idx_max || vtx_ptr > vtx_max)')
    s=once(s,'#ifdef YQ2_OPENFPGA\n\tif (world_count)\n\t\tR_ApplyGLBuffer();\n#endif',
        '#ifdef YQ2_OPENFPGA\n\tif (world_count || alias_direct)\n\t\tR_ApplyGLBuffer();\n#endif')
    return s

def mesh(s):
    s=once(s,'\t\tR_BufferAliasIndices(mesh);',
        '\t\tint direct = R_BufferAliasDirect(mesh);\n\t\tif (!direct) R_BufferAliasIndices(mesh);')
    s=once(s,'\t\t\tif (!shell)\n\t\t\t{\n\t\t\t\ttexcoords[0] = entry->s;',
        '\t\t\tif (!shell && !direct)\n\t\t\t{\n\t\t\t\ttexcoords[0] = entry->s;')
    s=once(s,'\t\tif (!shell) gl_buf.tx += mesh->numverts * 2;',
        '\t\tif (!shell && !direct) gl_buf.tx += mesh->numverts * 2;')
    return s

def unit(s):
    # This established mesh differential retains its original buffered path.
    # Alternate direct views exercise the same submitted per-index attributes.
    s=once(s,'static int vertices, indices;',
        'static int vertices, indices;\nstatic int direct_enabled;\nstatic const of_alias_mesh_t *direct_mesh;')
    anchor='void R_BufferAliasIndices(const of_alias_mesh_t *mesh)\n{'
    s=once(s,anchor,'''int R_BufferAliasDirect(const of_alias_mesh_t *mesh)
{
\tif (!direct_enabled) return 0;
\tassert(!bounds_enabled && !vertices && !indices);
\tdirect_mesh = mesh; vertices = mesh->numverts; indices = mesh->numindices;
\treturn 1;
}
'''+anchor)
    s=once(s,'\tgl_buf.vt = gl_buf.tx = gl_buf.cl = vertices = indices = 0;',
        '\tgl_buf.vt = gl_buf.tx = gl_buf.cl = vertices = indices = 0;\n\tdirect_mesh = NULL;')
    s=once(s,'\t\treset_buffer(); model.of_alias = mesh;',
        '\t\treset_buffer(); model.of_alias = mesh;\n\t\tdirect_enabled = iteration & 1;')
    s=once(s,'gl_buf.tx == (textured ? 10 : 0)',
        'gl_buf.tx == (textured && !direct_enabled ? 10 : 0)')
    before='''\t\t\tint index = gl_buf.idx[k];
\t\t\tassert(!memcmp(positions + 3 * k, gl_buf.vtx + 3 * index, 3 * sizeof(float)));
\t\t\tif (textured) assert(!memcmp(texcoords + 2 * k, gl_buf.tex[0] + 2 * index, 2 * sizeof(float)));'''
    after='''\t\t\tconst unsigned short *draw_indices = direct_mesh ?
\t\t\t\t(const unsigned short *)(direct_mesh->vertices + direct_mesh->numverts) : gl_buf.idx;
\t\t\tint index = draw_indices[k];
\t\t\tassert(!memcmp(positions + 3 * k, gl_buf.vtx + 3 * index, 3 * sizeof(float)));
\t\t\tconst float *uv = direct_mesh ? &direct_mesh->vertices[index].s : gl_buf.tex[0] + 2 * index;
\t\t\tif (textured) assert(!memcmp(texcoords + 2 * k, uv, 2 * sizeof(float)));'''
    s=once(s,before,after)
    s=once(s,'PASS: MD2 cached/fallback parity;', 'PASS: MD2 direct/cached/fallback parity;')
    return s

def main():
    base=P/'work/base';work=P/'work/alias'
    assert not work.exists() and not OUT.exists()
    OUT.mkdir(parents=True);shutil.copytree(base,work)
    changes={GAME/'gl1_buffer.c':buffer,GAME/'gl1_mesh.c':mesh,
        GAME/'header/local.h':lambda s:once(s,'void R_BufferAliasIndices(const of_alias_mesh_t *mesh);',
            'void R_BufferAliasIndices(const of_alias_mesh_t *mesh);\nint R_BufferAliasDirect(const of_alias_mesh_t *mesh);'),
        Path('tools/tests/quake2_aliasmesh_test.c'):unit}
    manifest={};patch=[]
    for rel,fn in changes.items():
        old=(base/rel).read_text();new=fn(old);(work/rel).write_text(new)
        saved=OUT/'payload'/rel;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_text(new)
        manifest[str(rel)]=dict(before=sha(base/rel),after=sha(work/rel))
        patch.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=str(rel),tofile=str(rel)))
    (OUT/'change.patch').write_text(''.join(patch))
    (OUT/'manifest.json').write_text(json.dumps(dict(candidate=str(work),files=manifest,
        changes='Use model-owned indices and strided UVs only for an empty standalone alias batch; preserve ordinary positions, colors and all fallback math.',
        lifetime='R_DrawAliasModel immediately applies its draw; reset/apply and subsequent append clear or flush the direct view.'),indent=2)+'\n')
    print((OUT/'manifest.json').read_text())

if __name__=='__main__':main()
