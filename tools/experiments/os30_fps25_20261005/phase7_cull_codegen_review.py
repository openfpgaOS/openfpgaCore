#!/usr/bin/env python3
"""Check actual retained and candidate RV32 culling FMA operation graphs."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase7'
DEST=OUT/'combined-v1/independent-review'
DEST.mkdir(exist_ok=True)

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def mul(a,b):return ('mul',*sorted([a,b],key=repr))
def fused(a,b,c):return ('fmsub',*sorted([a,b],key=repr),c)
def insns(text):
    rows=[]
    for line in text.splitlines():
        m=re.match(r'\s*([0-9a-f]+):\s+[0-9a-f]+\s+(\S+)\s*(.*)',line)
        if m:rows.append((int(m[1],16),m[2],[x.strip() for x in m[3].split(',')],line))
    return rows

def step(row,registers):
    _,op,a,_=row
    if op=='flw':
        m=re.fullmatch(r'(0x[0-9a-f]+|-?\d+)\((\w+)\)',a[1]);assert m,row
        registers[a[0]]=('load',m[2],int(m[1],0))
    elif op=='fmv.w.x':registers[a[0]]=('int',a[1])
    elif op=='fmv.s':registers[a[0]]=registers[a[1]]
    elif op=='fmul.s':registers[a[0]]=mul(registers[a[1]],registers[a[2]])
    elif op=='fmsub.s':registers[a[0]]=fused(registers[a[1]],registers[a[2]],registers[a[3]])
    elif op=='fsub.s':registers[a[0]]=('sub',registers[a[1]],registers[a[2]])

def leaves(value):
    if value[0]=='load':return [value]
    return [x for item in value[1:] if isinstance(item,tuple) for x in leaves(item)]

def replace(value,labels):
    if value[0]=='load':return (labels[value[1]],{0x3ac:'x',0x3b0:'y',0x3bc:'inv',0x3b8:'w'}[value[2]])
    if value[0] in ('mul','fmsub'):
        children=[replace(x,labels) for x in value[1:]]
        return mul(*children) if value[0]=='mul' else fused(*children)
    return (value[0],*(replace(x,labels) if isinstance(x,tuple) else x for x in value[1:]))

def analyze(text):
    rows=insns(text);byaddr={r[0]:i for i,r in enumerate(rows)}
    starts=[i for i,r in enumerate(rows) if r[1]=='fmsub.s']
    assert len(starts)%4==0
    results=[]
    for group in range(0,len(starts),4):
        first,last=starts[group],starts[group+3]
        assert all(starts[group+j]==first+j for j in range(4)), 'Unexpected separated culling FMAs'
        begin=first-1
        # Each culling block loads all three reciprocals before its fused differences.
        seen=set()
        while begin>=0:
            if rows[begin][1]=='flw' and '0x3bc(' in rows[begin][2][1]:seen.add(rows[begin][2][1])
            if len(seen)==3:break
            begin-=1
        assert begin>=0 and first-begin<40
        # Include earlier x/y loads scheduled before the earliest reciprocal.
        while begin>0 and rows[begin-1][1] in ('flw','fmul.s'):begin-=1
        regs={};differences=[]
        for i in range(begin,last+1):
            step(rows[i],regs)
            if rows[i][1]=='fmsub.s':differences.append(regs[rows[i][2][0]])
        d0=differences[0]
        central={x[1] for x in leaves(d0[-1])};assert len(central)==1
        middle=central.pop()
        outer={x[1] for x in leaves(d0) if x[1]!=middle};assert len(outer)==1
        first_vertex=outer.pop()
        other={x[1] for d in differences for x in leaves(d)}-{middle,first_vertex};assert len(other)==1
        labels={middle:'v2',first_vertex:'v1',other.pop():'v3'}
        normalized=[replace(d,labels) for d in differences]
        expected=[fused((v,axis),(v,'inv'),mul(('v2',axis),('v2','inv')))
                  for v in ['v1','v3'] for axis in ['x','y']]
        assert sorted(normalized,key=repr)==sorted(expected,key=repr), normalized
        end=last+1
        while rows[end][1]!='fsub.s':
            step(rows[end],regs);end+=1;assert end-last<20
        step(rows[end],regs)
        cross=replace(regs[rows[end][2][0]],labels)
        pos=mul(expected[0],expected[3]);neg=mul(expected[1],expected[2])
        assert cross in [('sub',pos,neg),('sub',neg,pos)],cross
        # The alternate parity branch must use the reversed subtraction.
        parity=[r for r in rows[last+1:end] if r[1]=='beq'];assert len(parity)==1
        target=int(parity[0][2][2].split()[0],0);alt_index=byaddr[target]
        while rows[alt_index][1] in ('li','mv','nop','addi'):
            alt_index+=1
            assert alt_index-byaddr[target]<5
        alt=rows[alt_index]
        assert alt[1]=='fsub.s',alt
        altregs=dict(regs)
        # Recreate registers just before either mutually-exclusive subtraction.
        regs={}
        for i in range(begin,end):step(rows[i],regs)
        step(alt,regs);alternate=replace(regs[alt[2][0]],labels)
        assert alternate==('sub',cross[2],cross[1]),alternate
        results.append(dict(first_fmsub=hex(rows[first][0]),last_fmsub=hex(rows[last][0]),
                            direct_subtract=hex(rows[end][0]),opposite_parity_subtract=hex(target),
                            differences=normalized,cross=cross,alternate_cross=alternate,
                            rounded_central_xy_products=2,fused_differences=4,
                            rounded_cross_products=2,separate_final_subtraction=True))
    return results

cases=[('control',OUT/'control/app.elf'),('f8',OUT.parent/'phase6/candidate/diagnostic.elf'),
       ('combined-normal',OUT/'combined-v1/app.elf'),('combined-diagnostic',OUT/'combined-v1/diagnostic.elf')]
results={}
for label,path in cases:
    entries={}
    for name in ['gfx_sp_tri1','gfx_cpu_indexed_run']:
        command=['llvm-objdump','-d','--mattr=+f,+c,+m,+a','--disassemble-symbols='+name,str(path)]
        p=subprocess.run(command,check=True,text=True,capture_output=True)
        dest=DEST/(label+'-'+name+'.asm');dest.write_text(p.stdout)
        adjustments=[int(x,0) for x in re.findall(r'addi\s+sp, sp, (-?0x[0-9a-f]+)',p.stdout)]
        entries[name]=dict(disassembly_sha256=sha(dest),command=command,blocks=analyze(p.stdout),
                           stack_adjustments=adjustments)
    results[label]=dict(elf=str(path.relative_to(ROOT)),elf_sha256=sha(path),functions=entries)
record=dict(passed=True,script_sha256=sha(Path(__file__)),
            claim='Every checked target culling block retains two rounded middle-vertex coordinate products, four fused differences, two separately rounded cross products, and opposite final subtractions for W-sign parity. No standalone projected-coordinate cache or loss of FMA rounding.',
            results=results)
(DEST/'culling-codegen.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({label:{name:len(v['blocks']) for name,v in data['functions'].items()} for label,data in results.items()}))
