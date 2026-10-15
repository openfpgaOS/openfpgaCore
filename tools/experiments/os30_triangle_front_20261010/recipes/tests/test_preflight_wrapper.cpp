#include "Vgeometry_load_alias.h"
#include <verilated.h>
#include <cstdint>
#include <iostream>
#include <stdexcept>
static unsigned checks=0;
static void require(bool b,const char*m){++checks;if(!b)throw std::runtime_error(m);}
static void settle(Vgeometry_load_alias &v){v.clk=0;v.eval();}
static void tick(Vgeometry_load_alias &v){settle(v);v.clk=1;v.eval();settle(v);}
static uint32_t read(Vgeometry_load_alias &v,unsigned r){v.reg_addr=r;settle(v);return v.reg_rdata;}
static void write(Vgeometry_load_alias &v,unsigned r,uint32_t value){v.reg_addr=r;v.reg_wdata=value;v.reg_wstrb=15;v.reg_wr=1;tick(v);v.reg_wr=0;settle(v);}
static void launch(Vgeometry_load_alias &v,uint32_t address,unsigned count){write(v,3,address);write(v,4,count);write(v,2,1);}
static void accept(Vgeometry_load_alias &v,uint32_t address){
 for(unsigned i=0;i<20&&!v.m_arvalid;++i)tick(v);
 require(v.m_arvalid&&v.m_araddr==address,"raw DMA issues expected source after legal START");
 v.m_arready=1;tick(v);v.m_arready=0;require(v.m_rready,"accepted raw DMA owns response");
}
static void complete(Vgeometry_load_alias &v,unsigned error=0){
 v.m_rdata=0x12345678;v.m_rresp=error;v.m_rlast=1;v.m_rvalid=1;tick(v);
 v.m_rvalid=0;v.m_rlast=0;v.m_rresp=0;tick(v);tick(v);require(!(read(v,1)&1),"raw producer completes");
}
static void copy_done(Vgeometry_load_alias &v){
 unsigned n=0;while((read(v,1)&512)&&n++<300)tick(v);require(!(read(v,1)&512),"copier frees producer without GPU RELEASE");
}
static void release(Vgeometry_load_alias &v,uint32_t token){v.import_release=1;v.import_release_token=token;tick(v);v.import_release=0;settle(v);}
static void done(Vgeometry_load_alias &v){v.import_req=0;v.import_read_done=1;tick(v);v.import_read_done=0;settle(v);}
static void setup_front(Vgeometry_load_alias&v,unsigned count=4,unsigned mode=0){
 write(v,0x14,2);write(v,0x12,read(v,0xe));write(v,0x13,count|(mode<<8));
 for(unsigned i=0;i<count;i++)write(v,0x200+i,0|(1<<5)|(2<<10));
}
static void wait_front(Vgeometry_load_alias&v){unsigned n=0;while((read(v,0x14)&1)&&n++<10000)tick(v);require(!(read(v,0x14)&1)&&(read(v,0x14)&2),"preflight drains");}
int main(int argc,char**argv)try{
 Verilated::commandArgs(argc,argv);Vgeometry_load_alias v;v.reset=1;tick(v);v.reset=0;settle(v);
 require(read(v,0x11)==0x54465031,"TFP1 capability");
 for(unsigned i=0;i<16;i++)write(v,0x40+i,0x3f800000);
 launch(v,0x1200,4);accept(v,0x1200);complete(v);require(read(v,0xe)==1,"firstgeneration");
 // Snapshot copier and existing CPU burst drain before TFP owns the raw port.
 v.view_active=1;v.view_read_addr=13;write(v,0xf,1);require(read(v,1)&512,"copyreservation");
 setup_front(v);write(v,0x14,1);
 for(unsigned i=0;i<12;i++){tick(v);require((read(v,0x14)&1)&&v.view_block&&!v.m_arvalid,"preflight waits alias/copier");require(v.view_read_data==0x120d,"acceptedalias retains port");}
 v.view_active=0;unsigned wait=0;while(read(v,1)&512){tick(v);require((read(v,0x14)&1)&&++wait<300,"preflight waits actual snapshot copy");}
 // GPU reads its immutable bank while preflight reserves the unrelated raw bank.
 v.import_req=1;v.import_token=1;v.import_src=1;v.import_addr=1;settle(v);require(v.import_grant&&!v.import_error,"GPU snapshot grants duringpreflight");tick(v);v.import_req=0;tick(v);tick(v);require(v.import_data==0x1201,"GPU copiedbank independent");
 while(read(v,0x14)&1){tick(v);require(v.import_grant&&v.import_data==0x1201,"preflight doesnot corruptGPUread");}
 require(read(v,0x14)==6&&read(v,0x17)==15,"stub subnormalW yields expectedorderedfallback");done(v);release(v,1);
 // Mid-preflight START/CLAIM must reject without launching or mutating producer.
 setup_front(v,64,1);write(v,0x14,1);write(v,2,1);write(v,0xf,1);require(!(read(v,0x10)&1),"CLAIM deniedduringpreflight");
 write(v,3,0xab00);for(unsigned i=0;i<11;i++){tick(v);require(!v.m_arvalid&&read(v,0xe)==1,"rawSTART cannot stealpreflight");}
 wait_front(v);require((read(v,0x14)&8)&&!(read(v,1)&1),"conflict reportedwithout rawjob");
 // Consumer reset neither cancels the shared FMA nor clears its final result.
 setup_front(v,64,1);write(v,0x14,1);for(unsigned i=0;i<15;i++)tick(v);v.import_reset=1;for(unsigned i=0;i<13;i++)tick(v);v.import_reset=0;wait_front(v);
 uint32_t status=read(v,0x14),lo=read(v,0x17),hi=read(v,0x18);require(status==6&&lo==0xffffffff&&hi==0xffffffff,"consumerreset preservespreflight");v.import_reset=1;for(unsigned i=0;i<9;i++)tick(v);v.import_reset=0;require(read(v,0x14)==status&&read(v,0x17)==lo&&read(v,0x18)==hi,"consumerreset preservesstable result");
 // The busy-phase source write was ignored, so the original address remains.
 write(v,2,1);accept(v,0x1200);v.import_reset=1;for(unsigned i=0;i<7;i++){tick(v);require(v.m_rready&&!v.m_arvalid,"consumerreset preservesaccepted rawDMA");}v.import_reset=0;
 setup_front(v,1);write(v,0x14,1);require(read(v,0x14)==14&&v.m_rready,"preflight rejects activeproducer withoutabandoningDMA");complete(v);require(read(v,0xe)==2,"rawgeneration survivedconsumerreset");
 // CLEAR / failed raw source cannot authorize stale preflight.
 write(v,2,2);setup_front(v,1);write(v,0x14,1);require(read(v,0x14)==14,"CLEAR rejects stale preflight");
 launch(v,0xcd00,3);accept(v,0xcd00);complete(v,2);setup_front(v,1);write(v,0x14,1);require(read(v,0x14)==14&&read(v,0xe)==2,"failedLOAD rejects stale preflight");
 launch(v,0x2200,3);accept(v,0x2200);complete(v);setup_front(v,64,1);write(v,0x14,1);v.reset=1;tick(v);v.reset=0;for(unsigned i=0;i<12;i++)tick(v);require(read(v,0x14)==0&&!v.view_block&&read(v,0xe)==0,"fabricreset clearsallowners");
 std::cout<<"{\"pass\":true,\"checks\":"<<checks<<",\"scope\":\"Actual phase10 + BIM2 + TFP + sharedFMA; transaction producer stubbed\"}\n";return 0;
}catch(const std::exception&e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}
