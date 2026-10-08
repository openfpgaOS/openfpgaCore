#!/usr/bin/env python3
"""Private full-attribute plane input, to measure a replacement frontend budget.

This is a rasterizer interface prototype, not a deployable geometry processor.
The final producer must preserve the current fixed-point derivation contract.
"""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-parallel-20261004'
SOURCE=ROOT/'docs/measurements/os30-frontend-20261004/rtl'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def rep(s,old,new):
    assert s.count(old)==1,(old[:120],s.count(old))
    return s.replace(old,new,1)


def main():
    dest=OUT/'planes-rtl'
    dest.mkdir(exist_ok=False)
    for name in ['gpu_core.v','gpu_geometry_worker.v','gpu_edge_walker.v','audio_mixer.v']:
        shutil.copy2(SOURCE/name,dest/name)
    p=dest/'gpu_core.v';s=p.read_text()
    s=rep(s, 'localparam CMD_SET_TRI_STATE          = 8\'h4A;', '''// Private geometry-to-rasterizer ABI: three Q12.4 xy words, followed by
// seven (24 words total) or ten (33 words, C+D) origin/du/dv plane triples.
// Plane order: s/w, t/w, 1/w, green, red, blue, independent depth, Dr,Dg,Db.
// Reuses the complete 0x4A material, clip, blend and subpixel state. No Q29.
// Missing/stale state or a combine/length mismatch drains without drawing.
`ifdef INCLUDE_GPU_PLANE_INPUT
localparam PLANE_INPUT=1;
`else
localparam PLANE_INPUT=0;
`endif
localparam CMD_DRAW_PLANE_RGB=8'h60;
localparam CMD_SET_TRI_STATE          = 8'h4A;''')
    s=rep(s,'reg cmd_is_draw_vert_tri_rgb;','reg cmd_is_draw_plane_rgb;\nreg cmd_is_draw_vert_tri_rgb;')
    s=rep(s,'reg [4:0] cmd_class;','localparam [4:0] CMDCLS_PLANE_RGB = 5\'d22;\nreg [4:0] cmd_class;')
    s=rep(s,'|| (INCLUDE_PARAM_TRI_RECS != 0)) ? 1 : 0;',
            '|| (INCLUDE_PARAM_TRI_RECS != 0) || PLANE_INPUT) ? 1 : 0;')
    s=rep(s,'wire cmd_is_tri_walker = cmd_is_draw_param_tri || cmd_is_draw_vert_tri',
            'wire cmd_is_tri_walker = cmd_is_draw_plane_rgb || cmd_is_draw_param_tri || cmd_is_draw_vert_tri')
    s=rep(s,'4\'d4: spanprod_next_calc = (cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb',
            '4\'d4: spanprod_next_calc = (cmd_is_draw_plane_rgb || cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb')
    s=rep(s,'wire spanprod_rgb_mode_w = cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb',
            'wire spanprod_rgb_mode_w = cmd_is_draw_plane_rgb || cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb')
    s=rep(s,"        cmd_is_draw_vert_tri_rgb <= 0;",
            "        cmd_is_draw_plane_rgb <= 0;\n        cmd_is_draw_vert_tri_rgb <= 0;")
    s=rep(s,"            cmd_is_draw_vert_tri_rgb <= 1'b0;",
            "            cmd_is_draw_plane_rgb <= 1'b0;\n            cmd_is_draw_vert_tri_rgb <= 1'b0;")
    needle='            cmd_is_draw_vert_tri <= (INCLUDE_VERT_TRI != 0) &&'
    s=rep(s,needle,'''            cmd_is_draw_plane_rgb <= PLANE_INPUT && INCLUDE_DIRECT_COLOR
                && cmd_type == CMD_DRAW_PLANE_RGB
                && (cmd_payload_words == 24 || cmd_payload_words == 33);
'''+needle)
    needle='                CMD_DRAW_VERT_TRI:\n'
    s=rep(s,needle,'''                CMD_DRAW_PLANE_RGB:
                    cmd_class <= (PLANE_INPUT && INCLUDE_DIRECT_COLOR
                        && (cmd_payload_words == 24 || cmd_payload_words == 33))
                        ? CMDCLS_PLANE_RGB : CMDCLS_NONE;
'''+needle)
    # Explicit word decode avoids a division/modulo-by-three address cone.
    payload=['            CMDCLS_PLANE_RGB: if (PLANE_INPUT) begin', '                case (pay_idx)']
    for v in range(3):
        payload.append(f"                    6'd{v}: begin tri_v{v}_x <= ring_rd_data[15:0]; tri_v{v}_y <= ring_rd_data[31:16]; end")
    rows=[1,2,3,4,8,9,10,11,12,13]
    mirrors=[None,None,'attr2','light','R','B','depth','Dr','Dg','Db']
    for attr,row in enumerate(rows):
        narrow=attr in [3,4,5,7,8,9]
        value='{{8{ring_rd_data[23]}},ring_rd_data[23:0]}' if narrow else 'ring_rd_data'
        for col,field in enumerate(['origin','du','dv']):
            statements=f"store_pl_{field}(4'd{row}, {value});"
            if mirrors[attr] and col:
                statements+=f" spanprod_{mirrors[attr]}_{field} <= ring_rd_data"+('[23:0]' if narrow else '')+';'
            payload.append(f"                    6'd{3+attr*3+col}: begin {statements} end")
    payload+=['                    default: ;','                endcase','            end\n']
    needle='            CMDCLS_SET_FB: begin\n                if (pay_idx'
    s=rep(s,needle,'\n'.join(payload)+needle)
    needle='            CMDCLS_VERT_TRI, CMDCLS_VERT_TRI_RGB: begin\n'
    s=rep(s,needle,'''            CMDCLS_PLANE_RGB: begin
                if (PLANE_INPUT && tri_state_valid && spanprod_header_supported
                    && (spanprod_cd_combine ? cmd_payload_words==33 : cmd_payload_words==24)) begin
                    spanprod_idx <= 0; spanprod_calc_step <= 0;
                    src_done <= 0; persp_active <= 0; persp_first_done <= 0;
                    persp_swap_pending <= 0; persp_pss <= PSS_IDLE;
                    persp_pass <= PSS_PASS_ANCHOR; sp_seg_left <= 0;
                    tri_clip_x0 <= tri_state_clip_x0; tri_clip_x1 <= tri_state_clip_x1;
                    tri_clip_y0 <= tri_state_clip_y0; tri_clip_y1 <= tri_state_clip_y1;
                    spanprod_attr_q29 <= 0; spanprod_q29_attr_shift <= 0;
                    spanprod_active <= 1; tri_start <= 1; tri_fill_idx <= 0;
                    state <= S_TRI_FILL;
                end else state <= S_IDLE;
            end
'''+needle)
    p.write_text(s)
    (OUT/'planes-source.json').write_text(json.dumps({str(p.relative_to(ROOT)):sha(p)
        for p in dest.iterdir()},indent=2)+'\n')
    print(dest)


if __name__=='__main__':main()
