#ifndef MESH_TASK_PRODUCER_V1_HPP
#define MESH_TASK_PRODUCER_V1_HPP
// Executable source-only mechanism. The four-word mock publication record is
// not a hardware ABI, and no host execution time predicts Pocket performance.
#include <array>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
#include <utility>
#include <vector>

namespace mesh_task {
struct Task {
    uint32_t address, next, source_cpu, source_psram, generation, handle;
    unsigned count, first;
    std::vector<uint32_t> commands;
    std::vector<uint8_t> vertices;
    unsigned triangles;
};
struct Context {
    uint32_t epoch, generation, handle;
    std::vector<uint32_t> words;
};
struct Submission {
    std::shared_ptr<const Task> task;
    std::shared_ptr<const Context> context;
    uint32_t sequence;
    // {operation, task handle, immutable context handle, completion sequence}
    std::array<uint32_t,4> publication_words() const {
        return {0x4d530001u,task->handle,context->handle,sequence};
    }
};
class Producer {
    using Key=std::pair<uint32_t,uint32_t>;
    std::map<Key,std::shared_ptr<const Task>> tasks_;
    std::map<Key,std::shared_ptr<const Context>> contexts_;
    std::deque<Submission> accepted_;
    std::map<uint32_t,std::vector<uint8_t>> source_ranges_;
    const unsigned credits_,max_tasks_,max_contexts_;
    uint32_t generation_=1,next_handle_=1,next_sequence_=1;
    static void check(bool p,const char*why){if(!p)throw std::runtime_error(why);}
public:
    uint64_t cold_task_compiles=0,cold_triangle_decodes=0,task_hits=0;
    uint64_t context_copies=0,submissions=0,published_words=0,credit_rejects=0;
    uint64_t completed=0,drain_callbacks=0,recycles=0;
    explicit Producer(unsigned credits=8,unsigned max_tasks=512,unsigned max_contexts=512)
        :credits_(credits),max_tasks_(max_tasks),max_contexts_(max_contexts){check(credits>0,"zero credits");}
    uint32_t generation() const{return generation_;}
    unsigned pending() const{return accepted_.size();}
    unsigned cached_tasks() const{return tasks_.size();}
    // Cold registration validates immutable source exactly once per pool
    // generation. Existing key publication never rereads original task bytes.
    bool has_task(uint32_t address) const{return tasks_.count({generation_,address});}
    void register_task(uint32_t address,const std::vector<uint32_t>& words,
                       const std::vector<uint8_t>& vertices,uint32_t physical_source) {
        check(words.size()>=4&&!(words.size()&1),"task word shape");
        check(words.size()/2-1<=64,"task exceeds64 TRI commands");
        const auto existing=tasks_.find({generation_,address});
        if(existing!=tasks_.end()) {
            check(existing->second->commands==words&&existing->second->vertices==vertices&&existing->second->source_psram==physical_source,"immutable task key changed");return;
        }
        check(tasks_.size()<max_tasks_,"task cache capacity");
        uint32_t load=words[0];check(load>>24==1,"task must begin with LOAD");
        unsigned count=(load>>12)&255,end=(load>>1)&127;
        check(count&&end>=count&&end<=32,"LOAD scope");unsigned first=end-count;
        check(vertices.size()==count*24u,"port raw vertex stride is24B");
        check(!(words[1]&3)&&!(physical_source&3),"vertex source alignment");
        check(uint64_t(physical_source)+vertices.size()<=0x1000000ull,"PSRAM source bounds");
        // Logical arena lease. The real adapter must validate this BEFORE
        // uploading/reusing physical bytes; owning std::vector does not stop
        // an external DMA writer from violating the hardware lease.
        for(const auto& range:source_ranges_) {
            const uint64_t a=physical_source,b=a+vertices.size();
            const uint64_t x=range.first,y=x+range.second.size();
            if(a<y&&x<b)check(a==x&&vertices==range.second,"live source arena overlap");
        }
        unsigned triangles=0;
        for(unsigned w=2;w<words.size();w+=2) {
            unsigned op=words[w]>>24;check(op==5||op==6,"state/list boundary inside task");
            // Validate all members before publishing any part of a TRI2.
            for(unsigned j=0;j<(op==6?2u:1u);j++)for(unsigned shift:{16u,8u,0u}) {
                unsigned slot=((words[w+j]>>shift)&255)/2;
                check(slot>=first&&slot<end,"task references preceding LOAD");
            }
            triangles+=op==6?2:1;
            check(triangles<=64,"task exceeds64 total triangles");
        }
        const uint32_t next=address+words.size()*4;
        check(next>address,"command range overflow");
        tasks_[{generation_,address}]=std::make_shared<const Task>(Task{address,next,words[1],physical_source,generation_,next_handle_++,count,first,words,vertices,triangles});
        source_ranges_[physical_source]=vertices;
        ++cold_task_compiles;cold_triangle_decodes+=triangles;
    }
    void register_context(uint32_t epoch,const std::vector<uint32_t>& words) {
        check(!words.empty()&&words.size()<=64,"context shape");
        const auto old=contexts_.find({generation_,epoch});
        if(old!=contexts_.end()){check(old->second->words==words,"immutable context epoch changed");return;}
        check(contexts_.size()<max_contexts_,"context cache capacity");
        contexts_[{generation_,epoch}]=std::make_shared<const Context>(Context{epoch,generation_,next_handle_++,words});++context_copies;
    }
    // Steady producer operation: two bounded cache lookups, capacity check,
    // publication. No vertex or command fetch/decode and no geometry result.
    bool submit(uint32_t address,uint32_t epoch,uint32_t expected_generation) {
        check(expected_generation==generation_,"stale producer generation");
        const auto task=tasks_.find({generation_,address});const auto ctx=contexts_.find({generation_,epoch});
        check(task!=tasks_.end()&&ctx!=contexts_.end(),"unregistered immutable input");
        if(accepted_.size()==credits_){++credit_rejects;return false;}
        accepted_.push_back({task->second,ctx->second,next_sequence_++});
        ++task_hits;++submissions;published_words+=4;return true;
    }
    const Submission& front() const{check(!accepted_.empty(),"empty worker queue");return accepted_.front();}
    void acknowledge(uint32_t sequence) {
        check(!accepted_.empty()&&accepted_.front().sequence==sequence,"out-of-order completion");
        accepted_.pop_front();++completed;
    }
    void drain(const std::function<void(const Submission&)>& worker) {
        while(!accepted_.empty()) {const auto serial=front().sequence;worker(front());++drain_callbacks;acknowledge(serial);}
    }
    // A worker completion means no source read or repair can still reference
    // this generation. The cold recycle fence precedes physical arena reuse.
    void recycle(const std::function<void(const Submission&)>& worker) {
        drain(worker);check(accepted_.empty(),"recycle with accepted work");
        tasks_.clear();contexts_.clear();source_ranges_.clear();++generation_;check(generation_!=0,"generation wrap requires global reset");++recycles;
    }
};
}
#endif
