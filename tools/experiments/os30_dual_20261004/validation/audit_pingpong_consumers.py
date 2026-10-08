#!/usr/bin/env python3
"""Record and guard the actual invalid-payload consumers of the two-bank CPU."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
OUT=BASE/'validation/pingpong-consumer-audit-v1'
REF=BASE/'raw-hold-load-v1/immediate/VexiiRiscv.v'
NEW=BASE/'decode-pingpong-v1/immediate/VexiiRiscv.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
s=REF.read_text();new=NEW.read_text();lines=s.splitlines()
assert sha(REF)=='1bce3882b721c09e4a5091cc3ef00360366df9b1b0b79ffb0b1938b820105569'
assert sha(NEW)=='d471c5da61b3f5f67ce3c78eb0bf34d98cb828407ba0da15b63ac1044ed50f86'
contract=json.loads((BASE/'decode-pingpong-v1/immediate/source-contracts.json').read_text())
rows=[]
for field in contract['data_fields']:
    refs={}
    for kind in ['saved','head','output']:
        name=field[kind]
        reads=[]
        for i,line in enumerate(lines,1):
            if not re.search(r'\b'+name+r'\b',line):continue
            if re.match(r'\s*(?:reg|wire)\b',line):continue
            if re.match(r'\s*(?:assign )?'+name+r'\s*(?:<=|=)',line):continue
            reads.append({'line':i,'text':line.strip()})
        refs[kind]=reads
    is_trap='_TRAP_' in field['head'];is_raw='_INSTRUCTION_RAW_' in field['head']
    assert len(refs['saved'])==(2 if is_trap else 1),field
    assert len(refs['output'])==(2 if is_trap else 1),field
    assert len(refs['head'])==(47 if is_raw else 1),field
    mux=f"    if(decode_skid_rValid) begin\n      {field['output'].replace('_down_','_up_')} = {field['saved']};\n    end else begin\n      {field['output'].replace('_down_','_up_')} = {field['input']};\n    end"
    assert mux in s and mux in new,field
    rows.append(dict(field=field,reads=refs))
# Freeze exact unchanged source guards at the validity boundaries reviewed below.
ranges=[(12322,12355),(14389,14403),(15855,15855),(16155,16155),(16428,16428),
        (17425,17435),(17458,17469),(17512,17522),(17545,17556),
        (17638,17642),(17692,17696),(17759,17765),(17786,17804),
        (18994,18998),(19496,19501),(21651,21690),(21730,21763),
        (23249,23254),(23299,23302),(23941,23980),(24688,24723)]
guards=[]
for a,b in ranges:
    text='\n'.join(lines[a-1:b])
    assert text in new,(a,b)
    guards.append({'reference_start_line':a,'reference_end_line':b,'sha256':hashlib.sha256(text.encode()).hexdigest(),'text':text})
OUT.mkdir(exist_ok=True)
(OUT/'consumers.json').write_text(json.dumps(rows,indent=2)+'\n')
(OUT/'guards.json').write_text(json.dumps(guards,indent=2)+'\n')
(OUT/'manifest.json').write_text(json.dumps({'reference':str(REF.relative_to(ROOT)),'reference_sha256':sha(REF),'candidate':str(NEW.relative_to(ROOT)),'candidate_sha256':sha(NEW),'payload_fields':30,'payload_bits':276,'direct_saved_read_sites':sum(len(r['reads']['saved']) for r in rows),'direct_head_read_sites':sum(len(r['reads']['head']) for r in rows),'direct_output_read_sites':sum(len(r['reads']['output']) for r in rows),'unchanged_boundary_regions':len(guards),'consumer_census_sha256':sha(OUT/'consumers.json'),'guard_extracts_sha256':sha(OUT/'guards.json'),'audit_script_sha256':sha(Path(__file__)),'status':'Source guard/census checks passed; validity argument is documented human review, complementary to sequential refinement and CPU simulation.'},indent=2)+'\n')
print(f'PASS: 30 fields/276 bits, all direct head/tail/skid-output consumers enumerated, {len(guards)} unchanged validity-boundary regions.')
