#include "task_producer.hpp"
#include "vectors.hpp"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <random>
using namespace mesh_task;
static unsigned checks;
static void check(bool x){++checks;assert(x);}
template<class F> static void rejects(F f){bool caught=false;try{f();}catch(const std::runtime_error&){caught=true;}check(caught);}
int main() {
    auto vectors=actual_vectors();Producer p;std::mt19937 rng(0x6349338d);
    std::vector<uint32_t> observed;std::vector<uint32_t> expected;
    unsigned max_pending=0;uint64_t visits=0;
    auto consume=[&](const Submission&s){
        check(s.task->generation==p.generation());check(s.context->generation==p.generation());
        check(s.task->vertices.size()==24*s.task->count);
        check(s.publication_words()[1]==s.task->handle);
        observed.insert(observed.end(),s.task->commands.begin(),s.task->commands.end());++visits;
    };
    // Three exact invocations of the captured task sequence demonstrate a
    // cache hit mechanism, not three measured game frames or frame stability.
    for(unsigned replay=0;replay<3;replay++)for(const auto&v:vectors) {
        if(!p.has_task(v.address))p.register_task(v.address,v.commands,v.vertices,v.psram);
        // The mode fingerprint is only a mechanism-test context. Full runtime
        // material/transform payloads are joined separately by passive trace.
        std::vector<uint32_t> ctx={v.geometry,0x3f800000,0xabcdef01};
        p.register_context(v.geometry,ctx);
        while(!p.submit(v.address,v.geometry,p.generation())) {auto serial=p.front().sequence;consume(p.front());p.acknowledge(serial);}
        expected.insert(expected.end(),v.commands.begin(),v.commands.end());
        if(p.pending()>max_pending)max_pending=p.pending();
        if(rng()%4==0){auto serial=p.front().sequence;consume(p.front());p.acknowledge(serial);}
    }
    p.drain(consume);check(observed==expected);check(p.cold_triangle_decodes<2459u*3);check(p.credit_rejects>0);check(max_pending==8);
    uint64_t main_compiles=p.cold_task_compiles,main_decodes=p.cold_triangle_decodes,main_words=p.published_words;
    // Caller-owned input buffers and context storage may immediately mutate:
    // accepted descriptors and cached registrations retain detached bytes.
    auto v=vectors.front();Producer life(2);
    auto command_copy=v.commands;auto vertex_copy=v.vertices;std::vector<uint32_t>ctx={1,2,3,4};
    life.register_task(v.address,command_copy,vertex_copy,v.psram);life.register_context(7,ctx);
    check(life.submit(v.address,7,1));command_copy.assign(command_copy.size(),0);vertex_copy.assign(vertex_copy.size(),0);ctx.assign(4,0);
    check(life.front().task->commands==v.commands);check(life.front().task->vertices==v.vertices);check(life.front().context->words==std::vector<uint32_t>({1,2,3,4}));
    rejects([&]{life.register_context(7,ctx);});rejects([&]{life.register_task(v.address,command_copy,vertex_copy,v.psram);});
    rejects([&]{life.acknowledge(life.front().sequence+1);});
    unsigned drained=0;life.recycle([&](const Submission&s){check(life.generation()==1);check(s.task->source_psram==v.psram);++drained;});
    check(drained==1&&life.generation()==2&&life.pending()==0&&life.cached_tasks()==0);
    rejects([&]{life.submit(v.address,7,1);});rejects([&]{life.submit(v.address,7,2);});
    // Old physical offsets may be reused only after the preceding drain; new
    // generation then admits the same CPU address with newly owned bytes.
    v.vertices[0]^=1;life.register_task(v.address,v.commands,v.vertices,v.psram);life.register_context(7,ctx);check(life.submit(v.address,7,2));
    check(life.front().task->generation==2&&life.front().task->vertices==v.vertices);
    life.drain([&](const Submission&s){check(s.context->words==ctx);});
    auto bad=v.commands;bad[2]=0xde000000;Producer negative;
    rejects([&]{negative.register_task(v.address,bad,v.vertices,v.psram);});
    auto raw=v.vertices;raw.pop_back();rejects([&]{negative.register_task(v.address,v.commands,raw,v.psram);});
    rejects([&]{negative.register_task(v.address,v.commands,v.vertices,0xffffffu);});
    auto too_many=v.commands;too_many.resize(2);for(unsigned i=0;i<65;i++){too_many.push_back(0x05000000u|(v.commands[2]&0xffffff));too_many.push_back(0);}
    rejects([&]{negative.register_task(v.address,too_many,v.vertices,v.psram);});
    too_many.resize(2);for(unsigned i=0;i<33;i++){too_many.push_back(0x06000000u|(v.commands[2]&0xffffff));too_many.push_back(v.commands[2]&0xffffff);}
    rejects([&]{negative.register_task(v.address,too_many,v.vertices,v.psram);});
    Producer overlap;overlap.register_task(v.address,v.commands,v.vertices,v.psram);auto changed=v.vertices;changed[0]^=1;
    rejects([&]{overlap.register_task(v.address+8,v.commands,changed,v.psram);});
    rejects([&]{overlap.register_task(v.address+8,v.commands,v.vertices,v.psram+4);});
    Producer cap(1,1,1);cap.register_task(v.address,v.commands,v.vertices,v.psram);cap.register_context(1,{1});
    rejects([&]{cap.register_context(2,{2});});rejects([&]{cap.register_task(v.address+8,v.commands,v.vertices,v.psram);});
    check(cap.submit(v.address,1,1));check(!cap.submit(v.address,1,1));
    // Arbitrary renderer-state changes allocate a new immutable epoch while
    // old work remains accepted; neither caller storage nor a later epoch may
    // mutate the context seen by that earlier task.
    Producer epochs(2,1,2);epochs.register_task(v.address,v.commands,v.vertices,v.psram);
    epochs.register_context(1,{11,12});check(epochs.submit(v.address,1,1));
    epochs.register_context(2,{21,22});check(epochs.submit(v.address,2,1));
    rejects([&]{epochs.register_context(3,{31,32});});
    rejects([&]{epochs.register_task(v.address+8,v.commands,v.vertices,v.psram);});
    check(epochs.pending()==2&&epochs.front().context->words==std::vector<uint32_t>({11,12}));
    // A worker that cannot finish must not permit pool reuse or remove any
    // accepted ownership. The adapter is responsible for reporting completion
    // only once all DMA and repair users have drained.
    rejects([&]{epochs.recycle([](const Submission&){throw std::runtime_error("worker incomplete");});});
    check(epochs.generation()==1&&epochs.pending()==2&&epochs.cached_tasks()==1);
    epochs.acknowledge(epochs.front().sequence);
    check(epochs.front().context->words==std::vector<uint32_t>({21,22}));
    epochs.recycle([](const Submission&){});check(epochs.generation()==2&&epochs.pending()==0);
    std::printf("{\"passed\":true,\"checks\":%u,\"actual_tasks_per_sequence\":%zu,\"mock_sequences\":3,\"tasks_registered\":%llu,\"cold_triangles_decoded\":%llu,\"submissions\":%llu,\"publication_words\":%llu,\"steady_words_per_sequence\":%zu,\"steady_per_triangle_scans\":0,\"credit_stalls\":%llu,\"max_pending\":%u,\"completed\":%llu}\n",checks,vectors.size(),(unsigned long long)main_compiles,(unsigned long long)main_decodes,(unsigned long long)p.submissions,(unsigned long long)main_words,vectors.size()*4,(unsigned long long)p.credit_rejects,max_pending,(unsigned long long)visits);
}
