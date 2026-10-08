"""Executable ISA specification. Cycle count excludes OUT backpressure."""
from isa import OP
MASK = (1 << 32)-1
WIDE = (1 << 64)-1
def signed(x, n=32):
    x &= (1 << n)-1
    return x-(1 << n) if x & (1 << (n-1)) else x

def run(program, bank=None, scratch=None, limit=1000000, entry=0, compact=False):
    def context_addr(index):
        if not compact:return index
        if index<=7:return index+194
        if index<=26:return index+119
        if 50<=index<=65:return index+96
        if 80<=index<=93:return index+82
        if 100<=index<=116:return index+76
        if index==240:return 193
        raise ValueError(index)
    bank = list(bank or [0]*256)
    r = list(scratch or [0]*256)
    pc=entry; acc=rem=count=0;cycles=1; flags=[False]*4; output=[]
    while count < limit:
        word=program[pc];pc=(pc+1)&1023;count+=1
        op,d,a,b=word>>24,(word>>16)&255,(word>>8)&255,word&255
        v,u=r[a],r[b];imm=word&65535; cost=2; write=None
        if op==OP['HALT']:
            cycles+=1
            return dict(output=output,cycles=cycles,instructions=count,bank=bank,scratch=r,fault=0)
        elif op==OP['LI']:write=signed(imm,16);cost=1
        elif op==OP['LUI']:write=imm<<16;cost=1
        elif op==OP['ORI']:write=r[d]|imm
        elif op==OP['MOV']:write=v
        elif op in (OP['ADD'],OP['SUB'],OP['ADDC'],OP['SUBB'],OP['CMP']):
            sub=op in (OP['SUB'],OP['SUBB'],OP['CMP'])
            carry=flags[3] if op in (OP['ADDC'],OP['SUBB']) else sub
            full=v+(u^MASK if sub else u)+int(carry)
            flags=[v==u,signed(v)<signed(u),v<u,bool(full>>32)]
            if op!=OP['CMP']:write=full
        elif op==OP['ADDI']:write=v+signed(b,8)
        elif op==OP['AND']:write=v&u
        elif op==OP['OR']:write=v|u
        elif op==OP['XOR']:write=v^u
        elif op==OP['SHL']:write=v<<(u&31)
        elif op==OP['SHR']:write=v>>(u&31)
        elif op==OP['SAR']:write=signed(v)>>(u&31)
        elif op in (OP['MUL'],OP['MAC']):
            acc=((acc if op==OP['MAC'] else 0)+signed(v)*signed(u))&WIDE
            write=acc;cost=5
        elif op==OP['ACCLO']:write=acc
        elif op==OP['ACCHI']:write=acc>>32
        elif op==OP['ACCSAR']:write=signed(acc,64)>>(b&63)
        elif op==OP['ACCSHL']:acc=(acc<<(b&63))&WIDE
        elif op==OP['ACCSET']:acc=(v<<32)|u
        elif op==OP['ACCADD']:acc=(acc+((v<<32)|u))&WIDE
        elif op==OP['ACCSUB']:acc=(acc-((v<<32)|u))&WIDE
        elif op==OP['SAT']:write=max(-2**31,min(2**31-1,signed(acc,64)))
        elif op==OP['NEG']:write=-v
        elif op==OP['SEXT'] and b<=2:write=signed(v,(b+1)*8)
        elif op in (OP['DIVU'],OP['DIVS'],OP['DIVW']):
            n=acc if op==OP['DIVW'] else signed(v) if op==OP['DIVS'] else v
            den=((v&1)<<32)|u if op==OP['DIVW'] else signed(u) if op==OP['DIVS'] else u
            if den:
                q=abs(n)//abs(den)
                if (n<0)!=(den<0):q=-q
                rem=(n-q*den)&((1<<33)-1)
                acc=(signed(q&MASK) if op==OP['DIVS'] else q)&WIDE
                cost=67 if op==OP['DIVW'] else 35
            else:
                acc=WIDE;rem=n&MASK;cost=3
            write=acc
        elif op==OP['VERTEX']:
            # Dedicated shader retains all40bits of positive light intensity;
            # unlike generic32bit scratch arithmetic it never truncates it.
            raw=[bank[(v+i)&255] for i in range(4)]
            state=r[64:88]
            xyz=[signed(raw[0],16),signed(raw[0]>>16,16),signed(raw[1],16)]
            transformed=[signed((sum(signed(state[row*4+i])*xyz[i] for i in range(3))+
                                 (signed(state[row*4+3])<<16))>>16) for row in range(4)]
            uv=[signed((signed(raw[2]>>(i*16),16)*((state[21]>>(i*16))&65535))>>16,16)<<11
                for i in range(2)]
            color=[(raw[3]>>(8*i))&255 for i in range(4)]
            cost=39
            if state[22]&0x20000:
                intensity=max(0,sum(signed(raw[3]>>(8*i),8)*signed(state[16+i]) for i in range(3)))
                color[:3]=[min(255,((state[20]>>(8*i))&255)+
                               ((intensity*((state[19]>>(8*i))&255))>>30)) for i in range(3)]
                cost+=15+(3 if intensity>>32 else 0)
            if state[22]&0x10000:
                zmul=transformed[2]*signed(state[23],16)
                offset=signed(state[23]>>16,16);w=transformed[3]
                numerator=zmul+offset*w if w>0 else zmul*(32767 if w<0 else 1000)+(offset<<8)
                denominator=w if w>0 else 256
                color[3]=min(255,max(0,abs(numerator)//denominator*(-1 if numerator<0 else 1)))
                cost+=4 if w>0 else 6
                if numerator>0:cost+=(64 if numerator>>32 else 32)+2
            values=transformed+uv+[sum(c<<(8*i) for i,c in enumerate(color))]
            for index,value in enumerate(values):bank[(u+index)&255]=value&MASK
        elif op in (OP['MATROW'],OP['DOT3']):
            acc=sum(signed(r[(v+i)&255])*signed(r[(u+i)&255]) for i in range(3))&WIDE
            write=((acc>>16)+r[(v+3)&255]) if op==OP['MATROW'] else acc
            cost=11
        elif op==OP['NORM']:
            locations=[(v+vertex*10+i)&255 for vertex in range(3) for i in range(3)]
            shift=0
            while shift<16 and all(-(1<<28)<=signed(bank[i])<(1<<28) for i in locations):
                for i in locations:bank[i]=(bank[i]<<1)&MASK
                shift+=1
            cost=23+shift+(18 if shift else 0)
        elif op==OP['ATTR']:
            vs,vt,zi,depth,color,additive=[r[(v+i)&255] for i in range(1,7)]
            values=[(signed(vs)*signed(zi))>>16,(signed(vt)*signed(zi))>>16,zi,
                    ((color>>5)&63)<<16,((color>>11)&31)<<16,(color&31)<<16,depth]
            if r[d]:values += [((additive>>11)&31)<<16,((additive>>5)&63)<<16,(additive&31)<<16]
            for index,value in enumerate(values):bank[(u+index)&255]=value&MASK
            cost=19 if r[d] else 16
        elif op==OP['SCOPY']:
            source=[(v+i)&255 for i in range(d+1)]
            destination=[(u+i)&255 for i in range(d+1)]
            if set(source)&set(destination):
                raise ValueError('SCOPY requires disjoint modulo-256 source/destination ranges')
            values=[r[i] for i in source]
            for address,value in zip(destination,values):r[address]=value
            cost=d+4
        elif op==OP['COPY']:
            for i in range(d+1):r[(u+i)&255]=bank[(v+i)&255]
            cost=d+4
        elif op==OP['REM']:write=rem
        elif op==OP['LOAD']:write=bank[(v+signed(b,8))&255];cost=4
        elif op==OP['STORE']:bank[(v+signed(b,8))&255]=r[d];cost=3
        elif op==OP['SLOAD']:write=r[(v+signed(b,8))&255];cost=4
        elif op==OP['SSTORE']:r[(v+signed(b,8))&255]=r[d];cost=3
        elif op==OP['GATHER']:
            ordinal=v&63
            index=(r[ordinal>>1]>>((ordinal&1)*16))&32767
            vertices=[[signed(bank[7*((index>>(5*i))&31)+j]) for j in range(7)] for i in range(3)]
            masks=[]
            for vertex in vertices:
                vx,vy,vz,vw=vertex[:4]
                masks.append(sum(int(vw+sign*vertex[axis]<0)<<bit
                                 for bit,(axis,sign) in enumerate(((2,-1),(2,1),(1,-1),(1,1),(0,-1),(0,1)))))
            fault=any(not -(1<<29)<=value<=(1<<29) for vertex in vertices for value in vertex[:4])
            for i,vertex in enumerate(vertices):
                for j,value in enumerate(vertex):r[(u+7*i+j)&255]=value&MASK
            r[d]=(masks[0]|masks[1]|masks[2])|(int(fault)<<31)
            r[(d+1)&255]=masks[0]&masks[1]&masks[2]
            cost=62
        elif op in (OP['PROJ'],OP['PROJA']):
            vx,vy,vz,vw,vs,vt,rgba=[r[(v+i)&255] for i in range(7)]
            depth=(1<<38)//max(signed(vw),256);zi=depth>>6
            rx=signed((signed(vx)*zi)>>16);ry=signed((signed(vy)*zi)>>16)
            sx=max(-32768,min(32767,(signed(r[49])<<4)+((signed(r[51])*rx)>>12)))
            sy=max(-32768,min(32767,signed(r[50])-((signed(r[52])*ry)>>16)))
            color=(((rgba>>24)&255)<<16)|(((rgba>>3)&31)<<11)|(((rgba>>10)&63)<<5)|((rgba>>19)&31)
            additive=0
            if op==OP['PROJA']:
                vs=(vs+r[60])&MASK;vt=(vt+r[61])&MASK
                additive=(color&65535) if r[53]&(1<<17) else (r[54]&65535)
                if r[53]&(1<<16):color=(color&0xffff0000)|(r[55]&65535)
            values=[((sy&65535)<<16)|(sx&65535),vs,vt,zi,depth,color,additive]
            for index,value in enumerate(values):r[(u+index)&255]=value&MASK
            # 2^38 begins with k known-zero quotient bits for denominator
            # floor(log2(den))=k. The shared divider starts after those bits.
            cost=(78 if op==OP['PROJA'] else 72)-(max(signed(vw),256).bit_length()-1)
        elif op in (OP['PLANE'],OP['PLANES']):
            # Independent wide-integer contract; RTL uses two split24 DSP
            # products rather than this host's unlimited precision multiply.
            batch=op==OP['PLANES'];planes=(r[d]&15) if batch else 1
            if batch and not 1<=planes<=10:raise ValueError('PLANES count must be1..10')
            cost=5;context_loaded=False
            for plane in range(planes):
                av=[signed(bank[(i+plane)&255]) for i in (v,u,v+20 if batch else r[d])]
                dx1,dx2,dy1,dy2=[signed(r[context_addr(i)],17) for i in (100,101,102,103)]
                da1=max(-2**31,min(2**31-1,av[1]-av[0]))
                da2=max(-2**31,min(2**31-1,av[2]-av[0]))
                nu=(da1*dy2-da2*dy1)<<4
                nv=(da2*dx1-da1*dx2)<<(4 if r[context_addr(12)]&1 else 0)
                shift=31 if r[context_addr(107)]&1 else 44
                sign=-1 if r[context_addr(108)]&1 else 1
                scale=lambda n:max(-2**31,min(2**31-1,((n*(r[context_addr(106)]&0x7fffffff))>>shift)*sign))
                du,dv=scale(nu),scale(nv)
                origin=(av[0]-((du*signed(r[context_addr(21)],16))>>4)
                        -((dv*signed(r[context_addr(22)],16))>>(4 if r[context_addr(12)]&1 else 0))+(du>>1)+(dv>>1))
                constant=av[0]==av[1]==av[2]
                if batch:
                    width=32 if plane in (0,1,2,6) else 24
                    output.extend(signed(x,width)&MASK for x in (origin,du,dv))
                    cost+=12 if constant else 33
                    if not constant and not context_loaded:cost+=10;context_loaded=True
                else:
                    r[context_addr(114)],r[context_addr(115)],r[context_addr(116)]=du&MASK,dv&MASK,origin&MASK
                    cost=17 if constant else 48
        elif op==OP['EDGE']:
            x0,y0,x1,y1,x2,y2=[signed(r[context_addr(i)],16) for i in (21,22,23,24,25,26)]
            subpixel=bool(r[context_addr(12)]&1)
            scan=lambda y:(y+7)>>4 if subpixel else y
            first=max(scan(y0),signed(r[context_addr(16)],16))
            middle=scan(y1);last=min(scan(y2),signed(r[context_addr(16)]>>16,16))
            left=signed((r[context_addr(105)]<<32)|r[context_addr(104)],64)>0
            bottom=first>=middle
            r[context_addr(80)]=r[context_addr(82)]=r[context_addr(84)]=r[context_addr(91)]=r[context_addr(92)]=r[context_addr(93)]=0
            r[context_addr(86)],r[context_addr(87)],r[context_addr(88)]=first&MASK,middle&MASK,last&MASK
            r[context_addr(89)],r[context_addr(90)]=int(left),int(bottom)
            cost=28
            if first<last:
                slopes=[];divisions=0
                for dx,dy in ((x2-x0,y2-y0),(x1-x0,y1-y0),(x2-x1,y2-y1)):
                    q=0 if dy==0 else (abs(dx)<<(16 if subpixel else 12))//dy
                    slopes.append(-q if dx<0 else q);divisions+=dy!=0
                offset=lambda slope,line,y:(slope*((line<<4)+8-y))>>4 if subpixel else slope*(line-y)
                xl=(x0<<12)+offset(slopes[0],first,y0)
                xs=((x1 if bottom else x0)<<12)+offset(slopes[2 if bottom else 1],first,y1 if bottom else y0)
                mid=(x1<<12)+(offset(slopes[2],middle,y1) if subpixel and not bottom else 0)
                for addr,value in zip((80,82,84),slopes):r[context_addr(addr)]=value&MASK
                r[context_addr(91)],r[context_addr(92)]=(xl&MASK,xs&MASK) if left else (xs&MASK,xl&MASK)
                r[context_addr(93)]=mid&MASK
                cost=37+34*divisions+4*(3 if subpixel and not bottom else 2)
        elif op==OP['OUT']:output.append(v);cost=3
        elif op==OP['SETUP']:
            if not compact:return dict(output=output,cycles=cycles+2,instructions=count,bank=bank,scratch=r,fault=1)
            vertices=[(signed(r[i],16),signed(r[i]>>16,16),i) for i in (235,242,249)]
            x0,y0,_=vertices[0];x1,y1,_=vertices[1];x2,y2,_=vertices[2]
            winding=-((x1-x0)*(y2-y0)-(x2-x0)*(y1-y0))
            vertices.sort(key=lambda v:v[1])
            x0,y0,_=vertices[0];x1,y1,_=vertices[1];x2,y2,_=vertices[2]
            delta=[x1-x0,x2-x0,y1-y0,y2-y0]
            determinant=delta[0]*delta[3]-delta[1]*delta[2]
            locations=[137,138,139,140,141,142,143,144,145,176,177,178,179,180,181,199,200]
            values=[*[vertex[2] for vertex in vertices],*[xy for vertex in vertices for xy in vertex[:2]],
                    *delta,determinant,determinant>>32,winding,winding>>32]
            for location,value in zip(locations,values):r[location]=value&MASK
            cost=32
        elif op==OP['OUTBLOCK']:
            output.extend(r[(v+i)&255] for i in range(d+1))
            cost=d+4
        elif op in (OP['JMP'],OP['JAL'],OP['BR']):
            cond=[flags[0],not flags[0],flags[1],not flags[1],flags[2],not flags[2],flags[3],not flags[3]]
            if op!=OP['BR'] or cond[d&7]:
                if op==OP['JAL']:write=pc
                pc=word&1023
            cost=1
        elif op==OP['JREG']:pc=v&1023;cost=3
        else:return dict(output=output,cycles=cycles+2,instructions=count,bank=bank,scratch=r,fault=1)
        if op in (OP['LI'],OP['LUI'],OP['JAL'],OP['ORI'],OP['MOV'],OP['ADD'],OP['SUB'],OP['ADDC'],OP['SUBB'],OP['ADDI'],OP['AND'],OP['OR'],OP['XOR'],OP['SHL'],OP['SHR'],OP['SAR'],OP['ACCLO'],OP['ACCHI'],OP['ACCSAR'],OP['REM'],OP['SAT'],OP['NEG'],OP['SEXT']):cost+=1
        if write is not None:r[d]=write&MASK
        cycles+=cost
    raise RuntimeError('Microcode instruction limit exceeded')
