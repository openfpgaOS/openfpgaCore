
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
static uint32_t memory[0x1000000/4];
static unsigned owner,waits,drains,uploads,checks;
static uint32_t gfx_async_stats[16],gfx_async_hint_stats[4],gfx_async_source_stats[4];
static void check(int v){++checks;assert(v);}
void gfx_gpu_async_drain(void){++drains;owner=0;}
void gfx_gpu_mesh_wait_source(void){++waits;owner=0;}
void gfx_async_forget_sources(void);
void _of_gpu_fast_tex_upload(uint32_t address,const uint32_t *source,unsigned count){
 check(!owner);check(!(address&3));check(address+count*4<=sizeof(memory));
 memcpy((uint8_t*)memory+address,source,count*4);++uploads;
}
struct MeshTask {
    const Gfx *source;
    Gfx *next;
    uint16_t commands, triangles;
    uint16_t dead_after, prefer_legacy;
};
struct AsyncTaskSources {
    const void *vertices;
    uint32_t generation,vertex_offset,command_offset,counts;
};
static struct MeshTask mesh_tasks[512];
static struct AsyncTaskSources async_task_sources[512];
#define CRAM_VERTEX_BEGIN 0x00fc0000u
#define CRAM_VERTEX_END   0x01000000u
#define CRAM_VERTEX_ENTRIES 1024u
#define CRAM_VERTEX_PROBES 16u
struct CramVertexSource { uintptr_t source; uint32_t offset_count; };
static struct CramVertexSource g_cram_vertex_source[CRAM_VERTEX_ENTRIES];
static uint32_t g_cram_vertex_top=CRAM_VERTEX_BEGIN;
static uint32_t g_cram_source_generation=1;
uint32_t gfx_gpu_cram_source_generation(void) { return g_cram_source_generation; }
/* Entries bind immutable TRI command bytes to one physical arena generation.
 * Cold uploads only; steady task hits never rescan or repack triangle words. */
struct CramMeshSource { uintptr_t source; uint32_t offset_count; };
static struct CramMeshSource g_cram_mesh_source[512];
static unsigned g_cram_vertex_enabled;
static uint32_t g_cram_vertex_frame,g_cram_vertex_last_recycle;
static unsigned g_cram_vertex_recycled;
/* Recycles and throttled capacity misses; cold-path counters only. */
uint32_t cram_vertex_recycle_stats[2];
static __attribute__((cold,noinline)) int gpu_cram_vertex_recycle(void) {
    if(g_cram_vertex_recycled &&
       (uint32_t)(g_cram_vertex_frame-g_cram_vertex_last_recycle)<120u) {
        cram_vertex_recycle_stats[1]++;return 0;
    }
    gfx_gpu_async_drain();
    gfx_gpu_mesh_wait_source();
    /* The synchronous caller has drained LOAD and copied or abandoned its
     * BRAM result. GPU packets contain values, never source-pool pointers.
     * No renderer/interrupt concurrently uses this CPU-only descriptor table;
     * clear every old key before any later upload can reuse its storage. */
    memset(g_cram_vertex_source,0,sizeof(g_cram_vertex_source));
    memset(g_cram_mesh_source,0,sizeof(g_cram_mesh_source));
    ++g_cram_source_generation;
    /* Invalidate parallel task-offset hints on every real recycle, including
     * generation wrap. This does not discard immutable topology/repair hints. */
    gfx_async_forget_sources();
    g_cram_vertex_top=CRAM_VERTEX_BEGIN;
    g_cram_vertex_last_recycle=g_cram_vertex_frame;
    g_cram_vertex_recycled=1;cram_vertex_recycle_stats[0]++;
    return 1;
}
/* calls,hits,uploads,uploaded words,capacity failures,probe failures,probes,
 * unavailable. These include the real client work in every measured build. */
