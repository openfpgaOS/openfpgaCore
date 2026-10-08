#!/usr/bin/env python3
"""Private whole-mesh microcode driver: explicit material ABI, no game enablement.

State0..23: transform/light/UV/fog;24..40: exact17word0x4A material;
41..44: xc,ycQ4,xs,ysQ4;45..46: clipx/y;47:flags (subpixel/normalize/
combine low3, front/back cull bits8/9, constantC bit16, shadeD bit17,
shadealpha bit18);48..49: UV offsets;50:additiveRGB565;51:constantRGB565.
This explicit producer format differs from shadow capture's diagnostic state.
No synthetic conversion of shadow material selectors is treated as real state.
"""
from pathlib import Path
import argparse,json,sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'engine'))
sys.path.insert(0,str(HERE.parent/'kernel'))
from isa import Program,OP,ins
from microcode import build as triangle_program
from mesh_transform import build_transform,build_clip

def remap(program,mapping):
    result=[]
    for w in program.words:
        op,d,a,b=w>>24,(w>>16)&255,(w>>8)&255,w&255
        m=lambda r:mapping.get(r,r)
        if op in (OP['HALT'],OP['JMP'],OP['BR']):pass
        elif op in (OP['LI'],OP['LUI'],OP['ORI'],OP['JAL']):w=(w&0xff00ffff)|(m(d)<<16)
        elif op in (OP['ADDI'],OP['LOAD'],OP['STORE'],OP['SLOAD'],OP['SSTORE'],OP['ACCSAR'],OP['ACCSHL'],OP['SEXT']):
            w=(op<<24)|(m(d)<<16)|(m(a)<<8)|b
        elif op in (OP['COPY'],OP['SCOPY']):w=(op<<24)|(d<<16)|(m(a)<<8)|m(b)
        else:w=(op<<24)|(m(d)<<16)|(m(a)<<8)|m(b)
        result.append(w)
    program.words=result;return program

def append(parent,child,prefix,halt_target=None):
    offset=len(parent.words)
    for pc,w in enumerate(child.words):
        if w>>24==OP['HALT'] and halt_target is not None:
            parent.fixups.append((offset+pc,halt_target));w=ins('JMP')
        parent.words.append(w)
    for label,pc in child.labels.items():parent.labels[prefix+label]=offset+pc
    for pc,label in child.fixups:parent.fixups.append((offset+pc,prefix+label))

