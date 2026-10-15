/* Low-frequency hardware measurements. No text is drawn or printed while
 * collecting. Select+L+R displays the last complete two-second window; that
 * snapshot stays frozen so the panel's own rendering cannot bias it. */
#include "perf_pocket.h"
#if SM64_PERF_DIAG
#include <stdio.h>
#include <string.h>
#include "of_caps.h"
#include "of_input.h"
#include "of_video.h"
#include "sm64.h"
#include "game/area.h"
#include "game/game_init.h"
#include "game/print.h"
#include "gfx/gfx_gpu.h"

extern unsigned sm64_geometry_vertices,sm64_geometry_triangles;
void gpu_ring_prof_get(unsigned *spins,unsigned *min_free);
void gpu_present_prof_get(unsigned *flip,unsigned *acquire);
void gpu_wait_prof_read(uint32_t *ring,uint32_t *dma,uint32_t *fence);
void gpu_submit_prof_read(uint32_t *ticks,uint32_t *words,uint32_t *batches,uint32_t *cpu_batches);
void gpu_upload_prof_read(uint32_t *ticks,uint32_t *words,uint32_t *calls,uint32_t *burst);

struct PerfWindow {
    uint64_t game,render,audio,wait,tail,ring;
    uint64_t detail[SM64_PERF_BUCKETS];
    uint64_t vertex_counts[SM64_PERF_VERTEX_COUNTS];
    uint32_t samples;
    uint32_t begin,frames,steps,presents,vertices,triangles;
    uint64_t ring_wait,dma_wait,fence_wait,host;
    uint64_t top_ticks[SM64_PERF_TOP_BUCKETS];
    uint64_t top_render;
    uint32_t top_samples;
    uint64_t submit_ticks,submit_words,submit_batches,submit_cpu_batches;
    uint32_t submit_invalid_frames;
    uint64_t upload_ticks,upload_words,upload_calls;
};
/* Exported for the capture harness; values are real hardware wall-clock
 * measurements on device, never the qsim estimated-cycle report. */
struct PerfSnapshot {
    uint32_t valid,level,clock_hz,frames,steps,elapsed_ticks;
    uint32_t render_fps10,display_fps10,sim_fps10;
    uint32_t game_ms10,render_ms10,audio_ms10,wait_ms10,gap_ms10;
    uint32_t ring_spins,geometry,vertices,triangles;
    /* Append only: the original P1 fields keep their capture ABI. */
    uint32_t version,samples,sample_render_ms10;
    uint32_t detail_ms10[SM64_PERF_LEGACY_BUCKETS];
    /* P3 appends MTX, PREP, ATTR, CPU, REST and RAW, CPU, OUT, REC.
     * The existing VTX total includes all five parts, rounded only once. */
    uint32_t vertex_ms10[SM64_PERF_VERTEX_PARTS];
    uint32_t vertex_counts[SM64_PERF_VERTEX_COUNTS];
    /* F7 append-only. REND above retains its F6 meaning: render bracket
     * minus acquire. HOST additionally excludes the three measured waits.
     * LOAD/TRI average sampled frames only, unlike all-frame REND/HOST. */
    uint32_t host_ms10,ring_wait_ms10,dma_wait_ms10,fence_wait_ms10;
    uint32_t top_samples,top_vertex_ms10,top_triangle_ms10,top_period;
    uint32_t top_render_ms10;
    /* F8 append-only, same all-rendered-frame denominator as HOST. COPY is
     * command publication wall time, excluding separately measured waits.
     * REST includes packing, arithmetic, cache/memory stalls and all other
     * host work. LOAD/TRI above retain their sparse F7 scope and INCLUDE COPY.
     * Per-frame counts truncate only after the full window is accumulated. */
    uint32_t submit_ms10,host_rest_ms10,submit_words,submit_batches;
    uint32_t submit_cpu_batches,submit_mode,submit_invalid_frames;
    /* F12: actual fast-memory upload calls inside rendered frames. Upload is
     * a subset of HOST/REST, not additional time. Same denominator as HOST. */
    uint32_t upload_ms10,upload_words,upload_calls10,upload_burst,psram_mib;
};
_Static_assert(sizeof(struct PerfSnapshot)<=64*sizeof(uint32_t),"capture snapshot exceeds64 words");
struct PerfSnapshot sm64_perf_snapshot;
static struct PerfWindow window;
static uint32_t tick_hz,step_render,step_frames;
static int window_active,visible,chord_held,warmup=2,last_level=-1;

