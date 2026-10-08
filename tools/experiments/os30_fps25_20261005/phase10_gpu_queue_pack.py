#!/usr/bin/env python3
"""Private v8 GPU queue storage edits, separately attributable; no fitter."""
import difflib,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'span-lookahead-v6/gpu_core.v';OUT=P/'gpu-queue-pack-v8'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b,n=1):
 assert s.count(a)==n,(a[:80],s.count(a),n)
 return s.replace(a,b)
def patch(a,b,first,last):
 return ''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile=first,tofile=last))
def prepare():
 s=BASE.read_text();OUT.mkdir(exist_ok=False)
 t=rep(s,'reg [31:0] fbwq_data [0:FBWQ_DEPTH-1];\n(* ramstyle = "MLAB, no_rw_check" *) reg [3:0]  fbwq_strb [0:FBWQ_DEPTH-1];',
       '(* ramstyle = "M10K, no_rw_check" *) reg [35:0] fbwq_payload [0:FBWQ_DEPTH-1];')
 t=rep(t,'m_wr_wdata   <= fbwq_data[fbwq_rd_ptr];\n                m_wr_wstrb   <= fbwq_strb[fbwq_rd_ptr];',
       '{m_wr_wstrb,m_wr_wdata} <= fbwq_payload[fbwq_rd_ptr];',3)
 for source in ['req','stage']:
  t=rep(t,f'fbwq_data[fbwq_wr_ptr] <= fbwq_{source}_data;\n                fbwq_strb[fbwq_wr_ptr] <= fbwq_{source}_strb;',
       f'fbwq_payload[fbwq_wr_ptr] <= {{fbwq_{source}_strb,fbwq_{source}_data}};')
 (OUT/'01-packed-payload.v').write_text(t)
 (OUT/'01-packed-payload.patch').write_text(patch(s,t,'span-v6/gpu_core.v','01-packed-payload.v'))
 line="                m_wr_awaddr  <= {{(32-GPU_ADDR_W){1'b0}}, fbwq_addr[fbwq_rd_ptr]};\n"
 u=rep(t,line,'',2)
 anchor='            if (fbwq_start_now) begin\n'
 u=rep(u,anchor,'            // Both AW paths read the same head and preserve the same clock edge.\n'
   '            // One syntactic read prevents needless duplication of address RAM.\n'
   '            if (fbwq_start_now || fbwq_aw_ahead_issue)\n'+line+'\n'+anchor)
 (OUT/'02-common-aw.patch').write_text(patch(t,u,'01-packed-payload.v','gpu_core.v'))
 (OUT/'gpu_core.v').write_text(u)
 (OUT/'candidate.patch').write_text(patch(s,u,'span-v6/gpu_core.v','gpu-v8/gpu_core.v'))
 (OUT/'prepared.json').write_text(json.dumps(dict(parent_sha256=sha(BASE),candidate_sha256=sha(OUT/'gpu_core.v'),
  payload_only_sha256=sha(OUT/'01-packed-payload.v'),recipe_sha256=sha(Path(__file__)),
  state_arithmetic_protocol_cycles_unchanged=True,full_fit_authorized=False),indent=2)+'\n')
 print(sha(OUT/'gpu_core.v'))
if __name__=='__main__':globals()[sys.argv[1]]()
