#!/usr/bin/env python3
"""Read-only symbolic audit of the two actual RISC-V gfx_pc objects.

This binds to the first confirmed-culling candidate. It checks instruction
order, including FMA contraction; it is not a timing or whole-program proof.
"""
from pathlib import Path
import hashlib, json, re, subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-batch-cull-20261005'
DEST = OUT / 'independent-audit'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def op(name, *args):
    # Multiplication is commutative, as is addition. Keep the addend and
    # multiply stage separate for FMA; never reassociate an arithmetic tree.
    if name in ('mul', 'add'):
        args = tuple(sorted(args, key=repr))
    elif name == 'fma':
        args = (*sorted(args[:2], key=repr), args[2])
    return (name, *args)


def expected(axis):
    d = op('mul', 'y', f'm1{axis}')
    d = op('fma', 'x', f'm0{axis}', d)
    d = op('add', d, f'm3{axis}')
    d = op('fma', 'z', f'm2{axis}', d)
    return op('mul', d, 'aspect') if axis == 0 else d


def instructions(asm, section):
    body = asm.split('Disassembly of section .text.' + section + ':', 1)[1]
    body = body.split('Disassembly of section ', 1)[0]
    found = []
    for line in body.splitlines():
        m = re.match(r'\s*([0-9a-f]+):\s+[0-9a-f]+\s+(\S+)\s*(.*)', line)
        if m:
            found.append((int(m[1], 16), m[2], [x.strip() for x in m[3].split(',')]))
    return found


def matrix(offset, base):
    n = (offset - base) // 4
    assert offset >= base and (offset-base) % 4 == 0 and n < 16
    return f'm{n//4}{n%4}'


def evaluate(insns, ranges, mem, initial=None):
    regs = dict(initial or {})
    history = []
    for addr, name, args in insns:
        if not any(lo <= addr <= hi for lo, hi in ranges):
            continue
        if name == 'flw':
            match = re.fullmatch(r'(-?0x[0-9a-f]+)\((\w+)\)', args[1])
            assert match, args
            value = mem(int(match[1], 16), match[2])
            regs[args[0]] = value if value is not None else ('unused-load', args[1])
        elif name in ('fmul.s', 'fadd.s', 'fmadd.s'):
            assert all(x in regs for x in args[1:]), (addr, args, regs)
            kind = {'fmul.s': 'mul', 'fadd.s': 'add', 'fmadd.s': 'fma'}[name]
            regs[args[0]] = op(kind, *(regs[x] for x in args[1:]))
            history.append((hex(addr), args[0], regs[args[0]]))
    return regs, history


