#!/usr/bin/env python3
"""Private span-v7 depth-gradient relay using dead product high bits; no Quartus."""
import difflib,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'span-lookahead-v6';OUT=P/'span-lookahead-v7'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a[:80],s.count(a));return s.replace(a,b)
def main():
 old=BASE/'gpu_core.v';prep=json.loads((BASE/'prepared.json').read_text())
 assert sha(old)==prep['source_sha256']
 s=old.read_text()
 s=rep(s,'// Q29 anchor a0<<(13-sh) pre-formed in DRV_ORG_CAP', '''// In the ORG_CAP -> ORG_FORM interval only each product's low32 bits
// carry the origin term. Reuse already-live numerator high slices as a
// depth-du relay, preserving the FORM commit edge without adding registers.
wire signed [31:0] drv_depth_du_relay = {drv2_prod_r[46:32], drv_prod_r[48:32]};
// Q29 anchor a0<<(13-sh) pre-formed in DRV_ORG_CAP''')
 s=rep(s,'''                    drv_prod_r  <= dsp_p >>> 4;
                    drv2_prod_r <= spanprod_subpix_y ? (dsp2_p >>> 4) : dsp2_p;
''','''                    drv_prod_r  <= dsp_p >>> 4;
                    drv2_prod_r <= spanprod_subpix_y ? (dsp2_p >>> 4) : dsp2_p;
                    // FORM reads only product[31:0]. Both high slices were
                    // consumed by earlier numerator/scale states and are
                    // overwritten by the next capture before their next use.
                    // The later partial writes deliberately override only
                    // dead high product bits; low32 origin terms stay exact.
                    drv_prod_r[48:32] <= dv_du[16:0];
                    drv2_prod_r[46:32] <= dv_du[31:17];
''')
 s=rep(s,'spanprod_depth_du         <= dv_du;','spanprod_depth_du         <= drv_depth_du_relay;')
 OUT.mkdir(exist_ok=False);(OUT/'gpu_core.v').write_text(s)
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.read_text().splitlines(True),s.splitlines(True),fromfile='span-v6/gpu_core.v',tofile='span-v7/gpu_core.v')))
 shutil.copy2(__file__,OUT/'recipe.py')
 (OUT/'prepared.json').write_text(json.dumps(dict(private_only=True,source_sha256=sha(OUT/'gpu_core.v'),parent_sha256=sha(old),parent_prepared_sha256=sha(BASE/'prepared.json'),recipe_sha256=sha(Path(__file__)),declared_added_flip_flops=0,ram_added=0,dsp_added=0,state_cycles_changed=False,quartus_run=False,deployed=False),indent=2)+'\n')
 print(json.dumps(json.loads((OUT/'prepared.json').read_text()),indent=2))
if __name__=='__main__':main()
