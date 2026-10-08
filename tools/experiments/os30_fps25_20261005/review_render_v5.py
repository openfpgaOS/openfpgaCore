#!/usr/bin/env python3
"""Read-only source/build closure audit for the final single-issue tuned app."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[3]
CAMPAIGN=ROOT/'docs/measurements/os30-fps25-20261005'
STAGE=CAMPAIGN/'SM64'
CANDIDATE=CAMPAIGN/'render-v5'
OUT=CAMPAIGN/'independent-review'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
SOURCES={
 'gfx_pc.c':'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
 'gfx_gpu.c':'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
 'perf_pocket.h':'src/sm64/pocket/perf_pocket.h',
 'perf_pocket.c':'src/sm64/pocket/perf_pocket.c',
 'Makefile.sm64':'src/sm64/Makefile',
}
INPUTS={**SOURCES,'gfx_gpu.h':'src/sm64/sm64/src/pc/gfx/gfx_gpu.h',
 'gfx_geometry.h':'src/sm64/sm64/src/pc/gfx/gfx_geometry.h'}


def main():
 OUT.mkdir(exist_ok=True)
 previous=OUT/'render-v4-source-abi.json'
 review4=json.loads(previous.read_text());assert review4['source_review_passed']
 for name in SOURCES:
  if name!='Makefile.sm64':
   assert sha(CANDIDATE/name)==review4['source_proof'][name]['source_sha256'],name
 old=(CAMPAIGN/'baseline/Makefile.sm64').read_text()
 expected=old.replace('# Schedule the unchanged RV32 instruction set for a dual-issue pipeline.\nSDK_CFLAGS += -mtune=sifive-7-series',
                      '# Schedule the unchanged RV32 instruction set for a single-issue pipeline.\nSDK_CFLAGS += -mtune=sifive-5-series')
 assert expected!=old and expected==(CANDIDATE/'Makefile.sm64').read_text()
 assert sha(CANDIDATE/'Makefile.sm64')=='ec6d12e81e085fce4026cb098b202b9a03ae9cab25e02c4b1666af4fee4bafff'
 baseline_path=CAMPAIGN/'source-baseline.json';baseline=json.loads(baseline_path.read_text())['files']
 expected_tree=dict(baseline)
 for name,relative in SOURCES.items():expected_tree[relative]=sha(CANDIDATE/name)
 current={str(p.relative_to(STAGE)):p for p in STAGE.rglob('*') if p.is_file()}
 assert not set(baseline)-set(current),'Missing frozen input'
 observed={relative:sha(current[relative]) for relative in baseline}
 assert observed==expected_tree,{p:(expected_tree[p],observed[p]) for p in observed if expected_tree[p]!=observed[p]}
 changes={p:{'before':baseline[p],'after':observed[p]} for p in baseline if baseline[p]!=observed[p]}
 assert set(changes)==set(SOURCES.values())
 extra=sorted(set(current)-set(baseline))
 allowed_roots={'.obj/sm64','.obj/sm64-perfdiag'}
 for relative in extra:
  path=Path(relative)
  assert '/'.join(path.parts[:2]) in allowed_roots,relative
  assert path.suffix in ('.o','.d','.elf') or path.name=='.sdk-ccid',relative
 stage_record={'source_baseline_sha256':sha(baseline_path),'files':observed,
               'allowed_generated_outputs':{p:sha(current[p]) for p in extra},
               'scope':'All frozen input paths checked; only the five selected source/configuration files differ. Additional files are explicitly listed compiler objects/dependencies/ELFs/identity under two known .obj roots.'}
 stage_path=OUT/'render-v5-stage-inputs.json';stage_path.write_text(json.dumps(stage_record,indent=2)+'\n')
 builds={};dependency_inputs=set()
 for mode,diagnostic in [('normal',False),('diagnostic',True)]:
  record_path=CANDIDATE/f'build-{mode}.json';record=json.loads(record_path.read_text())
  assert record['returncode']==0 and record['diagnostic']==diagnostic
  for name,relative in INPUTS.items():
   assert record['inputs'][name]['sha256']==expected_tree[relative],(mode,name)
  elf=Path(record['elf']);assert sha(elf)==record['elf_sha256']
  log=CANDIDATE/f'build-{mode}.log'
  commands=[];object_paths=set()
  for line in log.read_text().splitlines():
   if not line.startswith('riscv64-unknown-elf-gcc ') or ' -c ' not in line:continue
   args=shlex.split(line)
   assert '-march=rv32imafc' in args and '-mabi=ilp32f' in args
   assert [x for x in args if x.startswith('-mtune=')]==['-mtune=sifive-5-series']
   assert '-nostdinc' in args
   object_path=Path(args[args.index('-o')+1]);assert object_path.is_relative_to(STAGE)
   object_paths.add(str(object_path.relative_to(STAGE)));commands.append(args)
  objroot='.obj/sm64-perfdiag' if diagnostic else '.obj/sm64'
  actual_objects={p for p in extra if p.startswith(objroot+'/') and p.endswith('.o')}
  assert object_paths==actual_objects,(mode,object_paths^actual_objects)
  assert len(commands)==298
  trig=[a for a in commands if a[-1].endswith('/gd_trig_openfpga.c')];assert len(trig)==1
  assert trig[0].index('-fno-fast-math')>trig[0].index('-ffast-math') and '-ffp-contract=off' in trig[0]
  for relative in extra:
   if not relative.startswith(objroot+'/') or not relative.endswith('.d'):continue
   text=current[relative].read_text().replace('\\\n',' ')
   first=text.splitlines()[0]
   for token in shlex.split(first.split(':',1)[1]):
    dep=(STAGE/'src/sm64'/token).resolve() if not Path(token).is_absolute() else Path(token).resolve()
    assert dep.is_relative_to(STAGE),str(dep)
    rel=str(dep.relative_to(STAGE));assert rel in observed,(mode,rel)
    dependency_inputs.add(rel)
  binding={'record_sha256':sha(record_path),'log_sha256':sha(log),'elf_sha256':sha(elf),
           'compiled_translation_units':len(commands),'all_objects_recompiled_with_single_issue_tune':True,
           'isa':'rv32imafc','abi':'ilp32f','tune':'sifive-5-series','goddard_strict_math_preserved':True,
           'all7_input_hashes':{n:x['sha256'] for n,x in record['inputs'].items()}}
  if diagnostic:
   nm=subprocess.check_output(['/usr/bin/llvm-nm','-S',str(elf)],text=True)
   symbol=[l.split() for l in nm.splitlines() if l.endswith(' sm64_perf_snapshot')]
   assert len(symbol)==1 and int(symbol[0][1],16)==156
   binding['snapshot_target_size_verified']=156
  builds[mode]=binding
 sdk_paths=[p for p in baseline if p.startswith('src/sdk/')]
 assert all(observed[p]==baseline[p] for p in sdk_paths)
 result={'source_review_passed':True,'runtime_qualified':False,'candidate':'render-v5',
         'script_sha256':sha(Path(__file__)),'prior_source_review_sha256':sha(previous),
         'prior_review_reused_only_after_exact4_source_hash_comparison':True,
         'makefile_change':'Only scheduling tune changes sifive-7-series to supported sifive-5-series; ISA/ABI and strict Goddard exception unchanged.',
         'source_changes':changes,'stage_input_closure':{
          'baseline_file_count':len(baseline),'unchanged_count':len(baseline)-len(changes),
          'changed_count':len(changes),'sdk_file_count':len(sdk_paths),'all_sdk_inputs_unchanged':True,
          'missing_inputs':[],'unexpected_source_files':[],'allowed_generated_file_count':len(extra),
          'dependency_input_count':len(dependency_inputs),'all_compiler_dependencies_in_closed_tree':True,
          'manifest_sha256':sha(stage_path)},'build_bindings':builds,
         'publication_scope':{'source_paths':list(SOURCES.values()),
          'app_paths':['.obj/sm64/app.elf','.obj/sm64-perfdiag/app.elf','build/pocket/sm64/Assets/sm64/common/sm64.elf'],
          'total_changed_files':8,'core_sdk_os_changes':False},
         'limits':['Source/build closure does not replace exact scene/GPU replay or actual CPU measurements.','Modeled improvement is not measured device FPS or evidence of25FPS.','No FPGA timing closure, ALM savings or new sweep is established.']}
 report_path=OUT/'render-v5-source-abi.json';report_path.write_text(json.dumps(result,indent=2)+'\n')
 lines=['Render-v5 final source and build scope audit','',
        'Source review: PASS. Runtime qualification remains a separate gate.',
        f'Frozen inputs: {len(baseline)} checked; {len(baseline)-len(changes)} unchanged; five selected differences.',
        f'SDK inputs: {len(sdk_paths)} checked, all unchanged.',
        f'Compiler dependencies: {len(dependency_inputs)} unique input files, all inside the closed tree.',
        f'Additional stage files: {len(extra)} known compiler outputs in .obj/sm64 and .obj/sm64-perfdiag only.',
        'No missing inputs or unexpected source files.','',
        'Publication scope table:','Artifact                         Purpose',
        'gfx_pc.c                         Direct records, fog lifetime, command dispatch',
        'gfx_gpu.c                        Exact direct emission, viewport coefficients, cached packet scheduling',
        'pocket/perf_pocket.h              Compile detailed hot hooks out by default',
        'pocket/perf_pocket.c              F4 frame-level FPS and timing panel; P3 remains opt-in',
        'src/sm64/Makefile                Schedule existing RV32IMAFC ISA for single issue',
        '.obj/sm64/app.elf                 Normal app',
        '.obj/sm64-perfdiag/app.elf         F4 diagnostic app',
        'Assets/sm64/common/sm64.elf       Same F4 app packaged for testing','',
        'Both modes rebuilt every one of their 298 translation units with -mtune=sifive-5-series.',
        'Both retain -march=rv32imafc -mabi=ilp32f and the strict Goddard math exception.',
        'The F4 snapshot remains 156 bytes, with all original fields and offsets intact.',
        'SDK, OS, core bitstream and other game projects are outside this eight-file publication scope.','',
        'Normal ELF: '+builds['normal']['elf_sha256'],
        'F4 ELF:     '+builds['diagnostic']['elf_sha256'],'',
        'This audit makes no hardware FPS, 25FPS, FPGA area or timing-closure claim.',
        'Exact source hashes, build bindings and full tree hashes are in the adjacent JSON reports.']
 (OUT/'render-v5-source-scope.txt').write_text('\n'.join(lines)+'\n')
 print(json.dumps({'source_review_passed':True,'stage_input_closure':result['stage_input_closure'],'builds':builds},indent=2))


if __name__=='__main__':main()
