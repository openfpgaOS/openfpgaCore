/* Private mesh-job producer. No MMIO ABI or capability bit is allocated here. */
#ifndef OS30_MESH_CLIENT_H
#define OS30_MESH_CLIENT_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>

enum { MESH_VERTICES=32, MESH_TRIANGLES=64, MESH_STATE_WORDS=64,
       MESH_CACHE_ENTRIES=1024, MESH_VERTEX_POOL=16384, MESH_INDEX_POOL=16384,
       MESH_BANK_VERTICES=0, MESH_BANK_STATE=128, MESH_BANK_INDICES=192 };
enum { MESH_STATE_MATRIX=0, MESH_STATE_LIGHT=16, MESH_STATE_SCALE=21,
       MESH_STATE_VERTEX_MODE=22, MESH_STATE_FOG=23,
       MESH_STATE_GEOMETRY_MODE=24, MESH_STATE_OTHER_LO=25,
       MESH_STATE_OTHER_HI=26, MESH_STATE_COMBINE=27,
       MESH_STATE_VIEWPORT=28, MESH_STATE_SCISSOR=30,
       MESH_STATE_TILE=32, MESH_STATE_COLORS=36,
       MESH_STATE_FLOAT_MATRIX=40, MESH_STATE_RESERVED=56 };
enum { MESH_OK=0, MESH_MUTABLE=1, MESH_FORMAT=2, MESH_CAPACITY=3,
       MESH_MIXED_LOAD=4, MESH_STATE_UNSUPPORTED=5, MESH_QUEUE_FULL=6 };
struct MeshVertexEntry { uintptr_t key; uint32_t offset; uint8_t count,valid;
                         int16_t bounds[6]; };
struct MeshRunEntry { uintptr_t key; uint32_t offset,commands,used;
                     uint8_t first,count,triangles,valid,compact; };
struct MeshCache {
    struct MeshVertexEntry vertex[MESH_CACHE_ENTRIES];
    struct MeshRunEntry run[MESH_CACHE_ENTRIES];
    uint32_t vertices[MESH_VERTEX_POOL][4],indices[MESH_INDEX_POOL];
    uint32_t vertex_used,index_used,vertex_decodes,run_decodes,vertex_hits,run_hits;
};
/* Callbacks are invoked only on cache misses, never for a warm immutable job. */
typedef int (*mesh_vertex_read)(void *,unsigned,uint32_t[4]);
typedef unsigned (*mesh_command_read)(void *,unsigned,unsigned[6]);
static unsigned mesh_hash(uintptr_t key) {
    return (uint32_t)((key>>3)*UINT32_C(2654435761))>>22;
}
static int mesh_vertices_get(struct MeshCache *c,uintptr_t key,unsigned count,
        int immutable,mesh_vertex_read read,void *ctx,const struct MeshVertexEntry **out) {
    if(!immutable)return MESH_MUTABLE;
    if(!key||!count||count>MESH_VERTICES)return MESH_FORMAT;
    unsigned h=mesh_hash(key^(count*40503u));
    for(unsigned n=0;n<MESH_CACHE_ENTRIES;n++,h=(h+1)&(MESH_CACHE_ENTRIES-1)) {
        struct MeshVertexEntry *e=&c->vertex[h];
        if(e->key==key&&e->count==count) {
            c->vertex_hits++;*out=e;return e->valid?MESH_OK:MESH_FORMAT;
        }
        if(e->key)continue;
        if(c->vertex_used+count>MESH_VERTEX_POOL)return MESH_CAPACITY;
        e->key=key;e->count=count;e->offset=c->vertex_used;e->valid=1;
        /* Failed formats are negatively cached, but storage is never recycled
         * while an old descriptor might still point into this pool. */
        for(unsigned i=0;i<count;i++) {
            c->vertex_decodes++;
            if(!read(ctx,i,c->vertices[e->offset+i]))e->valid=0;
            const uint32_t *v=c->vertices[e->offset+i];
            int16_t position[3]={(int16_t)v[0],(int16_t)(v[0]>>16),(int16_t)v[1]};
            for(unsigned k=0;k<3;k++) {
                if(!i||position[k]<e->bounds[2*k])e->bounds[2*k]=position[k];
                if(!i||position[k]>e->bounds[2*k+1])e->bounds[2*k+1]=position[k];
            }
        }
        c->vertex_used+=count;*out=e;return e->valid?MESH_OK:MESH_FORMAT;
    }
    return MESH_CAPACITY;
}
static int mesh_run_get_format(struct MeshCache *c,uintptr_t key,unsigned first,unsigned count,
        int immutable,int compact,mesh_command_read read,void *ctx,const struct MeshRunEntry **out) {
    if(!immutable)return MESH_MUTABLE;
    if(!key||first>=MESH_VERTICES||!count||count>MESH_VERTICES-first)return MESH_FORMAT;
    unsigned h=mesh_hash(key^((first+count*32u)*40503u));
    for(unsigned n=0;n<MESH_CACHE_ENTRIES;n++,h=(h+1)&(MESH_CACHE_ENTRIES-1)) {
        struct MeshRunEntry *e=&c->run[h];
        if(e->key==key&&e->first==first&&e->count==count&&e->compact==compact) {
            c->run_hits++;*out=e;return e->valid?MESH_OK:MESH_MIXED_LOAD;
        }
        if(e->key)continue;
        e->key=key;e->first=first;e->count=count;e->offset=c->index_used;e->compact=compact;
        for(unsigned command=0;;command++) {
            unsigned slot[6],triangles=read(ctx,command,slot);c->run_decodes++;
            if(!triangles||triangles>2||e->triangles+triangles>MESH_TRIANGLES)break;
            int fits=1;
            for(unsigned i=0;i<3*triangles;i++)
                if(slot[i]<first||slot[i]>=first+count)fits=0;
            /* Stop only at whole-opcode boundaries: TRI2 is indivisible. */
            if(!fits)break;
            unsigned needed=compact?(e->triangles+triangles+1)/2-(e->triangles+1)/2:triangles;
            if(c->index_used+needed>MESH_INDEX_POOL)break;
            for(unsigned i=0;i<triangles;i++) {
                unsigned a=slot[i*3]-first,b=slot[i*3+1]-first,d=slot[i*3+2]-first;
                uint32_t packed=a|(b<<5)|(d<<10);
                if(compact&&(e->triangles&1))c->indices[c->index_used-1]|=packed<<16;
                else c->indices[c->index_used++]=packed;
                e->used|=(1u<<a)|(1u<<b)|(1u<<d);e->triangles++;
            }
            e->commands++;
        }
        e->valid=e->triangles!=0;*out=e;
        return e->valid?MESH_OK:MESH_MIXED_LOAD;
    }
    return MESH_CAPACITY;
}
static int mesh_run_get(struct MeshCache *c,uintptr_t key,unsigned first,unsigned count,
        int immutable,mesh_command_read read,void *ctx,const struct MeshRunEntry **out) {
    return mesh_run_get_format(c,key,first,count,immutable,0,read,ctx,out);
}
static int mesh_run_get_compact(struct MeshCache *c,uintptr_t key,unsigned first,unsigned count,
        int immutable,mesh_command_read read,void *ctx,const struct MeshRunEntry **out) {
    return mesh_run_get_format(c,key,first,count,immutable,1,read,ctx,out);
}