def build(*, fused_copy=True, unroll_project=True, outblock=True, fused_planes=True, unroll_attributes=True, fused_setup=True, honor_guardband=True):
    p=Program();E,C,L,J=p.emit,p.const,p.label,p.jump
    # All game-specific state must be concretely resolved before this entry.
    C(224,0);C(229,2);C(230,7)
    C(192,152);C(193,32);E('COPY',16,192,193)
    J('material_emit',link=231)
    C(192,169);C(193,49);E('COPY',3,192,193)
    C(192,175);E('LOAD',53,192);E('LOAD',60,192,1);E('LOAD',61,192,2)
    E('LOAD',54,192,3);E('LOAD',55,192,4)
    J('transform_entry')
    L('transformed');E('MOV',57,63);C(56,0)
    L('triangle');E('CMP',0,56,57);J('done','GEU')
    C(224,0)
    C(192,64);E('GATHER',62,56,192)
    E('CMP',0,62,224);J('fault','LT') # malformed coordinate range
    if honor_guardband:
        C(192,1<<19);E('AND',192,53,192);E('CMP',0,192,224);J('clip_masks_ready','EQ')
        C(192,3);E('AND',62,62,192);E('AND',63,63,192)
        L('clip_masks_ready')
    E('CMP',0,63,224);J('next_triangle','NE');C(59,3)
    E('CMP',0,62,224);J('clipped','EQ');J('clip_entry')
    L('clip_return');C(224,0);E('CMP',0,63,224);J('fault','NE')
    L('clipped');C(58,1)
    L('fan');C(224,0);C(226,16);C(230,7)
    E('ADDI',192,58,1);E('CMP',0,192,59);J('next_triangle','GEU')
    # Kernel inputs are separate from the persistent final clipped polygon.
    E('AND',232,53,230);E('MOV',233,46);E('MOV',234,47)
    if unroll_project:
        C(193,235);C(194,64)
        L('project');E('PROJA',0,194,193)
        E('MUL',194,58,230);E('ADDI',194,194,64);C(193,242);E('PROJA',0,194,193)
        E('ADDI',194,194,7);C(193,249);E('PROJA',0,194,193)
    else:
        C(192,0);C(193,235);C(194,64)
        L('project');E('PROJA',0,194,193)
        E('ADDI',193,193,7);E('ADDI',192,192,1);C(195,3);E('CMP',0,192,195);J('projected','GEU')
        E('ADD',195,58,192);E('ADDI',195,195,-1);E('MUL',195,195,230);E('ADDI',194,195,64);J('project')
    L('projected')
    # Projected winding sign, preserving the original face order. y increases
    # downwards, so this sign matches the raw worker's projected orientation.
    if fused_setup:E('SETUP')
    else:
        E('SAR',192,235,226);E('SAR',193,242,226);E('SAR',194,249,226)
        E('SEXT',195,235,1);E('SEXT',196,242,1);E('SEXT',197,249,1)
        E('SUB',192,192,193);E('SUB',194,194,193);E('SUB',195,195,196);E('SUB',197,197,196)
        E('MUL',198,195,194);E('ACCLO',199);E('ACCHI',200)
        E('MUL',198,192,197);E('ACCLO',201);E('ACCHI',202)
        E('SUB',199,199,201);E('SUBB',200,200,202)
    C(192,0x300);E('AND',192,53,192);E('CMP',0,192,224);J('emit','EQ')
    C(193,0x300);E('CMP',0,192,193);J('next_fan','EQ')
    E('OR',193,199,200);E('CMP',0,193,224);J('next_fan','EQ')
    C(193,0x100);E('CMP',0,192,193);J('cull_back','NE')
    E('CMP',0,200,224);J('next_fan','GE');J('emit')
    L('cull_back');E('CMP',0,200,224);J('next_fan','LT')
    L('emit');C(192,0x40000);E('AND',192,53,192);E('CMP',0,192,224);J('alpha_ready','EQ')
    E('SHR',192,240,226);C(194,255);E('AND',192,192,194)
    E('CMP',0,192,48);J('alpha_ready','EQ');E('MOV',48,192);J('material_emit',link=231)
    L('alpha_ready');J('kernel_entry',link=193)
    L('next_fan');E('ADDI',58,58,1);J('fan')
    L('next_triangle');E('ADDI',56,56,1);J('triangle')
    L('done');C(63,0);E('HALT')
    L('fault');C(63,1);p.words.append(0xff000000)
    L('material_emit');C(192,32)
    if not outblock:C(194,49)
    C(193,0x4a000011);E('OUT',0,193)
    L('material_word')
    if outblock:E('OUTBLOCK',16,192)
    else:
        E('SLOAD',193,192);E('OUT',0,193);E('ADDI',192,192,1)
        E('CMP',0,192,194);J('material_word','LTU')
    E('JREG',0,231)
    L('transform_entry')
    C(192,128);C(193,64);E('COPY',23,192,193)
    C(192,192);C(193,0);E('COPY',31,192,193)
    E('ADDI',194,62,-1)
    L('vertex_shader');E('SHL',192,194,229);E('MUL',193,194,230)
    E('VERTEX',0,192,193);E('ADDI',194,194,-1);E('CMP',0,194,224);J('vertex_shader','GE')
    J('transformed')
    L('clip_entry');append(p,remap(build_clip(prevalidated=True,fused_copy=fused_copy),{48:62,49:59,50:58,51:63}),'clip_',halt_target='clip_return')
    L('kernel_entry');append(p,triangle_program(fused=True,fused_edges=True,compact=True,fused_norm=True,return_link=240,fused_attributes=True,
                                               fused_planes=fused_planes,unroll_attributes=unroll_attributes,prepared_setup=fused_setup),'kernel_')
    L('projected_fallback');C(192,0);C(193,232);E('COPY',23,192,193)
    if fused_setup:E('SETUP')
    J('kernel_entry',link=193);E('HALT')
    return p

if __name__=='__main__':
    p=build();print(json.dumps(dict(instructions=len(p.words),labels=p.labels),indent=2))