unsigned sm64_perf_detail_active;
static unsigned detail_serial;
struct Sm64PerfDetail sm64_perf_detail;
static uint32_t render_wait_begin[3];
static uint32_t render_submit_begin[4];
static uint32_t render_upload_begin[4];

unsigned sm64_perf_top_active;
static unsigned top_serial,top_bucket=SM64_PERF_TOP_BUCKETS,top_depth;
static unsigned top_parents[8];
static uint32_t top_stamp,top_wait_stamp[3];
static uint64_t top_ticks[SM64_PERF_TOP_BUCKETS];

static void wait_read(uint32_t waits[3]) {
    gpu_wait_prof_read(&waits[0],&waits[1],&waits[2]);
}

static void submit_read(uint32_t submit[4]) {
    gpu_submit_prof_read(&submit[0],&submit[1],&submit[2],&submit[3]);
}

static uint64_t wait_delta(const uint32_t end[3],const uint32_t begin[3]) {
    /* Each cumulative counter wraps independently. Frame/scope intervals
     * must be shorter than one32-bit wrap; uint64sum avoids sum overflow. */
    return (uint64_t)(uint32_t)(end[0]-begin[0])
         +(uint32_t)(end[1]-begin[1])+(uint32_t)(end[2]-begin[2]);
}

static uint64_t without_wait(uint64_t elapsed,uint64_t waits) {
    return elapsed>waits?elapsed-waits:0;
}

static void top_transition(unsigned bucket) {
    uint32_t now=sm64_perf_clock(),waits[3];
    wait_read(waits);
    if(top_bucket<SM64_PERF_TOP_BUCKETS)
        top_ticks[top_bucket]+=without_wait((uint32_t)(now-top_stamp),wait_delta(waits,top_wait_stamp));
    top_stamp=now;memcpy(top_wait_stamp,waits,sizeof(waits));top_bucket=bucket;
}

void sm64_perf_top_enter(unsigned bucket) {
    if(top_depth==sizeof(top_parents)/sizeof(top_parents[0]) || bucket>=SM64_PERF_TOP_BUCKETS) {
        /* Discard a malformed sample rather than publish partial timings. */
        sm64_perf_top_active=0;return;
    }
    top_parents[top_depth++]=top_bucket;
    if(top_bucket!=bucket)top_transition(bucket);
}

void sm64_perf_top_leave(void) {
    if(!top_depth) {sm64_perf_top_active=0;return;}
    unsigned previous=top_parents[--top_depth];
    if(previous!=top_bucket)top_transition(previous);
}

void sm64_perf_render_begin(uint32_t begin) {
    wait_read(render_wait_begin);
    submit_read(render_submit_begin);
    gpu_upload_prof_read(&render_upload_begin[0],&render_upload_begin[1],
                         &render_upload_begin[2],&render_upload_begin[3]);
    sm64_perf_top_active=0;
#if SM64_PERF_DETAILS
    sm64_perf_detail_active=0;
    if(!window_active || visible)return;
    /* Keep phase across windows so sampling is not reset by a 2s boundary. */
    if((detail_serial++ & 7u)!=0)return;
    memset(sm64_perf_detail.ticks,0,sizeof(sm64_perf_detail.ticks));
    memset(sm64_perf_detail.vertices,0,sizeof(sm64_perf_detail.vertices));
    sm64_perf_detail.depth=0;sm64_perf_detail.bucket=SM64_PERF_MISC;sm64_perf_detail.stamp=begin;
    sm64_perf_detail_active=1;
#else
    (void)begin;
    sm64_perf_detail_active=0;
    if(!window_active || visible)return;
    /* Keep phase across two-second windows; sample every16 rendered frames. */
    if((top_serial++&15u)!=0)return;
    memset(top_ticks,0,sizeof(top_ticks));
    top_depth=0;top_bucket=SM64_PERF_TOP_BUCKETS;
    sm64_perf_top_active=1;
#endif
}

