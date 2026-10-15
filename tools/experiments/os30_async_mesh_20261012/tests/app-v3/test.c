
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
static Gfx memory[65536];
#define _start memory[0]
#define __EH_FRAME_BEGIN__ (memory+65536)
enum {G_VTX=1,G_TRI1=5,G_TRI2=6};
static int gfx_gpu_async_supported(void){return 1;}
static int gfx_gpu_mesh_supported(void){return 0;}
static uint32_t gfx_mesh_stats[16],gfx_async_hint_stats[4];
static unsigned checks;
#define C(x) do{++checks;assert(x);}while(0)
struct MeshTask {
    const Gfx *source;
    Gfx *next;
    uint16_t commands, triangles;
    uint16_t dead_after, prefer_legacy;
};
static struct MeshTask mesh_tasks[512];
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
                if(!entry->prefer_legacy) {entry->prefer_legacy=1;++gfx_async_hint_stats[2];}
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
static const struct MeshTask *gfx_mesh_lookup(Gfx *cmd) {
    if(!gfx_gpu_async_supported()&&!gfx_gpu_mesh_supported())return NULL;
    gfx_mesh_stats[0]++;
    const uintptr_t p=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if((p&7)||p<(uintptr_t)&_start||p>=end||end-p<16)return NULL;
    const unsigned h=((p>>3)^(p>>13))&511u;
    struct MeshTask *entry=NULL;
    for(unsigned probe=0;probe<16;++probe) {
        struct MeshTask *e=&mesh_tasks[(h+probe)&511u];
        if(e->source==cmd) {
            gfx_mesh_stats[1]++;
            return e->next?e:NULL;
        }
        if(!e->source){entry=e;break;}
    }
    if(!entry)return NULL;
    entry->source=cmd;
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
    gfx_mesh_stats[2]++;
    return entry;
}

static unsigned bucket(const Gfx *p){return (((uintptr_t)p>>3)^((uintptr_t)p>>13))&511;}
static void topology(Gfx *p){p[0].words.w0=0x01003006;p[1].words.w0=0x05000204;p[2].words.w0=0x01003006;p[3].words.w0=0xdf000000;}
static unsigned flags(void){unsigned n=0;for(unsigned i=0;i<512;i++)n+=mesh_tasks[i].prefer_legacy!=0;return n;}
int main(void) {
 Gfx *keys[20];unsigned found=0,target=bucket(memory+4);
 for(unsigned i=4;i+4<65536&&found<20;i+=4)if(bucket(memory+i)==target)keys[found++]=memory+i;
 C(found==20);
 for(unsigned i=0;i<20;i++)topology(keys[i]);
 for(unsigned i=0;i<16;i++) {
  const struct MeshTask *entry=gfx_mesh_lookup(keys[i]);C(entry&&entry->source==keys[i]&&!entry->prefer_legacy&&entry->dead_after);
 }
 C(!gfx_mesh_lookup(keys[16]));
 for(unsigned i=0;i<16;i++) {
  gfx_async_note_repair(keys[i]+1);C(flags()==i+1);
  C(gfx_mesh_lookup(keys[i])->prefer_legacy==1);
  for(unsigned j=0;j<16;j++)C((gfx_mesh_lookup(keys[j])->prefer_legacy!=0)==(j<=i));
 }
 C(gfx_async_hint_stats[0]==16&&gfx_async_hint_stats[1]==16&&gfx_async_hint_stats[2]==16);
 for(unsigned i=0;i<10000;i++) {
  unsigned k=i%16;gfx_async_note_repair(keys[k]+1);C(flags()==16);C(gfx_mesh_lookup(keys[k])->prefer_legacy);
 }
 C(gfx_async_hint_stats[2]==16);gfx_async_note_repair(keys[16]+1);C(flags()==16);
 /* Different keys in the same bucket cannot inherit the route hint. The
  * existing cache does not evict, but clearing/replacing an entry explicitly
  * must clear the hint through the real initialization path. */
 struct MeshTask *entry=&mesh_tasks[target];C(entry->source==keys[0]);entry->source=NULL;
 const struct MeshTask *replacement=gfx_mesh_lookup(keys[16]);C(replacement==entry);C(!replacement->prefer_legacy);C(flags()==15);
 gfx_async_note_repair(keys[0]+1);C(!replacement->prefer_legacy);C(flags()==15);
 gfx_async_note_repair(keys[16]+1);C(replacement->prefer_legacy);C(flags()==16);
 /* Invalid/uncompiled slots do not acquire a hint. */
 entry->next=NULL;entry->prefer_legacy=0;gfx_async_note_repair(keys[16]+1);C(!entry->prefer_legacy);
 gfx_async_note_repair(NULL);gfx_async_note_repair((void *)((uintptr_t)memory+1));gfx_async_note_repair(memory);gfx_async_note_repair(memory+65536);C(flags()==15);
 memset(mesh_tasks,0,sizeof(mesh_tasks));C(!flags());C(!gfx_mesh_lookup(keys[0])->prefer_legacy);
 printf("{\"status\":\"PASS\",\"checks\":%u,\"repeated_exact_keys\":10000,\"collision_chain\":16}\n",checks);
}
