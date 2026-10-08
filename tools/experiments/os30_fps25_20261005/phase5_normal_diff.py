#!/usr/bin/env python3
"""Prove the archived DIAG0 mismatch is only assertion source-line constants."""
import hashlib,json,re,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase5'
OLD=ROOT/'docs/measurements/os30-fps25-20261005/phase4/combined-cold/aspect-texmem-cold-v1'
NEW=OUT/'attempts/line-shift-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def sections(data):
    e=struct.unpack_from('<16sHHIIIIIHHHHHH',data)
    assert e[0][:6]==b'\x7fELF\x01\x01'
    sh=[struct.unpack_from('<10I',data,e[6]+i*e[11]) for i in range(e[12])]
    ns=data[sh[e[13]][4]:sh[e[13]][4]+sh[e[13]][5]]
    return {ns[x[0]:].split(b'\0')[0].decode():x for x in sh}

def main():
    a=(OLD/'app.elf').read_bytes();b=(NEW/'app.elf').read_bytes()
    assert len(a)==len(b)
    sa=sections(a);sb=sections(b);assert sa==sb
    diff=[i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
    assert len(diff)==15
    text=sa['.text'];addr,offset,size=text[3:6]
    assert all(offset<=x<offset+size for x in diff)
    cmd=['riscv64-elf-objdump','-d','--disassemble=gfx_run_dl',str(OLD/'app.elf')]
    dis=subprocess.check_output(cmd,text=True)
    insns={int(pc,16):(raw,desc.strip()) for pc,raw,desc in re.findall(r'^\s*([0-9a-f]+):\s+([0-9a-f]{4,8})\s+([^\n]*)',dis,re.M)}
    pcs=set()
    for off in diff:
        pc=addr+off-offset
        matches=[at for at,(raw,_) in insns.items() if at<=pc<at+len(raw)//2]
        assert len(matches)==1;pcs.add(matches[0])
    assert len(pcs)==11
    oldlines=(OLD/'gfx_pc.c').read_text().splitlines();newlines=(NEW/'gfx_pc.c').read_text().splitlines()
    rows=[]
    for pc in sorted(pcs):
        pos=pc-addr+offset;x,y=struct.unpack_from('<I',a,pos)[0],struct.unpack_from('<I',b,pos)[0]
        # All changed instructions are ADDI a2,a2,imm after LUI a2,1.
        assert x&0xfffff==y&0xfffff==0x60613
        ix=(x>>20)^0x800;ix-=0x800;iy=(y>>20)^0x800;iy-=0x800
        oldline=4096+ix;newline=4096+iy;assert newline==oldline+3
        oldtext=oldlines[oldline-1];newtext=newlines[newline-1]
        assert oldtext==newtext and ('SUPPORT_CHECK(' in oldtext or 'assert(' in oldtext)
        rows.append(dict(pc=hex(pc),before_instruction=hex(x),after_instruction=hex(y),
            before_line=oldline,after_line=newline,source=oldtext.strip(),instruction=insns[pc][1]))
        assert a[pos:pos+4]!=b[pos:pos+4]
    normalized=bytearray(b)
    for pc in pcs:
        pos=pc-addr+offset;normalized[pos:pos+4]=a[pos:pos+4]
    assert normalized==a,'Additional change outside the11 assertionline immediates'
    current=(OUT/'candidate/gfx_pc.c').read_text().splitlines()
    for row in rows:assert current[row['before_line']-1]==oldlines[row['before_line']-1]
    result=dict(passed=True,scope=__doc__,parent_elf_sha256=sha(OLD/'app.elf'),archived_elf_sha256=sha(NEW/'app.elf'),
        parent_source_sha256=sha(OLD/'gfx_pc.c'),archived_source_sha256=sha(NEW/'gfx_pc.c'),current_source_sha256=sha(OUT/'candidate/gfx_pc.c'),
        bytes_total=len(a),changed_bytes=len(diff),changed_instructions=rows,section_headers_identical=True,
        every_other_elf_byte_identical=True,assertions_unchanged=True,current_source_restores_all_assertion_line_numbers=True,
        limit='The archived mismatch affects assertion-reported source locations only. Full byte identity after line-neutral formatting requires the separate rebuilt normal ELF comparison.',
        script_sha256=sha(Path(__file__)))
    (OUT/'normal-line-shift-proof.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS15 differing bytes are11 assertion-line ADDI immediates; all other ELF bytes identical; current source restores original lines')

if __name__=='__main__':main()
