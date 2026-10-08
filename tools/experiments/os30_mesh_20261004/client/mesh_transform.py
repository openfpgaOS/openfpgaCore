#!/usr/bin/env python3
"""Private fixed-point mesh transform/lighting/clipping reference and ISA producer.

Float comparisons reconstruct source semantics for sensitivity only; they are
not captured CPU instruction results or proof of color/depth equality.
"""
from pathlib import Path
import argparse,collections,json,math,struct,sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'engine'))
from isa import Program
from emulator import run
from analyze import OUT,read,signed

LIGHTING=0x20000
FOG=0x10000
MASK=(1<<32)-1
PLANES=((2,-1),(2,1),(1,-1),(1,1),(0,-1),(0,1))
def word(v):return v&MASK
def f32(v):return struct.unpack('<f',struct.pack('<f',v))[0]
def asfloat(v):return struct.unpack('<f',struct.pack('<I',v))[0]
def truncdiv(a,b):
    if not b:raise ZeroDivisionError
    q=abs(a)//abs(b);return -q if (a<0)!=(b<0) else q
def clamp(v):return min(255,max(0,v))
def components(rgba):return [(rgba>>(k*8))&255 for k in range(4)]
def colorpack(c):return sum((int(v)&255)<<(8*k) for k,v in enumerate(c))

def transform_vertex(raw,state,*,fraction=8):
    """Seven persistent words: clipx/y/z/w, S/T Q16.16 texels, RGBA8."""
    pos=[signed(raw[0],16),signed(raw[0]>>16,16),signed(raw[1],16)]
    q=[]
    for row in range(4):
        acc=sum(signed(state[row*4+k])*pos[k] for k in range(3))+(signed(state[row*4+3])<<16)
        q.append(signed(acc>>(24-fraction)))
    uv=[signed((signed(raw[2]>>(k*16),16)*((state[21]>>(k*16))&65535))>>16,16)<<11 for k in range(2)]
    rgba=components(raw[3])
    if state[22]&LIGHTING:
        intensity=max(0,sum(signed(raw[3]>>(8*k),8)*signed(state[16+k]) for k in range(3)))
        for k in range(3):rgba[k]=clamp(((state[20]>>(8*k))&255)+((intensity*((state[19]>>(8*k))&255))>>30))
    if state[22]&FOG:
        mul,offset=signed(state[23],16),signed(state[23]>>16,16)
        if q[3]>0:fog=truncdiv(q[2]*mul+offset*q[3],q[3])
        elif q[3]<0:fog=truncdiv(q[2]*32767*mul+(offset<<fraction),1<<fraction)
        else:fog=truncdiv(q[2]*1000*mul+(offset<<fraction),1<<fraction)
        rgba[3]=clamp(fog)
    return q+uv+[colorpack(rgba)]

def float_vertex(raw,state):
    pos=[signed(raw[0],16),signed(raw[0]>>16,16),signed(raw[1],16)]
    q=[]
    for row in range(4):
        m=[asfloat(x) for x in state[40+row*4:44+row*4]]
        value=f32(f32(f32(f32(pos[0]*m[0])+f32(pos[1]*m[1]))+f32(pos[2]*m[2]))+m[3])
        if row==0:value=f32(value*asfloat(state[56]))
        q.append(value)
    fixed=transform_vertex(raw,state)
    return q+[f32(x/65536) for x in fixed[4:6]]+[fixed[6]]

def clip_code(v):
    return sum(int(v[3]+sign*v[axis]<0)<<i for i,(axis,sign) in enumerate(PLANES))

def clip_polygon(vertices,*,floating=False,plane_mask=63):
    """Mirror CPU plane order, strict inside predicate and RGBA truncation.

    Fixed intersection uses aQ30 fraction and signed wide intermediates.
    Coordinates/S/T round down; signed color increments truncate toward zero.
    This is deterministic but is NOT claimed float-byte-exact.
    """
    codes=[clip_code(v)&plane_mask for v in vertices]
    if codes[0]&codes[1]&codes[2]:return []
    clip_or=codes[0]|codes[1]|codes[2]
    result=[list(v) for v in vertices]
    for plane,(axis,sign) in enumerate(PLANES):
        if not (clip_or>>plane)&1:continue
        output=[]
        for i,a in enumerate(result):
            b=result[(i+1)%len(result)]
            da=a[3]+sign*a[axis];db=b[3]+sign*b[axis]
            if floating:da,db=f32(da),f32(db)
            inside_a,inside_b=da>0,db>0
            if inside_a:output.append(a[:])
            if inside_a!=inside_b:
                v0,v1,d0,d1=(a,b,da,db) if inside_a else (b,a,db,da)
                if floating:
                    t=f32(d0/f32(d0-d1))
                    vertex=[f32(v0[k]+f32(t*f32(v1[k]-v0[k]))) for k in range(6)]
                    color=[c0+int(f32(t*(c1-c0))) for c0,c1 in zip(components(v0[6]),components(v1[6]))]
                else:
                    t=(d0<<30)//(d0-d1)
                    vertex=[signed(v0[k]+(((v1[k]-v0[k])*t)>>30)) for k in range(6)]
                    color=[c0+truncdiv((c1-c0)*t,1<<30) for c0,c1 in zip(components(v0[6]),components(v1[6]))]
                vertex.append(colorpack(color));output.append(vertex)
        if len(output)<3:return []
        assert len(output)<=9,len(output)
        result=output
    return result

