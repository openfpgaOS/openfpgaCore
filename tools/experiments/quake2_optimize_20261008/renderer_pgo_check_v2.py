#!/usr/bin/env python3
"""Seal actual counter provenance and bounded GCC13 mixed-profile LTO failure."""
import csv,json,re,shutil,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
import renderer_pgo_v2 as b

def main():
 p=b.OUT;assert not (p/'manifest.json').exists();prep=b.guard();linked=[]
 with (p/'training/app.elf').open('rb')as f:
  e=ELFFile(f)
  info=e.get_section_by_name('.gcov_info');assert info and info['sh_size']==8
  keys=struct.unpack('<2I',info.data());assert len(set(keys))==2
  for s in e.get_section_by_name('.symtab').iter_symbols():
   if s.name.startswith('__gcov_.'):
    assert s['st_size']==24
    sec=e.get_section(s['st_shndx']);off=s['st_value']-sec['sh_addr'];v=struct.unpack('<6I',sec.data()[off:off+24])
    assert v[0] in keys
    linked.append(dict(name=s.name,key=v[0],ident=v[1],line=v[2],cfg=v[3],count=v[4],counter_address=v[5]))
 records=[];nonzero=zero=0;function_rx=r'FUNCTION ident=(\d+), lineno_checksum=(0x[0-9a-f]+), cfg_checksum=(0x[0-9a-f]+)'
 tu={}
 for name in b.NAMES:
  gn=(p/'training'/f'{name}.gcno.dump').read_text();gd=(p/'training'/f'{name}.gcda.dump').read_text()
  assert sorted(re.findall(function_rx,gn))==sorted(re.findall(function_rx,gd))
  assert re.search(r':stamp (\d+)',gn)[1]==re.search(r':stamp (\d+)',gd)[1]
  assert not re.search(r'COUNTERS (?!arcs)',gd)
  assert 'OBJECT_SUMMARY runs=0, sum_max=0' in gd
  use=(p/'use'/f'{name}-profile.txt').read_text()
  assert 'Profile feedback for function is available' in use and 'Read edge' in use
  assert 'Setting hotness threshold to 0' in use
  compilelog=(p/'use'/f'{name}-compile.log').read_text().split('\n',1)[1]
  assert not re.search(r'warning:|error:',compilelog)
  tu[name]=dict(functions=len(re.findall(function_rx,gd)),read_edges=use.count('Read edge'),
    available_functions=use.count('Profile feedback for function is available'),summary_runs=0,summary_sum_max=0,hotness_threshold=0)
 for f in sorted(b.PROFILE.rglob('*.gcda')):
  data=f.read_bytes();assert data[:8]==b'adcg*23B';pos=16;current=None
  while pos+8<=len(data):
   tag,n=struct.unpack_from('<Ii',data,pos);pos+=8
   if tag==0:break
   payload=data[pos:pos+max(n,0)];pos+=max(n,0)
   if tag==0x01000000:
    assert n==12;ident,line,cfg=struct.unpack('<3I',payload);current=dict(tu=f.name,ident=ident,line=line,cfg=cfg)
   elif tag==0x01a10000:
    assert current is not None and abs(n)%8==0
    num=abs(n)//8;values=struct.unpack('<'+'Q'*num,payload)if n>0 else (0,)*num
    current.update(count=num,nonzero=sum(x!=0 for x in values),total=sum(values));records.append(current);current=None
    nonzero+=sum(x!=0 for x in values);zero+=sum(x==0 for x in values)
   else:assert tag==0xa1000000,(hex(tag),n)
  assert current is None
 for record in records:
  match=[s for s in linked if all(s[k]==record[k]for k in ('ident','line','cfg','count'))]
  assert len(match)==1,(record,len(match));record['linked_symbol']=match[0]['name']
 assert len(records)==len(linked)==sum(x['functions']for x in tu.values())==170
 old=json.loads((p/'training/linked-counter-check.json').read_text())
 assert old['records']==records and old['nonzero_counters']==nonzero and old['counters']==nonzero+zero
 baseline=list(csv.DictReader((b.SCREEN/'lightmap/frames.csv').open()));training=list(csv.DictReader((p/'capture-train/frames.csv').open()))
 assert len(baseline)==len(training)==362
 keys=['frame','host_framecount','buffer','gpu_cmd_words','gpu_pixels','hash_cmd','hash_px','hash_z','hash_pal','gpu_barriers','gpu_wait_px']
 differences=[dict(frame=i,key=k,before=a[k],after=z[k])for i,(a,z)in enumerate(zip(baseline,training))for k in keys if a[k]!=z[k]]
 # Instrumented feedback is not timing evidence. Semantic comparison remains explicit.
 errors={}
 for label in ('use','use-link-v3'):
  log=p/label/'link.log';t=log.read_text();assert 'internal compiler error: Segmentation fault' in t and 'get_hot_bb_threshold()' in t
  assert not (p/label/'app.elf').exists()
  errors[label]=dict(log_sha256=b.sha(log),failure='GCC13.2 LTO IPA modref internal compiler error in get_hot_bb_threshold')
 r=dict(passed=False,real_counter_provenance_passed=True,compile_feedback_consumption_passed=True,
  baseline_elf_sha256=b.BASE_SHA,training_elf_sha256=b.sha(p/'training/app.elf'),
  functions=len(records),counters=nonzero+zero,nonzero_counters=nonzero,per_tu=tu,
  training_frames=362,training_semantic_keys=keys,training_semantic_differences=differences,
  lto_preserved=True,source_exact=True,normal_diagnostic_isolation=True,
  failures=errors,uninstrumented_pgo_candidate_exists=False,performance_gain_measured=False,
  deployment_ready=False,decision='Stop after one link-policy retry; retain selected lightmap-only normal ELF.',
  limitations=['Same deterministic demo would train and evaluate: only an in-sample screen, not generalization.',
   'Actual GCC edge counters were exported by guest libgcov after final reports; no host proxy or counter/statistic fabrication.',
   'Freestanding export has real counts but runs=0/sum_max=0; compiler threshold0 left unchanged.',
   'Quake2 retains whole-program LTO. Both original link and one PGO-policy link retry failed before a usable ELF.',
   'No PGO execution-speed, framebuffer, hardware or FPS claim is available.'])
 b.write(p/'result.json',r)
 (p/'REPORT.txt').write_text('Quake2 two-TU edge-PGO: NOT RETAINED (compiler link failure)\n\n'
  'Selected lightmap-only input is6f4ec790/a920bb51; prior combined/rotation preparationv1 was stopped before training.\n'
  f'Real GCC13.2 training completed362frames. Actual linkedELF and GCDA agree on170 function identities/line+CFG checksums and{nonzero+zero} counters ({nonzero} nonzero). No counter or summary edits.\n'
  'Two strict profile-use compilations consumed feedback without warnings. Original LTO link failed in get_hot_bb_threshold during IPA modref; a single link-only retry with the same profile-use policy failed identically. Both attempts are preserved. No LTO disabling, extra compiler flag fishing or retraining performed.\n'
  'No uninstrumented PGO ELF exists; no speed gain or correctness/deployment claim. Retain measured lightmap-only software.\n')
 shutil.copy2(__file__,p/'analysis-recipe.py');shutil.copy2(Path(b.__file__),p/'recipe-final.py')
 files={str(f.relative_to(p)):b.sha(f)for f in sorted(p.rglob('*'))if f.is_file()and not any(x in f.relative_to(p).parts for x in ('work','build','__pycache__'))and f.name!='manifest.json'}
 evidence={str(f.relative_to(b.ROOT)):b.sha(f)for f in [b.BUILD/'result.json',b.SCREEN/'prepared.json',b.SCREEN/'lightmap-result.json',Path(b.__file__),Path(__file__),Path(__file__).with_name('renderer_pgo_link_v3.py')]}
 b.write(p/'manifest.json',dict(passed=False,provenance_checks_passed=True,files=files,evidence_sha256=evidence,deployment_ready=False))
 print(json.dumps({k:v for k,v in r.items()if k not in ('limitations','training_semantic_differences')},indent=2));print('manifest',b.sha(p/'manifest.json'),'semantic_differences',len(differences))
if __name__=='__main__':main()
