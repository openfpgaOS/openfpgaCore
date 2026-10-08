#!/usr/bin/env python3
"""Independent forward composition and ABI/source review for render-v4."""
import csv
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
CAMPAIGN=ROOT/'docs/measurements/os30-fps25-20261005'
OUT=CAMPAIGN/'independent-review'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
LEAVES=['direct-record-v2','dispatch-v1','deferred-fog-v2','viewport-coeff-v1','vc-packet-v4','fps-panel-v1']
EXPECTED={
 'gfx_pc.c':'a5c16f135e4b89006a08891794562329f7b0a56daf0b22bb1cd35d791f7f90b5',
 'gfx_gpu.c':'c98384c86f8106553f09f66014313f20affd8c2628704d8bcfa1674ca70036fb',
 'perf_pocket.h':'77ddb6cea8d240a7ac0aac27effc36f6ff3470f73e871a8a51c662ebf1771036',
 'perf_pocket.c':'add77bb15e560101873f01da07f92c7f9e6dde715999d74740faff160c944659',
}


def forward(name):
 baseline=(CAMPAIGN/'baseline'/name).read_text().splitlines(True)
 edits=[];inputs={}
 for leaf in LEAVES:
  p=CAMPAIGN/leaf/name
  if not p.exists():continue
  inputs[leaf]=sha(p);text=p.read_text().splitlines(True)
  for tag,start,stop,new_start,new_stop in difflib.SequenceMatcher(None,baseline,text,autojunk=False).get_opcodes():
   if tag!='equal':edits.append((start,stop,text[new_start:new_stop],leaf))
 edits.sort(key=lambda e:(e[0],e[1]))
 rebuilt=[];cursor=0
 for start,stop,new,leaf in edits:
  assert cursor<=start,(name,leaf,cursor,start)
  rebuilt.extend(baseline[cursor:start]);rebuilt.extend(new);cursor=stop
 rebuilt.extend(baseline[cursor:])
 selected=CAMPAIGN/'render-v4'/name
 assert ''.join(rebuilt)==selected.read_text(),name
 assert sha(selected)==EXPECTED[name],name
 return {'passed':True,'baseline_sha256':sha(CAMPAIGN/'baseline'/name),'source_sha256':sha(selected),
         'leaf_sha256':inputs,'hunks':len(edits),'method':'Independent forward traversal of frozen-baseline edit intervals; no overlapping replacement permitted.'}


def main():
 OUT.mkdir(exist_ok=True)
 source={name:forward(name) for name in EXPECTED}
 baseline_c=(CAMPAIGN/'baseline/perf_pocket.c').read_text()
 candidate_c=(CAMPAIGN/'render-v4/perf_pocket.c').read_text()
 def struct(s):
  s=s[s.index('struct PerfSnapshot {'):];return s[:s.index('\n};')+3]
 assert struct(baseline_c)==struct(candidate_c)
 report={'source_review_passed':True,'runtime_qualified':False,'candidate':'render-v4',
         'script_sha256':sha(Path(__file__)),'components':LEAVES,'source_proof':source,
         'snapshot_abi':{'definition_exact':True,'size_bytes':156,'first18_fields_preserved':True,
           'version_offset':72,'samples_offset':76,'detail_ms10_offset':84,'vertex_ms10_offset':120,'vertex_counts_offset':140,
           'semantics':'F4 version4/samples0 marks detailed arrays unavailable; UI only shows measured frame-level values. DETAILS1 retains original P3.'},
         'build_bindings':{},'source_findings':[
          'Direct fallback entry remains limited to existing simple cache-eligible CPU materials. Generic C+D, decal, XLU, alpha-mask, unsupported capabilities and optional worker paths remain intact.',
          'Direct records and legacy VBO share a typed union; transition guards flush pending representation, backend reads only its active member, selector resets after synchronous draw. No batching/count/order changes are introduced by representation dispatch.',
          'Deferred fog and direct records compose safely: fog materials force SHADE alpha255; non-fog transition materializes pending original vertex alpha before any clipping or direct record fill. Only rdp.other_mode_l setter changes that predicate; frame-end covers configuration changes.',
          'Dispatch invalidation retains every material-changing command, both rectangle boundaries and the prior unknown-opcode behavior. Nonrectangle handlers do not prepare triangle materials, so a second invalidation is unnecessary.',
          'Viewport integer coefficients have exact matching initial values and are refreshed by the sole viewport setter. All clip projection consumers use the same rounding and integer arithmetic.',
          'Cached packet reservation is2..29words for at most3dirty slots. It claims after material/viewport commands, writes unchanged0x56 payloads and0x54 indices, and commits exactly the reserved size. No nested emitter can invalidate its pointer.',
          'Empty RV32 asm only constrains six independent integer conversion results before dependent stores; it emits no instructions and changes neither arithmetic nor rounding. OF_PC preserves existing public emission helpers.',
          'FPS hot scopes/counts compile away; exact snapshot layout and formulas for frame-level measurements remain unchanged. The detailed P3 override was independently conditionally compared.',
         ],
         'limits':['Runtime scene, exact pixel/depth replay and CPU gains are separate root-owned qualification gates.','No hardware FPS or25FPS achievement follows from source review or modeled cycles.','No new FPGA fit, ALM reduction, seed sweep or100MHz timing closure is established.']}
 for buildname in ['build-normal.json','build-diagnostic.json']:
  path=CAMPAIGN/'render-v4'/buildname
  if not path.exists():continue
  build=json.loads(path.read_text());assert build['returncode']==0
  for name,digest in EXPECTED.items():assert build['inputs'][name]['sha256']==digest,(buildname,name)
  for name in ['gfx_gpu.h','gfx_geometry.h','Makefile.sm64']:
   assert build['inputs'][name]['sha256']==sha(CAMPAIGN/'baseline'/name),(buildname,name)
  elf=Path(build['elf']);assert sha(elf)==build['elf_sha256']
  binding={'record_sha256':sha(path),'elf_sha256':sha(elf),'all7_input_hashes':{n:x['sha256'] for n,x in build['inputs'].items()}}
  if build['diagnostic']:
   symbols=subprocess.check_output(['/usr/bin/llvm-nm','-S',str(elf)],text=True)
   rows=[l.split() for l in symbols.splitlines() if l.endswith(' sm64_perf_snapshot')]
   assert len(rows)==1 and int(rows[0][1],16)==156,rows
   binding['snapshot_target_size_verified']=156
  report['build_bindings'][buildname]=binding
 path=OUT/'render-v4-source-abi.json';path.write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'source_review_passed':True,'source_files':{n:r['hunks'] for n,r in source.items()},'builds':report['build_bindings']},indent=2))


if __name__=='__main__':main()
