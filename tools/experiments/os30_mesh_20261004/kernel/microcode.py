#!/usr/bin/env python3
"""Private ISA v1 projected-triangle kernel, assembled without HDL arithmetic.

Bank words0..23 are reference.decode_inputs's wire format. Words40..95 are
working storage. Emits complete0x61 packet or no packet when Y clipped.
"""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'engine'))
from isa import Program


def build(fused=False, fused_edges=False, compact=False, fused_norm=False, return_link=None, fused_attributes=False, fused_planes=False, unroll_attributes=False, prepared_setup=False):
    assert not compact or (fused and fused_edges), "compact ABI requires both sequencers"
    assert not fused_attributes or compact, "ATTR reads projected inputs from scratch"
    assert not fused_planes or fused, "PLANES requires the shared plane sequencer"
    assert not prepared_setup or compact, "prepared SETUP uses compact register addresses"
    assert not unroll_attributes or fused_attributes, "attribute unrolling requires ATTR"
    p = Program()
    E, C, L, J = p.emit, p.const, p.label, p.jump
    input_base=232 if compact else 0
    attribute_base=224 if compact else 64
    pointer_table=232 if compact else 40
    def LD(d,a=0,b=0): E("SLOAD" if compact else "LOAD",d,a,b)
    # Shared constants. Register0 is an explicitly initialized zero.
    for reg, value in enumerate([0, 1, 4, 16, 31, 24, 28, -1, 31, 63, 0xffffff]):
        C(reg, value)
    LD(11,0,input_base); E('AND', 12, 11, 1)
    C(50, 2); E('AND', 13, 11, 50)
    C(50, 4); E('AND', 14, 11, 50)
    LD(15,0,input_base+1); LD(16,0,input_base+2)
    C(17, 7); E('CMP', 0, 14, 0); J('no_combine', 'EQ'); C(17, 10); L('no_combine')
    if not prepared_setup:
        for pointer, address, xy in [(18, 3, 21), (19, 10, 23), (20, 17, 25)]:
            C(pointer, address+input_base); LD(xy,pointer,0)
        for n, (a, b, xa, xb) in enumerate([(18,19,21,23),(19,20,23,25),(18,19,21,23)]):
            E('SAR', 50, xa, 3); E('SAR', 51, xb, 3); E('CMP', 0, 51, 50)
            J(f'sort{n}', 'GE')
            for ra, rb in [(a,b),(xa,xb)]:
                E('MOV', 52, ra); E('MOV', ra, rb); E('MOV', rb, 52)
            L(f'sort{n}')
        for xy in (21,23,25):
            E('SAR', xy+1, xy, 3); E('SEXT', xy, xy, 1)
        # Common sorted deltas and determinant.
        for d, a, b in [(100,23,21),(101,25,21),(102,24,22),(103,26,22)]: E('SUB', d,a,b)
        E('MUL', 50, 100, 103); E('ACCLO',104); E('ACCHI',105)
        E('MUL',50,101,102); E('ACCLO',50); E('ACCHI',51)
        E('SUB',104,104,50); E('SUBB',105,105,51)
    if fused_edges:
        E('EDGE'); E('CMP',0,86,88); J('halt','GE')
    else:
        E('CMP',0,105,0); J('det_negative','LT')
        E('OR',50,104,105); E('CMP',0,50,0); J('det_zero','EQ')
        C(89,1); J('det_done')
        L('det_negative'); C(89,0); J('det_done')
        L('det_zero'); C(89,0)
        L('det_done')
        # Pixel-center Y quantization and scissor bounds.
        E('MOV',86,22); E('MOV',87,24); E('MOV',88,26)
        E('CMP',0,12,0); J('integer_scan','EQ')
        for r in (86,87,88): E('ADDI',r,r,7); E('SAR',r,r,2)
        L('integer_scan'); E('SEXT',50,16,1); E('SAR',51,16,3)
        E('CMP',0,86,50); J('start_clipped','GE'); E('MOV',86,50); L('start_clipped')
        E('CMP',0,88,51); J('end_clipped','LT'); E('MOV',88,51); L('end_clipped')
        E('CMP',0,86,88); J('halt','GE')
        C(90,0); E('CMP',0,86,87); J('bottom_decided','LT'); C(90,1); L('bottom_decided')
        # slope helper takes dx50/dy51; returns low52/correction53.
        for dst, dx, dy in [(80,101,103),(82,100,102)]:
            E('MOV',50,dx); E('MOV',51,dy); J('slope',link=240)
            E('MOV',dst,52); E('MOV',dst+1,53)
        E('SUB',50,25,23); E('SUB',51,26,24); J('slope',link=240)
        E('MOV',84,52); E('MOV',85,53)
        # Prestep helper slope52/correction53, line54 and rawY55 -> offset56.
        E('MOV',52,80); E('MOV',53,81); E('MOV',54,86); E('MOV',55,22)
        J('prestep',link=240)
        # Shift constant12 is kept in temporary57.
        C(57,12); E('SHL',50,21,57); E('ADD',91,50,56)
        E('MOV',52,82); E('MOV',53,83); E('MOV',55,22); E('MOV',58,21)
        E('CMP',0,90,0); J('short_selected','EQ')
        E('MOV',52,84); E('MOV',53,85); E('MOV',55,24); E('MOV',58,23)
        L('short_selected'); E('MOV',54,86); J('prestep',link=240)
        E('SHL',50,58,57); E('ADD',92,50,56)
        E('CMP',0,89,0); J('edge_sides','NE')
        E('MOV',50,91); E('MOV',91,92); E('MOV',92,50); L('edge_sides')
        E('SHL',93,23,57); E('CMP',0,12,0); J('mid_done','EQ')
        E('CMP',0,90,0); J('mid_done','NE')
        E('MOV',52,84); E('MOV',53,85); E('MOV',54,87); E('MOV',55,24)
        J('prestep',link=240); E('ADD',93,93,56); L('mid_done')
    # Header and nine edge words may stall independently at the FIFO boundary.
    C(50,3); E('MUL',50,17,50); E('ADDI',50,50,9)
    C(51,0x61000000); E('OR',50,50,51); E('OUT',0,50)
    for r in (91,92,80,82,84,93): E('OUT',0,r)
    E('SHL',50,87,3); C(51,65535); E('AND',52,86,51); E('OR',50,50,52); E('OUT',0,50)
    C(52,18); E('SHL',50,12,52); C(52,17); E('SHL',53,89,52)
    E('OR',50,50,53); E('SHL',53,90,3); E('OR',50,50,53)
    E('AND',53,88,51); E('OR',50,50,53); E('OUT',0,50); E('OUT',0,15)
    if unroll_attributes:
        L('attributes')
        for source, address in [(18,attribute_base),(19,attribute_base+10),(20,attribute_base+20)]:
            C(61,address);E('ATTR',14,source,61)
    else:
        # Store sorted raw pointers in bank40..42. Expanded attributes occupy64..93.
        for source, address in [(18,pointer_table),(19,pointer_table+1),(20,pointer_table+2)]:
            E('SSTORE' if compact else 'STORE',source,0,address)
        C(60,pointer_table); C(61,attribute_base)
        L('attributes'); LD(62,60)
        if fused_attributes:
            E('ATTR',14,62,61)
        else:
            LD(63,62,3)
            for offset, dest in [(1,0),(2,1)]:
                LD(50,62,offset); E('MUL',50,50,63); E('ACCSAR',50,0,16); E('STORE',50,61,dest)
            E('STORE',63,61,2); LD(50,62,4); E('STORE',50,61,6)
            for input_offset, offsets in [(5,(4,3,5)),(6,(7,8,9))]:
                if input_offset==6:
                    E('CMP',0,14,0); J('attributes_no_additive','EQ')
                LD(50,62,input_offset)
                for shift, mask, target in [(11,8,offsets[0]),(5,9,offsets[1]),(0,8,offsets[2])]:
                    C(51,shift); E('SHR',52,50,51); E('AND',52,52,mask); E('SHL',52,52,3); E('STORE',52,61,target)
        L('attributes_no_additive')
        E('ADDI',60,60,1); E('ADDI',61,61,10); C(50,pointer_table+3); E('CMP',0,60,50); J('attributes','LTU')
    if fused_norm:
        E('CMP',0,13,0); J('normalized','EQ')
        C(60,attribute_base); E('NORM',0,60)
    else:
        # One magnitude-OR scan computes the identical common normalization shift.
        # For negative values XOR(sign) measures the redundant sign bits exactly;
        # abs(INT_MIN) would instead require a carry and overflow special case.
        E('CMP',0,13,0); J('normalized','EQ'); C(64,0); C(65,0)
        C(60,attribute_base); C(61,0)
        L('norm_vertex'); C(62,0)
        L('norm_attribute'); E('LOAD',50,60); E('SAR',51,50,4); E('XOR',51,50,51); E('OR',65,65,51)
        E('ADDI',60,60,1); E('ADDI',62,62,1); C(50,3); E('CMP',0,62,50); J('norm_attribute','LTU')
        E('ADDI',60,60,7); E('ADDI',61,61,1); E('CMP',0,61,50); J('norm_vertex','LTU')
        C(51,1<<28)
        L('norm_count'); E('CMP',0,65,51); J('norm_counted','GEU')
        E('CMP',0,64,3); J('norm_counted','GEU'); E('SHL',65,65,1); E('ADDI',64,64,1); J('norm_count')
        L('norm_counted'); E('CMP',0,64,0); J('normalized','EQ')
        C(60,attribute_base); C(61,0)
        L('norm_shift_vertex'); C(62,0)
        L('norm_shift_attribute'); E('LOAD',50,60); E('SHL',50,50,64); E('STORE',50,60)
        E('ADDI',60,60,1); E('ADDI',62,62,1); C(50,3); E('CMP',0,62,50); J('norm_shift_attribute','LTU')
        E('ADDI',60,60,7); E('ADDI',61,61,1); E('CMP',0,61,50); J('norm_shift_vertex','LTU')
    L('normalized')
    # Reciprocal rounded |det| divisor, floor zero to1, Q31 for small subpixel.
    C(108,0); E('CMP',0,105,0); J('absolute_det','GE'); C(108,1)
    E('SUB',104,0,104); E('SUBB',105,0,105)
    L('absolute_det'); E('OR',50,104,105); E('CMP',0,50,0); J('nonzero_det','NE'); C(104,1)
    L('nonzero_det'); C(107,0); E('CMP',0,12,0); J('recip_large','EQ')
    E('CMP',0,105,0); J('recip_large','NE'); C(50,8192); E('CMP',0,50,104); J('recip_large','LTU'); C(107,1)
    L('recip_large'); E('ACCSET',0,105,104); E('ACCSAR',50,0,1); C(51,0)
    E('CMP',0,107,0); J('numerator44','EQ'); C(52,0x80000000); E('ADD',50,50,52); J('numerator_done')
    L('numerator44'); C(51,4096)
    L('numerator_done'); E('ACCSET',0,51,50); E('DIVW',50,105,104)
    E('ACCHI',51); E('ACCLO',106); C(52,0x7fffffff)
    E('CMP',0,51,0); J('recip_clamp','NE'); E('CMP',0,52,106); J('recip_ready','GEU')
    L('recip_clamp'); E('MOV',106,52); L('recip_ready')
    if fused_planes:
        C(110,attribute_base);C(111,attribute_base+10)
        L('plane');E('PLANES',17,110,111)
    else:
        C(110,attribute_base); C(111,attribute_base+10); C(112,attribute_base+20); C(113,0)
        L('plane')
        if fused:
            E('PLANE',112,110,111)
        else:
            E('LOAD',120,110); E('LOAD',121,111); E('LOAD',122,112)
            # sat32(attributeN-attribute0), using signextended64 subtraction.
            for src, dst in [(121,123),(122,124)]:
                E('SAR',50,120,4); E('SAR',51,src,4); E('ACCSET',0,51,src)
                E('ACCSUB',0,50,120); E('SAT',dst)
            E('MUL',50,123,103); E('ACCLO',125); E('ACCHI',126)
            E('MUL',50,124,102); E('ACCLO',50); E('ACCHI',51)
            E('SUB',125,125,50); E('SUBB',126,126,51); E('ACCSET',0,126,125); E('ACCSHL',0,0,4)
            J('gradient',link=240); E('MOV',114,127)
            E('MUL',50,124,100); E('ACCLO',125); E('ACCHI',126)
            E('MUL',50,123,101); E('ACCLO',50); E('ACCHI',51)
            E('SUB',125,125,50); E('SUBB',126,126,51); E('ACCSET',0,126,125)
            E('CMP',0,12,0); J('dv_scaled','EQ'); E('ACCSHL',0,0,4); L('dv_scaled')
            J('gradient',link=240); E('MOV',115,127)
            E('MUL',50,114,21); E('ACCSAR',50,0,4); E('SUB',116,120,50)
            E('MUL',50,115,22); E('ACCSAR',50,0,0)
            E('CMP',0,12,0); J('origin_y','EQ'); E('ACCSAR',50,0,4); L('origin_y')
            E('SUB',116,116,50); E('SAR',50,114,1); E('ADD',116,116,50)
            E('SAR',50,115,1); E('ADD',116,116,50)
        C(50,3); E('CMP',0,113,50); J('plane_full','LTU')
        C(50,6); E('CMP',0,113,50); J('plane_full','EQ')
        for r in (116,114,115): E('SEXT',r,r,2)
        L('plane_full')
        for r in (116,114,115): E('OUT',0,r)
        for r in (110,111,112,113): E('ADDI',r,r,1)
        E('CMP',0,113,17); J('plane','LTU')
    L('halt')
    if return_link is None:E('HALT')
    else:E('JREG',0,return_link)
    if not fused_edges:
        # Signed33 slope encoded as low32 plus correction to signextended low32.
        L('slope'); C(52,0); C(53,0); E('CMP',0,51,0); J('slope_return','EQ')
        E('SAR',54,50,4); E('XOR',52,50,54); E('SUB',52,52,54)
        E('CMP',0,12,0); J('slope_integer','EQ'); E('SHL',52,52,3); J('slope_div')
        L('slope_integer'); C(55,12); E('SHL',52,52,55)
        L('slope_div'); E('DIVU',52,52,51)
        E('CMP',0,54,0); J('slope_positive','EQ'); E('NEG',52,52)
        L('slope_positive'); E('SAR',53,52,4); E('SUB',53,54,53)
        L('slope_return'); E('JREG',0,240)
        L('prestep'); E('CMP',0,12,0); J('prestep_integer','EQ')
        E('SHL',56,54,2); E('ADDI',56,56,8); E('SUB',56,56,55)
        E('MUL',50,52,56); E('ACCSAR',50,0,4)
        E('MUL',56,53,56); E('SHL',56,56,6); E('ADD',56,50,56); E('JREG',0,240)
        L('prestep_integer'); E('SUB',56,54,55); E('MUL',56,52,56); E('JREG',0,240)
    if not fused:
        # Exact split24 numerator*reciprocal -> signed saturated gradient.
        L('gradient'); E('ACCLO',128); E('AND',128,128,10); E('ACCSAR',129,0,24)
        E('MUL',50,129,106); E('ACCLO',130); E('ACCHI',131)
        E('MUL',50,128,106); E('ACCSAR',50,0,24); E('ACCSET',0,131,130); E('ACCADD',0,0,50)
        # Final >>20 or >>7, including high bits required before sign/saturation.
        E('CMP',0,107,0); J('gradient_large','EQ'); E('ACCSAR',50,0,7); J('gradient_shifted')
        L('gradient_large'); E('ACCSAR',50,0,20)
        L('gradient_shifted')
        # NOTE: high32 after shift isn't just sign when a gradient overflows. Use
        # original high32 >> shift as upperword of shifted64, preserve saturation.
        E('ACCHI',51); E('CMP',0,107,0); J('gradient_hi_large','EQ'); C(52,7); J('gradient_hi_shift')
        L('gradient_hi_large'); C(52,20)
        L('gradient_hi_shift'); E('SAR',51,51,52)
        E('CMP',0,108,0); J('gradient_sign','EQ'); E('SUB',50,0,50); E('SUBB',51,0,51)
        L('gradient_sign'); E('ACCSET',0,51,50); E('SAT',127); E('JREG',0,240)
    if compact:
        from register_map import remap_program
        remap_program(p)
    return p


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--fused',action='store_true')
    parser.add_argument('--edges',action='store_true')
    parser.add_argument('--norm',action='store_true')
    parser.add_argument('--attributes',action='store_true')
    parser.add_argument('--compact',action='store_true')
    parser.add_argument('--callable',action='store_true')
    args=parser.parse_args()
    p=build(args.fused,args.edges,args.compact,args.norm,240 if args.callable else None,args.attributes)
    words=p.finish()
    out=Path(__file__).resolve().parent/'program'
    out.mkdir(exist_ok=True)
    stem='triangle'
    for flag,label in [(args.fused,'fused'),(args.edges,'edges'),(args.norm,'norm'),
                       (args.compact,'compact'),(args.callable,'callable'),(args.attributes,'attributes')]:
        if flag:stem+='-'+label
    (out/(stem+'.hex')).write_text(''.join(f'{w:08x}\n' for w in words))
    (out/(stem+'.json')).write_text(json.dumps(dict(words=len(words),labels=p.labels),indent=2)+'\n')
    print(len(words),'instructions')
