#!/usr/bin/env python3
"""Actual-function equivalence and invalidation dependency checks."""
import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase7'
PARENT = OUT.parent / 'phase6/candidate'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def function(src, name):
    m = re.search(r'^(?:static[^\n]* |void )[^\n]*\b' + name + r'\(', src, re.M)
    assert m, name
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: ' ' * len(m[0]), src, flags=re.S)
    pos = masked.index('{', m.start()) + 1
    depth = 1
    while depth:
        depth += (masked[pos] == '{') - (masked[pos] == '}')
        pos += 1
    return src[m.start():pos]

def compile_run(directory, text):
    source = directory / 'focused-proof.c'
    source.write_text(text)
    (directory / 'focused-test-driver.py').write_bytes(Path(__file__).read_bytes())
    results = {}
    for mode, flags in [('checked', ['-O2', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']),
                        ('optimized', ['-O3', '-ffast-math', '-ffp-contract=fast'])]:
        exe = directory / ('focused-proof-' + mode)
        cmd = ['gcc', '-std=c11', *flags, str(source), '-lm', '-o', str(exe)]
        subprocess.run(cmd, check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True,
                                env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        (directory / ('focused-' + mode + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr
        results[mode] = dict(command=cmd, stdout=result.stdout.strip(), binary_sha256=sha(exe))
    return dict(passed=True, harness_sha256=sha(source), script_sha256=sha(Path(__file__)), runs=results)

def resident():
    import sys
    directory = OUT / (sys.argv[2] if len(sys.argv) > 2 else 'resident-v1')
    # Reuse only the mock transport declarations from the prior actual-packet test.
    tree = ast.parse(Path(__file__).with_name('check_vc_packet_v3.py').read_text())
    prefix = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'prefix' for t in n.targets))
    sdk = (directory / 'of_gpu.h').read_text()
    gpu = (directory / 'gfx_gpu.c').read_text()
    baseline = (PARENT / 'gfx_gpu.c').read_text()
    struct = re.search(r'struct gfx_vc_vtx \{.*?\n\};', (directory / 'gfx_gpu.h').read_text(), re.S)[0]
    obj = re.search(r'typedef struct \{\n    int32_t  matrix\[5\]\[4\].*?\n\} of_gpu_object_state_t;', sdk, re.S)[0]
    names = ['_gpu_stream_reserve_words', '_gpu_ring_write', '_gpu_ring_claim', '_gpu_ring_commit',
             '_gpu_cmd_header', 'of_gpu_set_object_state', 'of_gpu_load_vert_clip',
             'of_gpu_draw_indexed_tri', 'of_gpu_kick']
    helpers = '\n'.join(function(sdk, n) for n in names)
    baseline = function(baseline, 'gfx_gpu_vtx_cache_batch').replace('gfx_gpu_vtx_cache_batch(', 'reference(')
    candidate = function(gpu, 'gfx_gpu_vtx_cache_batch').replace('gfx_gpu_vtx_cache_batch(', 'candidate(')
    suffix = r'''
struct result {uint32_t words[8192],count,wrptr,counters[4];
 unsigned reserves,publications,material;int cd,subpix,vpvalid,matrix;uint8_t alpha;float vp[4];};
static uint32_t rng=0x841b71a9;
static uint32_t next(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static void setup(unsigned initial,unsigned variant) {
 caps.hw_features=OF_HW_GPU_VERT_TRI|OF_HW_GPU_CLIP_LOAD;
 _gpu_cmd_words=initial;_gpu_wrptr=(variant*1176u+initial*4u)&16383u;
 if(override_wrptr>=0)_gpu_wrptr=(uint32_t)override_wrptr;
 _gpu_known_rdptr=(_gpu_wrptr+4u+(override_free>=0?(unsigned)override_free:16380u))&16383u;
 _gpu_unflushed_sync=0;transcript_words=reserves=publications=0;
 for(unsigned i=0;i<initial;i++)staging[i]=0xCA000000u+i;
 g_cd_active=1;g_subpix_tri=0;g_vc_textured=variant&1;g_surf_alpha=0;
 g_cx=160;g_cy=120;g_hw=160;g_hh=-120;
 g_vc_vp[0]=g_cx;g_vc_vp[1]=g_cy;g_vc_vp[2]=g_hw;g_vc_vp[3]=g_hh;
 g_vc_vp_valid=(variant>>1)&1;g_geometry_matrix_valid=1;g_vc_rgb_input=(variant&8)?0:-1;
 g_vc_words=19;g_vc_loads=2;g_vc_tris_cached=3;g_vc_kick=variant%32;material_calls=0;
}
static void collect(struct result *r) {
 _gpu_flush_cmd_stream();memset(r,0,sizeof(*r));
 memcpy(r->words,transcript,transcript_words*4);r->count=transcript_words;r->wrptr=_gpu_wrptr;
 r->counters[0]=g_vc_words;r->counters[1]=g_vc_loads;r->counters[2]=g_vc_tris_cached;r->counters[3]=g_vc_kick;
 r->reserves=reserves;r->publications=publications;r->material=material_calls;
 r->cd=g_cd_active;r->subpix=g_subpix_tri;r->vpvalid=g_vc_vp_valid;r->matrix=g_geometry_matrix_valid;
 r->alpha=g_surf_alpha;memcpy(r->vp,g_vc_vp,sizeof(r->vp));
}
static unsigned long cases;
static void check(unsigned initial,unsigned variant,struct gfx_vc_vtx *v,uint32_t *jobs,unsigned count) {
 struct result a,b;
 setup(initial,variant);reference(v,jobs,count,137);collect(&a);
 setup(initial,variant);candidate(v,jobs,count,137);collect(&b);
 if(memcmp(&a,&b,sizeof(a))) {fprintf(stderr,"mismatch init=%u var=%u count=%u\n",initial,variant,count);abort();}
 cases++;
}
int main(void) {
 struct gfx_vc_vtx v[32]={0};uint32_t jobs[64];
 for(unsigned k=0;k<32;k++) {
  v[k].cx=k*13.25f;v[k].cy=-(float)k*0.33333f;v[k].cw=2+k*99.5f;
  v[k].u=k*2.875f;v[k].v=-v[k].u;v[k].w_inv=1.0f/v[k].cw;
  v[k].r=17+k*7;v[k].g=254-k*3;v[k].b=134+k*2;
  v[k].use_exact_depth=k&1;v[k].exact_depth=0x80000001u+k;
 }
 for(unsigned initial=0;initial<=4095;initial++)for(unsigned mask=0;mask<8;mask++) {
  jobs[0]=31u|(17u<<5)|(31u<<10)|(mask<<15);jobs[1]=jobs[0]&0x7fffu;
  check(initial,initial+mask,v,jobs,2);
 }
 unsigned counts[]={0,1,2,31,32,33,63,64};
 for(unsigned n=0;n<8;n++)for(unsigned k=0;k<32;k++)for(unsigned tail=0;tail<40;tail++) {
  for(unsigned i=0;i<64;i++)jobs[i]=(i&31u)|((i&31u)<<5)|(((i+1)&31u)<<10)|((i%5==0?7u:0u)<<15);
  override_wrptr=(16380u-tail*4u)&16383u;override_free=(tail%8)*4u;
  check(4095u-tail,k,v,jobs,counts[n]);
 }
 override_wrptr=override_free=-1;
 for(unsigned trial=0;trial<10000;trial++) {
  for(unsigned i=0;i<64;i++)jobs[i]=(next()&0x7fffu)|((next()%4==0?(next()&7u):0u)<<15);
  check(next()%4096,next(),v,jobs,next()%65);
 }
 /* Clean jobs must not dereference vertex records at all. */
 for(unsigned i=0;i<64;i++)jobs[i]=i&0x7fffu;
 check(4095,31,NULL,jobs,64);
 printf("PASS %lu cases; exact packets, reservations, publication count, pointers and counters; all staging positions, all dirty masks, duplicates, 0..64 jobs, wrap and all kick phases\n",cases);
}
'''
    result = compile_run(directory, prefix + struct + obj + helpers + baseline + candidate + suffix)
    result.update(source_sha256=sha(directory / 'gfx_gpu.c'), parent_sha256=sha(PARENT / 'gfx_gpu.c'),
                  sdk_sha256=sha(directory / 'of_gpu.h'),
                  limitation='Host actual-function test; target capture and RTL replay are separate.')
    (directory / 'focused-proof.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))

def invalidation():
    directory=OUT/'invalidation-v1'
    src=(directory/'gfx_pc.c').read_text()
    signature=src[src.index('static struct {\n    uint32_t cc_id;',src.index('static uint32_t vc_slot_stale')):]
    signature=signature[:signature.index('} vc_tex_sig;')+len('} vc_tex_sig;')]
    func=function(src,'gfx_vc_texture_state')
    prefix=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
struct ColorCombiner {uint32_t cc_id;};
static bool triangle_vc_texture_ready;
static uint32_t vc_slot_stale;
static struct {struct {uint16_t uls,ult;uint8_t cms,cmt;} texture_tile;
 struct {const uint8_t *addr;} loaded_texture[2];} rdp;
static struct {struct {uint16_t s,t;} texture_scaling_factor;} rsp;
'''
    suffix=r'''
int main(void) {
 unsigned cases=0;struct ColorCombiner comb={13};
 rdp.texture_tile.uls=19;rdp.texture_tile.ult=23;
 rsp.texture_scaling_factor.s=0xffff;rsp.texture_scaling_factor.t=0xabcd;
 assert(gfx_vc_texture_state(&comb,1,0.5f));vc_slot_stale=0;
 for(unsigned addr=0;addr<16;addr++)for(unsigned cms=0;cms<4;cms++)for(unsigned cmt=0;cmt<4;cmt++) {
  rdp.loaded_texture[0].addr=(const uint8_t *)(uintptr_t)(addr*4096);
  rdp.texture_tile.cms=cms;rdp.texture_tile.cmt=cmt;
  triangle_vc_texture_ready=false;
  assert(!gfx_vc_texture_state(&comb,1,0.5f));assert(vc_slot_stale==0);cases++;
 }
 for(unsigned dep=0;dep<7;dep++) {
  int textured=1;float bias=0.5f;
  switch(dep){case 0:comb.cc_id++;break;case 1:rdp.texture_tile.uls++;break;
   case 2:rdp.texture_tile.ult++;break;case 3:rsp.texture_scaling_factor.s--;break;
   case 4:rsp.texture_scaling_factor.t--;break;case 5:textured=0;break;case 6:bias=0;break;}
  triangle_vc_texture_ready=false;vc_slot_stale=0;
  assert(gfx_vc_texture_state(&comb,textured,bias));assert(vc_slot_stale==0xffffffffu);
  vc_slot_stale=0;assert(!gfx_vc_texture_state(&comb,textured,bias));assert(vc_slot_stale==0);cases++;
  triangle_vc_texture_ready=false;gfx_vc_texture_state(&comb,1,0.5f);vc_slot_stale=0;
 }
 /* G_VTX/frame/color invalidations cannot be cleared by a surface-only change. */
 for(unsigned slot=0;slot<32;slot++) {
  vc_slot_stale=1u<<slot;triangle_vc_texture_ready=false;rdp.texture_tile.cms^=1;
  assert(!gfx_vc_texture_state(&comb,1,0.5f));assert(vc_slot_stale==(1u<<slot));cases++;
 }
 vc_slot_stale=0xffffffffu;triangle_vc_texture_ready=false;
 assert(!gfx_vc_texture_state(&comb,1,0.5f));assert(vc_slot_stale==0xffffffffu);cases++;
 printf("PASS %u actual signature cases: 256 surface-only changes, seven payload dependencies, memo and externally stale slots\n",cases);
}
'''
    result=compile_run(directory,prefix+signature+func+suffix)
    # Prove the only edits are signature maintenance. Actual material setters,
    # payload preparation, G_VTX/color invalidation and backend are unchanged.
    parent=(PARENT/'gfx_pc.c').read_text()
    unchanged=['gfx_dp_set_env_color','gfx_dp_set_prim_color',
               'gfx_cpu_indexed_run','gfx_vc_try_push','gfx_prepare_triangle_material']
    for name in unchanged:assert function(src,name)==function(parent,name),name
    # Conditional source branches intentionally share a closing brace.
    start='static __attribute__((noinline)) void gfx_sp_vertex('
    end='static inline struct ColorCombiner *gfx_pick_combiner('
    assert src[src.index(start):src.index(end)]==parent[parent.index(start):parent.index(end)]
    unchanged.append('gfx_sp_vertex')
    assert sha(directory/'gfx_gpu.c')==sha(PARENT/'gfx_gpu.c')
    result.update(source_sha256=sha(directory/'gfx_pc.c'),parent_sha256=sha(PARENT/'gfx_pc.c'),
                  unchanged_actual_functions=unchanged,backend_byte_identical=True,
                  limitation='Actual signature mutation test plus source lifetime proof; full target and GPU replay remain separate.')
    (directory/'focused-proof.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    import sys
    {'resident':resident,'invalidation':invalidation}[sys.argv[1]]()