uint32_t cram_vertex_stats[8];
int gfx_gpu_cram_vertex_source(const void *source,unsigned count,uint32_t *offset) {
    cram_vertex_stats[0]++;
    if(!g_cram_vertex_enabled||!count||count>32||((uintptr_t)source&3)) {
        cram_vertex_stats[7]++;return 0;
    }
    const uintptr_t p=(uintptr_t)source;
    const unsigned h=((p>>4)^(p>>14)^(count*33u))&(CRAM_VERTEX_ENTRIES-1);
retry_after_recycle:
    for(unsigned probe=0;probe<CRAM_VERTEX_PROBES;probe++) {
        struct CramVertexSource *e=&g_cram_vertex_source[(h+probe)&(CRAM_VERTEX_ENTRIES-1)];
        cram_vertex_stats[6]++;
        if(e->source==p&&(e->offset_count>>24)==count) {
            *offset=e->offset_count&0xffffffu;cram_vertex_stats[1]++;return 1;
        }
        if(!e->source) {
            const unsigned bytes=count*24u,allocation=(bytes+63u)&~63u;
            if(allocation>CRAM_VERTEX_END-g_cram_vertex_top) {
                if(gpu_cram_vertex_recycle())goto retry_after_recycle;
                cram_vertex_stats[4]++;return 0;
            }
            /* Caller has drained any prior LOAD. Upload helper drains the final
             * word before publication of this immutable descriptor or START. */
            _of_gpu_fast_tex_upload(g_cram_vertex_top,(const uint32_t *)source,count*6u);
            e->offset_count=g_cram_vertex_top|(count<<24);
            e->source=p;
            *offset=g_cram_vertex_top;g_cram_vertex_top+=allocation;
            cram_vertex_stats[2]++;cram_vertex_stats[3]+=count*6u;return 1;
        }
    }
    cram_vertex_stats[5]++;return 0;
}

int gfx_gpu_mesh_sources(const void *vertices,unsigned count,const void *commands,
                         unsigned command_count,uint32_t *vertex_offset,
                         uint32_t *command_offset) {
    if(!command_count||command_count>64||((uintptr_t)commands&3))return 0;
    gfx_gpu_mesh_wait_source();
    for(unsigned attempt=0;attempt<3;++attempt) {
        if(!gfx_gpu_cram_vertex_source(vertices,count,vertex_offset))return 0;
        const uint32_t generation=g_cram_source_generation;
        const uintptr_t p=(uintptr_t)commands;
        const unsigned h=((p>>3)^(p>>13)^(command_count*33u))&511u;
        unsigned probe;
        for(probe=0;probe<16;++probe) {
            struct CramMeshSource *e=&g_cram_mesh_source[(h+probe)&511u];
            if(e->source==p&&(e->offset_count>>24)==command_count) {
                *command_offset=e->offset_count&0xffffffu;return 1;
            }
            if(!e->source) {
                const unsigned allocation=(command_count*8u+63u)&~63u;
                if(allocation>CRAM_VERTEX_END-g_cram_vertex_top) {
                    if(!gpu_cram_vertex_recycle())return 0;
                    break;
                }
                _of_gpu_fast_tex_upload(g_cram_vertex_top,
                                        (const uint32_t *)commands,command_count*2u);
                e->offset_count=g_cram_vertex_top|(command_count<<24);
                e->source=p;
                *command_offset=g_cram_vertex_top;g_cram_vertex_top+=allocation;
                return 1;
            }
        }
        if(generation==g_cram_source_generation)return 0;
        /* Command allocation recycled the arena after resolving the vertex
         * source. Resolve BOTH again; never return that stale vertex offset. */
    }
    return 0;
}


