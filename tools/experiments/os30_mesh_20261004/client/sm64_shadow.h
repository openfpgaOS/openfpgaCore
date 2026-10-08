/* Included after gfx_geometry_decode. Captures producer inputs, never replaces
 * rendering, claims a capability, or writes the GPU. Measurements of this app
 * are coverage/correctness fixtures, not accelerator performance results. */
#include "mesh_client.h"
#include "sm64_guardband.h"
#include "sm64_shader_cache.h"
enum { MESH_SHADOW_JOBS=512 };
static struct MeshCache mesh_shadow_cache;
static const struct MeshVertexEntry *mesh_shadow_vertices;
static uint32_t mesh_shadow_vertex_state[MESH_STATE_WORDS];
static unsigned mesh_shadow_first,mesh_shadow_vertices_count;
static uintptr_t mesh_shadow_source;
static const Gfx *mesh_shadow_next;
unsigned mesh_shadow_count,mesh_shadow_stats[16];
struct MeshOwnedJob mesh_shadow_jobs[MESH_SHADOW_JOBS];

static int mesh_shadow_read_vertex(void *opaque,unsigned i,uint32_t out[4]) {
    const Vtx *vertices=opaque;int16_t position[3];
    for(unsigned k=0;k<3;k++) {
        float f=vertices[i].v.ob[k];
        if(!(f>=-32768.0f&&f<=32767.0f))return 0;
        position[k]=(int16_t)f;if((float)position[k]!=f)return 0;
    }
    out[0]=(uint16_t)position[0]|((uint32_t)(uint16_t)position[1]<<16);
    out[1]=(uint16_t)position[2];
    out[2]=(uint16_t)vertices[i].v.tc[0]|((uint32_t)(uint16_t)vertices[i].v.tc[1]<<16);
    memcpy(&out[3],vertices[i].v.cn,4);return 1;
}
static unsigned mesh_shadow_read_command(void *opaque,unsigned i,unsigned slot[6]) {
    const Gfx *cmd=(const Gfx *)opaque+i;
    uintptr_t end=(uintptr_t)__EH_FRAME_BEGIN__,address=(uintptr_t)cmd;
    if(address>=end||sizeof(*cmd)>end-address)return 0;
    return gfx_geometry_decode(cmd,slot);
}
static void mesh_shadow_capture_vertex(size_t count,size_t first,const Vtx *vertices,float aspect) {
    mesh_shadow_stats[0]++;mesh_shadow_vertices=NULL;mesh_shadow_next=NULL;
    if(first>=32||!count||count>32-first) {mesh_shadow_stats[2]++;return;}
    uintptr_t address=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    int immutable=address>=(uintptr_t)&_start&&address<end&&count<=(end-address)/sizeof(Vtx);
    const struct MeshVertexEntry *entry=NULL;
    int status=mesh_vertices_get(&mesh_shadow_cache,address,count,immutable,
        mesh_shadow_read_vertex,(void *)vertices,&entry);
    if(status) {mesh_shadow_stats[status==MESH_MUTABLE?1:status==MESH_CAPACITY?3:2]++;return;}
    /* Hybrid mode never submits a guard-rejected load. Leave the source
     * unavailable to lazy capture so its original CPU load executes once,
     * without constructing an unused fixed shader or saved RSP snapshot.
     * Diagnostic old-capability captures retain their original full coverage. */
    const int mesh_enabled=gfx_gpu_mesh_enabled();
    unsigned accepted=mesh_enabled?mesh_guardband_accepted(entry,count,first,aspect):0;
    if(mesh_enabled&&!accepted)return;
    uint32_t *state=mesh_shadow_vertex_state;
    int shader_status=mesh_shadow_update_shader(state,aspect);
    if(shader_status) {mesh_shadow_stats[shader_status]++;return;}
    state[21]=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    state[22]=rsp.geometry_mode;
    /* Fog changes vertex alpha when this vertex is loaded, even if material
     * fog is toggled again before the triangle command uses the cached slot. */
    if(!configEnableFog)state[22]&=~G_FOG;
    state[23]=(uint16_t)rsp.fog_mul|((uint32_t)(uint16_t)rsp.fog_offset<<16);
    state[57]=mesh_enabled?accepted:mesh_guardband_accepted(entry,count,first,aspect);
    mesh_shadow_vertices=entry;mesh_shadow_first=first;mesh_shadow_vertices_count=count;
    mesh_shadow_source=address;
}
static void mesh_shadow_capture_run(const Gfx *cmd) {
    if(mesh_shadow_next&&(uintptr_t)cmd<(uintptr_t)mesh_shadow_next)return;
    mesh_shadow_stats[6]++;
    if(!mesh_shadow_vertices) {mesh_shadow_stats[7]++;return;}
    uintptr_t address=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    int immutable=address>=(uintptr_t)&_start&&address<end&&sizeof(Gfx)<=end-address;
    const struct MeshRunEntry *run=NULL;
    int status=mesh_run_get(&mesh_shadow_cache,address,mesh_shadow_first,mesh_shadow_vertices_count,
        immutable,mesh_shadow_read_command,(void *)cmd,&run);
    if(status) {mesh_shadow_stats[8]++;return;}
    mesh_shadow_next=cmd+run->commands;
    if(mesh_shadow_count==MESH_SHADOW_JOBS) {mesh_shadow_stats[9]++;return;}
    struct MeshOwnedJob *job=&mesh_shadow_jobs[mesh_shadow_count++];
    job->sequence=++mesh_shadow_stats[10];job->vertex_count=mesh_shadow_vertices_count;
    job->triangle_count=run->triangles;job->flags=0;
    job->vertex_addr=(uintptr_t)&mesh_shadow_cache.vertices[mesh_shadow_vertices->offset];
    job->index_addr=(uintptr_t)&mesh_shadow_cache.indices[run->offset];
    job->source_vertices=mesh_shadow_source;job->source_commands=(uintptr_t)cmd;
    memcpy(job->state,mesh_shadow_vertex_state,sizeof(job->state));
    uint32_t *s=job->state;
    s[24]=rsp.geometry_mode;s[25]=rdp.other_mode_l;s[26]=rdp.other_mode_h;s[27]=rdp.combine_mode;
    memcpy(s+28,&rdp.viewport,8);memcpy(s+30,&rdp.scissor,8);
    s[32]=rdp.texture_tile.uls|((uint32_t)rdp.texture_tile.ult<<16);
    s[33]=rdp.texture_tile.lrs|((uint32_t)rdp.texture_tile.lrt<<16);
    s[34]=rdp.texture_tile.fmt|((uint32_t)rdp.texture_tile.siz<<8)
         |((uint32_t)rdp.texture_tile.cms<<16)|((uint32_t)rdp.texture_tile.cmt<<24);
    s[35]=rdp.texture_tile.line_size_bytes;
    memcpy(s+36,&rdp.env_color,4);memcpy(s+37,&rdp.prim_color,4);
    memcpy(s+38,&rdp.fog_color,4);memcpy(s+39,&rdp.fill_color,4);
    mesh_shadow_stats[11]+=run->triangles;
    mesh_shadow_stats[12]=mesh_shadow_cache.vertex_decodes;
    mesh_shadow_stats[13]=mesh_shadow_cache.run_decodes;
    mesh_shadow_stats[14]=mesh_shadow_cache.vertex_hits;
    mesh_shadow_stats[15]=mesh_shadow_cache.run_hits;
}