static uint32_t perf_hz(void) {
#ifdef OF_PC
    return 1000000u;
#else
    const struct of_capabilities *caps=of_get_caps();
    return caps && caps->cpu_freq_hz ? caps->cpu_freq_hz : 100000000u;
#endif
}

uint32_t sm64_perf_step_begin(void) {
    uint32_t now=sm64_perf_clock();
    if(!tick_hz)tick_hz=perf_hz();
    step_render=step_frames=0;
    if(last_level!=gCurrLevelNum) {
        last_level=gCurrLevelNum;window_active=0;warmup=2;
        sm64_perf_snapshot.valid=0;
    }
    if(!visible && !warmup && !window_active) {
        of_video_timing_t timing;
        of_video_get_timing(&timing);
        memset(&window,0,sizeof(window));window.begin=now;
        window.presents=timing.present_count;
        window.vertices=sm64_geometry_vertices;
        window.triangles=sm64_geometry_triangles;
        window_active=1;
    }
    return now;
}

void sm64_perf_render_end(uint32_t begin,uint32_t end) {
    uint32_t waits[3];wait_read(waits);
    uint32_t submit[4];submit_read(submit);
    uint32_t upload[4];
    gpu_upload_prof_read(&upload[0],&upload[1],&upload[2],&upload[3]);
    const uint32_t ring_wait=waits[0]-render_wait_begin[0];
    const uint32_t dma_wait=waits[1]-render_wait_begin[1];
    const uint32_t fence_wait=waits[2]-render_wait_begin[2];
    const unsigned top_sample_valid=sm64_perf_top_active && !top_depth && window_active && !visible;
    if(sm64_perf_top_active) {
        sm64_perf_top_active=0;
        if(!top_depth && window_active && !visible) {
            for(unsigned i=0;i<SM64_PERF_TOP_BUCKETS;i++)window.top_ticks[i]+=top_ticks[i];
            window.top_samples++;
        }
    }
#if SM64_PERF_DETAILS
    if(sm64_perf_detail_active) {
        sm64_perf_detail.ticks[sm64_perf_detail.bucket]+=end-sm64_perf_detail.stamp;
        sm64_perf_detail_active=0;
        if(window_active && !visible) {
            for(unsigned i=0;i<SM64_PERF_BUCKETS;i++)window.detail[i]+=sm64_perf_detail.ticks[i];
            for(unsigned i=0;i<SM64_PERF_VERTEX_COUNTS;i++)
                window.vertex_counts[i]+=sm64_perf_detail.vertices[i];
            window.samples++;
        }
    }
#endif
    unsigned flip,acquire,spins,free_words;
    gpu_present_prof_get(&flip,&acquire);
    gpu_ring_prof_get(&spins,&free_words);
    uint32_t duration=end-begin;
    step_render+=duration;step_frames++;
    if(!window_active || visible)return;
    window.render+=duration;
    window.wait+=acquire;
    window.ring+=spins;
    window.ring_wait+=ring_wait;window.dma_wait+=dma_wait;window.fence_wait+=fence_wait;
    const uint64_t host=without_wait(duration,(uint64_t)acquire+ring_wait+dma_wait+fence_wait);
    window.host+=host;
    /* Unsigned subtraction handles each independently wrapping cumulative
     * counter. Only deltas inside this render bracket are charged; uploads
     * during loading/overlay/warmup cannot leak into the next window. */
    const uint32_t copy_ticks=submit[0]-render_submit_begin[0];
    const uint32_t words=submit[1]-render_submit_begin[1];
    const uint32_t batches=submit[2]-render_submit_begin[2];
    const uint32_t cpu_batches=submit[3]-render_submit_begin[3];
    if(copy_ticks>host || cpu_batches>batches)window.submit_invalid_frames++;
    /* Defensive saturation is explicitly marked in the snapshot/panel.
     * Valid intervals partition HOST exactly in ticks before rounding. */
    window.submit_ticks+=copy_ticks>host?host:copy_ticks;
    window.submit_words+=words;window.submit_batches+=batches;
    window.submit_cpu_batches+=cpu_batches;
    window.upload_ticks+=(uint32_t)(upload[0]-render_upload_begin[0]);
    window.upload_words+=(uint32_t)(upload[1]-render_upload_begin[1]);
    window.upload_calls+=(uint32_t)(upload[2]-render_upload_begin[2]);
    if(top_sample_valid)window.top_render+=without_wait(duration,acquire);
    window.frames++;
    /* Flip submission belongs to render. Ring polls are reported as a count:
     * their variable MMIO latency cannot honestly be converted to time here. */
    (void)flip;(void)free_words;
}

