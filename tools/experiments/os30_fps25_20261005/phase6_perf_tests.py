#!/usr/bin/env python3
"""Host fake-clock checks for F8 command-transfer accounting; no application build."""
import hashlib,json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase6'
CAND=BASE/'candidate'

HEADERS={
'of_timer.h':'#include <stdint.h>\nuint32_t of_time_us(void);\n',
'of_caps.h':'#include <stdint.h>\nstruct of_capabilities { uint32_t cpu_freq_hz; };\nconst struct of_capabilities *of_get_caps(void);\n',
'of_input.h':'#define OF_BTN_SELECT 1u\n#define OF_BTN_L1 2u\n#define OF_BTN_R1 4u\n',
'of_video.h':'#include <stdint.h>\ntypedef struct { uint32_t present_count; } of_video_timing_t;\nvoid of_video_get_timing(of_video_timing_t *);\n',
'sm64.h':'''extern int *gDisplayListHead;
#define gDPPipeSync(p) ((void)(p))
#define gDPSetScissor(p,...) ((void)(p))
#define gDPSetCycleType(p,...) ((void)(p))
#define gDPSetFillColor(p,...) ((void)(p))
#define gDPFillRectangle(p,...) ((void)(p))
''',
'game/area.h':'extern int gCurrLevelNum;\n',
'game/game_init.h':'',
'game/print.h':'void print_text(int,int,const char *);\nvoid render_text_labels(void);\n',
'gfx/gfx_gpu.h':'int gfx_gpu_geometry_enabled(void);\n',
}

