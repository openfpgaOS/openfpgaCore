#!/usr/bin/env python3
"""Read-only independent audit of real edge counters and rejected PGO link."""
import ast
import hashlib
import json
import re
import struct
from pathlib import Path
from elftools.elf.elffile import ELFFile

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-20261008'
D=P/'renderer-pgo-v2'
O=P/'renderer-pgo-review-v1'
E={}
def bind(p):
    p=Path(p);h=hashlib.sha256(p.read_bytes()).hexdigest();E[str(p.relative_to(R))]=h;return h
def text(p):bind(p);return p.read_text()
def js(p):return json.loads(text(p))
assert not O.exists()
prepared=js(D/'prepared.json')
assert prepared['baseline_sha256']==bind(D/'baseline.elf')=='6f4ec790371d6a610976fa72d220df6636cfb5fbccf36284f028dc4893bc9959'
assert prepared['of_gl1_sha256']=='a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'
for rel,h in prepared['source_inputs'].items():assert bind(D/'work/src'/rel)==h,rel
assert bind(D/'recipe-prepare.py')==prepared['recipe_sha256']
tree=ast.parse(text(D/'recipe-prepare.py'))
bridge=next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='BRIDGE'for t in n.targets))
for name,h in prepared['parent_qsim_sources'].items():
    src=P/'demo-screen-v2/qsim'/name;assert bind(src)==h
    before=text(src);after=text(D/'qsim'/name)
    if name=='main.c':
        assert before.count('    env_free(&E);')==1
        assert after==before.replace('    env_free(&E);',bridge+'    env_free(&E);')
        assert after.index(bridge)>after.index('    fclose(js);')
        assert after.index(bridge)>after.index('        write_profile(&E, &P, path, tsv, top, sha);')
    else:assert after==before,name
    assert bind(D/'qsim'/name)==prepared['qsim_sources'][name]
assert bind(D/'qsim/qsim')==prepared['qsim_sha256']
assert 'E.flips!=flips_before' in bridge and 'guest exit(0)' in bridge
export=text(D/'capture-train/pgo-export.txt')
assert export.count('PGO_BEGIN\n')==export.count('PGO_END\n')==1
lines=export.split('PGO_BEGIN\n')[1].split('PGO_END\n')[0].splitlines()
assert all(re.fullmatch(r'PGO:[0-9a-f]+',x)for x in lines)
stream=bytes.fromhex(''.join(x[4:]for x in lines))
bind(D/'training/profile.stream');assert stream==(D/'training/profile.stream').read_bytes()
capture=js(D/'capture-result.json');prov=js(D/'training/counter-provenance.json')
for key,path in [('elf_sha256',D/'training/app.elf'),('stream_sha256',D/'training/profile.stream'),
                 ('export_sha256',D/'capture-train/pgo-export.txt'),('capture_sha256',D/'capture-result.json')]:
    assert prov[key]==bind(path)
assert capture['summary_sha256']==bind(D/'capture-train/summary.json')
summary=js(D/'capture-train/summary.json')
assert summary['frames']==362 and summary['stop_reason']=='frame limit'
assert summary['env']['unmapped_mmio']==summary['gpu_sync']['ring_overflows']==0
assert 'GEOMETRY=0' in text(D/'capture-train/console.log')
assert 'PGO export executed after frozen timing/profile/report outputs' in text(D/'capture-train.log')
assert all(prov[k]for k in ('real_guest_counters','edge_only','summaries_unmodified','profile_ids_not_rewritten','export_after_final_profile'))
cmd=js(D/'commands.json');assert set(cmd['compile'])=={'of_gl1','of_emit_q2'}
assert '-flto=auto' in cmd['link'] and all('-flto=auto'in x for x in cmd['compile'].values())
assert '-fprofile-update=single'in cmd['training_flags']
assert '-fprofile-generate'not in cmd['training_flags']
assert '-Werror=coverage-mismatch'in cmd['use_flags'] and '-Werror=missing-profile'in cmd['use_flags']
assert '13.2.0'in text(D/'training/compiler.log')
training=js(D/'training/result.json')
assert len(training['changed_objects'])==2 and training['info_pointers']==2 and training['value_profiling']is False

linked={}
with (D/'training/app.elf').open('rb')as f:
    elf=ELFFile(f)
    assert elf.get_section_by_name('.gcov_info')['sh_size']==8
    for sym in elf.get_section_by_name('.symtab').iter_symbols():
        if not sym.name.startswith('__gcov_.'):continue
        assert sym['st_size']==24
        sec=elf.get_section(sym['st_shndx']);off=sym['st_value']-sec['sh_addr']
        info,ident,line,cfg,count,pointer=struct.unpack_from('<6I',sec.data(),off)
        key=(ident,line,cfg);assert key not in linked
        linked[key]=(count,sym.name)
