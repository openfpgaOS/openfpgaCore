/*
 * Combined scanout->Analogizer cart-pin observer (VGA / scandoubler mode).
 *
 * Pin mapping (from openFPGA_Pocket_Analogizer.v):
 *   bank3 = {Rout[5:0], HsyncOut, VsyncOut}  -> hsync=bank3[1], vsync=bank3[0]
 *   bank2 = {Bout[0], BLANKnOut, Gout[5:0]}
 *   bank1 = {snac[7:6], cart_video_clk, Bout[5:1]} -> DAC clock = bank1[5]
 *
 * Checks the cart pins actually carry a valid VGA signal: hsync ~31.5 kHz,
 * vsync ~60 Hz, DAC clock toggling, RGB present. If all true, the RTL logic
 * is fine and a hardware "no signal" is a HW/clock/SDC/adapter issue.
 */
#include <cstdio>
#include <vector>
#include <algorithm>
#include "Vtb_analogizer_chain.h"
#include "verilated.h"

static Vtb_analogizer_chain *tb;
static uint64_t cyc = 0;
static void tick() { tb->clk=0; tb->eval(); tb->clk=1; tb->eval(); cyc++; }

static long med(std::vector<uint64_t>&ts){ if(ts.size()<4)return -1; std::vector<long>d;
    for(size_t i=1;i<ts.size();i++)d.push_back((long)(ts[i]-ts[i-1])); std::sort(d.begin(),d.end()); return d[d.size()/2]; }

int main(int argc,char**argv){
    Verilated::commandArgs(argc,argv);
    tb=new Vtb_analogizer_chain;
    const double CLK=49.152e6;

    tb->reset_n=0; for(int i=0;i<60;i++)tick(); tb->reset_n=1; for(int i=0;i<10;i++)tick();

    std::vector<uint64_t> hs,vs;
    int phs=-1,pvs=-1; long dac_tr=0; int pclk=-1; int maxrgb=0; long rgb_samples=0,rgb_nz=0;
    const uint64_t RUN=6000000ULL;
    for(uint64_t i=0;i<RUN;i++){
        tick();
        int b1=tb->cart_bank1, b2=tb->cart_bank2, b3=tb->cart_bank3;
        int hsync=(b3>>1)&1, vsync=b3&1, dclk=(b1>>5)&1, blankn=(b2>>6)&1;
        int R=(b3>>2)&0x3F, G=b2&0x3F, B=((b2>>7)&1)<<5 | (b1&0x1F);
        if(phs==0&&hsync==1) hs.push_back(cyc);
        if(pvs==0&&vsync==1) vs.push_back(cyc);
        if(pclk!=dclk && pclk!=-1) dac_tr++;
        if(blankn){ rgb_samples++; if(R|G|B) rgb_nz++; if((R|G|B)>maxrgb) maxrgb=(R|G|B); }
        phs=hsync; pvs=vsync; pclk=dclk;
    }

    long hsc=med(hs), vsc=med(vs);
    printf("=== Analogizer cart-pin output (VGA mode 5) ===\n");
    printf("HSYNC (bank3[1]) edges=%zu, period=%ld clk -> %.2f kHz\n", hs.size(), hsc, hsc>0?CLK/hsc/1000.0:0);
    printf("VSYNC (bank3[0]) edges=%zu, period=%ld clk -> %.2f Hz\n", vs.size(), vsc, vsc>0?CLK/vsc:0);
    printf("DAC clock (bank1[5]) transitions=%ld (in %lluM clk) -> %s\n",
           dac_tr,(unsigned long long)(RUN/1000000), dac_tr>1000000?"TOGGLING":"STUCK");
    printf("RGB during active: %ld/%ld samples nonzero, max=%d -> %s\n",
           rgb_nz, rgb_samples, maxrgb, rgb_nz>0?"PRESENT":"ABSENT");

    bool hsync_ok = hsc>0 && CLK/hsc/1000.0>28 && CLK/hsc/1000.0<35;   // ~31.5 kHz
    bool vsync_ok = vsc>0 && CLK/vsc>55 && CLK/vsc<65;                  // ~60 Hz
    bool dac_ok   = dac_tr>1000000;
    bool rgb_ok   = rgb_nz>0;
    bool ok = hsync_ok && vsync_ok && dac_ok && rgb_ok;
    printf("\nCART PINS: hsync=%s vsync=%s dacclk=%s rgb=%s\n",
           hsync_ok?"OK":"BAD", vsync_ok?"OK":"BAD", dac_ok?"OK":"BAD", rgb_ok?"OK":"BAD");
    printf("RESULT: %s\n", ok?"LOGIC OK (valid VGA at cart pins) => HW issue":"LOGIC BUG (cart pins invalid)");
    delete tb; return ok?0:1;
}