def main():
    DEST.mkdir(exist_ok=True)
    source = OUT/'SM64/src/sm64/sm64/src/pc/gfx'
    expected_sources = {
        'gfx_geometry.h': '7330ec550945050c8786204719db05c0e5475d78ad9166e10517dbc7fb388c0d',
        'gfx_pc.c': '7c4d101835499f9404f93894ef9c71053af0146123a0bb2f3f9f8f0b175fe96a',
        'gfx_vertex_cull.h': '6c48e51766818099864b4629a1c06a28abd665da6ec4188605b755244b7788d2',
    }
    assert {name:sha(source/name) for name in expected_sources} == expected_sources
    results = []
    for label, app in [('diagnostic','sm64-perfdiag'),('normal','sm64')]:
        obj = OUT/f'SM64/.obj/{app}/sm64/src/pc/gfx/gfx_pc.o'
        cmd = ['llvm-objdump','-dr', '--section=.text.geometry_confirm_outside',
               '--section=.text.gfx_sp_vertex','--section=.text.vertex_cull_materialize',str(obj)]
        asm = subprocess.check_output(cmd, text=True)
        (DEST/f'gfx_pc-{label}.asm').write_text(asm)
        # Actual fallback coefficient loads bind matrix offsets; a2 points
        # 18 bytes into the 24-byte Vtx. fs0 holds the call's aspect argument.
        def fallback_mem(off, reg):
            if reg == 's0' and 0x2c4 <= off <= 0x300:
                return matrix(off, 0x2c4)
            if reg == 'a2' and off in (-18,-14,-10):
                return {-18:'x',-14:'y',-10:'z'}[off]
        fi = instructions(asm,'gfx_sp_vertex')
        ranges = [(0x24c,0x30e)] if label=='diagnostic' else [(0x236,0x30a)]
        fr, fh = evaluate(fi, ranges, fallback_mem, {'fs0':'aspect'})
        assert [fr[r] for r in ['fa4','fa2','fa3','fa5']] == [expected(i) for i in range(4)]

        ci = instructions(asm,'geometry_confirm_outside')
        def helper_x(off, reg):
            if reg=='a5' and 0x2c4 <= off <= 0x300:
                return matrix(off,0x2c4)
            if reg=='a0' and off in (4,-24,-16):
                return {4:'y',-24:'x',-16:'z'}[off]
        xr, xh = evaluate(ci,[(0x110,0x12c),(0x146,0x172)],helper_x,{'fa0':'aspect'})
        assert xr['fa5']==expected(0) and xr['fa4']==expected(3)
        cases=[]
        for axis in (1,2):
            def helper_yz(off,reg):
                if off==4 and reg in ('a7','a6','a3','a5'):
                    return f'm{["a7","a6","a3","a5"].index(reg)}{axis}'
                if reg=='a4' and 0x2c4 <= off <= 0x300:
                    return matrix(off,0x2c4)
                if reg=='a0' and off in (4,-24,-16):
                    return {4:'y',-24:'x',-16:'z'}[off]
            for sign,lo,hi in [('negative',0xc0,0xec),('positive',0x1ba,0x1e6)]:
                rr,hh=evaluate(ci,[(0x76,0x92),(lo,hi)],helper_yz)
                assert any(t[2]==expected(axis) for t in hh), (axis,sign,hh)
                assert rr['fa5']==expected(3)
                cases.append(dict(axis=axis,sign=sign,history=hh))

        # Recovered positions use the same DAG from a saved context, rather
        # than relying on an algebraically equivalent real-number formula.
        def recover_mem(off,reg):
            if reg=='a5' and 0 <= off < 64:
                return matrix(off,0)
            if reg=='a5' and off==0x58:
                return 'aspect'
            if reg=='t2' and off in (0,4,8):
                return {0:'x',4:'y',8:'z'}[off]
        ri=instructions(asm,'vertex_cull_materialize')
        rg=[(0xa0,0x146)] if label=='diagnostic' else [(0x94,0x13a)]
        rr,rh=evaluate(ri,rg,recover_mem)
        assert [rr[r] for r in ['fa1','ft1','ft0','fa5']]==[expected(i) for i in range(4)]
        results.append(dict(label=label,object=str(obj),object_sha256=sha(obj),
            assembly_sha256=sha(DEST/f'gfx_pc-{label}.asm'),fallback=fh,
            confirmation_x=xh,confirmation_yz=cases,recovered=rh,passed=True))
    result=dict(passed=True,source_sha256=expected_sources,results=results,
        script_sha256=sha(Path(__file__)),
        scope='Actual diagnostic and normal RISC-V FP instruction DAGs, including FMA contraction, for XYZ/W confirmation/fallback/recovery.',
        canonicalization='Commutative add/multiply operands only; no reassociation, contraction or distributivity assumed. FMA addend stays separate.',
        manual_control_review=['Six single-bit clip values select the matching coordinate and comparison direction.',
            'Actual helper retains exponent-bit finite checks for coordinate and W before acceptance.',
            'For finite outputs the negated FLE rejection implements the CPU strict FLT outside predicate.',
            'The caller passes the same aspect value and unchanged MP matrix to helper and fallback.'],
        limitations=['Not a general guarantee for a different compiler, source or flags.',
            'This is a correctness audit, not a performance improvement claim; candidate1 is unselected after measured runtime regression.'])
    (DEST/'codegen-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS: diagnostic and normal target XYZ/W instruction DAGs agree.')


if __name__=='__main__':main()
