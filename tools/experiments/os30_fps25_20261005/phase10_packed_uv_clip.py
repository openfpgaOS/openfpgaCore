#!/usr/bin/env python3
"""Independent host check of packed/wide UV clipping ownership and values."""
import json, subprocess
from pathlib import Path
from phase10_experiment import CAMP, sha, write

HERE=Path(__file__).resolve().parent
BASE=CAMP/'phase9/checks-state-f11'
CAND=CAMP/'phase10/packed-uv-v1'
OUT=CAMP/'phase10/packed-uv-clip-v1'

def extract(s,a,b):return s[s.index(a):s.index(b,s.index(a))]

def code():
    texts=[(BASE/'gfx_pc.c').read_text(),(CAND/'gfx_pc.c').read_text()]
    src='''#include <cstdint>
#include <cstddef>
#include <cstring>
#include <vector>
#include <array>
#include <random>
#include <iostream>
using rspv_t=float;
#define RSPV_MUL(a,b) ((a)*(b))
#define RSPV_DIV(a,b) ((a)/(b))
#define RSPV_RCP(a) (1.0f/(a))
#define RSPV_TO_INT(a) ((int)(a))
#define RSPV_FROM_INT(a) ((float)(a))
struct RGBA {uint8_t r,g,b,a;};
using Row=std::array<uint32_t,9>;
static uint32_t bits(float f){uint32_t x;memcpy(&x,&f,4);return x;}
'''
    for j,s in enumerate(texts):
        ns=('baseline','candidate')[j]
        structure=extract(s,'struct LoadedVertex {','struct TextureHashmapNode {')
        clip=extract(s,'static inline rspv_t rspv_lerp','\n#endif\n\nstatic void gfx_sp_tri1')
        uv=('p->u,p->v','gfx_vertex_u(p),gfx_vertex_v(p)')[j]
        src+='namespace '+ns+' {\n'+structure+'''
static std::vector<Row> result;
static int gfx_push_triangle(const LoadedVertex*a,const LoadedVertex*b,const LoadedVertex*c,bool) {
 for(auto*p:{a,b,c}){float uv[2]={'''+uv+'''};uint32_t rgba;memcpy(&rgba,&p->color,4);
  result.push_back({bits(p->x),bits(p->y),bits(p->z),bits(p->w),bits(p->w_inv),bits(uv[0]),bits(uv[1]),rgba,p->clip_rej});}
 return 1;
}
'''+clip+'\n}\n'
    src+='''static uint8_t clip(float x,float y,float z,float w){return(x<-w?32:0)|(x>w?16:0)|(y<-w?8:0)|(y>w?4:0)|(z<-w?2:0)|(z>w?1:0);}
int main(){std::mt19937 r(0x928541);unsigned tests=0,emitted=0,multiplane=0,wide=0;
 for(unsigned test=0;test<24000;test++){
  baseline::LoadedVertex a[3]{};candidate::LoadedVertex b[3]{};float uv[3][2];
  uint8_t ca=63,co=0;
  for(unsigned j=0;j<3;j++){
   float w=0.25f+float(r()%2000)/64, x=float(int(r()%8193)-4096)/64,
         y=float(int(r()%8193)-4096)/64,z=float(int(r()%8193)-4096)/64;
   if(test<48){w=1;float*axis=(test%3==0?&x:test%3==1?&y:&z);*axis=j==0?-3:j==1?0:3;}
   a[j].x=b[j].x=x;a[j].y=b[j].y=y;a[j].z=b[j].z=z;a[j].w=b[j].w=w;a[j].w_inv=b[j].w_inv=1/w;
   a[j].color=b[j].color=RGBA{uint8_t(r()),uint8_t(r()),uint8_t(r()),uint8_t(r())};
   a[j].clip_rej=b[j].clip_rej=clip(x,y,z,w);ca&=a[j].clip_rej;co|=a[j].clip_rej;
   if(test%7==0){uv[j][0]=float(int(r()%4000001)-2000000)/8;uv[j][1]=float(int(r()%4000001)-2000000)/8;b[j].wide_uv=uv[j];b[j].uv_wide=1;wide++;}
   else{b[j].u=(int16_t)r();b[j].v=(int16_t)r();uv[j][0]=b[j].u;uv[j][1]=b[j].v;}
   a[j].u=uv[j][0];a[j].v=uv[j][1];
  }
  if(__builtin_popcount(co)>1)multiplane++;
  baseline::result.clear();candidate::result.clear();
  bool aa=baseline::gfx_clip_triangle(a,a+1,a+2,ca),bb=candidate::gfx_clip_triangle(b,b+1,b+2,ca);
  if(aa!=bb||baseline::result!=candidate::result){std::cerr<<"Mismatch test "<<test<<" oldrows "<<baseline::result.size()<<" newrows "<<candidate::result.size()<<"\\n";return 1;}
  tests++;emitted+=baseline::result.size();
 }
 std::cout<<"{\\"passed\\":true,\\"triangles\\":"<<tests<<",\\"emitted_rows\\":"<<emitted<<",\\"multiple_clip_planes\\":"<<multiplane<<",\\"wide_input_vertices\\":"<<wide<<"}\\n";
}
'''
    return src

def main():
    OUT.mkdir(exist_ok=False);s=code();(OUT/'test.cpp').write_text(s)
    command=['g++','-std=c++17','-O2','-ffp-contract=off','test.cpp','-o','test']
    subprocess.run(command,cwd=OUT,check=True)
    run=subprocess.run([str(OUT/'test')],capture_output=True,text=True,check=True)
    result=json.loads(run.stdout);result.update(scope='Host scalar arithmetic tests pointer lifetime, packed integer expansion, fractional UV clipping and rectangle-wide input semantics. Not native-FMA or runtime performance.',recipe_sha256=sha(Path(__file__)),source_sha256=sha(OUT/'test.cpp'),baseline_sha256=sha(BASE/'gfx_pc.c'),candidate_sha256=sha(CAND/'gfx_pc.c'),command=command)
    # Negative control deliberately leaves copied wide pointers in the older
    # ping-pong buffer, which should fail later multi-plane interpolation.
    begin=s.index('                if (vthis->uv_wide) {',s.index('namespace candidate'))
    end=s.index('\n                }',begin)+len('\n                }')
    bad=s[:begin]+s[end:];(OUT/'negative.cpp').write_text(bad)
    subprocess.run(['g++','-std=c++17','-O2','-ffp-contract=off','negative.cpp','-o','negative'],cwd=OUT,check=True)
    neg=subprocess.run([str(OUT/'negative')],capture_output=True,text=True)
    assert neg.returncode==1,neg.stdout+neg.stderr
    result['negative_pointer_ownership_detected']=True;result['negative_output']=neg.stderr.strip()
    write(OUT/'result.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':main()
