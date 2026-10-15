#ifndef OS30_GPU_BOTTLENECK_OBSERVER_HPP
#define OS30_GPU_BOTTLENECK_OBSERVER_HPP
// Const-only, pre-active-edge GPU observer. No model evaluation or mutation.
#include <array>
#include <cstdint>
#include <fstream>
#include <map>
#include <string>
#include <vector>
#include <algorithm>

namespace gpu_bottleneck {
enum Partition {IDLE_EMPTY,IDLE_CACHE_DRAIN,COMMAND_FETCH,COMMAND_EXECUTE,COMMAND_BARRIER,
 CLEAR,TRIANGLE_SETUP,TRIANGLE_WALK,VERTEX_TRANSFORM,VERTEX_CACHE,SPAN_SETUP,FB_FLUSH,
 FRAG_RETIRE,FRAG_READ_RESPONSE,FRAG_READ_DRAIN,FRAG_WRITE_BACKPRESSURE,
 FRAG_DEPTH_READ_GATE,FRAG_TEXTURE_STARVED,FRAG_PERSPECTIVE_STARVED,
 FRAG_LOCAL_HOLD,FRAG_PIPE_PROGRESS,OTHER,PARTITIONS};
inline const std::array<const char*,PARTITIONS> partition_names={
 "idle_empty","idle_cache_drain","command_fetch","command_execute","command_barrier",
 "clear","triangle_setup","triangle_walk","vertex_transform","vertex_cache","span_setup","framebuffer_flush",
 "fragment_retire","fragment_color_depth_response","fragment_read_drain_or_setup","fragment_write_backpressure",
 "fragment_depth_read_gate","fragment_texture_starved","fragment_perspective_starved",
 "fragment_local_hold","fragment_pipeline_progress","other"};
#define GPU_METRICS(X) \
 X(gpu_busy) X(core_busy) X(cache_busy) X(ring_empty) X(ring_full) X(cpu_ring_upload) X(cpu_ring_publish) \
 X(cpu_retire) X(cpu_ring_poll_context) X(cpu_ring_poll_load_retire) X(cpu_ring_pointer_reads) \
 X(fragment_cycles) X(fragment_retired) X(fragment_discarded) X(fragment_depth_test) X(fragment_pipeline_stall) \
 X(fragment_backend_empty) X(fragment_texture_lookup_wait) X(fragment_texture_request_block) X(fragment_texture_credit_full) \
 X(fragment_texture_result_full) X(fragment_texture_response) X(fragment_perspective_wait) X(fragment_write_block) \
 X(fragment_depth_local_acc) X(fragment_depth_local_window) X(fragment_depth_acc_flush) X(fragment_depth_external_needed) \
 X(fragment_depth_barrier_wait) X(fragment_depth_older_read_wait) X(fragment_color_depth_read_wait) \
 X(fragment_depth_psram_ar_only_block) X(command_local_drain_wait) X(command_cache_barrier_wait) X(command_swap_wait) \
 X(fragment_framebuffer_drain_wait) X(fragment_inflight_full) X(triangle_records) X(texture_requests) \
 X(texture_responses) X(texture_fill_responses) X(texture_hit_responses) X(texture_flush_level) \
 X(cache_hits) X(cache_misses) X(cache_writebacks) X(cache_flush_entries) X(cache_flush_completions) \
 X(cache_maintenance_cycles) X(cache_maintenance_scan_cycles) X(cache_maintenance_write_cycles) \
 X(cache_fill_hazard_cycles) X(cache_idle_flush_level) X(cache_texture_flush_level) X(cache_barrier_flush_level) \
 X(cache_upstream_aw_accept) X(cache_upstream_aw_wait) X(cache_upstream_w_accept) X(cache_upstream_w_wait) X(cache_upstream_b) \
 X(cache_downstream_aw_accept) X(cache_downstream_aw_wait) X(cache_downstream_write_words) \
 X(sdram_read_words) X(sdram_write_words) X(sdram_write_bytes) X(sdram_word_read_commands) X(sdram_word_write_commands) \
 X(sdram_scan_words) X(sdram_scan_requests) X(sdram_refresh_cycles) X(psram_words) X(geometry_psram_words) X(texture_psram_words)
enum Metric {
#define ENUM(F) F,
 GPU_METRICS(ENUM)
#undef ENUM
 METRICS};
inline const std::array<const char*,METRICS> metric_names={
#define NAME(F) #F,
 GPU_METRICS(NAME)
#undef NAME
};
struct ReadSample {bool valid=false,ready=false,r=false,last=false;unsigned len=0;};
struct Sample {
 unsigned state=0,fbss=0,command=0,command_class=0,cache_state=0,tex_state=0;
 unsigned dstate=0,xf_state=0,walker_state=0,pss_state=0;
 unsigned ring_words=0,ring_unpublished=0,fbwq_count=0,arb_wq_count=0,wr_inflight=0;
 unsigned req_count=0,req_reserved=0,res_count=0,res_reserved=0,cache_wb_count=0;
 unsigned arb_state=0,arb_owner=0,controller_state=0,psram_state=0,psram_owner=0;
 bool gpu_busy=false,core_busy=false,cache_busy=false,ring_empty=true,ring_full=false;
 bool cache_has_lines=false,cache_maintenance=false,cache_flush=false,cache_done=false,cache_flush_done=false,swap_pending=false;
 bool idle_flush=false,texture_flush=false,barrier_flush=false,cache_fill_hazard=false;
 uint32_t cache_hits=0,cache_misses=0,cache_writebacks=0;
 bool p0a=false,p0=false,p1=false,p2=false,p2b=false,p3=false,discard=false,z_test=false;
 bool backend_empty=true,pipe_stall=false,p3_consumed=false,write_block=false,read_drain=false;
 bool depth_acc=false,depth_window=false,depth_acc_flush=false,depth_external=false,depth_barrier=false,depth_older=false,depth_psram_ar_only=false;
 bool texture_psram_route=false;
 bool texture_lookup_wait=false,texture_request_block=false,texture_response=false,texture_fill_response=false;
 bool texture_req_accept=false,perspective_wait=false,triangle_record=false,texture_flush_req=false;
 bool upstream_aw_accept=false,upstream_aw_wait=false,upstream_w_accept=false,upstream_w_wait=false,upstream_b=false;
 bool downstream_aw_accept=false,downstream_aw_wait=false,downstream_w=false;
 bool physical_read=false,physical_write=false,native_read=false,native_write=false,scan_word=false,scan_request=false;
 unsigned physical_write_strb=0;
 bool cpu_retire=false,ring_upload=false,ring_publish=false;uint32_t cpu_pc=0,per_read_addr=0;bool per_read=false;
 std::array<ReadSample,5> reads{}; // upstream colour/depth, downstream SDRAM, texture, geometry, combined PSRAM
};
struct Histogram {
 uint64_t count=0,sum=0,max=0;std::map<uint64_t,uint64_t> bins;
 void add(uint64_t v){++count;sum+=v;max=std::max(max,v);++bins[v];}
 uint64_t percentile(unsigned p)const {uint64_t n=0,target=(count*p+99)/100;for(auto &[v,c]:bins){n+=c;if(n>=target)return v;}return 0;}
};
struct ReadCounts {uint64_t requests=0,words=0,completions=0,ar_wait=0,outstanding=0,before_first=0;Histogram first,last;};
struct Counters {
 uint64_t cycles=0,protocol_errors=0;
 std::array<uint64_t,PARTITIONS> partition{},poll_partition{};
 std::array<uint64_t,METRICS> metrics{};
 std::array<uint64_t,64> states{},derive_states{},perspective_states{},controller_states{};
 std::array<uint64_t,32> cache_states{},transform_states{},walker_states{};
 std::array<uint64_t,16> fragment_fbss{},psram_states{};
 std::array<uint64_t,8> texture_states{},maintenance_reasons{},flush_entry_reasons{};
 std::array<uint64_t,17> ring_occupancy{},fbwq_occupancy{},arb_wq_occupancy{};
 std::array<uint64_t,5> texture_req_occupancy{},texture_res_occupancy{},cache_wb_occupancy{};
 std::array<uint64_t,256> command_entries{},command_cycles{};
 std::array<uint64_t,16*32> fragment_fbss_cache{};
 std::array<uint64_t,PARTITIONS*32> partition_cache{};
 std::array<uint64_t,PARTITIONS*8> partition_texture{};
 // [partition][read channel][mask]: bit0 AR blocked, bit1 accepted request active,
 // bit2 active request has not returned its first word. These are pre-edge observations.
 std::array<uint64_t,PARTITIONS*5*8> partition_read_activity{};
 std::array<uint64_t,12> sdram_owner_cycles{},sdram_owner_read_words{},sdram_owner_write_words{};
 std::array<uint64_t,5*16> psram_owner_state_cycles{};
 std::array<ReadCounts,5> reads{};
};
struct Bin {uint64_t begin=0,end=0,cycles=0,poll=0,retired=0,ring_sum=0,fbwq_sum=0,sd_r=0,sd_w=0,psram_r=0;
 std::array<uint64_t,PARTITIONS> partition{};std::array<uint64_t,8> texture{};std::array<uint64_t,32> cache{};};
class Observer {
 struct Read {bool active=false,first=false;unsigned beats=0,expected=0;uint64_t accepted=0;};
 std::array<Read,5> active_reads{};
 std::map<unsigned,Counters> windows;
 std::map<std::pair<unsigned,uint64_t>,Bin> bins;
 struct Event{uint64_t cycle;unsigned window;uint32_t pc;std::string name;};std::vector<Event> events;
 std::map<uint32_t,std::string> event_points;
 std::vector<std::pair<uint32_t,uint32_t>> poll_regions;
 std::vector<uint32_t> poll_loads;
 uint64_t previous_cycle=0;bool sampled=false;uint32_t last_pc=0,last_hits=0,last_misses=0,last_writebacks=0;
 unsigned previous_state=0,maintenance_reason=0;
 static unsigned popcount(unsigned x){unsigned n=0;for(;x;x>>=1)n+=x&1;return n;}
 bool polling(uint32_t pc)const {for(auto [lo,hi]:poll_regions)if(pc>=lo&&pc<hi)return true;return false;}
 bool poll_load(uint32_t pc)const{return std::find(poll_loads.begin(),poll_loads.end(),pc)!=poll_loads.end();}
public:
 Observer()=default;
 void add_event(uint32_t pc,const std::string &name){event_points[pc]=name;}
 void add_poll_region(uint32_t lo,uint32_t hi,uint32_t load_pc){poll_regions.emplace_back(lo,hi);poll_loads.push_back(load_pc);}
 const Counters &counts(unsigned w)const{return windows.at(w);}
 static Partition classify(const Sample &s) {
  if(s.state==0)return s.ring_empty?((s.cache_busy||s.cache_has_lines||s.idle_flush)?IDLE_CACHE_DRAIN:IDLE_EMPTY):COMMAND_FETCH;
  if(s.state==1||s.state==2||s.state==3||s.state==18)return COMMAND_FETCH;
  if(s.state==4)return ((s.command_class==1||s.command_class==2||s.command_class==3)&&(!s.read_drain||!s.cache_done||(s.command_class==2&&s.swap_pending)))?COMMAND_BARRIER:COMMAND_EXECUTE;
  if(s.state==7||s.state==8)return CLEAR;
  if(s.state==15)return TRIANGLE_SETUP;
  if(s.state==14)return TRIANGLE_WALK;
  if(s.state==16)return VERTEX_TRANSFORM;
  if(s.state==17||s.state==19)return VERTEX_CACHE;
  if(s.state>=9&&s.state<=13)return SPAN_SETUP;
  if(s.state==5)return FB_FLUSH;
  if(s.state!=6)return OTHER;
  if(s.p3_consumed)return FRAG_RETIRE;
  if(s.fbss==5||s.fbss==8||s.fbss==14)return FRAG_READ_RESPONSE;
  if(s.fbss==1||s.fbss==2||s.fbss==6)return FRAG_READ_DRAIN;
  if(s.write_block||s.wr_inflight>=14)return FRAG_WRITE_BACKPRESSURE;
  if(s.depth_external&&(s.depth_barrier||s.depth_older))return FRAG_DEPTH_READ_GATE;
  if(s.backend_empty&&(s.texture_lookup_wait||s.texture_request_block)&&!s.texture_response)return FRAG_TEXTURE_STARVED;
  if(s.backend_empty&&s.perspective_wait)return FRAG_PERSPECTIVE_STARVED;
  if(s.pipe_stall)return FRAG_LOCAL_HOLD;
  return FRAG_PIPE_PROGRESS;
 }
 void sample(uint64_t cycle,unsigned window,const Sample &s) {
  auto &n=windows[window];++n.cycles;
  if(sampled&&cycle!=previous_cycle+1)++n.protocol_errors;
  if(s.state>=64||s.fbss>=16||s.cache_state>=32||s.tex_state>=8||s.fbwq_count>16||s.arb_wq_count>16||s.req_count>4||s.res_count>4||s.cache_wb_count>4)++n.protocol_errors;
  if(s.cpu_retire) {
   last_pc=s.cpu_pc;
   auto e=event_points.find(s.cpu_pc);if(e!=event_points.end())events.push_back({cycle,window,s.cpu_pc,e->second});
  }
  bool poll=polling(last_pc);Partition part=classify(s);++n.partition[part];n.poll_partition[part]+=poll;
  ++n.states[s.state&63];++n.cache_states[s.cache_state&31];++n.texture_states[s.tex_state&7];
  ++n.controller_states[s.controller_state&63];++n.psram_states[s.psram_state&15];
  ++n.partition_cache[part*32+(s.cache_state&31)];
  ++n.partition_texture[part*8+(s.tex_state&7)];
  ++n.ring_occupancy[std::min(16u,(s.ring_words+255u)/256u)];++n.fbwq_occupancy[std::min(16u,s.fbwq_count)];
  ++n.arb_wq_occupancy[std::min(16u,s.arb_wq_count)];++n.texture_req_occupancy[std::min(4u,s.req_count)];
  ++n.texture_res_occupancy[std::min(4u,s.res_count)];++n.cache_wb_occupancy[std::min(4u,s.cache_wb_count)];
  if(s.state==15)++n.derive_states[s.dstate&63];
  if(s.state==16)++n.transform_states[s.xf_state&31];
  if(s.state==14)++n.walker_states[s.walker_state&31];
  if(s.state==6)++n.perspective_states[s.pss_state&63];
  if(sampled&&previous_state==2)++n.command_entries[s.command&255];
  if(s.state>2)++n.command_cycles[s.command&255];
  unsigned owner_index=(s.arb_state%3)*4+(s.arb_owner&3);++n.sdram_owner_cycles[owner_index];
  n.sdram_owner_read_words[owner_index]+=s.physical_read;n.sdram_owner_write_words[owner_index]+=s.physical_write;
  ++n.psram_owner_state_cycles[std::min(4u,s.psram_owner)*16+(s.psram_state&15)];
  auto &m=n.metrics;
#define ADD(F,V) m[F]+=uint64_t(V)
  ADD(gpu_busy,s.gpu_busy);ADD(core_busy,s.core_busy);ADD(cache_busy,s.cache_busy);ADD(ring_empty,s.ring_empty);ADD(ring_full,s.ring_full);
  ADD(cpu_ring_upload,s.ring_upload);ADD(cpu_ring_publish,s.ring_publish);ADD(cpu_retire,s.cpu_retire);
  ADD(cpu_ring_poll_context,poll);ADD(cpu_ring_poll_load_retire,s.cpu_retire&&poll_load(s.cpu_pc));ADD(cpu_ring_pointer_reads,s.per_read&&s.per_read_addr==0x4a000010u);
  const bool command_barrier=s.state==4&&(s.command_class==1||s.command_class==2||s.command_class==3);
  ADD(command_local_drain_wait,command_barrier&&!s.read_drain);ADD(command_cache_barrier_wait,command_barrier&&!s.cache_done);
  ADD(command_swap_wait,s.state==4&&s.command_class==2&&s.swap_pending);
  if(s.state==6) {
   ++n.fragment_fbss[s.fbss&15];++n.fragment_fbss_cache[(s.fbss&15)*32+(s.cache_state&31)];
   ADD(fragment_cycles,1);ADD(fragment_retired,s.p3_consumed);ADD(fragment_discarded,s.p3&&s.discard&&!s.pipe_stall);
   ADD(fragment_depth_test,s.p3&&!s.discard&&s.z_test);ADD(fragment_pipeline_stall,s.pipe_stall);ADD(fragment_backend_empty,s.backend_empty);
   ADD(fragment_texture_lookup_wait,s.texture_lookup_wait);ADD(fragment_texture_request_block,s.texture_request_block);
   ADD(fragment_texture_credit_full,s.p0a&&s.req_reserved>=4);ADD(fragment_texture_result_full,s.res_reserved>=4);
   ADD(fragment_texture_response,s.texture_response);ADD(fragment_perspective_wait,s.perspective_wait);ADD(fragment_write_block,s.write_block);
   ADD(fragment_depth_local_acc,s.depth_acc);ADD(fragment_depth_local_window,s.depth_window);ADD(fragment_depth_acc_flush,s.depth_acc_flush);
   ADD(fragment_depth_external_needed,s.depth_external);ADD(fragment_depth_barrier_wait,s.depth_external&&s.depth_barrier);ADD(fragment_depth_older_read_wait,s.depth_external&&s.depth_older);
   ADD(fragment_depth_psram_ar_only_block,s.depth_psram_ar_only);
   ADD(fragment_color_depth_read_wait,s.fbss==5||s.fbss==8||s.fbss==14);
   ADD(fragment_framebuffer_drain_wait,(s.fbss==1||s.fbss==6||s.depth_external)&&!s.read_drain);
   ADD(fragment_inflight_full,s.wr_inflight>=14);
  }
  ADD(triangle_records,s.triangle_record);ADD(texture_requests,s.texture_req_accept);ADD(texture_responses,s.texture_response);
  ADD(texture_fill_responses,s.texture_response&&s.texture_fill_response);ADD(texture_hit_responses,s.texture_response&&!s.texture_fill_response);
  ADD(texture_flush_level,s.texture_flush_req);
  if(sampled){ADD(cache_hits,uint32_t(s.cache_hits-last_hits));ADD(cache_misses,uint32_t(s.cache_misses-last_misses));ADD(cache_writebacks,uint32_t(s.cache_writebacks-last_writebacks));}
  if(s.cache_state==1&&s.cache_flush){maintenance_reason=(s.idle_flush?1u:0u)|(s.texture_flush?2u:0u)|(s.barrier_flush?4u:0u);++n.flush_entry_reasons[maintenance_reason];ADD(cache_flush_entries,1);}
  ADD(cache_flush_completions,s.cache_flush_done);
  ADD(cache_maintenance_cycles,s.cache_maintenance);if(s.cache_maintenance)++n.maintenance_reasons[maintenance_reason];
  ADD(cache_maintenance_scan_cycles,s.cache_maintenance&&(s.cache_state==23||s.cache_state==24));
  ADD(cache_maintenance_write_cycles,s.cache_maintenance&&(s.cache_state==14||s.cache_state==15||s.cache_state==16));
  ADD(cache_fill_hazard_cycles,s.cache_fill_hazard);ADD(cache_idle_flush_level,s.idle_flush);ADD(cache_texture_flush_level,s.texture_flush);ADD(cache_barrier_flush_level,s.barrier_flush);
  ADD(cache_upstream_aw_accept,s.upstream_aw_accept);ADD(cache_upstream_aw_wait,s.upstream_aw_wait);ADD(cache_upstream_w_accept,s.upstream_w_accept);
  ADD(cache_upstream_w_wait,s.upstream_w_wait);ADD(cache_upstream_b,s.upstream_b);ADD(cache_downstream_aw_accept,s.downstream_aw_accept);
  ADD(cache_downstream_aw_wait,s.downstream_aw_wait);ADD(cache_downstream_write_words,s.downstream_w);
  ADD(sdram_read_words,s.physical_read);ADD(sdram_write_words,s.physical_write);ADD(sdram_write_bytes,s.physical_write?popcount(s.physical_write_strb):0);
  ADD(sdram_word_read_commands,s.native_read);ADD(sdram_word_write_commands,s.native_write);ADD(sdram_scan_words,s.scan_word);ADD(sdram_scan_requests,s.scan_request);
  ADD(sdram_refresh_cycles,s.controller_state==60||s.controller_state==61);ADD(psram_words,s.reads[4].r);ADD(geometry_psram_words,s.reads[3].r);ADD(texture_psram_words,s.reads[2].r&&s.texture_psram_route);
#undef ADD
  for(unsigned i=0;i<5;++i) {
   const auto &v=s.reads[i];auto &a=active_reads[i];auto &r=n.reads[i];r.ar_wait+=v.valid&&!v.ready;r.outstanding+=a.active;r.before_first+=a.active&&!a.first;
   const unsigned activity=(v.valid&&!v.ready?1u:0u)|(a.active?2u:0u)|(a.active&&!a.first?4u:0u);
   ++n.partition_read_activity[(part*5+i)*8+activity];
   // An old request may complete on the same edge a new address is accepted.
   // No observed channel permits a zero-cycle response to a newly accepted AR.
   if(v.r){++r.words;if(!a.active)++n.protocol_errors;else {if(!a.first){a.first=true;r.first.add(cycle-a.accepted);}++a.beats;if(v.last!=(a.beats==a.expected))++n.protocol_errors;if(v.last){++r.completions;r.last.add(cycle-a.accepted);a.active=false;}}}
   if(v.valid&&v.ready){if(a.active)++n.protocol_errors;a={true,false,0,v.len+1,cycle};++r.requests;}
  }
  auto &b=bins[{window,cycle/4096}];if(!b.cycles)b.begin=cycle;b.end=cycle+1;++b.cycles;b.poll+=poll;b.retired+=s.cpu_retire;b.ring_sum+=s.ring_words;b.fbwq_sum+=s.fbwq_count;b.sd_r+=s.physical_read;b.sd_w+=s.physical_write;b.psram_r+=s.reads[4].r;
  ++b.partition[part];++b.texture[s.tex_state&7];++b.cache[s.cache_state&31];
  sampled=true;previous_cycle=cycle;previous_state=s.state;last_hits=s.cache_hits;last_misses=s.cache_misses;last_writebacks=s.cache_writebacks;
 }
 template<class TB,class SYS,class CPU> void sample(uint64_t cycle,unsigned window,const TB &tb,const SYS &sys,const CPU &) {
  Sample s;
#define G(F) sys.__PVT__gpu__DOT__##F
#define C(F) sys.__PVT__gpu_render_cache__DOT__##F
#define T(F) sys.__PVT__gpu__DOT__tex_stream__DOT__##F
  s.state=G(state);s.fbss=G(fbss);s.command=G(cmd_type);s.command_class=G(cmd_class);s.cache_state=C(state);s.tex_state=sys.gpu__DOT__tex_cache__DOT__state;
  s.dstate=G(dstate);s.xf_state=G(xf_state);s.walker_state=G(g_tri_walker__DOT__tri_walker__DOT__state);s.pss_state=G(persp_pss);
  if constexpr(requires {sys.__PVT__gpu__DOT__command_wrptr;})
   s.ring_words=(G(command_wrptr)-G(ring_rdptr))&4095;
  else s.ring_words=(G(ring_wrptr)-G(ring_rdptr))&4095;
  s.ring_unpublished=(G(ring_wr_addr)-G(ring_wrptr))&4095;
  s.fbwq_count=G(fbwq_count);s.arb_wq_count=sys.__PVT__sdram_arb__DOT__gpu_wq_count;s.wr_inflight=G(m_wr_inflight);
  s.req_count=T(req_count);s.req_reserved=T(req_reserved);s.res_count=T(res_count);s.res_reserved=T(res_reserved);s.cache_wb_count=popcount(C(wb_valid));
  s.arb_state=sys.__PVT__sdram_arb__DOT__arb_state;s.arb_owner=sys.__PVT__sdram_arb__DOT__grant;s.controller_state=sys.__PVT__sdram_ctrl__DOT__state;
  s.psram_state=sys.__PVT__paired__DOT__ctrl__DOT__state;s.psram_owner=sys.__PVT__paired__DOT__adapter__DOT__owner;
  s.gpu_busy=tb.obs_gpu_busy;s.core_busy=sys.__PVT__gpu_busy_core;s.cache_busy=sys.__PVT__gpu_cache_busy;s.ring_empty=G(ring_empty);s.ring_full=G(cpu_ring_full);
  s.cache_has_lines=C(live_lines)!=0;s.cache_maintenance=C(maintenance);s.cache_flush=sys.__Vcellinp__gpu_render_cache__flush_req;
  s.cache_done=sys.__PVT__gpu_cache_barrier_done;s.cache_flush_done=sys.__PVT__gpu_cache_flush_done;s.swap_pending=sys.__PVT__periph__DOT__fb_swap_pending;
  s.idle_flush=sys.__PVT__gpu_cache_idle_flush;s.texture_flush=sys.__PVT__gpu_cache_tex_flush_pending;
  s.barrier_flush=s.state==4&&(s.command_class==1||s.command_class==2||s.command_class==3)&&G(fb_write_drain_complete);
  s.cache_fill_hazard=s.cache_state==27&&C(fill_hazard_r);s.cache_hits=C(hits);s.cache_misses=C(misses);s.cache_writebacks=C(writebacks);
  s.p0a=G(p0a_valid);s.p0=G(p0_valid);s.p1=G(p1_valid);s.p2=G(p2_valid);s.p2b=G(p2b_valid);s.p3=G(p3_valid);s.discard=G(p3_discard);s.z_test=G(p3_z_test);
  s.backend_empty=!s.p1&&!s.p2&&!s.p2b&&!s.p3;s.pipe_stall=G(fp_pipe_stall);s.read_drain=G(fb_write_drain_complete);
  // Reconstruct the CURRENT-edge consume branch. The procedural p3_consumed
  // temporary in generated C++ is from the previous edge and must not be read.
  const bool opaque_commit=!G(blend_group_active)&&!s.z_test
      &&!(G(sp_truecolor)&&G(sp_blend)&&(!G(p3_cb_ready)||G(p3_cb_stale)))
      &&(!G(fb_acc_valid)||G(fb_acc_addr)==G(p3_fb_word_addr_w)||G(fbwq_can_push));
  const bool translucent_commit=!G(blend_group_active)
      ||(G(blend_group_word_addr)==G(p3_fb_word_addr_w)&&!(G(blend_group_mask)&G(p3_fb_lane_mask_w)));
  s.p3_consumed=s.state==6&&s.fbss==0&&s.p3&&!s.discard&&((G(p3_flags)&8)?translucent_commit:opaque_commit);
  s.write_block=s.p3&&!s.discard&&!(G(p3_flags)&8)&&G(fb_acc_valid)&&((G(fb_acc_addr)>>2)!=(G(p3_fb_addr)>>2))&&!G(fbwq_can_push);
  const uint32_t za=G(p3_z_addr)&~3u;const bool zactive=s.state==6&&s.fbss==0&&s.p3&&!s.discard&&!(G(p3_flags)&8)&&!G(blend_group_active)&&s.z_test;
  const bool acc=G(z_acc_valid)&&G(z_acc_addr)==za;const bool win=!G(zw_snoop_pending)&&((G(zw_valid)>>((za>>2)&7))&1)&&G(zw_base)==(za>>5);
  s.depth_acc=zactive&&acc;s.depth_window=zactive&&!G(z_acc_valid)&&win;s.depth_acc_flush=zactive&&G(z_acc_valid)&&!acc;
  s.depth_external=zactive&&!G(z_acc_valid)&&!win;s.depth_barrier=!s.read_drain;s.depth_older=G(zw_fill_active)||G(tex_axi_arvalid)||G(tex_m0_in_flight);
  s.texture_psram_route=G(gpu_tex_mem_route);
  s.depth_psram_ar_only=s.depth_external&&!G(zw_fill_active)&&!G(tex_m0_in_flight)&&s.read_drain&&G(tex_axi_arvalid)&&s.texture_psram_route;
  s.texture_response=T(res_push);s.texture_fill_response=G(tex_cache__DOT__fill_resp_valid_a);s.texture_lookup_wait=T(lookup_pending)&&!s.texture_response;
  s.texture_request_block=G(tex_req_valid)&&!G(tex_req_ready);s.texture_req_accept=T(req_pop);s.perspective_wait=G(persp_active)&&!G(persp_seg_a_ready);
  s.triangle_record=G(tri_rec_valid)&&(s.state==14||(s.state==6&&G(span_la)==6));s.texture_flush_req=G(tex_flush_req);
  s.upstream_aw_accept=sys.__PVT__gpu_s_wr_awvalid&&sys.__PVT__gpu_s_wr_awready;s.upstream_aw_wait=sys.__PVT__gpu_s_wr_awvalid&&!sys.__PVT__gpu_s_wr_awready;
  s.upstream_w_accept=sys.__PVT__gpu_s_wr_wvalid&&sys.__PVT__gpu_s_wr_wready;s.upstream_w_wait=sys.__PVT__gpu_s_wr_wvalid&&!sys.__PVT__gpu_s_wr_wready;s.upstream_b=sys.__PVT__gpu_s_wr_bvalid;
  s.downstream_aw_accept=sys.__PVT__gpu_wr_awvalid&&sys.__PVT__gpu_wr_awready;s.downstream_aw_wait=sys.__PVT__gpu_wr_awvalid&&!sys.__PVT__gpu_wr_awready;
  s.downstream_w=sys.__PVT__sdram_arb__DOT__m0_w_post_fire;
  s.physical_read=sys.__PVT__arb_rvalid&&sys.__PVT__arb_rready;s.physical_write=tb.dbg_w_valid&&tb.dbg_w_ready;s.physical_write_strb=tb.dbg_w_strb;
  s.native_read=tb.dbg_word_rd_sd;s.native_write=tb.dbg_word_wr_sd;s.scan_word=tb.inj_burst_data_valid;s.scan_request=tb.inj_burst_rd;
  s.cpu_retire=tb.obs_retire;s.cpu_pc=tb.obs_retire_pc;s.per_read=tb.dbg_per_arvalid&&tb.dbg_per_arready;s.per_read_addr=tb.dbg_per_araddr;
  s.ring_upload=G(cpu_ring_req)&&!G(cpu_ring_full)&&G(dma_state)==0;
  s.ring_publish=sys.__PVT__gpu_reg_wr&&sys.__PVT__gpu_reg_addr==0&&(tb.obs_gpu_mmio_data&8)&&G(cpu_ring_mode);
  const bool upstream_read=G(dma_owns_ar)?bool(G(dma_arvalid)):G(blend_owns_m0)?bool(G(blend_arvalid)):G(gpu_tex_mem_route)?false:bool(G(tex_axi_arvalid));
  s.reads[0]={upstream_read,bool(sys.__PVT__gpu_s_rd_arready),bool(sys.__PVT__gpu_s_rd_rvalid),bool(sys.__PVT__gpu_s_rd_rlast),unsigned(sys.__PVT__gpu_s_rd_arlen)};
  s.reads[1]={bool(sys.__PVT__gpu_rd_arvalid),bool(sys.__PVT__gpu_rd_arready),bool(sys.__PVT__gpu_rd_rvalid),bool(sys.__PVT__arb_rlast),unsigned(sys.__PVT__arb_arlen)};
  const bool tex_ready=G(gpu_tex_mem_route)?(sys.__PVT__cram_arbiter__DOT__state!=2&&!sys.__PVT__cram_arbiter__DOT__selected&&sys.__PVT__arready_m):(!G(dma_owns_ar)&&!G(blend_owns_m0)&&sys.__PVT__gpu_s_rd_arready);
  s.reads[2]={bool(G(tex_axi_arvalid)),tex_ready,bool(G(tex_axi_rvalid)),bool(G(tex_axi_rlast)),unsigned(G(tex_axi_arlen))};
  s.reads[3]={bool(sys.__Vcellinp__cram_arbiter__s1_arvalid),bool(sys.__PVT__geometry_arready),bool(sys.__PVT__geometry_rvalid),bool(sys.__PVT__geometry_rlast),unsigned(sys.__PVT__arlen_m)};
  s.reads[4]={bool(sys.__PVT__arvalid_m),bool(sys.__PVT__arready_m),bool(sys.__PVT__rvalid_m),bool(sys.__PVT__rlast_m),unsigned(sys.__PVT__arlen_m)};
#undef G
#undef C
#undef T
  sample(cycle,window,s);
 }
 void write(const std::string &prefix)const {
  std::ofstream f(prefix+".json");f<<"{\"schema\":1,\"passive\":true,\"partition_names\":[";bool comma=false;for(auto name:partition_names){if(comma)f<<",";comma=true;f<<"\""<<name<<"\"";}f<<"],\"windows\":{";
  comma=false;for(const auto &[window,n]:windows){if(comma)f<<",";comma=true;f<<"\""<<window<<"\":{\"cycles\":"<<n.cycles<<",\"protocol_errors\":"<<n.protocol_errors;
   auto array=[&](const char*name,const auto&a){f<<",\""<<name<<"\":[";bool c=false;for(auto v:a){if(c)f<<",";c=true;f<<v;}f<<"]";};
   array("partition",n.partition);array("poll_partition",n.poll_partition);array("states",n.states);array("cache_states",n.cache_states);array("texture_states",n.texture_states);
   array("fragment_fbss",n.fragment_fbss);array("fragment_fbss_cache",n.fragment_fbss_cache);array("partition_cache",n.partition_cache);
   array("partition_texture",n.partition_texture);array("partition_read_activity",n.partition_read_activity);
   array("derive_states",n.derive_states);array("transform_states",n.transform_states);array("walker_states",n.walker_states);array("perspective_states",n.perspective_states);
   array("ring_occupancy",n.ring_occupancy);array("fbwq_occupancy",n.fbwq_occupancy);array("arb_wq_occupancy",n.arb_wq_occupancy);
   array("texture_req_occupancy",n.texture_req_occupancy);array("texture_res_occupancy",n.texture_res_occupancy);array("cache_wb_occupancy",n.cache_wb_occupancy);
   array("command_entries",n.command_entries);array("command_cycles",n.command_cycles);array("maintenance_reasons",n.maintenance_reasons);array("flush_entry_reasons",n.flush_entry_reasons);
   array("sdram_owner_cycles",n.sdram_owner_cycles);array("sdram_owner_read_words",n.sdram_owner_read_words);array("sdram_owner_write_words",n.sdram_owner_write_words);
   array("controller_states",n.controller_states);array("psram_states",n.psram_states);array("psram_owner_state_cycles",n.psram_owner_state_cycles);
   f<<",\"metrics\":{";for(unsigned i=0;i<METRICS;++i){if(i)f<<",";f<<"\""<<metric_names[i]<<"\":"<<n.metrics[i];}f<<"},\"reads\":[";
   for(unsigned i=0;i<5;++i){if(i)f<<",";const auto&r=n.reads[i];f<<"{\"requests\":"<<r.requests<<",\"words\":"<<r.words<<",\"completions\":"<<r.completions<<",\"ar_wait\":"<<r.ar_wait<<",\"outstanding\":"<<r.outstanding<<",\"before_first\":"<<r.before_first;
    auto hist=[&](const char*name,const Histogram &h){f<<",\""<<name<<"\":{\"count\":"<<h.count<<",\"sum\":"<<h.sum<<",\"max\":"<<h.max<<",\"p50\":"<<h.percentile(50)<<",\"p95\":"<<h.percentile(95)<<",\"p99\":"<<h.percentile(99)<<"}";};hist("first",r.first);hist("last",r.last);f<<"}";}f<<"]}";
  }f<<"},\"pending_reads\":[";for(unsigned i=0;i<5;++i){if(i)f<<",";f<<active_reads[i].active;}f<<"]}\n";
  std::ofstream t(prefix+"-timeline.csv");t<<"window,begin,end,cycles,cpu_retire,cpu_poll_context,ring_word_sum,fbwq_sum,sdram_read_words,sdram_write_words,psram_read_words";for(auto name:partition_names)t<<","<<name;for(unsigned i=0;i<8;++i)t<<",tex_state_"<<i;for(unsigned i=0;i<32;++i)t<<",cache_state_"<<i;t<<"\n";
  for(const auto &[key,b]:bins){t<<key.first<<","<<b.begin<<","<<b.end<<","<<b.cycles<<","<<b.retired<<","<<b.poll<<","<<b.ring_sum<<","<<b.fbwq_sum<<","<<b.sd_r<<","<<b.sd_w<<","<<b.psram_r;for(auto v:b.partition)t<<","<<v;for(auto v:b.texture)t<<","<<v;for(auto v:b.cache)t<<","<<v;t<<"\n";}
  std::ofstream e(prefix+"-events.csv");e<<"window,cycle,pc,name\n";for(const auto &v:events)e<<v.window<<","<<v.cycle<<","<<v.pc<<","<<v.name<<"\n";
 }
};
}
#endif
