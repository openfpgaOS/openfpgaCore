#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
// Independent float-vs-Q30 precision check. This does not replace the RTL
// stream oracle or claim to simulate VexiiRiscv's FPU implementation.
static uint32_t rng=0x12720264;
static uint32_t random32() {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main() {
    unsigned different=0,max8=0,max565=0;
    for(unsigned test=0;test<1000000;test++) {
        float c[3];int n[3];int32_t q[3];
        for(unsigned k=0;k<3;k++) {c[k]=int32_t(random32())/2147483648.0f;n[k]=int8_t(random32());}
        // Include exact axes/integer lighting boundaries, not just random
        // directions; these expose the unavoidable Q30 / 127 rounding case.
        if((test&15)==0) {c[0]=1;c[1]=c[2]=0;n[0]=127;n[1]=n[2]=0;}
        float length=std::sqrt(c[0]*c[0]+c[1]*c[1]+c[2]*c[2]);
        if(length==0)continue;
        int64_t fixed=0;
        for(unsigned k=0;k<3;k++) {
            c[k]/=length;q[k]=std::lrintf(c[k]*(1073741824.0f/127.0f));fixed+=int64_t(q[k])*n[k];
        }
        fixed=std::max<int64_t>(0,fixed);assert(fixed<INT64_C(2147483648));
        float intensity=std::max(0.0f,std::fma(float(n[0]),c[0],std::fma(float(n[1]),c[1],n[2]*c[2]))/127.0f);
        bool diff=false;
        for(unsigned k=0;k<3;k++) {
            unsigned ambient=random32()&255,light=random32()&255;
            int a=std::min(255,int(std::fma(intensity,float(light),float(ambient))));
            int b=std::min<int64_t>(255,ambient+((fixed*light)>>30));
            unsigned e=std::abs(a-b);max8=std::max(max8,e);assert(e<=1);
            e=std::abs((a>>(k==1?2:3))-(b>>(k==1?2:3)));
            max565=std::max(max565,e);assert(e<=1);diff|=e!=0;
        }
        different+=diff;
    }
    std::printf("PASS: 1000000 lighting samples; %u RGB565 differences; maximum channel difference %u RGB8 / %u RGB565 step(s)\n",different,max8,max565);
}