void gfx_async_forget_sources(void) {
    memset(async_task_sources,0,sizeof(async_task_sources));
    ++gfx_async_source_stats[3];
}
static int gfx_async_sources(const struct MeshTask *task,const void *vertices,
        unsigned count,uint32_t *vertex_offset,uint32_t *command_offset) {
    struct AsyncTaskSources *entry=&async_task_sources[task-mesh_tasks];
    const uint32_t generation=gfx_gpu_cram_source_generation();
    const uint32_t counts=count|((uint32_t)task->commands<<16);
    if(entry->vertices==vertices&&entry->counts==counts&&entry->generation==generation) {
        *vertex_offset=entry->vertex_offset;*command_offset=entry->command_offset;
        ++gfx_async_source_stats[0];return 1;
    }
    if(!gfx_gpu_mesh_sources(vertices,count,task->source+1,task->commands,
                             vertex_offset,command_offset)) {
        ++gfx_async_source_stats[2];return 0;
    }
    /* Allocation can recycle between resolving vertices and commands. The
     * existing source API retries both, and this generation is read afterward. */
    entry->vertices=vertices;entry->counts=counts;
    entry->generation=gfx_gpu_cram_source_generation();
    entry->vertex_offset=*vertex_offset;entry->command_offset=*command_offset;
    ++gfx_async_source_stats[1];return 1;
}
static const struct MeshTask *gfx_async_route_task(const struct MeshTask *entry,int asynchronous) {
    if(!entry->next)return NULL;
    /* Static routing proof/hint needs no dynamic material/context setup. Keep
     * the original diagnostic accounting while skipping the larger helper. */
    if(asynchronous&&(entry->prefer_legacy||!entry->dead_after)) {
        ++gfx_async_stats[0];
        if(entry->prefer_legacy)++gfx_async_hint_stats[3];
        else ++gfx_async_stats[1];
        return NULL;
    }
    return entry;
}

