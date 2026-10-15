
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
typedef struct {uint32_t words[6];} Vtx;
#define G_VTX 1
#define G_TRI1 5
#define G_TRI2 6
static uint32_t image[65536] __attribute__((aligned(8)));
#define _start image[0]
#define __EH_FRAME_BEGIN__ (image+65536)
static uint32_t memory[0x1000000/4];
static unsigned checks,uploads,waits,drains,owner,group_supported=1,inject_wait;
static const void *inject_repair;
static uint32_t gfx_async_stats[16],gfx_mesh_stats[16],gfx_async_source_stats[4],gfx_async_hint_stats[4],gfx_async_group_stats[8];
static uint32_t async_hint_epoch=1;
static void check(int ok){++checks;assert(ok);}
void gfx_async_note_repair(const void *commands);
void gfx_async_forget_sources(void);
int gfx_gpu_async_supported(void){return 1;}
int gfx_gpu_async_group_supported(void){return group_supported;}
int gfx_gpu_mesh_supported(void){return 0;}
void gfx_gpu_async_drain(void){++drains;owner=0;}
void gfx_gpu_mesh_wait_source(void){++waits;owner=0;if(inject_wait&&!--inject_wait)gfx_async_note_repair(inject_repair);}
void _of_gpu_fast_tex_upload(uint32_t address,const uint32_t *source,unsigned words){
 check(!owner);check(!(address&7));check(address+words*4<=sizeof(memory));
 memcpy((uint8_t *)memory+address,source,words*4);++uploads;
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
struct AsyncGroup {
    Gfx *next;
    uint32_t generation,hint_epoch,descriptor_offset,loaded_mask;
    uint16_t members,triangles;
    uint32_t cull;
};

static struct MeshTask mesh_tasks[512];
static struct AsyncTaskSources async_task_sources[512];
static struct AsyncGroup async_groups[512];
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

/* The descriptors contain physical pool offsets, so an allocation recycle
 * invalidates the WHOLE vector. The caller retries every member instead of
 * uploading stale earlier offsets. The upload drains before publication. */
int gfx_gpu_async_group_upload(const uint32_t *descriptors,unsigned members,
                              uint32_t generation,uint32_t *offset) {
    if(!members||members>32||generation!=g_cram_source_generation)return 0;
    const unsigned bytes=members*24u,allocation=(bytes+63u)&~63u;
    if(allocation>CRAM_VERTEX_END-g_cram_vertex_top) {
        (void)gpu_cram_vertex_recycle();return 0;
    }
    _of_gpu_fast_tex_upload(g_cram_vertex_top,descriptors,members*6u);
    *offset=g_cram_vertex_top;g_cram_vertex_top+=allocation;
    return 1;
}


void gfx_async_forget_sources(void) {
    memset(async_task_sources,0,sizeof(async_task_sources));
    memset(async_groups,0,sizeof(async_groups));
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
void gfx_async_note_repair(const void *commands) {
    ++gfx_async_hint_stats[0];
    const uintptr_t p=(uintptr_t)commands,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if((p&7u)||p<(uintptr_t)&_start+sizeof(Gfx)||p>end-sizeof(Gfx))return;
    const Gfx *source=(const Gfx *)(p-sizeof(Gfx));
    const unsigned h=(((uintptr_t)source>>3)^((uintptr_t)source>>13))&511u;
    for(unsigned probe=0;probe<16;++probe) {
        struct MeshTask *entry=&mesh_tasks[(h+probe)&511u];
        if(entry->source==source) {
            if(entry->next&&entry->commands) {
                ++gfx_async_hint_stats[1];
                if(!entry->prefer_legacy) {
                    entry->prefer_legacy=1;++gfx_async_hint_stats[2];
                    if(!++async_hint_epoch) {
                        memset(async_groups,0,sizeof(async_groups));async_hint_epoch=1;
                    }
                }
            }
            return;
        }
        if(!entry->source)return;
    }
}
static unsigned gfx_async_dead_suffix(const Gfx *scan,unsigned count) {
    uint32_t pending=count==32?0xffffffffu:((1u<<count)-1u);
    const uintptr_t end=(uintptr_t)__EH_FRAME_BEGIN__;
    for(unsigned steps=0;steps<256;++steps,++scan) {
        const uintptr_t p=(uintptr_t)scan;
        if((p&7u)||p<(uintptr_t)&_start||p>end-8u)return 0;
        const uint32_t a=scan->words.w0,b=scan->words.w1;
        const unsigned op=a>>24;
        if(op==(uint8_t)G_VTX) {
            const unsigned n=(a>>12)&255u,limit=(a>>1)&127u;
            if(!n||n>32||limit<n||limit>32)return 0;
            const unsigned first=limit-n;
            const uint32_t mask=n==32?0xffffffffu:((1u<<n)-1u)<<first;
            pending&=~mask;
            if(!pending)return 1;
        } else if(op==(uint8_t)G_TRI1||op==(uint8_t)G_TRI2) {
            const unsigned n=op==(uint8_t)G_TRI2?2u:1u;
            for(unsigned t=0;t<n;++t) {
                const uint32_t w=t?b:a;
                for(unsigned shift=0;shift<=16;shift+=8) {
                    const unsigned slot=((w>>shift)&255u)/2u;
                    if(slot>=32||(pending&(1u<<slot)))return 0;
                }
            }
        } else return 0;
    }
    return 0;
}
static const struct MeshTask *gfx_async_route_task(const struct MeshTask *entry,int asynchronous,int account) {
    if(!entry->next)return NULL;
    /* Static routing proof/hint needs no dynamic material/context setup. Keep
     * the original diagnostic accounting while skipping the larger helper. */
    if(asynchronous&&(entry->prefer_legacy||!entry->dead_after)) {
        if(account) {
            ++gfx_async_stats[0];
            if(entry->prefer_legacy)++gfx_async_hint_stats[3];
            else ++gfx_async_stats[1];
        }
        return NULL;
    }
    return entry;
}
static const struct MeshTask *gfx_mesh_lookup_impl(Gfx *cmd,int asynchronous,int account) {
    if(account)gfx_mesh_stats[0]++;
    const uintptr_t p=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if((p&7)||p<(uintptr_t)&_start||p>=end||end-p<16)return NULL;
    const unsigned h=((p>>3)^(p>>13))&511u;
    struct MeshTask *entry=NULL;
    for(unsigned probe=0;probe<16;++probe) {
        struct MeshTask *e=&mesh_tasks[(h+probe)&511u];
        if(e->source==cmd) {
            if(account)gfx_mesh_stats[1]++;
            return gfx_async_route_task(e,asynchronous,account);
        }
        if(!e->source){entry=e;break;}
    }
    if(!entry)return NULL;
    entry->source=cmd;
    async_task_sources[entry-mesh_tasks].vertices=NULL;
    memset(&async_groups[entry-mesh_tasks],0,sizeof(struct AsyncGroup));
    entry->prefer_legacy=0;
    const unsigned count=(cmd->words.w0>>12)&255u;
    const unsigned limit=(cmd->words.w0>>1)&127u;
    if(!count||count>32||limit!=count)return NULL; /* initial dest0 scope */
    unsigned commands=0,triangles=0;
    Gfx *scan=cmd+1;
    while((uintptr_t)scan<=end-8u) {
        unsigned op=scan->words.w0>>24;
        if(op!=(uint8_t)G_TRI1&&op!=(uint8_t)G_TRI2)break;
        const unsigned n=op==(uint8_t)G_TRI2?2u:1u;
        if(commands==64||triangles+n>64)return NULL;
        for(unsigned t=0;t<n;++t) {
            const uint32_t word=t?scan->words.w1:scan->words.w0;
            if(((word>>16)&255u)/2u>=count||((word>>8)&255u)/2u>=count||
               (word&255u)/2u>=count)return NULL;
        }
        ++commands;triangles+=n;++scan;
    }
    if(!commands||(uintptr_t)scan>end-8u)return NULL;
    entry->commands=commands;entry->triangles=triangles;entry->next=scan;
    entry->dead_after=gfx_async_dead_suffix(scan,count);
    if(account)gfx_mesh_stats[2]++;
    return gfx_async_route_task(entry,asynchronous,account);
}
static const struct MeshTask *gfx_mesh_lookup(Gfx *cmd) {
    const int asynchronous=gfx_gpu_async_supported();
    if(!asynchronous&&!gfx_gpu_mesh_supported())return NULL;
    return gfx_mesh_lookup_impl(cmd,asynchronous,1);
}
static const struct AsyncGroup *gfx_async_group_sources(const struct MeshTask *first,
                                                       unsigned cull) {
    if(!gfx_gpu_async_group_supported())return NULL;
    struct AsyncGroup *group=&async_groups[first-mesh_tasks];
    uint32_t generation=gfx_gpu_cram_source_generation();
    if(group->members&&group->generation==generation&&
       group->hint_epoch==async_hint_epoch&&group->cull==cull) {
        ++gfx_async_group_stats[2];
        return group->members>1?group:NULL;
    }
    ++gfx_async_group_stats[3];
    for(unsigned attempt=0;attempt<3;++attempt) {
        uint32_t descriptors[32][6];
        const uint32_t epoch=async_hint_epoch;
        generation=gfx_gpu_cram_source_generation();
        const struct MeshTask *task=first;
        Gfx *next=first->next;
        unsigned members=0,triangles=0;uint32_t mask=0;
        while(task&&members<32&&!task->prefer_legacy&&task->dead_after) {
            const unsigned count=(task->source->words.w0>>12)&255u;
            const uintptr_t source=(uintptr_t)task->source->words.w1;
            const uintptr_t end=(uintptr_t)__EH_FRAME_BEGIN__;
            if(!count||count>32||(source&3u)||source<(uintptr_t)&_start||
               source>=end||count>(end-source)/sizeof(Vtx))break;
            uint32_t vertex_offset,command_offset;
            if(!gfx_async_sources(task,(const void *)source,count,
                                  &vertex_offset,&command_offset))break;
            if(generation!=gfx_gpu_cram_source_generation()||epoch!=async_hint_epoch)break;
            uint32_t *d=descriptors[members];
            d[0]=vertex_offset;d[1]=task->source->words.w0;d[2]=command_offset;
            d[3]=task->commands|((cull&3u)<<8);
            d[4]=(uint32_t)source;d[5]=(uint32_t)(uintptr_t)(task->source+1);
            ++members;triangles+=task->triangles;
            mask|=count==32?0xffffffffu:((1u<<count)-1u);
            next=task->next;
            if(members==32||(next->words.w0>>24)!=(uint8_t)G_VTX)break;
            task=gfx_mesh_lookup_impl(next,1,0);
        }
        if(generation!=gfx_gpu_cram_source_generation()||epoch!=async_hint_epoch) {
            ++gfx_async_group_stats[4];continue;
        }
        if(!members)break;
        uint32_t offset=0;
        if(members>1&&!gfx_gpu_async_group_upload(&descriptors[0][0],members,generation,&offset)) {
            if(generation!=gfx_gpu_cram_source_generation()||epoch!=async_hint_epoch) {
                ++gfx_async_group_stats[4];continue;
            }
            break;
        }
        if(epoch!=async_hint_epoch) {++gfx_async_group_stats[4];continue;}
        group->next=next;group->generation=generation;group->hint_epoch=epoch;
        group->descriptor_offset=offset;group->loaded_mask=mask;
        group->members=members;group->triangles=triangles;group->cull=cull;
        if(members<2)++gfx_async_group_stats[6];
        return members>1?group:NULL;
    }
    group->members=0;++gfx_async_group_stats[5];return NULL;
}

static Gfx *commands(void){return (Gfx *)(image+64);}
static Vtx *vertices(unsigned i){return (Vtx *)(image+4096+i*192);}
static void reset(void){
 memset(image,0,sizeof image);memset(mesh_tasks,0,sizeof mesh_tasks);memset(async_task_sources,0,sizeof async_task_sources);
 memset(async_groups,0,sizeof async_groups);memset(g_cram_vertex_source,0,sizeof g_cram_vertex_source);memset(g_cram_mesh_source,0,sizeof g_cram_mesh_source);
 memset(gfx_async_stats,0,sizeof gfx_async_stats);memset(gfx_mesh_stats,0,sizeof gfx_mesh_stats);memset(gfx_async_source_stats,0,sizeof gfx_async_source_stats);
 memset(gfx_async_hint_stats,0,sizeof gfx_async_hint_stats);memset(gfx_async_group_stats,0,sizeof gfx_async_group_stats);
 async_hint_epoch=1;group_supported=1;g_cram_vertex_enabled=1;g_cram_source_generation=1;
 g_cram_vertex_top=CRAM_VERTEX_BEGIN;g_cram_vertex_recycled=0;g_cram_vertex_frame=g_cram_vertex_last_recycle=0;
 uploads=waits=drains=owner=inject_wait=0;inject_repair=NULL;
}
static void build(unsigned members,unsigned varying){
 Gfx *c=commands();
 for(unsigned i=0;i<members;i++){
  unsigned n=varying?3+(i*7%30):3;
  c[2*i].words.w0=0x01000000|(n<<12)|(n<<1);c[2*i].words.w1=(uint32_t)(uintptr_t)vertices(i);
  c[2*i+1].words.w0=0x05000204;c[2*i+1].words.w1=0;
  for(unsigned j=0;j<32*6;j++)((uint32_t*)vertices(i))[j]=0x52491000u+i*104729u+j;
 }
 /* A final LOAD overwrites every group slot before ENDDL. It is a legacy
  * terminal, since it has no TRI. Thus every member's dead proof is real. */
 c[2*members].words.w0=0x01020040;c[2*members].words.w1=(uint32_t)(uintptr_t)vertices(members);
 c[2*members+1].words.w0=0xdf000000;
}
static const struct MeshTask *first(void){const struct MeshTask *p=gfx_mesh_lookup(commands());check(p!=NULL);return p;}
static void verify(const struct AsyncGroup *g,unsigned members,unsigned cull){
 check(g&&g->members==members&&g->triangles==members&&g->next==commands()+2*members);
 check(g->generation==g_cram_source_generation&&g->hint_epoch==async_hint_epoch&&g->cull==cull);
 const uint32_t *d=(const uint32_t *)((uint8_t *)memory+g->descriptor_offset);uint32_t mask=0;
 for(unsigned i=0;i<members;i++,d+=6){
  unsigned n=commands()[2*i].words.w0>>12&255;mask|=n==32?~0u:((1u<<n)-1);
  check(d[1]==commands()[2*i].words.w0&&d[3]==(1u|(cull<<8)));
  check(d[4]==(uint32_t)(uintptr_t)vertices(i)&&d[5]==(uint32_t)(uintptr_t)(commands()+2*i+1));
  check(!memcmp((uint8_t *)memory+d[0],vertices(i),n*24));
  check(!memcmp((uint8_t *)memory+d[2],commands()+2*i+1,8));
  check(d[0]<g->descriptor_offset&&d[2]<g->descriptor_offset);
 }
 check(g->loaded_mask==mask);
}
int main(void){
 check((uintptr_t)image<=UINT32_MAX-sizeof image);
 for(unsigned members=2;members<=32;members++)for(unsigned varying=0;varying<2;varying++){
  reset();build(members,varying);const struct MeshTask *p=first();
  const struct AsyncGroup *g=gfx_async_group_sources(p,2);verify(g,members,2);
  unsigned up=uploads,w=waits;owner=1;uint32_t oldoff=g->descriptor_offset;
  check(gfx_async_group_sources(p,2)==g);check(owner&&uploads==up&&waits==w&&g->descriptor_offset==oldoff);
  owner=0;g=gfx_async_group_sources(p,1);verify(g,members,1);check(g->descriptor_offset!=oldoff);
 }
 reset();build(40,1);verify(gfx_async_group_sources(first(),0),32,0);
 reset();build(1,0);const struct MeshTask *p=first();check(!gfx_async_group_sources(p,0));unsigned up=uploads;
 check(!gfx_async_group_sources(p,0)&&uploads==up);
 /* Exact task hint changes split a cached immutable group; no slot mutation
  * is interpreted as geometry correctness and a32-bit epoch wrap clears all. */
 reset();build(8,1);p=first();verify(gfx_async_group_sources(p,3),8,3);
 gfx_async_note_repair(commands()+2*3+1);check(async_hint_epoch==2);
 verify(gfx_async_group_sources(p,3),3,3);
 async_hint_epoch=UINT32_MAX;gfx_async_note_repair(commands()+2*5+1);check(async_hint_epoch==1);
 for(unsigned i=0;i<512;i++)check(!async_groups[i].members);
 /* A repair callback during a source-resolution wait invalidates a partial
  * build. The retry must exclude that exact member and every later member. */
 reset();build(8,1);p=first();inject_wait=4;inject_repair=commands()+2*3+1;
 verify(gfx_async_group_sources(p,0),3,0);check(gfx_async_group_stats[4]==1);
 /* Real pool recycle during member allocation, then specifically during the
  * table allocation, invalidates every earlier physical offset. */
 reset();build(8,1);p=first();g_cram_vertex_top=CRAM_VERTEX_END-64;
 verify(gfx_async_group_sources(p,2),8,2);check(g_cram_source_generation==2&&drains==1);
 reset();build(8,1);p=first();verify(gfx_async_group_sources(p,2),8,2);
 async_groups[p-mesh_tasks].members=0;g_cram_vertex_top=CRAM_VERTEX_END;
 verify(gfx_async_group_sources(p,2),8,2);check(g_cram_source_generation==2&&drains==1);
 reset();build(8,1);p=first();g_cram_source_generation=UINT32_MAX;g_cram_vertex_top=CRAM_VERTEX_END-64;
 verify(gfx_async_group_sources(p,2),8,2);check(g_cram_source_generation==0);
 reset();build(8,1);p=first();g_cram_vertex_top=CRAM_VERTEX_END;g_cram_vertex_recycled=1;
 check(!gfx_async_group_sources(p,0)&&gfx_async_group_stats[5]==1);
 /* Group availability cannot change AJ02's existing single-job route. */
 reset();build(8,0);p=first();group_supported=0;
 check(!gfx_async_group_sources(p,0)&&!uploads&&!waits);check(p->next==commands()+2);
 /* State and return boundaries terminate the compiler; they are never
  * elided just because the subsequent command bytes look like geometry. */
 reset();build(8,0);commands()[6].words.w0=0xdf000000;
 p=first();verify(gfx_async_group_sources(p,0),2,0);
 reset();build(8,0);commands()[6].words.w0=0xd7000000;
 p=first();verify(gfx_async_group_sources(p,0),2,0);
 uint32_t random=0x72910643;
 for(unsigned trial=0;trial<512;trial++){
  reset();random=random*1664525u+1013904223u;unsigned n=2+random%31;
  build(n,trial&1);p=first();random=random*1664525u+1013904223u;
  g_cram_vertex_top=CRAM_VERTEX_END-64*(random%70);
  verify(gfx_async_group_sources(p,trial&3),n,trial&3);
 }
 printf("{\"status\":\"PASS\",\"checks\":%u,\"random_groups\":512,\"bounded_shape_cases\":62}\n",checks);
}
