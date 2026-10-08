#!/usr/bin/env python3
"""Temporarily instrument the private app; restore after freezing diagnostic ELF."""
from pathlib import Path
import argparse
ROOT=Path(__file__).resolve().parents[4]
GFX=ROOT/'docs/measurements/os30-mesh-20261004/SM64/src/sm64/sm64/src/pc/gfx'
p=argparse.ArgumentParser();p.add_argument('--restore',action='store_true');a=p.parse_args()
names=('mesh_producer.h','sm64_material_gpu.h')
if a.restore:
    for name in names:
        path=GFX/name;backup=GFX/(name+'.coverage-save')
        assert backup.exists();path.write_bytes(backup.read_bytes());backup.unlink()
    raise SystemExit
for name in names:
    path=GFX/name;backup=GFX/(name+'.coverage-save')
    assert not backup.exists();backup.write_bytes(path.read_bytes())
path=GFX/'mesh_producer.h';s=path.read_text()
head=r'''
/* Diagnostic-only cumulative counters. Never used for timing comparisons.
 * Per-reason fields: calls, faces, referenced slots, accepted referenced slots,
 * faces with any accepted vertex, faces with all accepted vertices,
 * faces while current whole LOAD is accepted, pending referenced slots. */
uint32_t mesh_coverage[16][8],mesh_coverage_backend[32][4];
extern unsigned gfx_gpu_mesh_failure_reason(void);
static void mesh_coverage_record(const Gfx *cmd,unsigned reason,unsigned commands) {
    uint32_t *row=mesh_coverage[reason];row[0]++;
    uint32_t accepted=mesh_lazy_depth_valid|(mesh_lazy_fixed?mesh_lazy_pending:0);
    unsigned backend=reason==8?gfx_gpu_mesh_failure_reason():0;
    for(unsigned c=0;c<commands;c++) {
        unsigned slots[6],n=mesh_shadow_read_command((void *)cmd,c,slots);
        assert(n<=2);
        uint32_t used=0;
        for(unsigned i=0;i<n;i++) {
            uint32_t face=0;
            for(unsigned k=0;k<3;k++) {assert(slots[3*i+k]<32);face|=1u<<slots[3*i+k];}
            row[1]++;used|=face;
            row[4]+=(face&accepted)!=0;row[5]+=(face&accepted)==face;
            row[6]+=mesh_shadow_vertices!=NULL;
            if(reason==8) {
                mesh_coverage_backend[backend][0]++;
                mesh_coverage_backend[backend][1]+=(face&accepted)!=0;
                mesh_coverage_backend[backend][2]+=(face&accepted)==face;
                mesh_coverage_backend[backend][3]+=mesh_shadow_vertices!=NULL;
            }
        }
        row[2]+=__builtin_popcount(used);row[3]+=__builtin_popcount(used&accepted);
        row[7]+=__builtin_popcount(used&mesh_lazy_pending);
    }
}
'''
if 'struct MeshLazyContext' in (GFX/'sm64_lazy_state.h').read_text():
    head=head.replace('    uint32_t accepted=mesh_lazy_depth_valid|(mesh_lazy_fixed?mesh_lazy_pending:0);',
        '    uint32_t accepted=mesh_lazy_depth_valid;\n'
        '    for(unsigned i=0;i<32;i++)if(mesh_lazy_live&(1u<<i))\n'
        '        if(mesh_lazy_context[i].fixed)accepted|=mesh_lazy_context[i].pending;')
def rep(old,new):
    global s
    assert s.count(old)==1,(old,s.count(old));s=s.replace(old,new)
rep('    if(!gfx_gpu_mesh_enabled()||!mesh_shadow_vertices)return cmd;',
    '    if(!gfx_gpu_mesh_enabled()) {mesh_coverage_record(cmd,1,1);return cmd;}\n'
    '    if(!mesh_shadow_vertices) {mesh_coverage_record(cmd,2,1);return cmd;}')
rep('    if(!(mesh_shadow_vertex_state[57]&1u))return cmd;',
    '    if(!(mesh_shadow_vertex_state[57]&1u)) {mesh_coverage_record(cmd,3,1);return cmd;}')
rep('    if(mesh_run_get_compact(&mesh_shadow_cache,address,mesh_shadow_first,\n'
    '            mesh_shadow_vertices_count,immutable,mesh_shadow_read_command,(void *)cmd,&run))return cmd;',
    '    unsigned run_status=mesh_run_get_compact(&mesh_shadow_cache,address,mesh_shadow_first,\n'
    '            mesh_shadow_vertices_count,immutable,mesh_shadow_read_command,(void *)cmd,&run);\n'
    '    if(run_status) {mesh_coverage_record(cmd,3+run_status,1);return cmd;}')
rep('    if(mesh_capture_material_state(mesh_shadow_vertex_state,state)!=MESH_MATERIAL_OK)return cmd;',
    '    unsigned material_status=mesh_capture_material_state(mesh_shadow_vertex_state,state);\n'
    '    if(material_status) {mesh_coverage_record(cmd,7+material_status,1);return cmd;}')
rep('            run->triangles,state))return cmd;',
    '            run->triangles,state)) {mesh_coverage_record(cmd,12,1);return cmd;}')
rep('    return cmd+run->commands;',
    '    mesh_coverage_record(cmd,0,run->commands);\n    return cmd+run->commands;')
path.write_text('#include <assert.h>\n'+head+s)
path=GFX/'sm64_material_gpu.h';s=path.read_text()
s+=r'''
/* Backend rejection components: truecolor1, missing shader2, decal4,
 * blend texel alpha8, C+D textured combine16. */
unsigned gfx_gpu_mesh_failure_reason(void) {
    unsigned reason=(!g_truecolor?1u:0u)|(!g_cur_shader?2u:0u);
    if(!g_cur_shader)return reason;
    const struct CCFeatures *cc=&g_cur_shader->cc;
    int textured=cc->used_textures[0]&&g_cur_tex[0]&&g_cur_tex[0]->ci8;
    int a,b,d;
    if(g_decal)reason|=4;
    if(cc_blend_texel_alpha(cc))reason|=8;
    if(textured&&classify_combine_cd(cc,&a,&b,&d))reason|=16;
    return reason;
}
'''
path.write_text(s)
print('Temporary diagnostic headers staged; restore after building coverage ELF')
