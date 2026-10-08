#!/usr/bin/env python3
"""Private bit-exact fast alternative to the original transform microprogram.

Predecodes constant light channels and unrolls fixed vertex work. Full64bitfog
retains its original path; only a proven-zero high half uses32bitdivision.
"""
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'engine'))
from isa import Program
LIGHTING=0x20000
FOG=0x10000

def build_transform_fast():
    """Transform full bank in-place, descending, preserving packed indices/state.

    Inputs scratch62=vertex_count,63=triangle_count. Result bank[7*i:7*i+7].
    Scratch0..31 packed two15-bit indices/word,32..47 drawstate,64..87 loadstate.
    Data64..87 becomes dead after this phase. Scratch160..164 raw temp;
    scratch200..247 work and248..255 constants. No other bank is accessed.
    """
    p=Program();E,C,L,J=p.emit,p.const,p.label,p.jump
    for r,v in [(248,0),(249,1),(250,16),(251,31),(252,8),(253,2),(254,7)]:C(r,v)
    # Copy matrix/light/load context before the wider vertex output overwrites it.
    C(200,128);C(201,64);C(202,88)
    L('load_state');E('LOAD',203,200);E('SSTORE',203,201);E('ADDI',200,200,1);E('ADDI',201,201,1)
    E('CMP',0,201,202);J('load_state','LTU')
    C(201,32);C(202,48)
    L('draw_state');E('LOAD',203,200);E('SSTORE',203,201);E('ADDI',200,200,1);E('ADDI',201,201,1)
    E('CMP',0,201,202);J('draw_state','LTU')
    C(200,192);C(201,0);E('MOV',202,63)
    L('indices');E('CMP',0,202,248);J('indices_done','EQ')
    E('LOAD',203,200);E('ADDI',200,200,1);E('ADDI',202,202,-1)
    E('CMP',0,202,248);J('index_odd','EQ')
    E('LOAD',204,200);E('SHL',204,204,250);E('OR',203,203,204)
    E('ADDI',200,200,1);E('ADDI',202,202,-1)
    L('index_odd');E('SSTORE',203,201);E('ADDI',201,201,1);J('indices')
    L('indices_done')
    # Predecode per-job light colors once; these registers are dead after the
    # transform phase and can be reused by later clip/triangle programs.
    C(236,255);C(239,24)
    for packed,base in [(83,88),(84,91)]:
        E('AND',base,packed,236)
        E('SHR',base+1,packed,252);E('AND',base+1,base+1,236)
        E('SHR',base+2,packed,250);E('AND',base+2,base+2,236)
    E('ADDI',203,62,-1)
    L('vertex');E('SHL',202,203,253);E('MUL',201,203,254)
    E('LOAD',160,202);E('SAR',161,160,250);E('SEXT',160,160,1)
    E('LOAD',162,202,1);E('SEXT',162,162,1)
    E('LOAD',163,202,2);E('LOAD',164,202,3)
    C(200,64);C(206,160)
    L('row')
    for row in range(4):
        E('MATROW',208,200,206);E('STORE',208,201);E('ADDI',201,201,1)
        if row!=3:E('ADDI',200,200,4)
    # UV stays Q16.16 texel coordinates; raw N64ST has five fractional bits.
    C(208,11);C(209,65535)
    L('uv');E('SEXT',210,163,1);E('AND',211,85,209);E('MUL',210,210,211)
    E('ACCSAR',210,0,16);E('SEXT',210,210,1);E('SHL',210,210,208)
    E('STORE',210,201);E('ADDI',201,201,1)
    E('SAR',210,163,250);E('SHR',211,85,250);E('MUL',210,210,211)
    E('ACCSAR',210,0,16);E('SEXT',210,210,1);E('SHL',210,210,208)
    E('STORE',210,201);E('ADDI',201,201,1)
    C(205,LIGHTING);E('AND',205,86,205);E('CMP',0,205,248);J('color_done','EQ')
    E('SEXT',160,164,0);E('SHR',161,164,252);E('SEXT',161,161,0)
    E('SHR',162,164,250);E('SEXT',162,162,0);C(206,80);C(207,160);E('DOT3',210,206,207)
    E('ACCHI',206);E('ACCLO',205);E('CMP',0,206,248);J('intensity_positive','GE');C(205,0)
    L('intensity_positive');E('SHR',214,164,239)
    # Append B,G,R to preserved alpha; fixed shifts avoid a dynamic loop.
    for channel in (2,1,0):
        E('MUL',211,205,88+channel)
        E('CMP',0,205,248);J(f'unsigned_intensity_{channel}','GE')
        E('ACCADD',0,88+channel,248);L(f'unsigned_intensity_{channel}')
        E('ACCSAR',211,0,30);E('ADD',211,211,91+channel)
        E('CMP',0,211,236);J(f'channel_clamped_{channel}','LT');E('MOV',211,236)
        L(f'channel_clamped_{channel}');E('SHL',214,214,252);E('OR',214,214,211)
    E('MOV',164,214)
    L('color_done')
    C(205,FOG);E('AND',205,86,205);E('CMP',0,205,248);J('fog_done','EQ')
    E('LOAD',205,201,-4);E('LOAD',206,201,-3)
    E('SEXT',207,87,1);E('SAR',208,87,250);E('MUL',209,205,207)
    E('CMP',0,206,248);J('fog_positive_w','GE')
    C(210,32767);J('fog_bad_w')
    L('fog_positive_w');J('fog_normal_w','NE');C(210,1000)
    L('fog_bad_w');E('ACCLO',211);E('ACCHI',212);E('MUL',213,211,210)
    E('CMP',0,211,248);J('fog_unsigned_product','GE');E('ACCADD',0,210,248)
    L('fog_unsigned_product');E('ACCLO',213);E('ACCHI',214)
    E('MUL',215,212,210);E('ADD',214,214,215);E('ACCSET',0,214,213);C(206,256)
    L('fog_normal_w');E('MAC',209,208,206)
    E('ACCHI',209);E('CMP',0,209,248);J('fog_positive','GE');C(210,0);J('fog_pack')
    L('fog_positive');J('fog_wide','NE')
    E('ACCLO',210);E('DIVU',210,210,206);J('fog_compare')
    L('fog_wide');E('DIVW',210,248,206);E('ACCHI',209)
    E('CMP',0,209,248);J('fog_max','NE')
    L('fog_compare');E('CMP',0,210,236);J('fog_pack','LTU')
    L('fog_max');E('MOV',210,236)
    L('fog_pack');C(209,0x00ffffff);E('AND',164,164,209);C(211,24)
    E('SHL',210,210,211);E('OR',164,164,210)
    L('fog_done')
    E('STORE',164,201);E('ADDI',203,203,-1);E('CMP',0,203,248);J('vertex','GE')
    C(61,0);E('HALT')
    return p