HARNESS=r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint32_t now,wait_ring,wait_dma,wait_fence,presents,acquire;
static unsigned reads;
static uint32_t submit_ticks,submit_words,submit_batches,submit_cpu_batches;
static char panel[2048];static unsigned panel_lines;
uint32_t of_time_us(void){++reads;return now;}
#include "perf_pocket.c"
unsigned sm64_geometry_vertices,sm64_geometry_triangles;
int gCurrLevelNum=9;
static int dl[256];int *gDisplayListHead=dl;
void of_video_get_timing(of_video_timing_t *t){t->present_count=presents;}
void gpu_wait_prof_read(uint32_t *r,uint32_t *d,uint32_t *f){*r=wait_ring;*d=wait_dma;*f=wait_fence;}
void gpu_submit_prof_read(uint32_t *t,uint32_t *w,uint32_t *b,uint32_t *c){*t=submit_ticks;*w=submit_words;*b=submit_batches;*c=submit_cpu_batches;}
void gpu_ring_prof_get(unsigned *s,unsigned *f){*s=74;*f=8;}
void gpu_present_prof_get(unsigned *f,unsigned *a){*f=0;*a=acquire;acquire=0;}
int gfx_gpu_geometry_enabled(void){return 0;}
void print_text(int x,int y,const char *s){assert(x==12&&y>=10&&y<=210);assert(strlen(panel)+strlen(s)+2<sizeof(panel));strcat(panel,s);strcat(panel,"\n");++panel_lines;}
void render_text_labels(void){}
static void advance(uint32_t n,uint32_t r,uint32_t d,uint32_t f){now+=n;wait_ring+=r;wait_dma+=d;wait_fence+=f;}
static void submit_work(uint32_t t,uint32_t w,uint32_t b,uint32_t c){submit_ticks+=t;submit_words+=w;submit_batches+=b;submit_cpu_batches+=c;}
static void reset_state(void){
    memset(&window,0,sizeof(window));memset(&sm64_perf_snapshot,0,sizeof(sm64_perf_snapshot));
    now=wait_ring=wait_dma=wait_fence=presents=acquire=0;reads=0;
    submit_ticks=submit_words=submit_batches=submit_cpu_batches=0;panel[0]=0;panel_lines=0;gDisplayListHead=dl;
    tick_hz=1000000;step_render=step_frames=0;window_active=visible=chord_held=warmup=0;last_level=9;
    sm64_perf_top_active=top_serial=top_depth=0;top_bucket=SM64_PERF_TOP_BUCKETS;
    memset(top_ticks,0,sizeof(top_ticks));
}
static void vertex_work(void){
    SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_VERTEX);
    advance(12000,1000,0,0);submit_work(4000,41,1,1);
}
static void triangle_inner(int early){
    SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_TRIANGLE);
    advance(10000,2000,1000,500);submit_work(2500,83,1,1);
    if(early)return;
    assert(!"early-return test must return");
}
static void triangle_work(void){
    SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_TRIANGLE);
    advance(10000,2000,1000,500);submit_work(2500,83,1,1);triangle_inner(1);
}
static void frame(void){
    uint32_t begin=sm64_perf_step_begin(),rb=now;
    sm64_perf_render_begin(rb);
    vertex_work();triangle_work();advance(8000,0,0,0);acquire=2000;
    sm64_perf_render_end(rb,now);assert(!sm64_perf_top_active&&top_depth==0);
    advance(10000,0,0,0);uint32_t ge=now;advance(1000,0,0,0);uint32_t ae=now;
    advance(11500,0,0,0);++presents;sm64_perf_step_end(begin,ge,ae,now);
}
static void test_windows_and_abi(void){
    _Static_assert(offsetof(struct PerfSnapshot,host_ms10)==39*4,"legacy39-word snapshot moved");
    _Static_assert(offsetof(struct PerfSnapshot,submit_ms10)==48*4,"F7 48-word snapshot moved");
    _Static_assert(sizeof(struct PerfSnapshot)==55*4,"unexpected snapshot padding");
    reset_state();for(unsigned i=0;i<32;i++)frame();
    const struct PerfSnapshot *s=&sm64_perf_snapshot;
    assert(s->valid&&s->version==8&&s->frames==32&&s->steps==32&&s->elapsed_ticks==2000000);
    assert(s->render_fps10==160&&s->display_fps10==160&&s->sim_fps10==160);
    assert(s->render_ms10==380&&s->wait_ms10==20&&s->host_ms10==300);
    assert(s->ring_wait_ms10==50&&s->dma_wait_ms10==20&&s->fence_wait_ms10==10);
    assert(s->game_ms10==100&&s->audio_ms10==10&&s->gap_ms10==0);
    assert(s->submit_ms10==90&&s->host_rest_ms10==210&&s->submit_ms10+s->host_rest_ms10==s->host_ms10);
    assert(s->submit_words==207&&s->submit_batches==3&&s->submit_cpu_batches==3&&s->submit_mode==1&&!s->submit_invalid_frames);
    assert(s->samples==0&&s->top_samples==2&&s->top_period==16);
    assert(s->top_vertex_ms10==110&&s->top_triangle_ms10==130);
    assert(s->top_render_ms10==380&&s->top_vertex_ms10+s->top_triangle_ms10<=s->top_render_ms10);
    for(unsigned i=0;i<SM64_PERF_LEGACY_BUCKETS;i++)assert(!s->detail_ms10[i]);
    for(unsigned i=0;i<32;i++)frame();
    assert(s->top_samples==2&&top_serial==64);
    puts("PASS two windows; same-denominator COPY plus REST equals HOST; sparse scopes include copy;55-word append-only ABI");
}
static void test_wrap_and_scope_nesting(void){
    reset_state();window_active=1;now=UINT32_MAX-20;wait_ring=UINT32_MAX-2;wait_dma=UINT32_MAX-3;wait_fence=UINT32_MAX-4;
    submit_ticks=UINT32_MAX-9;submit_words=UINT32_MAX-1;submit_batches=UINT32_MAX;submit_cpu_batches=UINT32_MAX-2;
    uint32_t rb=now;sm64_perf_render_begin(rb);submit_work(31,100,7,4);
    {
        SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_TRIANGLE);
        advance(10,3,0,0);
        {SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_VERTEX);advance(20,2,4,3);}
        advance(30,1,1,2);
    }
    sm64_perf_render_end(rb,now);
    assert(window.render==60&&window.host==44&&window.ring_wait==6&&window.dma_wait==5&&window.fence_wait==5);
    assert(window.top_samples==1&&window.top_ticks[0]==11&&window.top_ticks[1]==33);
    assert(top_depth==0&&reads==4);
    assert(window.submit_ticks==31&&window.submit_words==100&&window.submit_batches==7&&window.submit_cpu_batches==4&&!window.submit_invalid_frames);
    puts("PASS independent32-bit clock/wait/submit counter wraps and exclusive cross-bucket nesting");
}
static void test_inactive_and_bad_sample(void){
    reset_state();vertex_work();triangle_work();assert(!reads&&!top_depth);
    window_active=1;sm64_perf_render_begin(now);
    for(unsigned i=0;i<9;i++)sm64_perf_top_enter(SM64_PERF_TOP_VERTEX);
    assert(!sm64_perf_top_active);sm64_perf_render_end(now,now);assert(!window.top_samples);
    top_serial=0;sm64_perf_render_begin(now);sm64_perf_top_enter(SM64_PERF_TOP_VERTEX);
    sm64_perf_render_end(now,now);assert(!window.top_samples); // unclosed scope rejected
    top_serial=0;sm64_perf_render_begin(now);assert(top_depth==0);vertex_work();sm64_perf_render_end(now-12000,now);assert(window.top_samples==1);
    puts("PASS inactive no timer reads; overflow/unclosed samples rejected; next frame recovers");
}
static void test_freeze_level_and_pause(void){
    reset_state();for(unsigned i=0;i<32;i++)frame();struct PerfSnapshot saved=sm64_perf_snapshot;
    assert(sm64_perf_input(7)==0&&visible);frame();assert(!memcmp(&saved,&sm64_perf_snapshot,sizeof saved));
    sm64_perf_draw();assert(!memcmp(&saved,&sm64_perf_snapshot,sizeof saved));
    assert(panel_lines==12&&strstr(panel,"PERF F8 SEND")&&strstr(panel,"COPY 9.0 REST 21.0")&&strstr(panel,"WORDS 207 PER FRAME")&&strstr(panel,"CPU BATCH 3 PER FRAME")&&strstr(panel,"SMP 2 MS INCL COPY"));
    assert(strstr(panel,"RING 5.0 DMA 2.0")&&strstr(panel,"FENC 1.0 ACQ 2.0")&&strstr(panel,"GAME 10.0 AUD 1.0"));
    assert(sm64_perf_input(0)==0);assert(sm64_perf_input(7)==0&&!visible);assert(sm64_perf_input(0)==0);
    frame();frame();assert(!warmup);gCurrLevelNum=10;frame();assert(!sm64_perf_snapshot.valid&&warmup==1);gCurrLevelNum=9;
    reset_state();uint32_t b=sm64_perf_step_begin();sm64_perf_render_begin(now);advance(100,0,0,0);sm64_perf_render_end(b,now);
    advance(5000000,0,0,0);sm64_perf_step_end(b,now,now,now);assert(!sm64_perf_snapshot.valid&&warmup==2);
    puts("PASS frozen overlay, reset/level warmup and long-pause discard");
}
static void test_submit_scope_and_modes(void){
    reset_state();submit_work(1234567,90000,5000,3000); // loading/history excluded
    for(unsigned i=0;i<32;i++)frame();
    assert(sm64_perf_snapshot.submit_ms10==90&&sm64_perf_snapshot.submit_words==207&&sm64_perf_snapshot.submit_mode==1);
    assert(sm64_perf_input(7)==0);for(unsigned i=0;i<5;i++)frame(); // overlay excluded
    assert(sm64_perf_input(0)==0);assert(sm64_perf_input(7)==0);assert(sm64_perf_input(0)==0);
    frame();frame(); // closing overlay warmup excluded
    for(unsigned i=0;i<32;i++){submit_work(50000,10000,1000,1000);frame();} // between-render uploads excluded
    assert(sm64_perf_snapshot.submit_ms10==90&&sm64_perf_snapshot.submit_words==207&&sm64_perf_snapshot.submit_batches==3);
    for(unsigned mode=0;mode<4;mode++){
        reset_state();
        for(unsigned i=0;i<32;i++){
            uint32_t b=sm64_perf_step_begin();sm64_perf_render_begin(now);advance(1000,0,0,0);
            if(mode)submit_work(123,121,2,mode==1?2:mode==2?0:1);
            sm64_perf_render_end(b,now);advance(61500,0,0,0);++presents;sm64_perf_step_end(b,now,now,now);
        }
        assert(sm64_perf_snapshot.submit_mode==mode&&!sm64_perf_snapshot.submit_invalid_frames);
        assert(sm64_perf_snapshot.submit_ms10+sm64_perf_snapshot.host_rest_ms10==sm64_perf_snapshot.host_ms10);
    }
    puts("PASS loading/between-render/overlay/warmup counters excluded; CPU/DMA/MIX/NONE modes");
}
static void test_submit_denominator_and_rounding(void){
    reset_state();
    for(unsigned i=0;i<16;i++){
        uint32_t b=sm64_perf_step_begin();
        for(unsigned j=0;j<2;j++){
            uint32_t rb=now;sm64_perf_render_begin(rb);advance(1000,0,0,0);
            const unsigned sparse=(i*2+j)%16==0;
            submit_work(sparse?100:900,sparse?11:13,sparse?1:3,sparse?0:2);
            sm64_perf_render_end(rb,now);++presents;
        }
        uint32_t ge=now;advance(123000,0,0,0);sm64_perf_step_end(b,ge,ge,now);
    }
    const struct PerfSnapshot *s=&sm64_perf_snapshot;
    assert(s->valid&&s->frames==32&&s->steps==16&&s->top_samples==2);
    assert(s->host_ms10==10&&s->submit_ms10==8&&s->host_rest_ms10==2);
    assert(s->submit_words==12&&s->submit_batches==2&&s->submit_cpu_batches==1&&s->submit_mode==3);
    assert(s->submit_ms10+s->host_rest_ms10==s->host_ms10);
    // Sparse averages would be1 instead of8; simulation-step averages17.
    assert(s->submit_ms10!=1&&s->submit_ms10!=17);
    reset_state();window.frames=3;window.host=UINT32_MAX*3ull+123;window.submit_ticks=UINT32_MAX*2ull+19;
    window.submit_words=UINT32_MAX*3ull;window.submit_batches=UINT32_MAX*2ull;
    assert(average_ms10(window.host)>average_ms10(window.submit_ticks));
    assert(window.submit_words/window.frames==UINT32_MAX); //64-bit window accumulation
    puts("PASS render-count denominator with multiple renders/step, sparse mismatch, decimal reconciliation and64-bit totals");
}
static void test_invalid_submit_interval(void){
    reset_state();uint32_t b=sm64_perf_step_begin();sm64_perf_render_begin(b);advance(100,0,0,0);
    submit_work(101,100,2,3);sm64_perf_render_end(b,now);
    assert(window.submit_invalid_frames==1&&window.submit_ticks==100&&window.host==100);
    advance(2000000,0,0,0);sm64_perf_step_end(b,now,now,now);
    assert(sm64_perf_snapshot.valid&&sm64_perf_snapshot.submit_invalid_frames==1);
    assert(sm64_perf_snapshot.submit_ms10==sm64_perf_snapshot.host_ms10&&!sm64_perf_snapshot.host_rest_ms10);
    assert(sm64_perf_input(7)==0);sm64_perf_draw();assert(strstr(panel,"BAD SEND SAMPLE"));
    puts("PASS impossible submit intervals visibly invalid; defensive COPY clamp cannot silently mislabel REST");
}
static void test_saturation_and_phase(void){
    reset_state();window_active=1;sm64_perf_render_begin(now);uint32_t rb=now;
    advance(10,8,9,7);acquire=2;sm64_perf_render_end(rb,now);assert(!window.host);
    // Window reset must not reset16-frame phase.
    window_active=0;top_serial=15;uint32_t b=sm64_perf_step_begin();sm64_perf_render_begin(b);assert(!sm64_perf_top_active);
    sm64_perf_render_end(b,b);sm64_perf_render_begin(b);assert(sm64_perf_top_active);
    puts("PASS defensive subtraction clamp and sample phase across windows");
}
int main(void){test_windows_and_abi();test_wrap_and_scope_nesting();test_inactive_and_bad_sample();test_freeze_level_and_pause();test_saturation_and_phase();test_submit_scope_and_modes();test_submit_denominator_and_rounding();test_invalid_submit_interval();return 0;}
'''

def main():
    with tempfile.TemporaryDirectory(prefix='f8-perf-',dir='/tmp') as name:
        d=Path(name)
        for n,s in HEADERS.items():p=d/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
        (d/'harness.c').write_text(HARNESS)
        cmd=['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-variable','-DSM64_PERF_DIAG=1','-DOF_PC=1','-I'+str(d),'-I'+str(CAND),str(d/'harness.c'),'-o',str(d/'tests')]
        p=subprocess.run(cmd,text=True,capture_output=True);assert p.returncode==0,p.stderr
        p=subprocess.run([str(d/'tests')],text=True,capture_output=True);assert p.returncode==0,p.stdout+p.stderr
        print(p.stdout,end='')
        (d/'disabled.c').write_text('#include "perf_pocket.h"\nunsigned demo(unsigned x){SM64_PERF_TOP_SCOPE(SM64_PERF_TOP_VERTEX);return x+1;}\n')
        subprocess.run(['cc','-std=gnu11','-O2','-DSM64_PERF_DIAG=0','-I'+str(CAND),'-c',str(d/'disabled.c'),'-o',str(d/'disabled.o')],check=True)
        undefined=subprocess.check_output(['nm','-u',str(d/'disabled.o')],text=True);assert not undefined.strip(),undefined
        pc=(CAND/'gfx_pc.c').read_text()
        assert sum('SM64_PERF_TOP_SCOPE(' in line and not line.lstrip().startswith('#') for line in pc.splitlines())==4
        results=dict(passed=True,scope=__doc__,tests=p.stdout.splitlines(),normal_macro_undefined_symbols=[],
            sampled_entrypoints=['gfx_sp_vertex','gfx_sp_tri1','gfx_draw_rectangle','gfx_cpu_indexed_run'],
            snapshot_words=55,snapshot_append={48:"submit_ms10",49:"host_rest_ms10",50:"submit_words",51:"submit_batches",52:"submit_cpu_batches",53:"submit_mode",54:"submit_invalid_frames"},source_sha256={n:hashlib.sha256((CAND/n).read_bytes()).hexdigest() for n in ('perf_pocket.h','perf_pocket.c','gfx_pc.c')},
            script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),limits='Host accounting checks only; target overhead, GPU exactness and UI remain root build/capture checks.')
        out=BASE/'perf-tests.json'
        if out.exists():
            previous=json.loads(out.read_text())
            assert previous['source_sha256']==results['source_sha256'], 'Preserve old evidence before testing changed sources'
            assert previous['tests']==results['tests'], 'Previously saved test outcomes differ'
        out.write_text(json.dumps(results,indent=2)+'\n')
        print('PASS DIAG0 macro has no profiler references; exactly4 top-level scope sites')

if __name__=='__main__':main()