records=[]
for gcda in sorted((D/'profile-data').rglob('*.gcda')):
    assert bind(gcda)==prov['gcda_sha256'][gcda.name]
    b=gcda.read_bytes();magic,version,stamp,checksum=struct.unpack_from('<4I',b)
    assert magic==0x67636461 and version==0x4233322a
    note=D/'training'/(gcda.stem+'.gcno');bind(note)
    assert struct.unpack_from('<3I',note.read_bytes())==(0x67636e6f,version,stamp)
    note_dump=text(D/'training'/(gcda.stem+'.gcno.dump'))
    gcno_ids={(int(i),int(l,16),int(c,16))for i,l,c in re.findall(r'FUNCTION ident=(\d+), lineno_checksum=0x([0-9a-f]+), cfg_checksum=0x([0-9a-f]+)',note_dump)}
    i=16;current=None;ids=set()
    while i<len(b):
        tag=struct.unpack_from('<I',b,i)[0];i+=4
        if not tag:assert i==len(b);break
        length=struct.unpack_from('<i',b,i)[0];i+=4
        payload=b[i:i+max(length,0)];i+=max(length,0)
        assert i<=len(b)
        if tag==0xa1000000:assert length==8 and payload==bytes(8)
        elif tag==0x01000000:
            assert length==12;current=struct.unpack('<3I',payload);assert current not in ids;ids.add(current)
        elif tag==0x01a10000:
            assert current is not None and abs(length)%8==0
            values=struct.unpack('<'+'Q'*(length//8),payload)if length>0 else [0]*(-length//8)
            assert current in linked and linked[current][0]==len(values)
            records.append(dict(tu=gcda.name,ident=current[0],line=current[1],cfg=current[2],count=len(values),
                nonzero=sum(x!=0 for x in values),total=sum(values),linked_symbol=linked[current][1]))
            current=None
        else:raise AssertionError(hex(tag))
    assert ids==gcno_ids
assert len(records)==len(linked)==170
author=js(D/'training/linked-counter-check.json')
assert records==author['records']
assert sum(x['count']for x in records)==1355
assert sum(x['nonzero']for x in records)==604
# Both final links failed before a runnable use ELF was accepted. Keep failures
# as evidence; never infer that correct counters imply a usable optimization.
for label in ('use','use-link-v3'):
    log=text(D/label/'link.log')
    for marker in ('during IPA pass: modref','internal compiler error: Segmentation fault','get_hot_bb_threshold','lto-wrapper failed'):
        assert marker in log
    assert not (D/label/'result.json').exists()
for name in ('of_gl1','of_emit_q2'):
    log=text(D/'use'/(name+'-compile.log')).split('\n',1)[1]
    assert 'error:'not in log and 'warning:'not in log
    bind(D/'use-link-v3'/(name+'.o'))
bind(R/'tools/experiments/quake2_optimize_20261008/renderer_pgo_link_v3.py')
for rel in ('training/pgo_export.o','pgo_export.c','training.ld','training/merge-stream.log','training/link.log'):
    bind(D/rel)
result=dict(passed_provenance_review=True,passed_for_production=False,
    source='Lightmap-only a920 baseline, real edge counters in of_gl1 and of_emit_q2, existing LTO retained.',
    linked_function_descriptors=170,actual_counters=1355,nonzero_counters=604,
    descriptor_gcno_gcda_identity_count_match=True,export_bytes_exact=True,
    source_inventory_files=len(prepared['source_inputs']),qsim_delta='Only post-report guest exporter invocation.',
    summary_runs=0,summary_sum_max=0,summaries_and_ids_unmodified=True,
    final_link_attempts=2,final_link_status='Both GCC13.2 LTO links fail at modref/get_hot_bb_threshold; no timed PGO candidate.',
    selected_fallback='Normal lightmap-only ELF6f4; parent decides publication.',
    limits=['One fixed public demo used for training and intended evaluation; no held-out/general gameplay claim.',
            'Instrumented training cycles are not used as performance evidence.',
            'Provenance review does not establish PGO compiler correctness or deployability.'],
    new_compiles_simulations_or_cad=0,sibling_writes=False)
O.mkdir();(O/'result.json').write_text(json.dumps(result,indent=2)+'\n')
bind(O/'result.json');bind(Path(__file__))
(O/'manifest.json').write_text(json.dumps(dict(passed_provenance_review=True,passed_for_production=False,evidence_sha256=E),indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(output=str(O),manifest_sha256=hashlib.sha256((O/'manifest.json').read_bytes()).hexdigest(),bindings=len(E))))
