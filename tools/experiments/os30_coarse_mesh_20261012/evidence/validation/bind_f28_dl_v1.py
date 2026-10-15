#!/usr/bin/env python3
"""Bind the reviewed F28 display-list call/register ABI to exact ELF bytes."""
from pathlib import Path
import hashlib
import json
import re
import struct

HERE=Path(__file__).resolve().parent
C=HERE.parent
APP=C/'work-app-v4/evidence/app-build-v4'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
receipt=json.loads((APP/'build-result.json').read_text())
elf=Path(receipt['elf']); image=elf.read_bytes()
assert sha(elf)==receipt['elf_sha256']=='7294008468ae3760e6aac7fda5539e5dadd255c604e3b4a6c66bc028f68e422b'
assert image[:6]==b'\x7fELF\x01\x01'
phoff=struct.unpack_from('<I',image,28)[0]
phsize,phcount=struct.unpack_from('<HH',image,42)
segments=[]
for i in range(phcount):
    kind,offset,virtual,physical,filesize,memsize,flags,alignment=struct.unpack_from('<8I',image,phoff+i*phsize)
    if kind==1:segments.append((virtual,offset,filesize))
def elf_bytes(pc,size):
    matches=[image[offset+pc-virtual:offset+pc-virtual+size]
             for virtual,offset,length in segments if virtual<=pc and pc+size<=virtual+length]
    assert len(matches)==1,(hex(pc),size)
    return matches[0]
instructions={}
for line in (APP/'app.dis').open():
    m=re.match(r'^([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)',line)
    if m:instructions[int(m[1],16)]=(m[2],(m[3]+' '+m[4]).strip())
proof_sites={
 0x104d6400:('7151','addi sp,sp,-240'),
 0x104d6424:('84aa','mv s1,a0'),
 0x104d6426:('c22e','sw a1,4(sp)'),
 0x104d6440:('4080','lw s0,0(s1)'),
 0x104d64b2:('409c','lw a5,0(s1)'),
 0x104d64b4:('40d0','lw a2,4(s1)'),
 0x104d64c6:('bfbfa0ef','jal 104d10c0 <gfx_sp_vertex>'),
 0x104d6b1a:('8082','ret'),
 0x104d6b1c:('00f41793','slli a5,s0,0xf'),
 0x104d6b20:('40c8','lw a0,4(s1)'),
 0x104d6b22:('7a07d463','bgez a5,104d72ca <gfx_run_dl+0xeca>'),
 0x104d6b26:('84aa','mv s1,a0'),
 0x104d6b28:('ba21','j 104d6440 <gfx_run_dl+0x40>'),
 0x104d72ca:('04a1','addi s1,s1,8'),
 0x104d72cc:('85a6','mv a1,s1'),
 0x104d72ce:('932ff0ef','jal 104d6400 <gfx_run_dl>'),
 0x104d72d2:('96eff06f','j 104d6440 <gfx_run_dl+0x40>'),
 0x104d802a:('8526','mv a0,s1'),
 0x104d802e:('4581','li a1,0'),
 0x104d8034:('bccfe0ef','jal 104d6400 <gfx_run_dl>'),
}
proof=[]
for pc,(bits,text) in proof_sites.items():
    actual_bits,actual_text=instructions[pc]
    assert actual_bits==bits and actual_text.replace('\t',' ')==text,(hex(pc),actual_text,text)
    size=len(bits)//2
    assert elf_bytes(pc,size)==int(bits,16).to_bytes(size,'little'),hex(pc)
    proof.append(dict(pc=pc,bits=int(bits,16),bytes=size,disassembly=actual_text))
returns=[pc for pc,(bits,text) in instructions.items() if 0x104d6400<=pc<0x104d7ae0 and text=='ret']
assert returns==[0x104d6b1a],returns
callers=[pc for pc,(bits,text) in instructions.items() if text.startswith('jal ') and text.endswith('<gfx_run_dl>')]
assert callers==[0x104d72ce,0x104d8034],callers
paths=[Path(__file__),elf,APP/'build-result.json',APP/'app.dis',APP/'symbols.txt',
       C/'work-app-v4/src/sm64/sm64/src/pc/gfx/gfx_pc.c']
binding=dict(schema='f28-display-list-observer-v1',elf_sha256=sha(elf),entry_pc=0x104d6400,
 entry_instruction=0x7151,vertex_entry_pc=0x104d10c0,command_register=9,parent_command_register=9,
 parent_command_addend=-8,app_continuation_address=0x10fe3da8,
 root_return_pcs=[0x104d8038],recursive_return_pcs=[0x104d72d2],vertex_return_pcs=[0x104d64ca],
 return_instructions=[dict(pc=0x104d6b1a,bits=0x8082,bytes=2)],reviewed_instructions=proof,
 rationale=[
  'The F28 gfx_run_dl prologue moves entry a0 to s1 and stores a1 at4(sp); opcode and VTX operands are loaded through s1.',
  'At the gfx_sp_vertex entry s1 is still the current VTX command; the call returns to104d64ca.',
  'A pushed G_DL loads its child target from4(s1), then advances s1 by8 before passing s1 in a1. At child entry the independent parent command witness is therefore s1-8, not s1.',
  'The tail G_DL branch updates s1 from a0 and jumps to the interpreter loop without entering gfx_run_dl; the passive invocation stack keeps its original continuation.',
  'The root gfx_run call explicitly loads a1=0. The single reviewed return instruction is followed by the actual retired caller resume; the stack pops exactly once.'
 ],inputs_sha256={str(p.resolve()):sha(p) for p in paths})
out=HERE/'f28-dl-binding-v1.json';assert not out.exists()
out.write_text(json.dumps(binding,indent=2)+'\n')
print(json.dumps(dict(path=str(out),sha256=sha(out),instruction_checks=len(proof)),indent=2))
