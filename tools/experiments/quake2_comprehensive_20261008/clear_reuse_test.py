#!/usr/bin/env python3
"""Differential framebuffer/lifecycle checks using both actual mini-GL sources."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import clear_reuse as recipe

R, B, W, C, GL = recipe.R, recipe.B, recipe.W, recipe.C, recipe.GL
O = C / 'checks'
O.mkdir(exist_ok=False)
G = W / 'src/quake2'
flags = ['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
         '-fno-omit-frame-pointer', '-fno-pie', '-no-pie', '-DOF_PC',
         '-DYQ2_OPENFPGA=1', '-DYQ2_GL1_GLES=1', '-ffunction-sections',
         '-fdata-sections', '-DYQ2OSTYPE="test"', '-DYQ2ARCH="host"']
flags += ['-I' + str(p) for p in (G, G / 'src/common/header',
          G / 'src/client/refresh/gl1/glad-gles1/include', W / 'src/sdk/include')]
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
commands = []
def run(cmd, name, expected=0):
    with (O / (name + '.log')).open('w') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
    commands.append(dict(argv=cmd, name=name, returncode=result.returncode))
    if expected == 0:
        assert result.returncode == 0, name
    else:
        assert result.returncode != 0, 'Negative control passed: ' + name

test = (W / 'tools/tests/quake2_renderer_test.c').read_text()
run(flags + [str(W / 'tools/tests/quake2_renderer_test.c'), '-Wl,--gc-sections',
             '-lm', '-o', str(O / 'renderer')], 'renderer-build')
run([str(O / 'renderer')], 'renderer-run')
test = test.replace('int main(int argc, char **argv)', 'int regression_main(int argc, char **argv)')
test = test.replace('static int draw_buffer, surface_calls;',
                    'static int draw_buffer, surface_calls;\nstatic unsigned color_clears, depth_clears;')
test = test.replace('''\tfor (int y = 0; y < h; y++) memset(p + y * stride, c, w);''',
                    '''\tif (p == ofgl_zbuf) depth_clears++; else color_clears++;
\tfor (int y = 0; y < h; y++) memset(p + y * stride, c, w);''')
# Successful GPU drawing is modeled as a recognizable colour write, not as a
# rasterizer. Actual RTL replay is a separate required gate for this candidate.
test = test.replace('\tgeometry_draws++;', '\tofgl_fb16()[100] = 0x1357;\n\tgeometry_draws++;')
test = test.replace('\tassert(draws < 32);', '\tofgl_fb16()[100] = 0x2468;\n\tassert(draws < 32);')
test += r'''
static unsigned steps;
static uint64_t digest(uint64_t h, const void *v, size_t n)
{
 const unsigned char *p = v;
 for (size_t i=0; i<n; i++) h=(h ^ p[i])*UINT64_C(1099511628211);
 return h;
}
static void snapshot(const char *name)
{
 uint64_t h=UINT64_C(1469598103934665603);
 h=digest(h,framebuffer,sizeof framebuffer);
 h=digest(h,other_framebuffers,sizeof other_framebuffers);
 if(ofgl_zbuf) h=digest(h,ofgl_zbuf,320*240*2);
 printf("%u %s %016llx pending=%d depth=%u\n",++steps,name,
  (unsigned long long)h,ofgl_gpu_pending,depth_clears);
}
static void clear_expected(const char *name, unsigned baseline, unsigned reused)
{
 unsigned before=color_clears;
 ofgl_clear_fb565();
 assert(color_clears-before == (EXPECT_REUSE ? reused : baseline));
 snapshot(name);
}
static void gpu_write(unsigned route)
{
 float v[4][7]={{-4,-4,-32,0,0,0,0},{4,-4,-32,1,0,0,0},
  {4,4,-32,1,1,0,0},{-4,4,-32,0,1,0,0}};
 GLushort indices[]={0,1,2,0,2,3};
 setup_world_arrays(v[0]);
 reset_geometry_recording();
 gpu_geometry=(route>=2);
 if(route==0) ofgl_DrawArrays(GL_TRIANGLES,0,3);
 else if(route==1 || route==2) ofgl_DrawElements(GL_TRIANGLES,6,GL_UNSIGNED_SHORT,indices);
 else {
  void *storage=malloc(OFGL_WorldPolyBytes(4));
  ofgl_world_poly_t *p=OFGL_BuildWorldPoly(storage,v[0],4);
  assert(p && p->raw_ok); OFGL_DrawWorldPolys(&p,1); free(storage);
 }
 assert(draws+geometry_draws>0 && ofgl_fb16()[100]!=0);
 gpu_geometry=0;
}
int main(void)
{
 assert(RI_InitContext(NULL));
 /* Z contents start defined, and remain part of every exact comparison. */
 memset(ofgl_zbuf,0x67,320*240*2);
 ofgl_clear565=0;
 clear_expected("first",1,1);
 clear_expected("duplicate",1,0);
 unsigned before=color_clears, zbefore=depth_clears;
 ofgl_Clear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT);
 assert(color_clears-before==(EXPECT_REUSE?0:1) && depth_clears==zbefore+1);
 assert(ofgl_gpu_pending); snapshot("depth-retained");
 ofgl_Viewport(10,20,100,80);
 clear_expected("viewport-full-buffer",1,0);
 ofgl_Viewport(0,0,320,240);

 /* Every solid/alpha/textured CPU path must invalidate the clear. */
 for(unsigned style=0;style<3;style++) {
  ofgl_Disable(GL_TEXTURE_2D); ofgl_Color4f(1,1,1,style==1?0.5f:1);
  GLuint id=43;
  if(style==2) {
   uint32_t pixels[]={0xff0000ff,0,0xff00ff00,0xffff0000};
   ofgl_store_texture=1; ofgl_Enable(GL_TEXTURE_2D);
   ofgl_EnableClientState(GL_TEXTURE_COORD_ARRAY);
   ofgl_BindTexture(GL_TEXTURE_2D,id);
   ofgl_TexImage2D(GL_TEXTURE_2D,0,GL_RGBA,2,2,0,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
  }
  int f=finishes, pending=ofgl_gpu_pending;
  ofgl_blit_rect(10,10,12,12,0,0,1,1);
  assert(finishes==f+pending && !ofgl_gpu_pending);
  snapshot("cpu-write"); clear_expected("after-cpu-write",1,1);
  clear_expected("after-cpu-duplicate",1,0);
  if(style==2) {ofgl_DeleteTextures(1,&id);ofgl_DisableClientState(GL_TEXTURE_COORD_ARRAY);}
 }
 for(unsigned route=0;route<4;route++) {
  gpu_write(route); snapshot("gpu-write");
  clear_expected("after-gpu-write",1,1);
  clear_expected("after-gpu-duplicate",1,0);
 }
 /* A rebind invalidates even when the surface address happens to repeat. */
 ofgl_refresh_fb(); clear_expected("same-address-rebind",1,1);
 for(unsigned i=0;i<6;i++) {
  unsigned c=color_clears;
  RI_EndFrame(); assert(color_clears==c+1);
  snapshot("flip-eager-clear"); clear_expected("next-frame-duplicate",1,0);
 }
 missing_surface=1;ofgl_refresh_fb();clear_expected("missing",0,0);
 missing_surface=0;ofgl_refresh_fb();clear_expected("restored",1,1);
 ofgl_clear565=0xffff;clear_expected("different-byte-color",1,1);
 clear_expected("white-duplicate",1,0);
 ofgl_clear565=0x1234;clear_expected("cpu-colored-fallback",0,0);
 ofgl_clear565=0xffff;clear_expected("after-cpu-fallback",1,1);
 ofgl_gpu_ok=0;ofgl_clear565=0;clear_expected("gpu-disabled-fallback",0,0);
 ofgl_gpu_ok=1;clear_expected("gpu-restored",1,1);
 ofgl_fb_width=319;clear_expected("width-change",1,1);
 ofgl_fb_height=239;clear_expected("height-change",1,1);
 ofgl_fb_stride=638;clear_expected("stride-change",1,1);
 ofgl_fb_width=320;ofgl_fb_height=240;ofgl_fb_stride=640;
 clear_expected("layout-restored",1,1);
 /* Initialization failures must also end the old tracking lifetime. */
 check_mode_error=-1;assert(!RI_InitContext(NULL));check_mode_error=0;
 clear_expected("failed-reinit",1,1);
 RI_ShutdownContext();assert(RI_InitContext(NULL));
 memset(ofgl_zbuf,0x67,320*240*2);ofgl_clear565=0;
 clear_expected("new-context",1,1);clear_expected("new-context-duplicate",1,0);
 RI_ShutdownContext();
 fprintf(stderr,"PASS lifecycle steps=%u color_clears=%u depth_clears=%u\n",steps,color_clears,depth_clears);
 return 0;
}
'''
(O / 'lifecycle.c').write_text(test)
for name, source, reuse in [('baseline', B / GL, 0), ('candidate', W / GL, 1)]:
    run(flags + ['-DOFGL_SOURCE="' + str(source) + '"', '-DEXPECT_REUSE=' + str(reuse),
                 str(O / 'lifecycle.c'), '-Wl,--gc-sections', '-lm', '-o', str(O / name)], name + '-build')
    run([str(O / name)], name + '-run')
def snapshots(name):
    return [line for line in (O / (name + '-run.log')).read_text().splitlines()
            if line and line[0].isdigit()]
assert snapshots('baseline') == snapshots('candidate')
# Each removed invalidation must be detected by an observable stale clear.
new = (W / GL).read_text()
negative_controls = {
    'cpu': ('\t/* Both solid/alpha fills and textured HUD blits write colour here. */\n\tofgl_fb_clear.fb = NULL;', ''),
    'gpu': ('\t/* Every CPU-prepared, indexed and raw-world GPU draw enters here. */\n\tofgl_fb_clear.fb = NULL;', ''),
    'rebind': ('\tuint8_t *fb = of_video_surface();\n\tofgl_fb_clear.fb = NULL;', '\tuint8_t *fb = of_video_surface();'),
    'init': ('\tofgl_fb_clear.fb = NULL;\n\t(void)win;', '\t(void)win;'),
}
for name, (old, replacement) in negative_controls.items():
    assert new.count(old) == 1
    source = O / ('missing-' + name + '.c')
    source.write_text(new.replace(old, replacement, 1))
    run(flags + ['-DOFGL_SOURCE="' + str(source) + '"', '-DEXPECT_REUSE=1',
                 '-I' + str(G / 'openfpga'), str(O / 'lifecycle.c'), '-Wl,--gc-sections',
                 '-lm', '-o', str(O / ('missing-' + name))], 'missing-' + name + '-build')
    run([str(O / ('missing-' + name))], 'missing-' + name + '-run', expected=1)
(O / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
shutil.copy2(__file__, C / 'test-recipe.py')
result = dict(passed=True, source_sha256=recipe.sha(W / GL), renderer_sanitizers=True,
              lifecycle_snapshots=len(snapshots('candidate')),
              framebuffer_depth_pending_state_exact=True, all_four_gpu_draw_routes=True,
              negative_controls_rejected=list(negative_controls),
              limitations='Host GPU stubs model a recognizable color write; actual selected RTL color/depth replay remains required.')
(C / 'tests.json').write_text(json.dumps(result, indent=2) + '\n')
(C / 'manifest.json').write_text(json.dumps(dict(passed=True, evidence_sha256={
    str(p.relative_to(R)):recipe.sha(p) for p in C.rglob('*') if p.is_file()}), indent=2) + '\n')
print(json.dumps(result, indent=2))