def project(v,state):
    """Current RAW worker projection, depth and zi arithmetic, pre-material."""
    x,y,_,w,s,t,color=v
    denominator=max(256,w)
    depth=(1<<38)//denominator
    zi=(depth&MASK)>>6
    vx,vy=state[28]&65535,state[28]>>16
    width,height=state[29]&65535,state[29]>>16
    hw,hh=width//2,height//2;cx,cy=vx+hw,240-vy-height+hh
    rx=signed((x*zi)>>16);ry=signed((y*zi)>>16)
    sx=min(32767,max(-32768,(cx<<4)+((hw*rx)>>12)))
    sy=min(32767,max(-32768,(cy<<4)-(((hh<<4)*ry)>>16)))
    return sx,sy,s,t,zi,depth&MASK,color

def build_transform():
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
    L('indices_done');E('ADDI',203,62,-1)
    L('vertex');E('SHL',202,203,253);E('MUL',201,203,254)
    E('LOAD',160,202);E('SAR',161,160,250);E('SEXT',160,160,1)
    E('LOAD',162,202,1);E('SEXT',162,162,1)
    E('LOAD',163,202,2);E('LOAD',164,202,3)
    C(200,64);C(205,0);C(206,160)
    L('row');E('MATROW',208,200,206);E('ADDI',200,200,4)
    E('STORE',208,201);E('ADDI',201,201,1)
    E('ADDI',205,205,1);C(208,4);E('CMP',0,205,208);J('row','LTU')
    # UV stays Q16.16 texel coordinates; raw N64ST has five fractional bits.
    E('MOV',205,163);E('MOV',206,85);C(207,0);C(208,11);C(209,65535)
    L('uv');E('SEXT',210,205,1);E('AND',211,206,209);E('MUL',210,210,211)
    E('ACCSAR',210,0,16);E('SEXT',210,210,1);E('SHL',210,210,208);E('STORE',210,201)
    E('SHR',205,205,250);E('SHR',206,206,250);E('ADDI',201,201,1)
    E('ADDI',207,207,1);E('CMP',0,207,253);J('uv','LTU')
    C(205,LIGHTING);E('AND',205,86,205);E('CMP',0,205,248);J('color_done','EQ')
    E('SEXT',160,164,0);E('SHR',161,164,252);E('SEXT',161,161,0)
    E('SHR',162,164,250);E('SEXT',162,162,0);C(206,80);C(207,160);E('DOT3',210,206,207)
    E('ACCHI',206);E('ACCLO',205);E('CMP',0,206,248);J('intensity_positive','GE');C(205,0)
    L('intensity_positive');E('MOV',206,83);E('MOV',207,84);C(208,0);C(212,255)
    C(214,0xff000000);E('AND',214,164,214)
    L('channel');E('AND',209,206,212);E('AND',210,207,212);E('MUL',211,205,209)
    E('CMP',0,205,248);J('unsigned_intensity','GE');E('ACCADD',0,209,248)
    L('unsigned_intensity');E('ACCSAR',211,0,30);E('ADD',211,211,210)
    E('CMP',0,211,212);J('channel_clamped','LT');E('MOV',211,212)
    L('channel_clamped');E('SHL',211,211,208);E('OR',214,214,211)
    E('SHR',206,206,252);E('SHR',207,207,252);E('ADDI',208,208,8)
    C(213,24);E('CMP',0,208,213);J('channel','LTU');E('MOV',164,214)
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
    L('fog_positive');E('DIVW',210,248,206);E('ACCHI',209);C(211,255)
    E('CMP',0,209,248);J('fog_max','NE');E('CMP',0,210,211);J('fog_pack','LTU')
    L('fog_max');E('MOV',210,211)
    L('fog_pack');C(209,0x00ffffff);E('AND',164,164,209);C(211,24)
    E('SHL',210,210,211);E('OR',164,164,210)
    L('fog_done')
    E('STORE',164,201);E('ADDI',203,203,-1);E('CMP',0,203,248);J('vertex','GE')
    C(61,0);E('HALT')
    return p

