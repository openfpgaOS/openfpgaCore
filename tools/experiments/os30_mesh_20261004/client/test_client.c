#include "mesh_client.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
static struct MeshCache cache;
struct Source { unsigned first,count,commands;int invalid;unsigned vcalls,icalls; };
static int vertex(void *ctx,unsigned i,uint32_t out[4]) {
    struct Source *s=ctx;s->vcalls++;
    for(unsigned k=0;k<4;k++)out[k]=(i+1)*100+k;
    return !s->invalid;
}
static unsigned command(void *ctx,unsigned i,unsigned slot[6]) {
    struct Source *s=ctx;s->icalls++;
    if(i>=s->commands)return 0;
    for(unsigned k=0;k<6;k++)slot[k]=s->first+(i*6+k)%s->count;
    return 2;
}
static unsigned command_odd(void *ctx,unsigned i,unsigned slot[6]) {
    return command(ctx,i,slot)?1+(i&1):0;
}
static int boundary_vertex(void *ctx,unsigned i,uint32_t out[4]) {
    (void)ctx;
    static const int16_t xyz[3][3]={{-32768,0,32767},{32767,-32768,-32768},{0,32767,-1}};
    out[0]=(uint16_t)xyz[i][0]|((uint32_t)(uint16_t)xyz[i][1]<<16);
    out[1]=(uint16_t)xyz[i][2];out[2]=out[3]=0;return 1;
}
int main(void) {
    struct Source source={5,19,16,0,0,0};
    const struct MeshVertexEntry *v=NULL;
    const struct MeshRunEntry *r=NULL;
    assert(mesh_vertices_get(&cache,0x10000000,19,1,vertex,&source,&v)==MESH_OK);
    assert(mesh_run_get(&cache,0x10010000,5,19,1,command,&source,&r)==MESH_OK);
    assert(v->count==19&&r->triangles==32&&r->commands==16);
    assert(v->bounds[0]==100&&v->bounds[1]==1900&&v->bounds[2]==0&&v->bounds[3]==0
           &&v->bounds[4]==101&&v->bounds[5]==1901);
    uint32_t vertex_copy[19][4],index_copy[32];
    memcpy(vertex_copy,&cache.vertices[v->offset],sizeof vertex_copy);
    memcpy(index_copy,&cache.indices[r->offset],sizeof index_copy);
    for(unsigned i=0;i<32;i++) {
        uint32_t p=index_copy[i];
        assert((p&31)==(i*3)%19&&((p>>5)&31)==(i*3+1)%19&&((p>>10)&31)==(i*3+2)%19);
    }
    unsigned vreads=source.vcalls,ireads=source.icalls;
    for(unsigned i=0;i<100000;i++) {
        assert(mesh_vertices_get(&cache,0x10000000,19,1,vertex,&source,&v)==MESH_OK);
        assert(mesh_run_get(&cache,0x10010000,5,19,1,command,&source,&r)==MESH_OK);
    }
    assert(vreads==source.vcalls&&ireads==source.icalls);
    unsigned compact_start=cache.index_used;
    assert(mesh_run_get_compact(&cache,0x10010000,5,19,1,command,&source,&r)==MESH_OK);
    assert(r->compact&&r->offset==compact_start&&cache.index_used==compact_start+16);
    for(unsigned i=0;i<32;i++)assert(((cache.indices[r->offset+i/2]>>((i&1)*16))&32767)==index_copy[i]);
    ireads=source.icalls;
    for(unsigned i=0;i<100000;i++)
        assert(mesh_run_get_compact(&cache,0x10010000,5,19,1,command,&source,&r)==MESH_OK);
    assert(ireads==source.icalls);
    const struct MeshVertexEntry *boundary;
    assert(mesh_vertices_get(&cache,0x11000000,3,1,boundary_vertex,NULL,&boundary)==MESH_OK);
    for(unsigned k=0;k<3;k++)assert(boundary->bounds[2*k]==-32768&&boundary->bounds[2*k+1]==32767);
    const struct MeshRunEntry *odd_plain,*odd_packed;
    assert(mesh_run_get(&cache,0x10010100,5,19,1,command_odd,&source,&odd_plain)==MESH_OK);
    assert(mesh_run_get_compact(&cache,0x10010100,5,19,1,command_odd,&source,&odd_packed)==MESH_OK);
    assert(odd_plain->triangles==24&&odd_packed->triangles==24&&odd_packed->commands==16);
    for(unsigned i=0;i<24;i++)assert(((cache.indices[odd_packed->offset+i/2]>>((i&1)*16))&32767)==
                                     cache.indices[odd_plain->offset+i]);
    assert(mesh_vertices_get(&cache,0x20000000,19,0,vertex,&source,&v)==MESH_MUTABLE);
    assert(mesh_vertices_get(&cache,0x20000000,33,1,vertex,&source,&v)==MESH_FORMAT);
    source.invalid=1;
    assert(mesh_vertices_get(&cache,0x20000000,19,1,vertex,&source,&v)==MESH_FORMAT);
    vreads=source.vcalls;
    assert(mesh_vertices_get(&cache,0x20000000,19,1,vertex,&source,&v)==MESH_FORMAT);
    assert(source.vcalls==vreads);source.invalid=0;
    /* Same commands bound to another partial load must not reuse stale indices. */
    assert(mesh_run_get(&cache,0x10010000,6,18,1,command,&source,&r)==MESH_MIXED_LOAD);
    source.commands=40;source.first=0;source.count=32;
    assert(mesh_run_get(&cache,0x10020000,0,32,1,command,&source,&r)==MESH_OK);
    assert(r->triangles==64&&r->commands==32);
    /* Fill the cache: no recycling can alter old asynchronously owned data. */
    for(unsigned i=0;i<2000;i++)
        mesh_vertices_get(&cache,0x30000000+i*0x100,32,1,vertex,&source,&v);
    assert(memcmp(vertex_copy,&cache.vertices[0],sizeof vertex_copy)==0);
    assert(memcmp(index_copy,&cache.indices[0],sizeof index_copy)==0);
    struct MeshRing ring={0};
    ring.submitted=ring.completed=UINT32_MAX-3;
    for(unsigned i=0;i<MESH_RING_SIZE;i++) {
        struct MeshOwnedJob *j=mesh_ring_reserve(&ring);assert(j);
        memset(j,0xa5,sizeof *j);mesh_ring_publish(&ring);
    }
    assert(!mesh_ring_reserve(&ring));
    uint32_t last=ring.submitted;
    assert(!mesh_ring_complete(&ring,last+1));
    assert(mesh_ring_complete(&ring,last-4));
    for(unsigned i=0;i<4;i++) {assert(mesh_ring_reserve(&ring));mesh_ring_publish(&ring);}
    assert(!mesh_ring_reserve(&ring));
    assert(!mesh_ring_complete(&ring,last-5));
    assert(mesh_ring_complete(&ring,ring.submitted));assert(mesh_ring_reserve(&ring));
    struct MeshOwnedJob job={.vertex_addr=0x40000000,.index_addr=0x40000100,
                             .vertex_count=32,.triangle_count=64};
    struct MeshSegment segments[3];
    assert(mesh_job_segments(&job,0x40010000,1,segments)==MESH_OK);
    assert(segments[0].words==128&&segments[0].destination==0&&!segments[0].commit);
    assert(segments[1].words==64&&segments[1].destination==128&&!segments[1].commit);
    assert(segments[2].words==64&&segments[2].destination==192&&segments[2].commit);
    job.flags=MESH_JOB_COMPACT_INDICES;
    assert(mesh_job_segments(&job,0x40010000,1,segments)==MESH_OK&&segments[2].words==32);
    job.triangle_count=63;
    assert(mesh_job_segments(&job,0x40010000,1,segments)==MESH_OK&&segments[2].words==32);
    assert(mesh_job_segments(&job,0x40010002,1,segments)==MESH_FORMAT);
    assert(mesh_job_segments(&job,0x40010000,2,segments)==MESH_FORMAT);
    job.triangle_count=65;assert(mesh_job_segments(&job,0x40010000,0,segments)==MESH_FORMAT);
    puts("PASS: 100000 warm jobs, packed order, partial-load exclusion, immutable lifetime, capacity, fence wrap/backpressure");
}