static uint32_t vertices[32*6],other_vertices[32*6];
static Gfx command_memory[65],other_commands[65];
static void reset(void){
 memset(g_cram_vertex_source,0,sizeof(g_cram_vertex_source));memset(g_cram_mesh_source,0,sizeof(g_cram_mesh_source));
 memset(async_task_sources,0,sizeof(async_task_sources));memset(mesh_tasks,0,sizeof(mesh_tasks));memset(gfx_async_source_stats,0,sizeof(gfx_async_source_stats));
 memset(gfx_async_stats,0,sizeof(gfx_async_stats));memset(gfx_async_hint_stats,0,sizeof(gfx_async_hint_stats));
 g_cram_vertex_top=CRAM_VERTEX_BEGIN;g_cram_source_generation=1;g_cram_vertex_enabled=1;g_cram_vertex_recycled=0;
 g_cram_vertex_frame=g_cram_vertex_last_recycle=0;uploads=waits=owner=drains=0;
 mesh_tasks[0].source=command_memory;mesh_tasks[0].commands=64;mesh_tasks[0].next=command_memory+64;
 mesh_tasks[1].source=other_commands;mesh_tasks[1].commands=1;mesh_tasks[1].next=other_commands+1;
}
static void pair(unsigned slot,const void *vertex,unsigned count,uint32_t v,uint32_t c){
 check(v+count*24<=c||c+mesh_tasks[slot].commands*8<=v);
 check(!memcmp((uint8_t*)memory+v,vertex,count*24));
 check(!memcmp((uint8_t*)memory+c,mesh_tasks[slot].source+1,mesh_tasks[slot].commands*8));
}
int main(void){
 for(unsigned i=0;i<32*6;i++){vertices[i]=0x72481111u^(i*1217u);other_vertices[i]=vertices[i]^0xffffffffu;}
 for(unsigned i=0;i<65;i++){command_memory[i].words.w0=0x05224466u+i;command_memory[i].words.w1=0xabcdef12u^i;other_commands[i]=command_memory[64-i];}
 uint32_t v,c,v2,c2;
 reset();owner=1;check(gfx_async_sources(mesh_tasks,vertices,32,&v,&c));pair(0,vertices,32,v,c);check(uploads==2&&gfx_async_source_stats[1]==1);
 owner=1;unsigned w=waits,u=uploads;check(gfx_async_sources(mesh_tasks,vertices,32,&v2,&c2));check(owner&&waits==w&&uploads==u&&v==v2&&c==c2);check(gfx_async_source_stats[0]==1);
 check(gfx_async_sources(mesh_tasks,other_vertices,32,&v2,&c2));pair(0,other_vertices,32,v2,c2);check(v2!=v&&c2==c);
 check(gfx_async_sources(mesh_tasks,other_vertices,2,&v2,&c2));pair(0,other_vertices,2,v2,c2);check(async_task_sources[0].counts==(2u|(64u<<16)));
 mesh_tasks[0].commands=1;check(gfx_async_sources(mesh_tasks,other_vertices,2,&v2,&c2));pair(0,other_vertices,2,v2,c2);check(async_task_sources[0].counts==(2u|(1u<<16)));
 /* Real allocator recycle between vertex and command resolution invalidates
  * every memo, including the one being rebound; it then retries both offsets. */
 reset();g_cram_vertex_top=CRAM_VERTEX_END-64;mesh_tasks[0].commands=1;
 check(gfx_async_sources(mesh_tasks,vertices,2,&v,&c));check(g_cram_source_generation==2&&gfx_async_source_stats[3]==1&&drains==1);pair(0,vertices,2,v,c);
 check(async_task_sources[0].generation==2);u=uploads;check(gfx_async_sources(mesh_tasks,vertices,2,&v2,&c2));check(uploads==u&&v==v2&&c==c2);
 /* A32-bit generation wrap does not let an ancient generation-zero memo
  * survive: the actual recycle callback clears keys before storage reuse. */
 reset();g_cram_source_generation=0xffffffffu;mesh_tasks[0].commands=1;
 check(gfx_async_sources(mesh_tasks,vertices,2,&v,&c));check(async_task_sources[0].vertices==vertices);
 g_cram_vertex_top=CRAM_VERTEX_END-64;check(gfx_async_sources(mesh_tasks+1,other_vertices,2,&v2,&c2));
 check(g_cram_source_generation==0&&async_task_sources[0].vertices==NULL&&async_task_sources[1].generation==0);pair(1,other_vertices,2,v2,c2);
 check(gfx_async_sources(mesh_tasks,vertices,2,&v,&c));pair(0,vertices,2,v,c);
 reset();g_cram_vertex_top=CRAM_VERTEX_END;g_cram_vertex_recycled=1;
 check(!gfx_async_sources(mesh_tasks,vertices,32,&v,&c));check(!async_task_sources[0].vertices&&gfx_async_source_stats[2]==1);
 /* Static dead-slot/historical route hints only select the unchanged legacy
  * path. Preserve hint precedence and accounting; MTF callers retain entries. */
 for(unsigned asynchronous=0;asynchronous<2;asynchronous++)for(unsigned live=0;live<2;live++)for(unsigned dead=0;dead<2;dead++)for(unsigned hint=0;hint<2;hint++){
  reset();struct MeshTask *t=mesh_tasks;t->next=live?command_memory+1:NULL;t->dead_after=dead;t->prefer_legacy=hint;
  const struct MeshTask *got=gfx_async_route_task(t,asynchronous);
  unsigned filtered=live&&asynchronous&&(hint||!dead);
  check(got==(live&&!filtered?t:NULL));check(gfx_async_stats[0]==filtered);check(gfx_async_hint_stats[3]==(filtered&&hint));check(gfx_async_stats[1]==(filtered&&!hint));
 }
 uint32_t rng=0x49183271;
 for(unsigned i=0;i<4096;i++){
  reset();rng=rng*1664525u+1013904223u;unsigned nv=1+(rng&31);
  rng=rng*1664525u+1013904223u;unsigned nc=1+(rng&63);mesh_tasks[0].commands=nc;
  rng=rng*1664525u+1013904223u;g_cram_vertex_top=CRAM_VERTEX_END-64*(rng%22);
  owner=1;check(gfx_async_sources(mesh_tasks,vertices,nv,&v,&c));pair(0,vertices,nv,v,c);
  u=uploads;w=waits;owner=1;check(gfx_async_sources(mesh_tasks,vertices,nv,&v2,&c2));
  check(owner&&waits==w&&uploads==u&&v2==v&&c2==c);check(async_task_sources[0].generation==g_cram_source_generation);
 }
 printf("{\"status\":\"PASS\",\"checks\":%u,\"random_source_pairs\":4096,\"route_combinations\":16}\n",checks);
}