def build_clip(*, prevalidated=False, fused_copy=False):
    """Clip one triangle in scratch64..84 to scratch64..126, preserving0..47.

    Input scratch48 is the three vertices' combined six-bit clip code. Output
    scratch49=count (zero rejects),50=64,51=status. Status1 means out-of-range
    input or polygon overflow, so callers must use an ordered fallback. Clip
    coordinates are restricted toabs<=2^29 Q24.8; this keeps signed distances
    and their positive difference within the divider's proved input range.
    Work uses192..247 plus constants248..255. Bank RAM is entirely untouched.
    prevalidated=True requires GATHER's range check on all three input vertices;
    convex interpolation cannot enlarge the coordinate range. The standalone
    entry retains its own validation by default. fused_copy uses SCOPY53 on
    disjoint polygon buffers, in complete seven-word vertex chunks.
    """
    p=Program();E,C,L,J=p.emit,p.const,p.label,p.jump
    for r,v in [(248,0),(249,1),(250,16),(251,31),(252,8),(253,2),(254,7)]:C(r,v)
    C(192,64);C(193,128);C(194,3);C(196,0);C(222,255);C(223,(1<<30)-1)
    C(231,30);C(232,6);C(233,24);C(234,9)
    C(49,3);C(50,64);C(51,0)
    # Validate the format before emitting any result; sumdistance stays signed32.
    if not prevalidated:
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
    L('copy_words')
    if fused_copy:E('SCOPY',6,208,214)
    else:E('SLOAD',216,208);E('SSTORE',216,214)
    step=7 if fused_copy else 1
    E('ADDI',208,208,step);E('ADDI',214,214,step);E('ADDI',215,215,-step)
    E('CMP',0,215,248);J('copy_words','NE');E('JREG',0,239)
    return p

def main():
    p=argparse.ArgumentParser();p.add_argument('run',nargs='?',default='run-360-8');p.add_argument('--limit',type=int,default=0)
    a=p.parse_args();dest=OUT/a.run
    jobs=[j for path in sorted(dest.glob('mesh-*.bin')) for j in read(path)[1]]
    if a.limit:jobs=jobs[:a.limit]
    program=build_transform().finish();counts=collections.Counter();max_error=0.0;maxpolygon=0
    for j in jobs:
        s=j['state'];raw=j['vertices'];fixed=[];floats=[]
        for i in range(j['header'][3]):
            fv=transform_vertex(raw[4*i:4*i+4],s);fl=float_vertex(raw[4*i:4*i+4],s)
            fixed.append(fv);floats.append(fl);counts['vertices']+=1
            if clip_code(fv)!=clip_code(fl):counts['clip_code_differences']+=1
            if fv[3]>256 and fl[3]>1:
                for axis in (0,1):
                    err=abs(fv[axis]/fv[3]-fl[axis]/fl[3])*160
                    max_error=max(max_error,err)
        if True:
            bank=[0]*256;bank[:len(raw)]=raw;bank[128:192]=s;bank[192:192+len(j['indices'])]=j['indices']
            scratch=[0]*256;scratch[62],scratch[63]=j['header'][3:5]
            result=run(program,bank,scratch)
            assert not result['fault'] and not result['scratch'][61]
            assert result['bank'][:7*len(fixed)]==[word(x) for v in fixed for x in v]
            packed=[j['indices'][i]|((j['indices'][i+1] if i+1<len(j['indices']) else 0)<<16) for i in range(0,len(j['indices']),2)]
            assert result['scratch'][:len(packed)]==packed
            assert result['scratch'][32:48]==s[24:40]
            counts['microcode_jobs']+=1;counts['microcode_cycles']+=result['cycles']
        for index in j['indices']:
            slots=[(index>>shift)&31 for shift in (0,5,10)]
            fp=clip_polygon([fixed[i] for i in slots]);fl=clip_polygon([floats[i] for i in slots],floating=True)
            maxpolygon=max(maxpolygon,len(fp));counts['triangles']+=1
            if len(fp)!=len(fl):counts['clip_topology_differences']+=1
            elif fp:
                for x,y in zip(fp,fl):
                    if x[6]!=y[6]:counts['clipped_rgba_differences']+=1
    result=dict(scope=__doc__,instructions=len(program),counts=dict(counts),max_polygon_vertices=maxpolygon,
                max_fixed_float_projected_delta_px=max_error,
                limitation='This test covers the generic transform ISA. check_clip.py and check_mesh_rtl.py separately exercise clipping and the integrated fixed-function shader. Float reconstruction shares fixed lighting and is not an actual CPU instruction trace.')
    (dest/'transform.json').write_text(json.dumps(result,indent=2)+'\n')
    (dest/'transform.hex').write_text(''.join(f'{w:08x}\n' for w in program))
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
