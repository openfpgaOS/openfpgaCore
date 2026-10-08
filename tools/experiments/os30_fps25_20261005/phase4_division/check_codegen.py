#!/usr/bin/env python3
"""Bind SDK output and input closure; never rebuild or edit a stage."""
import hashlib,json,re,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase4/division'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    records={}
    parent=ROOT/'docs/measurements/os30-fps25-20261005/phase2/exact-v1'
    for label in ['control-f5','aspect-v1']:
        records[label]={}
        for mode,elfname in [('normal','app.elf'),('diagnostic','diagnostic.elf')]:
            path=OUT/label/elfname
            build=json.loads((OUT/label/('build-'+mode+'.json')).read_text())
            assert build['returncode']==0 and sha(path)==build['elf_sha256']
            if label=='control-f5':assert sha(path)==sha(parent/elfname)
            dis=subprocess.check_output(['llvm-objdump','-d','--mattr=+m,+f,+c',str(path)],text=True)
            nm=subprocess.check_output(['llvm-nm','-S',str(path)],text=True)
            info={}
            for fn in ['gfx_sp_vertex','gfx_start_frame']:
                body=re.search(r'^([0-9a-f]+ <'+fn+r'>:\n.*?)(?=\n[0-9a-f]+ <|\Z)',dis,re.M|re.S)[1]
                asm=OUT/label/(mode+'-'+fn+'.asm');asm.write_text(body)
                sym=re.search(r'^([0-9a-f]+) ([0-9a-f]+) [tT] '+fn+'$',nm,re.M)
                fpops=re.findall(r'^\s*[0-9a-f]+:\s+[0-9a-f]+\s+(f(?:add|sub|mul|madd|msub|nmadd|nmsub|div|sqrt|cvt|lt|le|eq|sgnj)[^\s]*)',body,re.M)
                info[fn]={'address':sym[1],'size':int(sym[2],16),'stack_frame':int(re.search(r'addi\s+sp, sp, -0x([0-9a-f]+)',body)[1],16),'fdiv_instructions':[x.strip()for x in body.splitlines()if 'fdiv.s' in x],'fp_arithmetic_opcode_sequence':fpops,'stack_fp_load_store_sites':len(re.findall(r'\b(?:flw|fsw)\s+[^\n]*\(sp\)',body)),'assembly_sha256':sha(asm)}
            assert len(info['gfx_sp_vertex']['fdiv_instructions'])==(2 if label=='control-f5' else 1)
            assert len(info['gfx_start_frame']['fdiv_instructions'])==(1 if label=='control-f5' else 2)
            cache=re.search(r'^([0-9a-f]+) ([0-9a-f]+) [bBdDsS] gfx_vertex_aspect_scale$',nm,re.M)
            if label=='aspect-v1':assert cache and int(cache[2],16)==4
            records[label][mode]={'elf_sha256':sha(path),'functions':info,'cached_float_address':cache[1]if cache else None}
    for mode in ['normal','diagnostic']:
        a=records['control-f5'][mode]['functions']['gfx_sp_vertex'];b=records['aspect-v1'][mode]['functions']['gfx_sp_vertex']
        # First arithmetic opcode in original is the removed aspect division.
        assert a['fp_arithmetic_opcode_sequence'][0]=='fdiv.s'
        assert a['fp_arithmetic_opcode_sequence'][1:]==b['fp_arithmetic_opcode_sequence']
        assert a['stack_frame']==b['stack_frame']
    expected=json.loads((OUT/'source-baseline.json').read_text())['files']
    changed='src/sm64/sm64/src/pc/gfx/gfx_pc.c'
    expected[changed]=sha(OUT/'aspect-v1/gfx_pc.c')
    stage=OUT/'SM64';deps=set();counts={}
    for rel,h in expected.items():assert sha(stage/rel)==h,rel
    for mode,folder in [('normal','sm64'),('diagnostic','sm64-perfdiag')]:
        files=list((stage/'.obj'/folder).rglob('*.d'));assert len(files)==298
        counts[mode]=len(files)
        for path in files:
            first=path.read_text().replace('\\\n',' ').splitlines()[0]
            for token in shlex.split(first.split(':',1)[1]):
                p=Path(token)
                if not p.is_absolute():p=stage/'src/sm64'/p
                p=p.resolve();assert p.is_relative_to(stage)
                rel=str(p.relative_to(stage));assert rel in expected;deps.add(rel)
    result={'passed':True,'records':records,'relocated_controls_byte_identical_to_shipped_F5':True,'vertex_fp_opcode_sequence_preserved_except_removed_divide':True,'rounding_barrier':'SDK emits first FDIV, FSW to aspect+stack, FLW from volatile stack, second FDIV with original4/3 constant, cached FSW. No width/height reciprocal reassociation.','source_closure':{'files':len(expected),'changed_files':[changed],'source_sha256':expected,'dependency_files':counts,'compiler_dependency_inputs':len(deps),'all_compiler_dependencies_in_closed_tree':True},'evidence_sha256':{str(p.relative_to(ROOT)):sha(p)for p in [Path(__file__),OUT/'source-baseline.json',OUT/'aspect-v1/manifest.json',OUT/'dimension-proof/proof.json']},'limits':'FP opcode sequence comparison permits register renaming; scene/vector equality is a separate runtime gate. No direct-call actual-A fixture used; its state capture would require the new cached float.'}
    (OUT/'codegen-and-closure.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({label:{mode:{fn:{k:v for k,v in data.items()if k in ['size','stack_frame','stack_fp_load_store_sites','fdiv_instructions']}for fn,data in rec['functions'].items()}for mode,rec in modes.items()}for label,modes in records.items()},indent=2))
if __name__=='__main__':main()
