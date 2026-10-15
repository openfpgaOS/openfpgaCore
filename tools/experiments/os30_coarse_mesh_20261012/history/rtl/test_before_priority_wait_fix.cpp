#include "Vgeometry_load_alias.h"
#include "Vgeometry_load_alias___024root.h"
#define W(name) d.rootp->geometry_load_alias__DOT__async_mesh__DOT__##name
#include "verilated.h"
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <cmath>
static uint32_t bits(float x){uint32_t u;memcpy(&u,&x,4);return u;}
struct Sim{
 Vgeometry_load_alias d;uint64_t cycles=0,checks=0,reads=0,beats=0;uint32_t rng=51;
 const uint32_t base=0x1000,cb=0xf000,db=0x20000;std::vector<uint32_t> mem,cmd,desc,words;bool pending=false,blockAR=false,blockR=false,forceStall=false,held=false;uint32_t address=0,heldword=0;unsigned left=0;
 uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
 void ok(bool x,const char*s){checks++;if(!x)throw std::runtime_error(std::string(s)+" cycle="+std::to_string(cycles));}
 void tick(){
  d.clk=0;d.m_arready=!pending&&!blockAR&&(random()%4!=0);if(!(d.reg_wr&&d.reg_addr==0x28))d.mesh_word_ready=!forceStall&&(random()%4!=0);
  if(!d.m_rvalid&&pending&&!blockR&&random()%3){d.m_rvalid=1;d.m_rdata=address>=db?desc.at((address-db)/4):address>=cb?cmd.at((address-cb)/4):mem.at((address-base)/4);d.m_rlast=left==1;d.m_rresp=0;}
  d.eval();if(held&&!d.mesh_consumer_reset)ok(d.mesh_word_valid&&d.mesh_word_data==heldword,"packet changed under stall");
  held=d.mesh_word_valid&&!d.mesh_word_ready&&!d.mesh_consumer_reset;heldword=d.mesh_word_data;
  if(d.mesh_word_valid&&d.mesh_word_ready)words.push_back(d.mesh_word_data);
  bool ar=d.m_arvalid&&d.m_arready,r=d.m_rvalid&&d.m_rready;auto a=d.m_araddr;unsigned len=d.m_arlen+1;
  if(ar)ok(!pending,"multiple read owners");
  d.clk=1;d.eval();cycles++;
  if(r){beats++;address+=4;if(!--left)pending=false;d.m_rvalid=0;}
  if(ar){pending=true;address=a;left=len;reads++;}
 }
 template<class F>void wait(F f,const char*message,unsigned limit=20000){for(unsigned n=0;!f();n++){tick();if(n>limit)throw std::runtime_error(std::string(message)+" cycle="+std::to_string(cycles)+" legacy="+std::to_string(rd(1))+" status="+std::to_string(st()));}}
 void wr(unsigned a,uint32_t v){d.reg_wr=1;d.reg_addr=a;d.reg_wdata=v;d.reg_wstrb=15;tick();d.reg_wr=0;d.eval();}
 uint32_t rd(unsigned a){d.reg_addr=a;d.eval();return d.reg_rdata;}
 uint32_t st(){return rd(0x21);}
 void reset(){d.reset=1;d.reg_wr=0;d.view_active=0;d.mesh_ctx_valid=0;d.mesh_job_valid=0;d.mesh_consumer_reset=0;d.mesh_bridge_fault=0;d.m_rvalid=0;pending=false;for(int n=0;n<7;n++)tick();d.reset=0;}
 void acquire(unsigned target,unsigned ticket){wr(0x2b,target);wr(0x2c,ticket);wr(0x2d,1);wait([&]{return st()&16;},"legacy acquire");ok(rd(0x2f)==ticket,"lease ticket");}
 void release(){wr(0x2d,2);wait([&]{return !(st()&16);},"legacy release");}
 void ctx(unsigned i,uint32_t v){d.mesh_ctx_index=i;d.mesh_ctx_data=v;d.mesh_ctx_valid=1;d.eval();wait([&]{return d.mesh_ctx_ready;},"context blocked");tick();d.mesh_ctx_valid=0;d.eval();}
 void context(){float m[16]={1,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};for(unsigned i=0;i<16;i++)ctx(i,bits(m[i]));ctx(16,bits(1));for(unsigned i=17;i<22;i++)ctx(i,0);ctx(22,0xffffffff);ctx(23,0);for(unsigned i=24;i<28;i++)ctx(i,100+i);}
 uint32_t cv(unsigned i){float m[16]={1,0,0,0,0,1,0,0,0,0,1,1,0,0,0,2};if(i<16)return bits(m[i]);if(i==16)return bits(1);if(i==22)return 0xffffffff;return i>=24?100+i:0;}
 void prefix(unsigned lo,unsigned hi){for(unsigned i=lo;i<hi;i++)ctx(i,cv(i));}
 void request(unsigned target,unsigned ticket){wr(0x2b,target);wr(0x2c,ticket);wr(0x2d,1);}
 void vertices(bool partial=false){mem.clear();float xyz[3][3]={{-.25f,-.25f,-1},{.25f,-.25f,0},{0,.5f,2}};if(partial)xyz[0][0]=-4;for(auto&v:xyz){for(float a:v)mem.push_back(bits(a));mem.push_back(0);mem.push_back(0x22110000);mem.push_back(0x4433);}cmd={0x05000204,0};}
 void start(unsigned serial,unsigned count=3,unsigned commands=1,uint32_t flags=0xabcd,uint32_t uoff=0,uint32_t voff=0){words.clear();d.mesh_job_seq=serial;d.mesh_job_vertex=base;d.mesh_job_vtx_w0=0x01000000|(count<<12)|(count<<1);d.mesh_job_commands=cb;d.mesh_job_control=commands;d.mesh_job_flags=flags;d.mesh_job_uofs=uoff;d.mesh_job_vofs=voff;d.mesh_job_cpu_vertex=0x12345000;d.mesh_job_cpu_commands=0x54321000;d.mesh_job_alpha=255;d.mesh_job_valid=1;d.eval();wait([&]{return d.mesh_job_ready;},"job not ready");tick();d.mesh_job_valid=0;d.eval();}
 void group(unsigned seq,unsigned count){desc.clear();for(unsigned i=0;i<count;i++){std::vector<uint32_t>x={base,0x01003006,cb,1,0x12345000+i*72,0x54321000+i*8};desc.insert(desc.end(),x.begin(),x.end());}groupStart(seq,count);}
 void groupStart(unsigned seq,unsigned count){words.clear();d.mesh_job_seq=seq;d.mesh_job_vertex=db;d.mesh_job_vtx_w0=count;d.mesh_job_commands=0;d.mesh_job_control=0x80000000;d.mesh_job_flags=0xabcd;d.mesh_job_uofs=0;d.mesh_job_vofs=0;d.mesh_job_cpu_vertex=0;d.mesh_job_cpu_commands=0;d.mesh_job_alpha=255;d.mesh_job_valid=1;d.eval();wait([&]{return d.mesh_job_ready;},"GROUP not ready");tick();d.mesh_job_valid=0;d.eval();}
 bool restoring(unsigned i){return W(group_state)==5&&W(restore_index)==i;}
 bool descriptorOwned(){return W(mesh__DOT__descriptor_owner);}
 bool detached(){return W(snapshot_detached);}
 void shadowCheck(){for(unsigned i=0;i<28;i++)ok(W(mesh__DOT__engine__DOT__frontend__DOT__raw__DOT__attributes)[96+i]==cv(i),"immutable context shadow poisoned");}
 void idle(){wait([&]{return !(rd(1)&1);},"owner idle");}
 void done(){wait([&]{return d.mesh_job_done;},"job completion");ok(!d.mesh_word_valid,"done with output");tick();}
 uint32_t view(unsigned row,unsigned slot){ok(!d.view_block,"alias blocked");d.view_active=1;d.view_read_addr=row*32+slot;tick();auto v=d.view_read_data;d.view_active=0;d.eval();return v;}
 uint32_t data(unsigned index){wr(0x26,index);tick();return rd(0x27);}
 void consumerReset(){d.mesh_consumer_reset=1;tick();d.mesh_consumer_reset=0;d.eval();}
};
int main(){try{Sim s;unsigned cases=0;s.vertices();s.reset();s.ok(s.rd(0x20)==0x414a3033,"AJ03 capability");
 // ACQUIRE has precedence over the very first context word on the same edge.
 s.wr(0x2b,99);s.wr(0x2c,100);s.d.mesh_ctx_valid=1;s.d.mesh_ctx_index=0;s.d.mesh_ctx_data=s.cv(0);s.wr(0x2d,1);
 s.ok(!s.d.mesh_ctx_ready,"first context beat beat priority acquire");s.wait([&]{return s.st()&16;},"priority initial grant");s.ok(s.reads==0&&s.rd(0x23)==0,"initial grant consumed async job");s.d.mesh_ctx_valid=0;s.release();cases++;
 // Every opened context position, including the gap after word27, locks handoff.
 for(unsigned position=1;position<=28;position++){
  s.reset();s.vertices();s.prefix(0,position);s.request(99,100+position);
  for(unsigned i=0;i<9;i++){s.tick();s.ok(!(s.st()&16),"lease interrupted partial context");}
  s.prefix(position,28);for(unsigned i=0;i<13;i++){s.tick();s.ok(!(s.st()&16),"lease interrupted context-to-JOB gap");}
  s.forceStall=true;s.start(1);s.wait([&]{return s.st()&16;},"snapshot did not release raw");
  s.ok(s.rd(0x23)==0&&s.words.empty(),"early lease advanced output completion");
  for(unsigned i=0;i<17;i++){s.tick();s.ok((s.st()&16)&&s.rd(0x23)==0,"early lease or completion changed under stall");}
  s.forceStall=false;s.done();s.ok(s.rd(0x23)==1&&s.rd(0x2b)==99&&s.words.size()==29,"snapshot packet completion");s.release();cases++;
 }
 // Legacy may overwrite the shared context; the next complete bundle restores it.
 s.reset();s.vertices();s.context();s.start(1);s.done();auto expected=s.words;s.acquire(999,777);
 float legacy[16]={3,0,0,0,0,2,0,0,0,0,1,1,0,0,0,2};for(unsigned i=0;i<16;i++)s.wr(0x40+i,bits(legacy[i]));
 s.wr(5,bits(.5));s.wr(6,bits(.25));s.wr(7,bits(.5));s.wr(8,bits(.75));s.wr(9,0x112233);s.wr(10,0x778899);s.wr(11,0x12345678);s.wr(12,0);s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.idle();
 s.ok(s.view(0,0)!=bits(-.25f),"legacy context overwrite not exercised");s.release();s.context();s.start(2);s.done();s.ok(s.words==expected,"queued complete context did not restore raw arithmetic");cases++;
 // Raw and every scalar/matrix may change while old immutable packet stalls.
 s.reset();s.vertices();s.context();s.forceStall=true;s.start(1);s.request(99,888);
 s.wait([&]{return s.st()&16;},"snapshot lease overlap");s.ok(s.words.empty()&&s.rd(0x23)==0,"snapshot committed output early");
 for(unsigned i=0;i<16;i++)s.wr(0x40+i,bits(legacy[i]));
 s.wr(5,bits(1));s.wr(6,bits(.25));s.wr(7,bits(.5));s.wr(8,bits(.75));s.wr(9,0x112233);s.wr(10,0x778899);s.wr(11,0x12345678);s.wr(12,0);s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.idle();
 auto newRaw=s.view(0,0);s.ok(newRaw==bits(-.75f),"overlapping legacy LOAD did not overwrite raw");
 s.forceStall=false;s.done();s.ok(s.words==expected,"old packet reread new raw/context");
 s.ok((s.st()&16)&&s.rd(0x2f)==888&&s.view(0,0)==newRaw&&(s.rd(1)&6)==6,"old packet completion corrupted legacy generation");s.release();cases++;
 // Packet completion while legacy DMA is still outstanding must not ACK it.
 s.reset();s.vertices();s.context();s.forceStall=true;s.start(1);s.acquire(99,889);
 s.blockR=true;s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.wait([&]{return s.pending;},"overlap delayed legacy read missing");
 s.forceStall=false;s.done();s.ok((s.rd(1)&1)&&s.pending&&(s.st()&16),"packet completion aborted newer raw DMA");
 s.blockR=false;s.idle();s.ok((s.rd(1)&6)==6&&s.view(0,0)==bits(-.25f),"legacy DMA result after old packet done");s.release();cases++;
 // Consumer reset discards old packet, while preserving granted active legacy DMA.
 s.reset();s.vertices();s.context();s.forceStall=true;s.start(1);s.acquire(99,890);
 s.blockR=true;s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.wait([&]{return s.pending;},"reset overlap DMA missing");
 s.consumerReset();s.forceStall=false;s.blockR=false;s.idle();for(unsigned i=0;i<50;i++)s.tick();
 s.ok(s.words.empty()&&(s.st()&16)&&s.rd(0x2f)==890&&s.rd(0x23)==0&&s.view(0,0)==bits(-.25f),"reset coupled packet/newer raw incorrectly");s.release();cases++;
 // Maximum 32-slot / 64-survivor snapshot, exact payload oracle from
 // immutable raw words, then complete source/context/raw mutation before drain.
 for(unsigned round=0;round<8;round++){
  s.reset();s.mem.clear();s.cmd.clear();std::vector<uint32_t> indices;
  for(unsigned i=0;i<32;i++){s.mem.push_back(bits((int(i%7)-3)*.0625f));s.mem.push_back(bits((int(i%5)-2)*.0625f));s.mem.push_back(bits(float(i%3)*.25f));s.mem.push_back(0x10203040+i);s.mem.push_back(0x50607080+i);s.mem.push_back(0x90a0b0c0+i);}
  auto indexword=[](unsigned a,unsigned b,unsigned c){return (a*2<<16)|(b*2<<8)|(c*2);};
  for(unsigned j=0;j<32;j++){unsigned a=j,b=(j+1)%32,c=(j+2)%32,d=(j+3)%32;s.cmd.push_back(0x06000000|indexword(a,b,c));s.cmd.push_back(indexword(a,c,d));indices.push_back(a|(b<<5)|(c<<10));indices.push_back(a|(c<<5)|(d<<10));}
  uint32_t flags=0xabcd|(round<<16),uoff=(round*7300)&~7u,voff=(round*5900)&~7u;
  s.context();s.forceStall=true;s.start(1,32,32,flags,uoff,voff);s.acquire(99,1100+round);
  auto cvt=[](uint32_t value,bool depth){float f;memcpy(&f,&value,4);double x=double(f)*(depth?1073741824.0:256.0);if(depth){if(x<1)x=1;if(x>2147483520.0)x=2147483520.0;}return uint32_t(int32_t(std::nearbyint(x)));};
  std::vector<uint32_t> want;
  for(unsigned i=0;i<32;i++){auto x=s.view(0,i),y=s.view(1,i),w=s.view(3,i),iw=s.view(4,i),uv=s.view(5,i),rgba=s.view(6,i);
   uint32_t u=0,vv=0,rgb=0xabcd;if(flags&(1u<<16)){int bias=(flags&(1u<<17))?16:0;u=uint32_t((int32_t(int16_t(uv))-int32_t(uoff)+bias)*2048);vv=uint32_t((int32_t(int16_t(uv>>16))-int32_t(voff)+bias)*2048);}if(flags&(1u<<18))rgb=((rgba>>3)&31)<<11|((rgba>>10)&63)<<5|((rgba>>19)&31);
   std::vector<uint32_t> v={0x56000008,i,cvt(x,false),cvt(y,false),cvt(w,false),u,vv,rgb,cvt(iw,true)};want.insert(want.end(),v.begin(),v.end());}
  for(auto tri:indices){want.push_back(0x54000001);want.push_back(tri);}
  s.ok(want.size()==416&&s.words.empty()&&s.rd(0x23)==0,"maximum snapshot shape/early completion");
  for(unsigned i=0;i<16;i++)s.wr(0x40+i,bits(legacy[i]));s.wr(5,bits(.5f));s.wr(9,0xffffff);s.wr(10,0x010203);s.wr(11,round*773);s.wr(12,0);
  for(unsigned i=0;i<32;i++)s.mem[i*6]=bits(.125f+float(round)*.03125f);
  s.wr(3,s.base);s.wr(4,32);s.wr(2,1);s.idle();auto changed=s.view(0,31);
  s.forceStall=false;s.done();s.ok(s.words==want,"maximum snapshot payload changed after raw overwrite");s.ok(s.view(0,31)==changed&&(s.st()&16),"maximum old completion damaged newer raw");s.release();cases++;
 }
 s.vertices();
 // Pending priority waits through whole-task repair and accepted alias reads.
 s.vertices(true);s.context();s.start(3);s.wait([&]{return s.st()&2;},"repair missing");s.request(99,778);for(unsigned i=0;i<20;i++){s.tick();s.ok(!(s.st()&16),"lease interrupted repair");}
 s.wr(0x29,s.rd(0x25));for(unsigned i=0;i<600;i++){s.wait([&]{return s.st()&64;},"repair output credit");s.wr(0x28,0x70000000+i);}
 s.d.view_active=1;s.wr(0x2a,1);for(unsigned i=0;i<10;i++){s.tick();s.ok(!(s.st()&16),"lease interrupted accepted alias");}s.d.view_active=0;s.wait([&]{return s.st()&16;},"repair priority release");s.ok(s.rd(0x23)==3,"repair completion fence");s.release();cases++;
 // Reset cancels every partial context and pending acquire, not a granted lease.
 for(unsigned position=1;position<=28;position++){
  s.reset();s.vertices();s.prefix(0,position);s.request(99,900+position);s.consumerReset();s.ok(!(s.st()&24),"reset retained partial acquisition");
  s.acquire(99,1000+position);s.consumerReset();s.ok((s.st()&16)&&s.rd(0x2f)==1000+position,"reset revoked granted legacy ticket");s.release();s.context();s.start(1);s.done();s.ok(s.words.size()==29,"post-context-reset recovery");cases++;
 }
 // Malformed/repeated/skipped context, incomplete JOB and extra words fault visibly.
 for(unsigned variant=0;variant<6;variant++){
  s.reset();s.vertices();auto reads=s.reads;
  if(variant==0)s.ctx(1,s.cv(1));
  else if(variant==1){s.ctx(0,s.cv(0));s.ctx(0,s.cv(0));}
  else if(variant==2){s.ctx(0,s.cv(0));s.ctx(2,s.cv(2));}
  else if(variant==3){s.prefix(0,27);s.start(1);}
  else if(variant==4){s.context();s.ctx(28,0);}
  else{s.context();s.ctx(0,s.cv(0));}
  s.ok((s.st()&4)&&s.reads==reads,"bad context did not fault before DMA");cases++;
 }
 // Consumer reset preserves an accepted legacy DMA and invalidates repair tokens.
 s.reset();s.vertices();s.acquire(55,404);for(unsigned i=0;i<16;i++)s.wr(0x40+i,s.cv(i));s.wr(3,s.base);s.wr(4,3);s.blockR=true;s.wr(2,1);s.wait([&]{return s.pending;},"legacy DMA absent");s.consumerReset();s.blockR=false;s.idle();s.ok((s.st()&16)&&s.rd(0x2f)==404&&s.view(0,0)==bits(-.25f),"legacy reset lost result");s.release();
 s.vertices(true);s.context();s.start(1);s.wait([&]{return s.st()&2;},"first repair");auto token=s.rd(0x25);s.consumerReset();s.idle();s.context();s.start(2);s.wait([&]{return s.st()&2;},"second repair");s.ok(s.rd(0x25)!=token,"stale token reused");s.wr(0x29,token);s.wr(0x2a,1);s.ok(s.st()&4,"stale END accepted");cases++;
 // GROUP expands physical descriptors; each snapshot can release raw to a
 // poisoned legacy owner before the next member restores its protected context.
 s.reset();s.vertices();s.context();s.forceStall=true;s.group(1,32);
 std::vector<uint32_t> want32;
 for(unsigned member=0;member<32;member++){
  s.acquire(999,2000+member);s.ok(s.rd(0x23)==member&&s.detached(),"group granted before snapshot or advanced completion");
  s.shadowCheck();for(unsigned i=0;i<16;i++)s.wr(0x40+i,bits(legacy[i]));
  s.wr(5,bits(.5f));s.wr(6,bits(.125f));s.wr(7,bits(.5f));s.wr(8,bits(.25f));s.wr(9,0xabcdef);s.wr(10,0x123456);s.wr(11,0x24681357);s.wr(12,0);
  s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.idle();s.ok(s.view(0,0)!=bits(-.25f),"group legacy poison absent");s.shadowCheck();s.release();
  s.forceStall=false;s.wait([&]{return s.rd(0x23)==member+1;},"group member output completion");
  want32.insert(want32.end(),expected.begin(),expected.end());s.ok(s.words==want32,"group member payload/context changed");
  s.ok(bool(s.d.mesh_job_done)==(member==31),"GROUP done before final member");s.forceStall=member!=31;
 }
 s.tick();cases++;
 // An acquire at each restore write position waits through the entire member;
 // a new context cannot interleave and the shadow remains exact.
 for(unsigned pos=0;pos<28;pos++){
  s.reset();s.vertices();s.context();s.wr(0x2b,999);s.wr(0x2c,3000+pos);s.forceStall=true;s.group(1,2);
  s.wait([&]{return s.restoring(pos);},"restore position absent");s.wr(0x2d,1);
  s.wait([&]{if(s.st()&16)s.ok(s.detached(),"priority split context restore");return s.st()&16;},"restore acquire missing");s.shadowCheck();s.release();s.forceStall=false;s.done();
  std::vector<uint32_t> two=expected;two.insert(two.end(),expected.begin(),expected.end());s.ok(s.words==two&&s.rd(0x23)==2,"restore arbitration corrupted GROUP");cases++;
 }
 // Ordered repair remains per member with immutable original pointers/context.
 s.reset();s.vertices(true);s.context();s.group(10,3);
 for(unsigned m=0;m<3;m++){
  s.wait([&]{return s.st()&2;},"group repair absent");s.ok(s.rd(0x22)==10+m,"repair serial");
  for(unsigned i=0;i<28;i++)s.ok(s.data(i)==s.cv(i),"repair common context");
  s.ok(s.data(28)==0x12345000+m*72&&s.data(29)==0x54321000+m*8,"member repair source pointers");
  s.request(999,4000+m);s.wr(0x29,s.rd(0x25));
  for(unsigned n=0;n<37;n++){s.wait([&]{return s.st()&64;},"group repair credit");s.wr(0x28,0x71000000+m*100+n);}
  s.wr(0x2a,1);s.wait([&]{return s.st()&16;},"group repair legacy grant");
  s.ok(s.rd(0x23)==10+m,"group repair completion");s.shadowCheck();s.release();
 }
 s.ok(s.words.size()==111&&s.rd(0x23)==12,"group repair output count");cases++;
 // Cancel while descriptor AR is held or while its accepted R is delayed:
 // drain before another owner, no canceled member/output, then clean restart.
 for(unsigned mode=0;mode<2;mode++){
  s.reset();s.vertices();s.context();s.blockAR=mode==0;s.blockR=mode==1;s.group(1,2);
  s.wait([&]{return mode==0?bool(s.d.m_arvalid):s.pending;},"descriptor read absent");s.consumerReset();
  for(unsigned k=0;k<9;k++)s.tick();s.blockAR=false;s.blockR=false;
  s.acquire(77,5000+mode);s.ok(s.words.empty()&&s.rd(0x23)==0,"canceled descriptor launched output");s.release();s.context();s.group(3,2);s.done();s.ok(s.rd(0x23)==4&&s.words.size()==58,"descriptor reset recovery");cases++;
 }
 // Reset while old output and a granted legacy DMA overlap preserves legacy,
 // cancels all remaining group members and invalidates the final fence.
 s.reset();s.vertices();s.context();s.forceStall=true;s.group(1,3);s.acquire(99,6000);
 s.blockR=true;s.wr(3,s.base);s.wr(4,3);s.wr(2,1);s.wait([&]{return s.pending;},"group overlap DMA absent");s.consumerReset();s.blockR=false;s.forceStall=false;s.idle();
 for(unsigned i=0;i<90;i++)s.tick();s.ok(s.words.empty()&&s.rd(0x23)==0&&(s.st()&16)&&s.view(0,0)==bits(-.25f),"group reset corrupted legacy");s.release();cases++;
 // Invalid table bounds/count/sequence and an invalid second descriptor fault
 // without launching the offending member. Earlier complete output is retained.
 for(unsigned variant=0;variant<5;variant++){
  s.reset();s.vertices();s.context();s.desc={s.base,0x01003006,s.cb,1,0,0};
  if(variant==0)s.groupStart(1,0);else if(variant==1)s.groupStart(1,33);else if(variant==2)s.groupStart(0xffffffff,2);
  else if(variant==3){s.d.mesh_job_seq=1;s.d.mesh_job_vertex=0xfffff8;s.d.mesh_job_vtx_w0=2;s.d.mesh_job_control=0x80000000;s.d.mesh_job_flags=0xabcd;s.d.mesh_job_alpha=255;s.d.mesh_job_valid=1;s.tick();s.d.mesh_job_valid=0;}
  else{s.desc.insert(s.desc.end(),{s.base,0x01003006,s.cb,0x80000001,0,0});s.groupStart(1,2);}
  s.wait([&]{return s.st()&4;},"malformed GROUP did not fault");s.ok(s.words.size()==(variant==4?29:0),"invalid member emitted output");cases++;
 }
 std::cout<<"{\"status\":\"PASS\",\"cases\":"<<cases<<",\"checks\":"<<s.checks<<",\"cycles\":"<<s.cycles<<",\"AXI_bursts\":"<<s.reads<<"}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
