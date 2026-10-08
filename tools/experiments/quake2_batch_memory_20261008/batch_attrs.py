#!/usr/bin/env python3
"""Resolve invariant attribute views once per 3D draw; keep reads deferred."""
import difflib,hashlib,json,os,shutil,subprocess
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-batch-memory-20261008';B=P/'work/base';W=P/'work/batch';C=P/'components/batch-attrs-v1'
REL='src/quake2/openfpga/of_gl1.c'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def function(text,sig):
 a=text.index(sig);i=text.index('{',a)+1;depth=1
 while depth:
  if text[i]=='{':depth+=1
  elif text[i]=='}':depth-=1
  i+=1
 return text[a:i]
pins=json.loads((P/'before.json').read_text())['source_sha256'];assert sha(B/REL)==pins[REL];assert not W.exists() and not C.exists()
old=(B/REL).read_text();oldfn=function(old,'static void\nofgl_fetch_attrs(')
replacement='''enum
{
\tOFGL_ATTR_FIXED,
\tOFGL_ATTR_RGB,
\tOFGL_ATTR_RGBA,
\tOFGL_ATTR_LIGHTMAP,
\tOFGL_ATTR_CONSTANT_PENDING
};

static struct
{
\tconst uint8_t *uv_ptr, *color_ptr;
\tint uv_stride, color_stride;
\tunsigned mode;
\tuint16_t rgb;
\tuint8_t alpha;
} ofgl_attrs;

/* Array formats and material priority are invariant during a GL draw.
 * Resolve only their views here: reads still wait for a surviving triangle.
 * Do not snapshot the position view: raw world surfaces replace it inside
 * one begin3d/end3d batch. */
static void
ofgl_prepare_attrs(void)
{
\t/* Untextured draws may leave a dangling client texcoord array enabled. */
\tofgl_attrs.uv_ptr = (ofgl_tex2d_enabled && ofgl_va_texcoord.enabled)
\t\t? ofgl_va_texcoord.ptr : NULL;
\tofgl_attrs.uv_stride = ofgl_va_texcoord.stride ? ofgl_va_texcoord.stride
\t\t: ofgl_va_texcoord.size * (int)sizeof(float);

\tif (ofgl_lm_active && ofgl_lm_cur)
\t{
\t\tconst ofgl_array_t *lmc = &ofgl_va_texcoord_tmu[1];
\t\tofgl_attrs.mode = OFGL_ATTR_LIGHTMAP;
\t\tofgl_attrs.color_ptr = lmc->ptr;
\t\tofgl_attrs.color_stride = lmc->stride ? lmc->stride
\t\t\t: lmc->size * (int)sizeof(float);
\t}
\telse if (ofgl_tex2d_enabled && ofgl_texenv_mode(0) == GL_REPLACE)
\t{
\t\tofgl_attrs.mode = OFGL_ATTR_FIXED;
\t\tofgl_attrs.rgb = 0xffff;
\t\tofgl_attrs.alpha = 255;
\t}
\telse if (ofgl_va_color.enabled && ofgl_va_color.ptr)
\t{
\t\tofgl_attrs.mode = ofgl_va_color.size >= 4 ? OFGL_ATTR_RGBA : OFGL_ATTR_RGB;
\t\tofgl_attrs.color_ptr = ofgl_va_color.ptr;
\t\tofgl_attrs.color_stride = ofgl_va_color.stride ? ofgl_va_color.stride
\t\t\t: ofgl_va_color.size;
\t}
\telse
\t{
\t\t/* Convert constant color at most once, but not for rejected batches. */
\t\tofgl_attrs.mode = OFGL_ATTR_CONSTANT_PENDING;
\t}
}

static void
ofgl_fetch_attrs(int i, float tw, float th, ofgl_cvert_t *out)
{
\tif (ofgl_attrs.uv_ptr)
\t{
\t\tconst float *tc = (const float *)(ofgl_attrs.uv_ptr +
\t\t\t(size_t)i * ofgl_attrs.uv_stride);
\t\tout->s = tc[0] * tw;
\t\tout->t = tc[1] * th;
\t}
\telse
\t{
\t\tout->s = out->t = 0.0f;
\t}

\tif (ofgl_attrs.mode == OFGL_ATTR_LIGHTMAP)
\t{
\t\tconst float *lc = (const float *)(ofgl_attrs.color_ptr +
\t\t\t(size_t)i * ofgl_attrs.color_stride);
\t\tout->rgb = ofgl_lm_sample(ofgl_lm_cur, lc[0], lc[1]);
\t\tout->a = 255;
\t}
\telse if (ofgl_attrs.mode == OFGL_ATTR_RGB || ofgl_attrs.mode == OFGL_ATTR_RGBA)
\t{
\t\tconst uint8_t *c = ofgl_attrs.color_ptr +
\t\t\t(size_t)i * ofgl_attrs.color_stride;
\t\tout->rgb = (uint16_t)(((c[0] & 0xF8) << 8) | ((c[1] & 0xFC) << 3) |
\t\t\t(c[2] >> 3));
\t\tout->a = ofgl_attrs.mode == OFGL_ATTR_RGBA ? c[3] : 255;
\t}
\telse
\t{
\t\tif (ofgl_attrs.mode == OFGL_ATTR_CONSTANT_PENDING)
\t\t{
\t\t\tofgl_attrs.rgb = ofgl_rgba_to_565(
\t\t\t\t((uint32_t)(ofgl_cur_color[0] * 255.0f) & 0xff) |
\t\t\t\t(((uint32_t)(ofgl_cur_color[1] * 255.0f) & 0xff) << 8) |
\t\t\t\t(((uint32_t)(ofgl_cur_color[2] * 255.0f) & 0xff) << 16));
\t\t\tofgl_attrs.alpha = (uint8_t)(ofgl_cur_color[3] * 255.0f + 0.5f);
\t\t\tofgl_attrs.mode = OFGL_ATTR_FIXED;
\t\t}
\t\tout->rgb = ofgl_attrs.rgb;
\t\tout->a = ofgl_attrs.alpha;
\t}
\tout->attrs_valid = 1;
}'''
new=old.replace(oldfn,replacement,1)
anchor='\tofgl_vmemo_new_batch();\n\tofgl_gpu_slots_valid = 0;'
assert new.count(anchor)==1
new=new.replace(anchor,'\tofgl_prepare_attrs();\n'+anchor,1)
C.mkdir(parents=True);shutil.copytree(B,W,ignore=shutil.ignore_patterns('.obj','build','__pycache__'));(W/REL).write_text(new)
(C/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+REL,tofile='b/'+REL)))
shutil.copy2(W/REL,C/'of_gl1.c');shutil.copy2(__file__,C/'recipe.py')
put(C/'prepared.json',dict(parent_sha256=sha(B/REL),candidate_sha256=sha(W/REL),worktree=str(W),scope='Batch attribute pointer/stride/material dispatch only, deferred source reads and lazy constant conversion; no position/tri-state changes.'))
O=C/'checks';O.mkdir();G=W/'src/quake2';includes=[G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include',G/'openfpga']
common=['cc','-std=gnu11','-g','-fno-omit-frame-pointer','-fno-pie','-no-pie','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections','-DYQ2OSTYPE="test"','-DYQ2ARCH="host"','-DOFGL_SOURCE="'+str(W/REL)+'"']+['-I'+str(p) for p in includes]
commands=[];env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
def run(cmd,name):
 commands.append(dict(name=name,argv=cmd))
 with (O/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
run(common+['-O1','-fsanitize=address,undefined',str(W/'tools/tests/quake2_renderer_test.c'),'-Wl,--gc-sections','-lm','-o',str(O/'renderer')],'renderer-build');run([str(O/'renderer')],'renderer-run')
test=(W/'tools/tests/quake2_renderer_test.c').read_text().replace('int main(int argc, char **argv)','int unused_renderer_main(int argc, char **argv)')
test+='\n'+oldfn.replace('static void\nofgl_fetch_attrs(','static void __attribute__((noinline,noclone,noipa))\nreference_fetch_attrs(',1)+r'''
static uint32_t seed=0x43188fed;
static uint32_t random_word(void){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static float uv[520*8],lmuv[520*8];
static uint8_t colors[520*8];
static uint16_t lightmap[128*128];
int main(void){
 unsigned cases=0,mode_counts[5]={0},disabled_poison=0;
 for(unsigned i=0;i<sizeof uv/sizeof *uv;i++){uv[i]=((int)(random_word()%65537)-32768)*0.03125f;lmuv[i]=((int)(random_word()%513)-128)*0.00390625f;}
 for(unsigned i=0;i<sizeof colors;i++)colors[i]=random_word();
 for(unsigned i=0;i<128*128;i++)lightmap[i]=random_word();
 for(unsigned batch=0;batch<65536;batch++){
  ofgl_tex2d_enabled=random_word()&1;
  ofgl_va_texcoord.enabled=random_word()&1;
  ofgl_va_texcoord.ptr=(random_word()%3)?(const uint8_t *)uv:NULL;
  ofgl_va_texcoord.size=2+(random_word()%3);
  ofgl_va_texcoord.stride=(random_word()&1)?0:8*sizeof(float);
  if(!ofgl_tex2d_enabled||!ofgl_va_texcoord.enabled){ofgl_va_texcoord.ptr=(const uint8_t *)(uintptr_t)1;disabled_poison++;}
  ofgl_lm_active=random_word()&1;ofgl_lm_cur=(random_word()&1)?lightmap:NULL;
  ofgl_va_texcoord_tmu[1].ptr=(const uint8_t *)lmuv;
  ofgl_va_texcoord_tmu[1].size=2+(random_word()%3);
  ofgl_va_texcoord_tmu[1].stride=(random_word()&1)?0:8*sizeof(float);
  if(!(ofgl_lm_active&&ofgl_lm_cur))ofgl_va_texcoord_tmu[1].ptr=(const uint8_t *)(uintptr_t)1;
  ofgl_texenv[0]=(random_word()%3)==0?GL_REPLACE:(random_word()&1)?GL_MODULATE:GL_COMBINE;
  ofgl_va_color.enabled=random_word()&1;
  ofgl_va_color.ptr=(random_word()%3)?colors:NULL;
  ofgl_va_color.size=3+(random_word()%3);
  ofgl_va_color.stride=(random_word()&1)?0:8;
  if(!ofgl_va_color.enabled||(ofgl_lm_active&&ofgl_lm_cur)||(ofgl_tex2d_enabled&&ofgl_texenv[0]==GL_REPLACE))ofgl_va_color.ptr=(const uint8_t *)(uintptr_t)1;
  for(unsigned i=0;i<4;i++)ofgl_cur_color[i]=(random_word()%65537)*(1.0f/65536.0f);
  if(batch%8==0)ofgl_cur_color[3]=0;
  if(batch%8==1)ofgl_cur_color[3]=1;
  float tw=(float)(1u<<(random_word()%11)),th=(float)(1u<<(random_word()%11));
  ofgl_prepare_attrs();mode_counts[ofgl_attrs.mode]++;
  for(unsigned j=0;j<17;j++){
   int index=(random_word()%520);ofgl_cvert_t a,b;memset(&a,0xa5,sizeof a);b=a;
   reference_fetch_attrs(index,tw,th,&a);ofgl_fetch_attrs(index,tw,th,&b);
   if(memcmp(&a,&b,sizeof a)){printf("mismatch batch%u index%d mode%u s%a/%a t%a/%a rgb%04x/%04x a%u/%u\n",batch,index,ofgl_attrs.mode,a.s,b.s,a.t,b.t,a.rgb,b.rgb,a.a,b.a);abort();}
   cases++;
  }
 }
 for(unsigned i=0;i<5;i++)assert(mode_counts[i]>0);
 printf("PASS attrs=%u batches=65536 deferred_disabled_poison=%u mode_counts=%u,%u,%u,%u,%u\n",cases,disabled_poison,mode_counts[0],mode_counts[1],mode_counts[2],mode_counts[3],mode_counts[4]);
 return 0;
}
'''
(O/'attrs.c').write_text(test)
for label,opts in [('sanitized',['-O1','-fsanitize=address,undefined']),('fastmath',['-O3','-ffast-math','-fsingle-precision-constant'])]:
 run(common+opts+[str(O/'attrs.c'),'-Wl,--gc-sections','-lm','-o',str(O/label)],label+'-build');run([str(O/label)],label+'-run')
put(O/'commands.json',commands)
put(C/'result.json',dict(passed=True,actual_renderer_ASan_UBSan=True,differential_attributes_per_mode=65536*17,batches_per_mode=65536,modes=['O1 ASan+UBSan','O3 fast-math single-precision-constant'],coverage=['lightmap>replace>RGB/RGBA>constant','independent UV enable and texturing','disabled dangling UV/color/lightmap pointers','null source pointers where legal','implicit/explicit/padded strides','UV outside normalized range','nonzero index and indices >255','batch resets','varying particle alpha/depth/world pointer integration via actual renderer suite'],performance_measured=False))
put(C/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in C.rglob('*') if p.is_file()}))
print((O/'sanitized-run.log').read_text().strip());print((O/'fastmath-run.log').read_text().strip());print('source',sha(W/REL),'manifest',sha(C/'manifest.json'))
