#!/usr/bin/env python3
"""Replace edge setup as well as plane setup in the private raster backend."""
import hashlib
import json
from pathlib import Path
import shutil
import re

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-parallel-20261004'


def main():
    dest=OUT/'edges-rtl';shutil.copytree(OUT/'planes-rtl',dest)
    shutil.copy2(HERE/'gpu_span_walker.v',dest/'gpu_span_walker.v')
    p=dest/'gpu_core.v';s=p.read_text()
    s=s.replace("CMD_DRAW_PLANE_RGB=8'h60", "CMD_DRAW_PLANE_RGB=8'h61")
    # The private ABI replaces three xy words with nine prepared-edge words.
    for old,new in [(24,30),(33,39)]:
        s=re.sub(r'(cmd_payload_words\s*==\s*)'+str(old)+r'\b',r'\g<1>'+str(new),s)
    lo=s.index('            CMDCLS_PLANE_RGB: if (PLANE_INPUT) begin')
    hi=s.index('            CMDCLS_SET_FB: begin',lo)
    block=s[lo:hi]
    block=re.sub(r"^.*6'd[012]: begin tri_v[012]_x.*\n",'',block,flags=re.M)
    block=re.sub(r"6'd(\d+):",lambda m:f"6'd{int(m[1])+6}:",block)
    s=s[:lo]+block+s[hi:]
    needle='if (INCLUDE_TRI_WALKER != 0) begin : g_tri_walker'
    assert s.count(needle)==1
    s=s.replace(needle,'''if (PLANE_INPUT) begin : g_prepared_walker
    gpu_span_walker tri_walker (
        .clk(clk), .reset_n(reset_n), .abort(soft_reset), .start(tri_start),
        .load(state == S_PAY_DATA && cmd_class == CMDCLS_PLANE_RGB && pay_idx < 9),
        .load_index(pay_idx[3:0]), .load_data(ring_rd_data),
        .busy(tri_busy), .rec_valid(tri_rec_valid), .rec_u(tri_rec_u),
        .rec_v(tri_rec_v), .rec_count(tri_rec_count), .rec_ready(tri_rec_ready)
    );
end else '''+needle)
    s=s.replace('// Private geometry-to-rasterizer ABI: three Q12.4 xy words, followed by\n'
                '// seven (24 words total) or ten (33 words, C+D) origin/du/dv plane triples.',
                '// Private geometry-to-rasterizer ABI: nine prepared-edge words, followed by\n'
                '// seven (30 words total) or ten (39 words, C+D) origin/du/dv plane triples.')
    p.write_text(s)
    (OUT/'edges-source.json').write_text(json.dumps({str(p.relative_to(ROOT)):
        hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir()},indent=2)+'\n')
    print(dest)


if __name__=='__main__':main()