def build_clip():
    """Clip one triangle in scratch64..84 to scratch64..126, preserving0..47.

    Input scratch48 is the three vertices' combined six-bit clip code. Output
    scratch49=count (zero rejects),50=64,51=status. Status1 means out-of-range
    input or polygon overflow, so callers must use an ordered fallback. Clip
    coordinates are restricted toabs<=2^29 Q24.8; this keeps signed distances
    and their positive difference within the divider's proved input range.
    Work uses192..247 plus constants248..255. Bank RAM is entirely untouched.
    """
    p=Program();E,C,L,J=p.emit,p.const,p.label,p.jump
    for r,v in [(248,0),(249,1),(250,16),(251,31),(252,8),(253,2),(254,7)]:C(r,v)
    C(192,64);C(193,128);C(194,3);C(196,0);C(222,255);C(223,(1<<30)-1)
    C(231,30);C(232,6);C(233,24);C(234,9)
    C(49,3);C(50,64);C(51,0)
    # Validate the format before emitting any result; sumdistance stays signed32.
    C(199,64);C(200,0);C(202,1<<29);C(203,-(1<<29));C(204,4)
    L('check_vertex');C(201,0)
    L('check_axis');E('SLOAD',205,199);E('CMP',0,205,202);J('range_hi','LT');J('fault','NE')
    L('range_hi');E('CMP',0,205,203);J('fault','LT');E('ADDI',199,199,1);E('ADDI',201,201,1)
    E('CMP',0,201,204);J('check_axis','LTU');E('ADDI',199,199,3);E('ADDI',200,200,1)
    C(205,3);E('CMP',0,200,205);J('check_vertex','LTU')
    E('CMP',0,48,248);J('done','EQ')
    L('plane');E('SHL',224,249,196);E('AND',224,224,48);E('CMP',0,224,248);J('next_plane','EQ')
    E('SHR',197,196,249);E('SUB',197,253,197);E('AND',198,196,249)
    E('MOV',199,192);E('MOV',214,193);C(195,0);C(201,0)
    L('edge');E('ADDI',200,199,7);E('ADDI',210,201,1)
    E('CMP',0,210,194);J('next_pointer','LTU');E('MOV',200,192)
    L('next_pointer');E('ADD',210,199,197);E('SLOAD',211,210);E('SLOAD',202,199,3)
    E('ADD',210,200,197);E('SLOAD',212,210);E('SLOAD',203,200,3)
    E('CMP',0,198,248);J('distance_plus','NE')
    E('SUB',202,202,211);E('SUB',203,203,212);J('distances')
    L('distance_plus');E('ADD',202,202,211);E('ADD',203,203,212)
    L('distances');C(204,0);C(205,0)
    E('CMP',0,248,202);J('a_outside','GE');C(204,1)
    L('a_outside');E('CMP',0,248,203);J('b_outside','GE');C(205,1)
    L('b_outside');E('CMP',0,204,248);J('copy_skipped','EQ')
    E('CMP',0,195,234);J('fault','GEU')
    E('MOV',208,199);C(215,7);J('copy_words',link=239);E('ADDI',195,195,1)
    L('copy_skipped');E('CMP',0,204,205);J('intersection_done','EQ')
    E('CMP',0,195,234);J('fault','GEU')
    E('MOV',208,199);E('MOV',209,200);E('MOV',210,202);E('MOV',211,203)
    E('CMP',0,204,248);J('inside_endpoint','NE')
    E('MOV',208,200);E('MOV',209,199);E('MOV',210,203);E('MOV',211,202)
    L('inside_endpoint');E('SUB',206,210,211)
    E('SHL',220,210,231);E('SHR',221,210,253);E('ACCSET',0,221,220)
    E('DIVW',207,248,206);C(213,0)
    L('interpolate');E('SLOAD',210,208);E('SLOAD',211,209)
    E('SAR',216,210,251);E('SAR',217,211,251)
    E('SUB',218,211,210);E('SUBB',219,217,216)
    E('SAR',216,218,251);E('SUB',219,219,216)
    E('MUL',216,218,207);E('ACCSAR',216,0,30)
    E('MUL',217,219,207);E('SHL',217,217,253);E('ADD',216,216,217)
    E('ADD',216,216,210);E('SSTORE',216,214)
    E('ADDI',208,208,1);E('ADDI',209,209,1);E('ADDI',214,214,1);E('ADDI',213,213,1)
    E('CMP',0,213,232);J('interpolate','LTU')
    E('SLOAD',210,208);E('SLOAD',211,209);C(213,0);C(221,0)
    L('color');E('AND',216,210,222);E('AND',217,211,222);E('SUB',218,217,216)
    E('MUL',219,218,207);E('ACCSAR',219,0,30);E('ACCLO',220)
    E('CMP',0,218,248);J('color_truncated','GE')
    E('AND',220,220,223);E('CMP',0,220,248);J('color_truncated','EQ');E('ADDI',219,219,1)
    L('color_truncated');E('ADD',219,219,216);E('SHL',219,219,213);E('OR',221,221,219)
    E('SHR',210,210,252);E('SHR',211,211,252);E('ADDI',213,213,8)
    E('CMP',0,213,233);J('color','LT');J('color_alpha','EQ');J('color_done')
    # Alpha runs through the same loop once, stopping at32 rather than24.
    L('color_alpha');J('color')
    L('color_done');E('SSTORE',221,214);E('ADDI',214,214,1);E('ADDI',195,195,1)
    L('intersection_done')
    E('ADDI',199,199,7);E('ADDI',201,201,1);E('CMP',0,201,194);J('edge','LTU')
    C(210,3);E('CMP',0,195,210);J('reject','LTU')
    E('MOV',194,195);E('MOV',210,192);E('MOV',192,193);E('MOV',193,210)
    L('next_plane');E('ADDI',196,196,1);E('CMP',0,196,232);J('plane','LTU')
    E('MOV',49,194);C(210,64);E('CMP',0,192,210);J('done','EQ')
    E('MOV',208,192);E('MOV',214,210);E('MUL',215,194,254);J('copy_words',link=239)
    L('done');E('HALT')
    L('reject');C(49,0);E('HALT')
    L('fault');C(49,0);C(51,1);E('HALT')
    L('copy_words');E('SLOAD',216,208);E('SSTORE',216,214)
    E('ADDI',208,208,1);E('ADDI',214,214,1);E('ADDI',215,215,-1)
    E('CMP',0,215,248);J('copy_words','NE');E('JREG',0,239)
    return p

