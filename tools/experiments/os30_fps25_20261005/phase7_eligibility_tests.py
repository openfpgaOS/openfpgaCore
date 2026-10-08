#!/usr/bin/env python3
"""Differential execution of the actual F8 and candidate CPU indexed loops."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase6/candidate/gfx_pc.c'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase7/eligibility-v1/tests'
CAND = OUT.parent / 'gfx_pc.c'


def function(source, name):
    match = re.search(r'^static[^;\n]*\b' + name + r'\(', source, re.M)
    assert match, name
    brace = source.index('{', match.end())
    pos, depth = brace + 1, 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos]


PREFIX = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <assert.h>
#define TARGET_OPENFPGA 1
#define TARGET_POCKET 1
#define F3DEX_GBI 1
#define GFX_GPU_GEOMETRY_WORKER_APP 0
#define GFX_MANUAL_CLIPPING 1
#define SM64_PERF_SCOPE(x)
#define SM64_PERF_TOP_SCOPE(x)
#define RSPV_TO_FLOAT(x) (x)
#define RSPV_MUL(a,b) ((a)*(b))
#define G_CULL_FRONT 1
#define G_CULL_BACK 2
#define G_CULL_BOTH 3
#define G_MDSFT_TEXTFILT 0
#define G_TF_POINT 0
#define G_TRI1 0xbf
#define G_TRI2 0xb1
#define GFX_VC_TEXTURED 2
#define VC_W_PRESCALE (1.0f / 256.0f)
#define CC_PRIM 1
#define CC_SHADE 2
#define CC_ENV 3
#define CC_LOD 4
typedef float rspv_t;
typedef struct {struct {uint32_t w0,w1;} words;} Gfx;
struct RGBA {uint8_t r,g,b,a;};
struct LoadedVertex {float x,y,z,w,w_inv,u,v; struct RGBA color; uint8_t clip_rej;};
struct ColorCombiner {uint8_t shader_input_mapping[2][4];};
struct gfx_vc_vtx {float cx,cy,cw,w_inv,u,v;int use_exact_depth; uint32_t exact_depth; uint8_t r,g,b,a;};
static struct {struct LoadedVertex loaded_vertices[48];unsigned geometry_mode;} rsp;
static struct {struct RGBA prim_color,env_color;struct {unsigned uls,ult;} texture_tile;unsigned other_mode_h;} rdp;
static struct {unsigned total,clip_reject,cull_reject,clipped,direct;} tri_diag;
static struct ColorCombiner comb0;
static int triangle_vc_elig,triangle_vc_rgb_input,triangle_vc_alpha_input;
static bool triangle_material_ready;
static int triangle_cpu_indexed_elig;
static uint32_t vc_slot_stale;
static unsigned buf_vbo_len;
static unsigned run_calls,prepare_calls,prepare_cold,batch_calls,push_calls,flush_calls,texture_calls;
static unsigned reject_evaluations;
static unsigned cfg_elig,cfg_rgb,cfg_alpha,cfg_fog;
static unsigned cfg_pattern,cfg_dirty;
static bool mutate_fallback;
static uint64_t hash=1469598103934665603ull;
static void hash32(uint32_t v) {hash^=v;hash*=1099511628211ull;}
static void hash_float(float f) {uint32_t v;memcpy(&v,&f,4);hash32(v);}
static void gfx_flush(void) {flush_calls++;buf_vbo_len=0;hash32(0xf1000000);}
static bool gfx_vc_texture_state(const struct ColorCombiner *comb,int tex,float bias) {
    assert(comb==&comb0);texture_calls++;(void)tex;(void)bias;return false;
}
static void gfx_gpu_vtx_cache_batch(const struct gfx_vc_vtx *payload,const uint32_t *jobs,unsigned n,uint8_t alpha) {
    batch_calls++;hash32(0xb0000000|n);hash32(alpha);
    for(unsigned t=0;t<n;t++) {
        uint32_t job=jobs[t];hash32(job);
        for(unsigned j=0;j<3;j++)if(job&(1u<<(15+j))) {
            const struct gfx_vc_vtx *v=&payload[(job>>(5*j))&31];
            hash_float(v->cx);hash_float(v->cy);hash_float(v->cw);hash_float(v->w_inv);
            hash_float(v->u);hash_float(v->v);hash32(v->use_exact_depth);
            hash32(v->r|(v->g<<8)|(v->b<<16)|((uint32_t)v->a<<24));
        }
    }
}
static void record_push(const struct LoadedVertex *a,const struct LoadedVertex *b,const struct LoadedVertex *c) {
    hash32(0xd0000000);hash32(a-rsp.loaded_vertices);hash32(b-rsp.loaded_vertices);hash32(c-rsp.loaded_vertices);push_calls++;
    /* Mock deferred-attribute side effects make preparation-before-fallback
     * observable when an accepted prefix shares a slot with the fallback. */
    if(mutate_fallback) {
        const struct LoadedVertex *vertices[]={a,b,c};
        for(unsigned i=0;i<3;i++) {
            struct LoadedVertex *v=&rsp.loaded_vertices[vertices[i]-rsp.loaded_vertices];
            v->u+=17.0f;v->v-=9.0f;v->color.r+=3;
        }
    }
}
static bool gfx_clip_triangle(struct LoadedVertex *a,struct LoadedVertex *b,struct LoadedVertex *c,uint8_t mask) {
    assert(!mask);if(!(a->clip_rej|b->clip_rej|c->clip_rej))return false;
    hash32(0xc0000000);record_push(a,b,c);return true;
}
'''

PREPARE = r'''
static struct ColorCombiner *gfx_prepare_triangle_material(bool *fog,bool *alpha,bool *texture) {
    prepare_calls++;
    if(!triangle_material_ready) {
        prepare_cold++;triangle_vc_elig=cfg_elig;
        triangle_vc_rgb_input=cfg_rgb?0:-1;triangle_vc_alpha_input=cfg_alpha?0:-1;
        comb0.shader_input_mapping[0][0]=cfg_rgb;
        comb0.shader_input_mapping[1][0]=cfg_alpha;
#if CANDIDATE
        CAND_INIT
#endif
        triangle_material_ready=true;
    }
    *fog=cfg_fog;*alpha=cfg_alpha!=0;*texture=cfg_elig&GFX_VC_TEXTURED;
    return &comb0;
}
static int gfx_push_triangle(const struct LoadedVertex *a,const struct LoadedVertex *b,const struct LoadedVertex *c,bool geo) {
    bool f,al,t;assert(!geo);gfx_prepare_triangle_material(&f,&al,&t);record_push(a,b,c);return 1;
}
'''

TAIL = r'''
static Gfx commands[100];
static void reset(unsigned scenario,unsigned warm,unsigned geom) {
    memset(&rsp,0,sizeof rsp);memset(&rdp,0,sizeof rdp);memset(&tri_diag,0,sizeof tri_diag);
    memset(&comb0,0,sizeof comb0);triangle_material_ready=false;
    run_calls=prepare_calls=prepare_cold=batch_calls=push_calls=flush_calls=texture_calls=0;
    reject_evaluations=0;
    mutate_fallback=scenario>=12;
    triangle_vc_elig=triangle_vc_rgb_input=triangle_vc_alpha_input=0;
    vc_slot_stale=0xffffffff;buf_vbo_len=scenario&1;rsp.geometry_mode=geom;
    static const uint32_t dirty_masks[]={0xffffffff,0x55555555,0x80000000,0};
    vc_slot_stale=dirty_masks[cfg_dirty];
    rdp.prim_color=(struct RGBA){231,192,17,123};rdp.env_color=(struct RGBA){5,41,9,84};
    rdp.texture_tile.uls=5;rdp.texture_tile.ult=7;rdp.other_mode_h=scenario&3;
    for(unsigned i=0;i<48;i++) {
        struct LoadedVertex *v=&rsp.loaded_vertices[i];
        v->x=(i%3==1)?10.0f:0;v->y=(i%3==2)?10.0f:0;
        v->w=5.0f+i;v->w_inv=1.0f/v->w;v->u=97.0f*i;v->v=37.0f*i;
        v->color=(struct RGBA){i*7,i*11,i*3,i*4};
    }
    unsigned condition=scenario%12;
    switch(condition) {
      case 1:for(unsigned i=0;i<6;i++)rsp.loaded_vertices[i].clip_rej=1;break;
      case 2:rsp.loaded_vertices[3].clip_rej=1;break;
      case 3:rsp.loaded_vertices[0].clip_rej=1;break;
      case 4:for(unsigned i=0;i<3;i++)rsp.loaded_vertices[i].w=1;break;
      case 5:rsp.loaded_vertices[4].w=0;break;
      case 6:rsp.loaded_vertices[4].w=-1;break;
      case 7:rsp.loaded_vertices[4].w=32767;break;
      case 8:rsp.loaded_vertices[4].w=NAN;break;
      case 9:for(unsigned i=0;i<3;i++)rsp.loaded_vertices[i].clip_rej=2;break;
      case 10:rsp.loaded_vertices[3].clip_rej=2;break;
      case 11:vc_slot_stale=0;break;
    }
    for(unsigned i=0;i<100;i++) {
        commands[i].words.w0=((uint32_t)G_TRI2<<24)|(0<<16)|(2<<8)|4;
        commands[i].words.w1=(6<<16)|(8<<8)|10;
        if(cfg_pattern==1 || (cfg_pattern==2 && i==0)) {
            commands[i].words.w0=(uint32_t)G_TRI1<<24;
            commands[i].words.w1=(0<<16)|(2<<8)|4;
        }
        if(cfg_pattern==3) {
            unsigned a=(i*3)%32,b=(i*3+1)%32,c=(i*3+2)%32;
            commands[i].words.w0=(uint32_t)G_TRI1<<24;
            commands[i].words.w1=((a*2)<<16)|((b*2)<<8)|(c*2);
        }
        if(scenario>=12 && i==0) {
            /* Clean accepted prefix precedes the troublesome original
             * TRI2 halves, while sharing slot2 with the upcoming command. */
            commands[i].words.w0=(uint32_t)G_TRI1<<24;
            commands[i].words.w1=(12<<16)|(14<<8)|4;
        }
    }
    if(condition==10)commands[scenario>=12?1:0].words.w1=(64<<16)|(8<<8)|10;
    if(warm){bool f,a,t;gfx_prepare_triangle_material(&f,&a,&t);prepare_calls=prepare_cold=0;}
}
int main(void) {
    uint64_t run_total=0,prepare_total=0,cold_total=0,reject_total=0,cases=0;
    for(cfg_elig=0;cfg_elig<=3;cfg_elig++)
    for(cfg_rgb=0;cfg_rgb<=5;cfg_rgb++)for(cfg_alpha=0;cfg_alpha<=5;cfg_alpha++)
    for(cfg_fog=0;cfg_fog<2;cfg_fog++)for(unsigned warm=0;warm<2;warm++)
    for(cfg_pattern=0;cfg_pattern<4;cfg_pattern++)for(cfg_dirty=0;cfg_dirty<4;cfg_dirty++)
    for(unsigned geom=0;geom<4;geom++)for(unsigned scenario=0;scenario<24;scenario++)
    for(unsigned length=0;length<3;length++) {
        reset(scenario,warm,geom);unsigned end=length==0?1:length==1?3:34;
        commands[end].words.w0=0;commands[end].words.w1=0;
        unsigned expected=0;
        for(unsigned i=0;i<end;i++){unsigned s[6];expected+=gfx_geometry_decode(&commands[i],s);}
        Gfx *cmd=commands;
        while(cmd<commands+end) {
            if(DISPATCH_CONDITION) {
                run_calls++;Gfx *next=gfx_cpu_indexed_run(cmd);
                assert(next>=cmd && next<=commands+end);
                if(next!=cmd){cmd=next;continue;}
            }
            unsigned s[6], n=gfx_geometry_decode(cmd,s);assert(n==1 || n==2);
            for(unsigned t=0;t<n;t++)gfx_sp_tri1(s[3*t],s[3*t+1],s[3*t+2]);cmd++;
        }
        assert(tri_diag.total==expected);
        if(scenario==1 && cfg_pattern!=3)assert(!prepare_cold);
        hash32(tri_diag.total);hash32(tri_diag.clip_reject);hash32(tri_diag.cull_reject);
        hash32(tri_diag.clipped);hash32(tri_diag.direct);hash32(batch_calls);hash32(push_calls);
        hash32(vc_slot_stale);hash32(prepare_cold);hash32(flush_calls);hash32(texture_calls);
        run_total+=run_calls;prepare_total+=prepare_calls;cold_total+=prepare_cold;
        reject_total+=reject_evaluations;cases++;
    }
    printf("{\"cases\":%llu,\"semantic_hash\":\"%016llx\",\"run_calls\":%llu,\"prepare_calls\":%llu,\"cold_preparations\":%llu,\"rejection_evaluations\":%llu}\n",
        (unsigned long long)cases,(unsigned long long)hash,(unsigned long long)run_total,
        (unsigned long long)prepare_total,(unsigned long long)cold_total,(unsigned long long)reject_total);
}
'''


def main():
    global OUT, CAND, BASE
    if len(sys.argv) > 1:
        assert re.fullmatch(r'(eligibility|rejection|combined|prepared)-v\d+', sys.argv[1])
        OUT = OUT.parent.with_name(sys.argv[1]) / 'tests'
        CAND = OUT.parent / 'gfx_pc.c'
    normalize = OUT.parent.name.startswith(('combined-', 'prepared-'))
    OUT.mkdir(exist_ok=True)
    results = {}
    for label, path in [('baseline', BASE), ('candidate', CAND)]:
        source = path.read_text()
        candidate = label == 'candidate' and 'gfx_cpu_indexed_material_eligible' in source
        test = PREFIX + ('\n#define CANDIDATE 1\n' if candidate else '\n#define CANDIDATE 0\n')
        fixed = 'struct gfx_vc_prepared payload[32]' in source
        if fixed:
            test = test.replace('struct gfx_vc_vtx', 'struct gfx_vc_prepared')
            test = test.replace('''float cx,cy,cw,w_inv,u,v;int use_exact_depth; uint32_t exact_depth; uint8_t r,g,b,a;''',
                                '''int32_t x,y,w,u,v;uint32_t rgb,depth;''')
            test = test.replace('''            hash_float(v->cx);hash_float(v->cy);hash_float(v->cw);hash_float(v->w_inv);
            hash_float(v->u);hash_float(v->v);hash32(v->use_exact_depth);
            hash32(v->r|(v->g<<8)|(v->b<<16)|((uint32_t)v->a<<24));''',
                                '''            hash32(v->x);hash32(v->y);hash32(v->w);
            hash32(v->u);hash32(v->v);hash32(v->rgb);hash32(v->depth);''')
        elif normalize:
            # Instantiate the baseline backend's actual conversion code to
            # compare the old float payload with its exact encoded words.
            gpu = (BASE.parent / 'gfx_gpu.c').read_text()
            backend = gpu[gpu.index('void gfx_gpu_vtx_cache_batch('):]
            start = backend.index('        float df = vtx->w_inv')
            end = backend.index('#if defined(__riscv)', start)
            conversion = backend[start:end].replace('#ifndef OF_PC\n        {\n', '')
            old = '''            hash_float(v->cx);hash_float(v->cy);hash_float(v->cw);hash_float(v->w_inv);
            hash_float(v->u);hash_float(v->v);hash32(v->use_exact_depth);
            hash32(v->r|(v->g<<8)|(v->b<<16)|((uint32_t)v->a<<24));'''
            new = '''            const struct gfx_vc_vtx *vtx=v;
            const uint16_t rgb=triangle_vc_rgb_input<0?0xffff:rgba_to_565(v->r,v->g,v->b);
''' + conversion + '''
            hash32(qx);hash32(qy);hash32(qw);hash32(qu);hash32(qv);hash32(rgb);hash32(qd);'''
            assert old in test
            test = test.replace(old, new)
            marker = 'static void gfx_gpu_vtx_cache_batch('
            test = test.replace(marker, function(gpu, 'rgba_to_565') + '\n' + marker)
        if candidate:
            test += function(source, 'gfx_cpu_indexed_material_eligible') + '\n'
        prepare = PREPARE
        if candidate:
            initialization = re.search(r'triangle_cpu_indexed_elig = [^;]+;',
                                       function(source, 'gfx_prepare_triangle_material')).group()
            initialization = initialization.replace('comb, use_fog', '&comb0, cfg_fog')
            prepare = prepare.replace('CAND_INIT', initialization)
        test += prepare
        names = ['gfx_vc_resolve', 'gfx_sp_tri1', 'gfx_geometry_decode', 'gfx_cpu_batch_reject']
        if fixed:
            names += ['gfx_cpu_prepare_clip']
        if 'gfx_cpu_push_checked' in source:
            names += ['gfx_cpu_push_checked']
        for name in names + ['gfx_cpu_indexed_run']:
            definition = function(source, name)
            if name in ['gfx_sp_tri1', 'gfx_cpu_batch_reject']:
                definition = definition.replace('{', '{\n    reject_evaluations++;', 1)
            test += definition + '\n'
        conditions = re.findall(r'if \((!triangle_material_ready \|\| triangle_\w+)\) \{', source)
        assert len(conditions) == 2 and conditions[0] == conditions[1]
        test += TAIL.replace('DISPATCH_CONDITION', conditions[0])
        cfile = OUT / (label + '.c')
        cfile.write_text(test)
        binary = OUT / label
        command = ['cc', '-std=c11', '-O2', '-ffp-contract=fast', '-fno-pie', '-no-pie',
                   '-fsanitize=address,undefined', str(cfile), '-lm', '-o', str(binary)]
        subprocess.run(command, check=True)
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
        result = subprocess.run([str(binary)], check=True, text=True, capture_output=True, env=env)
        (OUT / (label + '.stdout')).write_text(result.stdout)
        (OUT / (label + '.stderr')).write_text(result.stderr)
        results[label] = json.loads(result.stdout)
    for key in ['cases', 'semantic_hash', 'cold_preparations']:
        assert results['baseline'][key] == results['candidate'][key], (key, results)
    if not OUT.parent.name.startswith('prepared-'):
        assert results['candidate']['rejection_evaluations'] < results['baseline']['rejection_evaluations']
    results['passed'] = True
    results['scope'] = ('Actual extracted clip/cull, TRI2 decode, generic fallback and indexed-run functions; '
        'mock renderer boundary records ordered batch jobs, dirty payloads, fallback triangles and counters. '
        'Does not establish full GPU pixel equivalence or device performance.')
    results['sources'] = {label: hashlib.sha256(path.read_bytes()).hexdigest()
                          for label, path in [('baseline', BASE), ('candidate', CAND)]}
    if OUT.parent.name.startswith('combined-'):
        # Deliberately consume the fallback before preparing/emitting its
        # accepted prefix. The ordered payload/side-effect oracle must fail.
        text = (OUT / 'candidate.c').read_text()
        old = function(text, 'gfx_cpu_indexed_run')
        block = '''    if (fallback) {
        for (unsigned t = 0; t < n; ++t) {
            const unsigned *s = &slot[3*t];
            gfx_cpu_push_checked(s[0], s[1], s[2], reject[t]);
        }
        ++cmd;
    }
'''
        assert old.count(block) == 1
        changed = old.replace(block, '').replace('submit:\n', 'submit:\n' + block)
        negative = text.replace(old, changed)
        (OUT / 'negative-order.c').write_text(negative)
        subprocess.run(['cc', '-std=c11', '-O2', '-ffp-contract=fast',
                        str(OUT / 'negative-order.c'), '-lm', '-o', str(OUT / 'negative-order')], check=True)
        result = subprocess.run([str(OUT / 'negative-order')], text=True, capture_output=True)
        (OUT / 'negative-order.stdout').write_text(result.stdout)
        (OUT / 'negative-order.stderr').write_text(result.stderr)
        detected = result.returncode != 0 or json.loads(result.stdout)['semantic_hash'] != results['baseline']['semantic_hash']
        assert detected, 'Oracle failed to detect fallback-before-prefix mutation'
        results['negative_fallback_before_prefix_detected'] = True
    results['recipe_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (OUT / 'RESULT.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
