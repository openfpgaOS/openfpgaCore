#!/usr/bin/env python3
"""Compare the actual fast classifier with all original plane predicates."""
from pathlib import Path
import importlib.util,subprocess,hashlib,json
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-frontend-20261004'
GFX=OUT/'SM64/src/sm64/sm64/src/pc/gfx'
spec=importlib.util.spec_from_file_location('prior_visibility',ROOT/'tools/experiments/sm64_geometry_fast_20261003/check_visibility.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
s=(GFX/'gfx_geometry.h').read_text()
a=s.index('    unsigned outside;');b=s.index('\n    unsigned context;',a)
body=s[a:b].replace('vertex_cull_capture(vertices,count,first,aspect,outside);','')
body=body.replace(' bounds_inside:', ' bounds_inside: return 1;')
pre=v.PRE+'\n#define SM64_PERF_SCOPE(x) ((void)0)\n#define SM64_PERF_COUNT(x,y) ((void)0)\n'
pre+='static void gfx_materialize_vertex_attributes(unsigned slot) {(void)slot;}\n'
pre+=(GFX/'gfx_vertex_cull.h').read_text()+s
pre+='\nstatic int fast(const float *box) {\n'+body+'\n}\n'
test=r'''static uint32_t rng=0x30202604;
static uint32_t random32(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static float range(float a,float b) {return a+(b-a)*(random32()*(1.0f/4294967296.0f));}
int main(void) {
 unsigned accepted=0;
 for(unsigned t=0;t<1000000;t++) {
  float box[6],center[3],dot[4]={0};
  for(unsigned k=0;k<3;k++) {
   center[k]=(int)(random32()%65000)-32500;
   box[2*k]=center[k]-range(0,64);box[2*k+1]=center[k]+range(0,64);
   for(unsigned j=0;j<4;j++)rsp.MP_matrix[k][j]=range(-126,126);
   rsp.MP_matrix[k][2]=rsp.MP_matrix[k][3]*0.5f;
   for(unsigned j=0;j<4;j++)dot[j]+=center[k]*rsp.MP_matrix[k][j];
  }
  float w=range(4,32760);
  rsp.MP_matrix[3][3]=w-dot[3];rsp.MP_matrix[3][2]=w*0.5f-dot[2];
  rsp.MP_matrix[3][0]=range(-4*w,4*w)-dot[0];
  rsp.MP_matrix[3][1]=range(-4*w,4*w)-dot[1];
  ++geometry_mvp_epoch;geometry_update_planes(1.0f);
  int original=geometry_outside(box)?2:1;
  if(original==1)for(unsigned i=0;i<8;i++) {
   const struct GeometryPlane *p=&geometry_planes[i];
   float low=box[p->corner[0]]*p->c[0]+box[p->corner[1]]*p->c[1]+box[p->corner[2]]*p->c[2]+p->c[3];
   if(!(low>0))original=0;
  }
  int actual=fast(box);assert(actual==original);accepted+=actual==1;
 }
 printf("PASS: 1000000 large-coordinate/cancelling bounds, %u accepted, exact original classification\n",accepted);
}
'''
c=OUT/'bounds-oracle.c';c.write_text(pre+test)
exe=OUT/'bounds-oracle'
cmd=['cc','-O3','-ffast-math','-std=gnu11','-fsanitize=undefined',str(c),'-lm','-o',str(exe)]
subprocess.run(cmd,check=True)
r=subprocess.run([str(exe)],text=True,capture_output=True,check=True)
(OUT/'bounds-oracle.json').write_text(json.dumps(dict(command=cmd,result=r.stdout.strip(),source_sha256=hashlib.sha256(s.encode()).hexdigest()),indent=2)+'\n')
print(r.stdout,end='')
