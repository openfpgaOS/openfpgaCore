#!/usr/bin/env python3
"""Three private, zero-cycle cuts of routed compact-v3 GPU critical cones."""
import difflib,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-paths-v11'
PARENT=P/'gpu-request-mux-v10/gpu_core.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a[:100],s.count(a))
 return s.replace(a,b)
def main():
 assert sha(PARENT)=='b67fb88a63870897cf5bae6c76a59a60d44edff2dfd15fc682dc981a9abe79ed'
 OUT.mkdir(exist_ok=False)
 old=PARENT.read_text();s=old
 start=s.index('                    // Edge deltas from the anchor (sorted slot 0).')
 end=s.index('                    // det = d1x*d2y',start)
 precompute=s[start:end]
 s=s[:start]+s[end:]
 s=rep(s,'                DRV_PROD_L0: begin\n',
  '                DRV_PROD_L0: begin : drv_xy_precompute_blk\n'+
  '                    // Final sorting completed on the preceding edge. XY\n'+
  '                    // stays fixed throughout product/normalization states.\n'+precompute)
 s=rep(s,'                DRV_DELTA: begin : drv_delta_blk',
  '                DRV_DELTA: begin')
 for signal,registered in [('e1x','dd1x'),('e2y','dd2y'),('e2x','dd2x'),('e1y','dd1y')]:
  s=rep(s,'{{15{'+signal+'[16]}}, '+signal+'}',
             '{{15{'+registered+'[16]}}, '+registered+'}')
 s=rep(s,'        ring_bram[ring_wr_addr] <= cpu_ring_write ? reg_wdata : dma_ring_wdata_raw;',
 '''        // With no command DMA, the only possible writer is the CPU.
        // Keep full/ownership qualification on WE, out of the BRAM data cone.
        ring_bram[ring_wr_addr] <= !INCLUDE_COMMAND_DMA ? reg_wdata
                                : (cpu_ring_write ? reg_wdata : dma_ring_wdata_raw);''')
 s=rep(s,"                        z_acc_addr  <= p2b_z_word_addr_w;\n",'')
 s=rep(s,'            // Always-on housekeeping (runs every non-reset cycle).',
 '''            // An invalid accumulator owns no address. Precapture the p2b
            // word without the late depth/window/pipe qualification; the
            // folded window-write can acquire it only when currently invalid.
            // Later readback/pending-source assignments retain their priority.
            if (!z_acc_valid) z_acc_addr <= p2b_z_word_addr_w;

            // Always-on housekeeping (runs every non-reset cycle).''')
 (OUT/'gpu_core.v').write_text(s)
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),
  fromfile='gpu-request-mux-v10/gpu_core.v',tofile='gpu-paths-v11/gpu_core.v')))
 (OUT/'prepared.json').write_text(json.dumps(dict(parent_sha256=sha(PARENT),
  candidate_sha256=sha(OUT/'gpu_core.v'),recipe_sha256=sha(Path(__file__)),
  scope='XY precompute in existing registers; DMA-off CPU ring direct data; unowned Z-accumulator address capture. No state, cycle or parameter changes.'),indent=2)+'\n')
 print(sha(OUT/'gpu_core.v'))
if __name__=='__main__':main()