static uint32_t average_ms10(uint64_t ticks) {
    return (uint32_t)((ticks*10000u)/(tick_hz*(uint64_t)window.frames));
}

void sm64_perf_step_end(uint32_t begin,uint32_t game_end,uint32_t audio_end,uint32_t end) {
    if(visible) {window_active=0;return;}
    if(warmup) {if(step_frames)warmup--;return;}
    if(!window_active)return;
    uint32_t total_game=game_end-begin;
    window.game+=total_game>=step_render?total_game-step_render:0;
    window.audio+=audio_end-game_end;
    window.tail+=end-audio_end;
    window.steps++;
    uint32_t elapsed=end-window.begin;
    if(elapsed<tick_hz*2u || !window.frames)return;
    window_active=0;
    /* A long system-menu pause is not a slow game frame. Discard that window
     * and allow both pending presentation and pacing state to settle. */
    if(elapsed>tick_hz*4u) {warmup=2;return;}
    of_video_timing_t timing;
    of_video_get_timing(&timing);
    struct PerfSnapshot *s=&sm64_perf_snapshot;
    s->valid=1;s->level=gCurrLevelNum;s->clock_hz=tick_hz;
    s->frames=window.frames;s->steps=window.steps;s->elapsed_ticks=elapsed;
    s->render_fps10=(uint64_t)window.frames*tick_hz*10u/elapsed;
    s->display_fps10=(uint64_t)(timing.present_count-window.presents)*tick_hz*10u/elapsed;
    s->sim_fps10=(uint64_t)window.steps*tick_hz*10u/elapsed;
    s->game_ms10=average_ms10(window.game);
    s->audio_ms10=average_ms10(window.audio);
    s->wait_ms10=average_ms10(window.wait);
    s->render_ms10=average_ms10(window.render>window.wait?window.render-window.wait:0);
    uint64_t accounted=window.game+window.render+window.audio+window.tail;
    s->gap_ms10=average_ms10(elapsed>accounted?elapsed-accounted:0);
    s->ring_spins=window.ring/window.frames;
    s->geometry=gfx_gpu_geometry_enabled();
    s->vertices=(sm64_geometry_vertices-window.vertices)/window.frames;
    s->triangles=(sm64_geometry_triangles-window.triangles)/window.frames;
    s->host_ms10=average_ms10(window.host);
    s->submit_ms10=average_ms10(window.submit_ticks);
    /* Reconcile decimal truncation so displayed COPY + REST equals HOST.
     * This changes REST by at most0.1ms from rounding its ticks directly. */
    s->host_rest_ms10=s->host_ms10-s->submit_ms10;
    s->submit_words=window.submit_words/window.frames;
    s->submit_batches=window.submit_batches/window.frames;
    s->submit_cpu_batches=window.submit_cpu_batches/window.frames;
    s->submit_mode=!window.submit_batches?0:!window.submit_cpu_batches?2:
        window.submit_cpu_batches==window.submit_batches?1:3;
    s->submit_invalid_frames=window.submit_invalid_frames;
    s->upload_ms10=average_ms10(window.upload_ticks);
    s->upload_words=window.upload_words/window.frames;
    s->upload_calls10=(window.upload_calls*10u)/window.frames;
    uint32_t upload_ticks,upload_words,upload_calls;
    gpu_upload_prof_read(&upload_ticks,&upload_words,&upload_calls,&s->upload_burst);
#ifndef OF_PC
    /* Experiment-only readback: stage build reports0, paired build32MiB. */
    s->psram_mib=*(volatile uint32_t *)0x400000d8u ? 32u : 16u;
#else
    s->psram_mib=0;
#endif
    s->ring_wait_ms10=average_ms10(window.ring_wait);
    s->dma_wait_ms10=average_ms10(window.dma_wait);
    s->fence_wait_ms10=average_ms10(window.fence_wait);
    s->top_samples=window.top_samples;s->top_period=16;
    s->top_vertex_ms10=window.top_samples
        ?(window.top_ticks[SM64_PERF_TOP_VERTEX]*10000u)/(tick_hz*(uint64_t)window.top_samples):0;
    s->top_triangle_ms10=window.top_samples
        ?(window.top_ticks[SM64_PERF_TOP_TRIANGLE]*10000u)/(tick_hz*(uint64_t)window.top_samples):0;
    s->top_render_ms10=window.top_samples
        ?(window.top_render*10000u)/(tick_hz*(uint64_t)window.top_samples):0;
#if SM64_PERF_DETAILS
    s->version=3;s->samples=window.samples;
    uint64_t sample_render=0;
    for(unsigned i=0;i<SM64_PERF_BUCKETS;i++)
        if(i!=SM64_PERF_WAIT)sample_render+=window.detail[i];
    for(unsigned i=0;i<SM64_PERF_LEGACY_BUCKETS;i++)
        s->detail_ms10[i]=window.samples
            ?(window.detail[i]*10000u)/(tick_hz*(uint64_t)window.samples):0;
    static const unsigned parts[SM64_PERF_VERTEX_PARTS]={
        SM64_PERF_VTX_MATRIX,SM64_PERF_VTX_PREP,SM64_PERF_VTX_ATTR,
        SM64_PERF_VTX_CPU,SM64_PERF_VTX};
    uint64_t vertex=0;
    for(unsigned i=0;i<SM64_PERF_VERTEX_PARTS;i++) {
        uint64_t ticks=window.detail[parts[i]];vertex+=ticks;
        s->vertex_ms10[i]=window.samples
            ?(ticks*10000u)/(tick_hz*(uint64_t)window.samples):0;
    }
    s->detail_ms10[SM64_PERF_VTX]=window.samples
        ?(vertex*10000u)/(tick_hz*(uint64_t)window.samples):0;
    for(unsigned i=0;i<SM64_PERF_VERTEX_COUNTS;i++)
        s->vertex_counts[i]=window.samples?window.vertex_counts[i]/window.samples:0;
    s->sample_render_ms10=window.samples
        ?(sample_render*10000u)/(tick_hz*(uint64_t)window.samples):0;
#else
    /* First39words remain compatible. Old fine-grained buckets stay zero;
     * F7 sparse samples and F8 all-frame submission timings are appended. */
    s->version=12;s->samples=0;s->sample_render_ms10=0;
    memset(s->detail_ms10,0,sizeof(s->detail_ms10));
    memset(s->vertex_ms10,0,sizeof(s->vertex_ms10));
    memset(s->vertex_counts,0,sizeof(s->vertex_counts));
#endif
}