/* An owned record survives ring retirement until completion of its GPU fence.
 * Vertices and indices are immutable pool objects. These records own state;
 * enqueue/dequeue alone cannot give it back to the producer. */
struct MeshOwnedJob {
    uint32_t sequence,vertex_addr,index_addr,vertex_count,triangle_count;
    uint32_t source_vertices,source_commands,flags;
    uint32_t state[MESH_STATE_WORDS];
};
struct MeshSegment { uint32_t address;uint16_t destination,words;uint8_t bank,commit; };
enum { MESH_JOB_COMPACT_INDICES=1 };
static int mesh_job_segments(const struct MeshOwnedJob *j,uint32_t state_address,
                            unsigned bank,struct MeshSegment segments[3]) {
    if(bank>1||!j->vertex_count||j->vertex_count>MESH_VERTICES||
       !j->triangle_count||j->triangle_count>MESH_TRIANGLES||
       ((j->vertex_addr|j->index_addr|state_address)&3))return MESH_FORMAT;
    segments[0]=(struct MeshSegment){j->vertex_addr,MESH_BANK_VERTICES,j->vertex_count*4,bank,0};
    segments[1]=(struct MeshSegment){state_address,MESH_BANK_STATE,MESH_STATE_WORDS,bank,0};
    unsigned index_words=(j->flags&MESH_JOB_COMPACT_INDICES)?(j->triangle_count+1)/2:j->triangle_count;
    segments[2]=(struct MeshSegment){j->index_addr,MESH_BANK_INDICES,index_words,bank,1};
    return MESH_OK;
}
#define MESH_RING_SIZE 8
struct MeshRing { struct MeshOwnedJob jobs[MESH_RING_SIZE];uint32_t submitted,completed; };
static struct MeshOwnedJob *mesh_ring_reserve(struct MeshRing *r) {
    return (uint32_t)(r->submitted-r->completed)<MESH_RING_SIZE?
        &r->jobs[r->submitted&(MESH_RING_SIZE-1)]:NULL;
}
static uint32_t mesh_ring_publish(struct MeshRing *r) {
    struct MeshOwnedJob *j=&r->jobs[r->submitted&(MESH_RING_SIZE-1)];
    j->sequence=++r->submitted;return j->sequence;
}
static int mesh_ring_complete(struct MeshRing *r,uint32_t fence) {
    uint32_t advance=fence-r->completed;
    if(advance>r->submitted-r->completed)return 0;
    r->completed=fence;return 1;
}
#endif
