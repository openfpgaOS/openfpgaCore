"""Phase allocator preserving indices, material and the final clipped polygon.

Kernel scratch:127..201. Projected input:232..255.
Persistent indices0..31, material32..55, polygon64..126 are untouched.
Bank0..223 holds32seven-word transformed vertices; attributes224..253.
"""
REGISTERS=[*range(27),*range(50,66),*range(80,94),*range(100,117),240]
SLOTS=[*range(194,202),*range(127,194),*range(202,232)]
REGISTER_MAP=dict(zip(REGISTERS,SLOTS))


def compact_address(old):
    return REGISTER_MAP[old]


def remap_program(program):
    # Preserve jump targets and immediate fields; relocate only RF operands.
    from isa import OP
    m=compact_address
    remapped=[]
    for value in program.words:
        op,d,a,b=value>>24,(value>>16)&255,(value>>8)&255,value&255
        if op in (OP['HALT'],OP['JMP'],OP['BR'],OP['EDGE']):pass
        elif op in (OP['LI'],OP['LUI'],OP['ORI'],OP['JAL']):
            value=(value&0xff00ffff)|(m(d)<<16)
        elif op in (OP['ADDI'],OP['LOAD'],OP['STORE'],OP['SLOAD'],OP['SSTORE'],OP['ACCSAR'],OP['ACCSHL'],OP['SEXT']):
            value=(op<<24)|(m(d)<<16)|(m(a)<<8)|b
        else:value=(op<<24)|(m(d)<<16)|(m(a)<<8)|m(b)
        remapped.append(value)
    program.words=remapped
    # JAL placeholders also need their relocated destination, while target
    # fixups stay absolute instruction addresses and are applied by finish().
    return program


RTL_FUNCTION='''
function [7:0] context_address;
    input [7:0] address;
    begin
        if(!COMPACT_LAYOUT)context_address=address;
        else if(address<=7)context_address=8'd194+address;
        else if(address<=26)context_address=8'd119+address;
        else if(address>=50&&address<=65)context_address=8'd96+address;
        else if(address>=80&&address<=93)context_address=8'd82+address;
        else if(address>=100&&address<=116)context_address=8'd76+address;
        else context_address=8'd193; // legacy link register240
    end
endfunction
'''