uint32_t sm64_perf_input(uint32_t buttons) {
    const uint32_t chord=OF_BTN_SELECT|OF_BTN_L1|OF_BTN_R1;
    if((buttons&chord)==chord && !chord_held) {
        visible=!visible;chord_held=1;window_active=0;warmup=2;
    }
    /* Swallow the whole chord until all three buttons are released, avoiding
     * first-person/camera events when fingers release at different times. */
    if(chord_held) {
        if(!(buttons&chord))chord_held=0;
        buttons&=~chord;
    }
    return buttons;
}

static void pair_line(int y,const char *a,uint32_t av,const char *b,uint32_t bv) {
    char text[40];
    snprintf(text,sizeof(text),"%s %u.%u %s %u.%u",a,av/10,av%10,b,bv/10,bv%10);
    print_text(12,y,text);
}

void sm64_perf_draw(void) {
    if(!visible)return;
    const struct PerfSnapshot *s=&sm64_perf_snapshot;
    gDPPipeSync(gDisplayListHead++);
    gDPSetScissor(gDisplayListHead++,G_SC_NON_INTERLACE,0,0,320,240);
    gDPSetCycleType(gDisplayListHead++,G_CYC_FILL);
    gDPSetFillColor(gDisplayListHead++,0x00010001);
    gDPFillRectangle(gDisplayListHead++,4,8,315,239);
    gDPPipeSync(gDisplayListHead++);
    gDPSetCycleType(gDisplayListHead++,G_CYC_1CYCLE);
#if SM64_PERF_DETAILS
    print_text(12,210,"PERF P3 VERT");
#else
    print_text(12,210,"PERF F28 GROUP");
#endif
    if(!s->valid) {
        print_text(12,178,"WAIT 5 SECONDS IN LEVEL");
        print_text(12,146,"THEN SELECT L R");
    } else {
        char text[40];
        pair_line(190,"FPS",s->render_fps10,"LCD",s->display_fps10);
#if SM64_PERF_DETAILS
        pair_line(170,"REND",s->render_ms10,"VERT",s->detail_ms10[SM64_PERF_VTX]);
        pair_line(150,"MATR",s->vertex_ms10[0],"PREP",s->vertex_ms10[1]);
        pair_line(130,"ATTR",s->vertex_ms10[2],"CPU",s->vertex_ms10[3]);
        pair_line(110,"REST",s->vertex_ms10[4],"TRI",s->detail_ms10[SM64_PERF_TRI]);
        snprintf(text,sizeof(text),"RAW %u CPU %u",s->vertex_counts[SM64_PERF_RAW],
            s->vertex_counts[SM64_PERF_CPU]);
        print_text(12,90,text);
        snprintf(text,sizeof(text),"OUT %u REC %u",s->vertex_counts[SM64_PERF_OUT],
            s->vertex_counts[SM64_PERF_RECOVER]);
        print_text(12,70,text);
        snprintf(text,sizeof(text),"SMP %u SUM %u.%u",s->samples,
            s->sample_render_ms10/10,s->sample_render_ms10%10);
        print_text(12,50,text);
#else
        pair_line(172,"REND",s->render_ms10,"HOST",s->host_ms10);
        pair_line(154,"COPY",s->submit_ms10,"REST",s->host_rest_ms10);
        pair_line(136,"UP",s->upload_ms10,"CALLS",s->upload_calls10);
        snprintf(text,sizeof(text),"UP WORDS %u PER FRAME",s->upload_words);
        print_text(12,118,text);
        /* US HUD has no V glyph: LOAD means the entire gfx_sp_vertex call,
         * including transform/lighting/attributes, not only memory loads. */
        pair_line(100,"LOAD",s->top_vertex_ms10,"TRI",s->top_triangle_ms10);
        if(s->submit_invalid_frames)snprintf(text,sizeof(text),"BAD SEND SAMPLE");
        else snprintf(text,sizeof(text),"PS%u %s SMP %u",s->psram_mib,
            s->upload_burst?"BURST":"WORD",s->top_samples);
        print_text(12,82,text);
        pair_line(64,"RING",s->ring_wait_ms10,"DMA",s->dma_wait_ms10);
        pair_line(46,"FENC",s->fence_wait_ms10,"ACQ",s->wait_ms10);
        pair_line(28,"GAME",s->game_ms10,"AUD",s->audio_ms10);
#endif
#if SM64_PERF_DETAILS
        snprintf(text,sizeof(text),"RING %u GEO %u",s->ring_spins,s->geometry);
        print_text(12,30,text);
#endif
    }
    print_text(12,10,"SELECT L R TO CLOSE");
    render_text_labels();
}
#endif
