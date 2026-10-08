#!/usr/bin/env python3
"""Exact opaque horizontal unit-texel HUD path; retain the full generic fallback."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-comprehensive-20261008'
B=P/'work/base';W=P/'work/hud';C=P/'components/hud-v1';REL='src/quake2/openfpga/of_gl1.c'
BASE='29818e61c88a358f30cde8a4282676c6904a71b12b77c98934844233ea84fbdc'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
def function(text,signature):
    a=text.index(signature);i=text.index('{',a)+1;depth=1
    while depth:
        if text[i]=='{':depth+=1
        elif text[i]=='}':depth-=1
        i+=1
    return text[a:i]
assert sha(B/REL)==BASE and not W.exists() and not C.exists()
old=(B/REL).read_text()
anchor='''\ts_at_cx0 = uls + (cx0 - x0 + 0.5f) * sstep;
\tt_at_cy0 = ult + (cy0 - y0 + 0.5f) * tstep;
'''
fast='''
\t/* For an opaque unit-texel walk, every horizontal sample is exact.
\t * A power-of-two width makes the normalized step a binary power;
\t * half-texel start plus in-range indices below 32768 needs at most
\t * 17 significant bits, so every original float add/multiply is exact.
\t * Vertical sampling is unchanged and may still be scaled or clipped. */
\tfloat first_texel = s_at_cx0 * tex->w;
\tif (alpha >= 256 && tex->w <= 32768 && !(tex->w & (tex->w - 1)) &&
\t\tsstep * tex->w == 1.0f && first_texel >= 0.5f &&
\t\tfirst_texel <= (float)tex->w - 0.5f)
\t{
\t\tint first = (int)first_texel;
\t\tif (first_texel == (float)first + 0.5f && cx1 - cx0 <= tex->w - first)
\t\t{
\t\t\tfor (py = cy0; py < cy1; py++)
\t\t\t{
\t\t\t\tfloat tcur = t_at_cy0 + (py - cy0) * tstep;
\t\t\t\tint ty = (int)(tcur * tex->h);
\t\t\t\tif (ty < 0) ty = 0; else if (ty >= tex->h) ty = tex->h - 1;
\t\t\t\tconst uint16_t *src = tex->texels + (size_t)ty * tex->w + first;
\t\t\t\tuint16_t *dst = fb16 + (size_t)py * spx + cx0;
\t\t\t\tfor (int px = 0; px < cx1 - cx0; px++)
\t\t\t\t{
\t\t\t\t\tuint16_t texel = src[px];
\t\t\t\t\tif (texel != OFGL_TEX_TRANSPARENT) dst[px] = texel;
\t\t\t\t}
\t\t\t}
\t\t\treturn;
\t\t}
\t}
'''
assert old.count(anchor)==1
new=old.replace(anchor,anchor+fast,1)
C.mkdir(parents=True);shutil.copytree(B,W,ignore=shutil.ignore_patterns('.obj','build','__pycache__'))
(W/REL).write_text(new);(C/'of_gl1.c').write_text(new)
(C/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+REL,tofile='b/'+REL)))
shutil.copy2(__file__,C/'recipe.py')
put(C/'prepared.json',dict(parent_sha256=BASE,candidate_sha256=sha(W/REL),worktree=str(W),scope='Opaque exact horizontal1texel/pixel path only; generic vertical coordinate evaluation, zero-key skip, clipping, GPUfinish and nonfast fallback retained.',proof='Width is a positive power of2 <=32768. Actual stored float sstep*width==1 proves sstep=1/width exactly. Actual first_texel is an inrange integer+0.5. All subsequent half-integers <=32767.5 need <=16significant bits; normalized additions and power-of2 products are exact, so generic truncating/clamped horizontal indices equal first+pixel.'))
O=C/'checks';O.mkdir();G=W/'src/quake2';includes=[G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include',G/'openfpga']
common=['cc','-std=gnu11','-g','-fno-omit-frame-pointer','-fno-pie','-no-pie','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections','-DYQ2OSTYPE="test"','-DYQ2ARCH="host"','-DOFGL_SOURCE="'+str(W/REL)+'"']+['-I'+str(p) for p in includes]
commands=[];env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
def run(cmd,name):
    commands.append(dict(argv=cmd,name=name))
    with (O/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
run(common+['-O1','-fsanitize=address,undefined',str(W/'tools/tests/quake2_renderer_test.c'),'-Wl,--gc-sections','-lm','-o',str(O/'renderer')],'renderer-build')
run([str(O/'renderer')],'renderer-run')
test=(W/'tools/tests/quake2_renderer_test.c').read_text().replace('int main(int argc, char **argv)','int unused_renderer_main(int argc, char **argv)')
reference=function(old,'static void\nofgl_blit_rect(').replace('static void\nofgl_blit_rect(','static void __attribute__((noinline,noclone,noipa))\nreference_blit_rect(',1)
test+='\n'+reference+r'''
static uint16_t expected_fb[320*240];
static uint32_t rng=0x74120a03;
static uint32_t rnd(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static unsigned cases;
static void check(float x,float y,float width,float height,float s,float t,float sw,float th,float alpha,int texture) {
  ofgl_tex2d_enabled=texture;ofgl_cur_color[3]=alpha;
  memset(framebuffer,0x39,sizeof framebuffer);ofgl_gpu_pending=1;finishes=0;
  reference_blit_rect(x,y,x+width,y+height,s,t,s+sw,t+th);
  memcpy(expected_fb,framebuffer,sizeof framebuffer);int expected_finishes=finishes,expected_pending=ofgl_gpu_pending;
  memset(framebuffer,0x39,sizeof framebuffer);ofgl_gpu_pending=1;finishes=0;
  ofgl_blit_rect(x,y,x+width,y+height,s,t,s+sw,t+th);
  if(memcmp(expected_fb,framebuffer,sizeof framebuffer)){
    printf("mismatch case%u xy=%g,%g wh=%g,%g st=%g,%g span=%g,%g alpha%g texture%d\n",cases,x,y,width,height,s,t,sw,th,alpha,texture);
    for(unsigned i=0;i<320*240;i++)if(expected_fb[i]!=framebuffer[i]){printf("pixel%u old%04x new%04x\n",i,expected_fb[i],framebuffer[i]);break;}abort();
  }
  assert(expected_finishes==finishes&&expected_pending==ofgl_gpu_pending);cases++;
}
int main(void) {
  ofgl_framebuffer=(uint8_t *)framebuffer;ofgl_fb_width=320;ofgl_fb_height=240;ofgl_fb_stride=640;
  ofgl_cur_tex=1;ofgl_va_texcoord.enabled=1;ofgl_cur_color[0]=0.1f;ofgl_cur_color[1]=0.7f;ofgl_cur_color[2]=0.9f;
  const unsigned widths[]={1,2,7,8,31,32,64,127,128,256,512,1024,32768};
  for(unsigned wi=0;wi<sizeof widths/sizeof *widths;wi++){
    unsigned tw=widths[wi],th=17;ofgl_tex_t *tex=&ofgl_tex[1];tex->w=tw;tex->h=th;tex->texels=malloc(tw*th*2);assert(tex->texels);
    for(unsigned i=0;i<tw*th;i++)tex->texels[i]=i%11==0?0:(uint16_t)rnd();
    for(unsigned n=0;n<240;n++){
      int w=1+rnd()%128,h=1+rnd()%64,sx=rnd()%tw;
      if(n%4==0){w=tw<128?tw:128;sx=0;}
      float x=(int)(rnd()%401)-40,y=(int)(rnd()%281)-20;
      float ss=(float)sx/tw,sw=(float)w/tw,t=(int)(rnd()%33)-8,ts=(1+rnd()%24);
      t/=th;ts/=th;
      float alpha=n%5==0?0:n%5==1?0.5f:n%5==2?0.999f:1.0f;
      if(n%8==0)alpha=1;
      if(n%7==1)sw*=0.5f;
      if(n%7==2)sw=-sw;
      if(n%7==3)ss+=0.25f/tw;
      if(n%7==4)x+=0.25f;
      if(n%7==5){x=-8;ss=-4.0f/tw;}
      check(x,y,w,h,ss,t,sw,ts,alpha,n%19!=0);
    }
    /* Native unit walks including exact source end, destination clipping,
     * half-texel phase rejection, and varying vertical filter coordinates. */
    unsigned w=tw<128?tw:128;
    check(0,0,w,17,0,0,(float)w/tw,1,1,1);
    check(-5,-3,w,23,0,0,(float)w/tw,1,1,1);
    check(13,27,w,13,(float)(tw-w)/tw,0,(float)w/tw,1,1,1);
    check(13,27,w,13,0.25f/tw,0,(float)w/tw,1,1,1);
    free(tex->texels);tex->texels=NULL;
  }
  printf("PASS blit_cases=%u framebuffer_and_finish_exact=1 source_widths=1..32768\n",cases);return 0;
}
'''
(O/'blit.c').write_text(test)
for label,opts in [('sanitized',['-O1','-fsanitize=address,undefined']),('fastmath',['-O3','-ffast-math','-fsingle-precision-constant'])]:
    run(common+opts+[str(O/'blit.c'),'-Wl,--gc-sections','-lm','-o',str(O/label)],label+'-build')
    run([str(O/label)],label+'-run')
put(O/'commands.json',commands)
put(C/'result.json',dict(passed=True,actual_renderer_sanitizers=True,differential_blits_per_mode=3172,modes=['O1 ASan+UBSan','O3 fast-math single-precision-constant'],compared=['entire framebuffer','GPUfinish count','pending flag'],coverage=['nativeunit','scaled','negativeUV','fractionalphase','clipped','opaque','partialalpha','transparent','solid','sourcebounds','powerof2/nonpowerof2'],performance_measured=False))
put(C/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in C.rglob('*') if p.is_file()}))
print((O/'sanitized-run.log').read_text().strip());print((O/'fastmath-run.log').read_text().strip());print('source',sha(W/REL),'manifest',sha(C/'manifest.json'))
