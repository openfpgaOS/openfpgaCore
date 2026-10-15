#!/usr/bin/env python3
"""Check or export one eligible targeted timing candidate. Never run Quartus.

The default action only validates inputs. Export additionally requires
--export, a new destination and a seed passing the unchanged >90MHz parser.
This does not select a full sweep, mutate old bundles or deploy anything.
"""
import argparse
import difflib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
REPO=ROOT.parents[2]
BASELINE=ROOT/'sweep-final-ring-audio'
PREVIOUS=REPO/'tools/experiments/quake2_tiny_audio_20261013'
DESTINATION=REPO/'tools/experiments/quake2_tiny_audio_timing_20261013'
BASE_SHA='f63613efe348448c45af303972d6804a41d15724ac3a716f17eff4b40e6eb2d3'
RECIPE_SHA={'sweep.py':'570a77c4d8aec800c2e90e9241721ec27cfa479f1638ddd91561c0fd834b46f2',
 'results.py':'192828cad6b134c0f6cacfd484611e1ec9db5ab27cf379974bd737296d4f110b',
 'report_sweep_timing.tcl':'2c66004df7b54c2e62a0c47ae976cf7c61da684ec034a169cb077ca00cdca34e'}
QSF='src/fpga/targets/pocket/ap_core.qsf'
OPTIONAL_RTL={'src/fpga/common/os30/audio_mixer.v','src/fpga/common/os30/gpu_color_depth_cache.sv'}
sys.path.insert(0,str(HERE))
from verify import sha,inventory,archive_inventory,verify,APP_SHA,active_qsf_sources,has_placement_effort3,PLACEMENT_LINE,PLACEMENT_ADAPTER_SHA,has_single_group,check_audio_profile,SINGLE_GROUP_LINE,check_gpu_gate
from verify import CPU,check_cpu_profile,SDRAM,check_sdram_profile,AMO_LINE,has_amo_profile,AMO_PROOF,OS_SHA,cpu_adapter_name

def save(path,data):
    path.write_text(json.dumps(data,indent=2)+'\n')

def load(path):
    return json.loads(path.read_text())

def cleanpath(path):
    p=path.absolute()
    if any(x.is_symlink()for x in [p,*p.parents]):raise ValueError('Symlink path: '+str(p))
    return p.resolve()

def evidence_path(name):
    p=PurePosixPath(name)
    if p.is_absolute()or '..'in p.parts or str(p)!=name or not p.parts:
        raise ValueError('Unsafe evidence name: '+name)
    path=cleanpath(ROOT/name)
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('Evidence escaped: '+name)
    return path

def driver():
    for name,digest in RECIPE_SHA.items():
        p=BASELINE/'recipes'/name
        if sha(p)!=digest:raise ValueError('Pinned driver changed: '+name)
    for name in ['results','sweep']:
        spec=importlib.util.spec_from_file_location(name,BASELINE/'recipes'/(name+'.py'))
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return sys.modules['sweep']

def protected_qsf(text):
    # Only reviewed physical-synthesis policy can differ; preserve all clocks,
    # device/pins, feature macros, files and instance settings verbatim.
    has_placement_effort3(text)
    has_single_group(text)
    has_amo_profile(text)
    return [x.strip()for x in text.splitlines()if x.strip()and not x.lstrip().startswith('#')and x.strip()not in [PLACEMENT_LINE,SINGLE_GROUP_LINE,AMO_LINE]
      and not re.fullmatch(r'set_global_assignment -name PHYSICAL_SYNTHESIS_(COMBO_LOGIC|EFFORT) (ON|OFF|NORMAL|FAST|EXTRA)',x.strip())]

def require_eligible(row):
    if row.get('state')!='complete' or not row.get('valid_reports') or not row.get('eligible_above_90_mhz_estimate') or row.get('minimum_main_restricted_fmax_mhz',0)<=90:
        raise ValueError('Seed is not eligible: requires complete verified reports, strictly >90MHz and every used-interface/nonsetup gate')

def targeted_scope(trial,manifest,seed):
    p=trial/'qualification-scope.json'
    if p.exists():
        scope=load(cleanpath(p));seeds=scope['seeds']
        if scope.get('source')!=manifest['frozen_sha256']or scope.get('dock_intact')is not True:
            raise ValueError('Targeted scope does not match frozen source/Dock constraint')
    else:
        seeds=manifest['seeds']
        if len(seeds)>4:raise ValueError('Larger targeted lists require explicit qualification-scope.json')
    if not 1<=len(seeds)<=30 or any(type(n)is not int or not 1<=n<=30 for n in seeds)or len(set(seeds))!=len(seeds)or not set(seeds)<=set(manifest['seeds'])or seed not in seeds:
        raise ValueError('Expected an explicit targeted1–30-seed scope and a requested seed')
    return seeds

def inputs(trial,seed,evidence_plan):
    trial=cleanpath(trial);sw=driver();manifest=load(trial/'manifest.json')
    targeted_scope(trial,manifest,seed)
    sw.SEEDS=manifest['seeds'];sw.verify(trial)
    if manifest['recipe_sha256']!=RECIPE_SHA:raise ValueError('Candidate timing recipes changed')
    base=load(BASELINE/'manifest.json');old=base['source_sha256'];files=manifest['source_sha256']
    if sw.fingerprint(old)!=BASE_SHA or len(files)!=128 or set(files)!=set(old):raise ValueError('Unexpected baseline/source inventory')
    if archive_inventory(trial/'source.tar.gz')!=files:raise ValueError('Frozen source archive differs')
    changed=sorted(n for n,h in files.items()if old[n]!=h)
    allowed={'src/fpga/common/gpu_geometry_worker.v','src/fpga/common/os30/gpu_core.v',QSF,CPU,SDRAM}|OPTIONAL_RTL
    if set(changed)-allowed:raise ValueError('Protected Dock/CPU/clock or unapproved source changed')
    if protected_qsf((trial/'frozen'/QSF).read_text())!=protected_qsf((BASELINE/'frozen'/QSF).read_text()):
        raise ValueError('Protected QSF settings changed')
    if has_placement_effort3((trial/'frozen'/QSF).read_text()):
        scope=load(trial/'qualification-scope.json')
        if scope.get('placement_effort_multiplier')!=3.0 or scope.get('preparation_adapter_sha256')!=PLACEMENT_ADAPTER_SHA or sha(trial/'placement-preparation.py')!=PLACEMENT_ADAPTER_SHA:
            raise ValueError('Placement adapter/scope mismatch')
    # Upstream verification reparses all four raw corners, validates every
    # report/artifact hash, exact source receipt and the DDIO connectivity proof.
    row=sw.verified_result(trial,seed,manifest);require_eligible(row)
    plan=load(evidence_plan)
    check_audio_profile((trial/'frozen'/QSF).read_text(),plan)
    cpu_scope=load(trial/'qualification-scope.json')if plan.get('cpu_profile')else None
    check_cpu_profile(plan,files,(trial/'frozen'/QSF).read_text(),ROOT,
      (trial/'frozen'/CPU).read_text(),cpu_scope,trial/cpu_adapter_name(plan))
    check_sdram_profile(plan,files,(trial/'frozen'/QSF).read_text(),ROOT,(trial/'frozen'/SDRAM).read_text(),cpu_scope)
    if (set(changed)-{QSF})-set(plan['qualified_rtl']):
        raise ValueError('Changed RTL lacks explicit functional qualification pins')
    active=active_qsf_sources((trial/'frozen'/QSF).read_text())
    for name,digest in plan['qualified_rtl'].items():
        if name not in active:raise ValueError('Qualified RTL is not an active QSF source: '+name)
        if files.get(name)!=digest:raise ValueError('Functional evidence does not match chosen RTL: '+name)
    for name,digest in plan['files'].items():
        p=evidence_path(name)
        if sha(p)!=digest:raise ValueError('Evidence changed: '+name)
    app=PREVIOUS/'app/app.elf'
    if sha(app)!=APP_SHA:raise ValueError('Unchanged application hash mismatch')
    check_gpu_gate(plan,files,ROOT)
    return sw,manifest,row,plan,changed

def assemble(trial,seed,evidence_plan,output,export=False):
    sw,manifest,row,plan,changed=inputs(trial,seed,evidence_plan)
    requested_seeds=targeted_scope(trial,manifest,seed)
    summary=dict(ready=True,exported=False,kind='targeted-seed-qualification',candidate=trial.name,
      seed=seed,minimum_main_restricted_fmax_mhz=row['minimum_main_restricted_fmax_mhz'],
      configured_mhz=100,timing_closed_at_configured_100_mhz=row['timing_closed_at_configured_100_mhz'],
      new_full_sweep_complete=False,full_sweep_release_selected=False,
      frozen_sha256=manifest['frozen_sha256'],changed_source_files=changed)
    if not export:return summary
    output=cleanpath(output)
    protected=[PREVIOUS,BASELINE,trial]
    if output.exists()or any(output==p or output.is_relative_to(p)for p in protected):raise ValueError('Export destination must be new and outside protected package/source/trial trees')
    output.mkdir(parents=True)
    def copy(src,name):
        src=cleanpath(src);dst=output/name;dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)
    copy(trial/'source.tar.gz','core/source.tar.gz')
    copy(trial/'manifest.json','core/source-manifest.json')
    patch=''.join(''.join(difflib.unified_diff((BASELINE/'frozen'/n).read_text().splitlines(True),
       (trial/'frozen'/n).read_text().splitlines(True),fromfile='a/'+n,tofile='b/'+n))for n in changed)
    (output/'core/source.patch').write_text(patch)
    copy(PREVIOUS/'app/app.elf','app/app.elf')
    if plan.get('cpu_profile')=='quake2-lrsc-only-v1':copy(evidence_path(AMO_PROOF+'portable/workload/os.bin'),'app/os.bin')
    copy(PREVIOUS/'app/clean-rebuild.json','app/prior-clean-rebuild.json')
    latest=load(trial/'seeds'/f'seed_{seed:02}'/'latest.json')['attempt']
    attempt=trial/'seeds'/f'seed_{seed:02}'/latest
    copy(attempt/'os30.rbf_r','core/os30.rbf_r')
    for p in attempt.rglob('*'):
        if p.is_symlink():raise ValueError('Symlink in chosen timing evidence')
        if p.is_file()and p.name not in ['os30.rbf_r','ap_core.rbf','ap_core.sof']:
            copy(p,'timing/chosen/'+str(p.relative_to(attempt)))
    for name in ['manifest.json','map-receipt.json']:
        copy(trial/name,'timing/trial-'+name)
    for name in ['run-configuration.json','qualification-scope.json']:
        if (trial/name).exists():copy(trial/name,'timing/trial-'+name)
    if has_placement_effort3((trial/'frozen'/QSF).read_text()):copy(trial/'placement-preparation.py','timing/placement-preparation.py')
    if plan.get('cpu_profile'):
        adapter=cpu_adapter_name(plan)
        copy(trial/adapter,'timing/'+adapter)
    for name in RECIPE_SHA:copy(trial/'recipes'/name,'recipes/'+name)
    for name in plan['files']:copy(evidence_path(name),'evidence/'+name)
    copy(evidence_plan,'evidence-plan.json');copy(HERE/'verify.py','verify.py')
    copy(HERE/'assemble.py','assembly-recipe.py')
    # Use the same parser through the exported evidence, independently of the
    # upstream row's extra orchestration metadata.
    from verify import parse_timing
    measured=parse_timing(output)
    qualification=dict(seed=seed,source_sha256=manifest['frozen_sha256'],
       bitstream_sha256=row['bitstream_sha256'],measured=measured,
       requested_seeds=requested_seeds,new_full_sweep_complete=False,
       scope='Chosen eligible targeted seed only; no new full30-sweep release selection')
    save(output/'qualification.json',qualification)
    audio_change=('The mixer predicate rewrite has exact cycle/output equivalence evidence;\nno audio format, voice, rate or buffer capacity changed.'
      if 'src/fpga/common/os30/audio_mixer.v'in changed else 'The audio mixer source is unchanged from the prior audio package.')
    if 'src/fpga/common/os30/gpu_color_depth_cache.sv'in changed:
        audio_change+='\nRender-cache metadata payload routing also preserves all enabled writes,\nprotocol behavior, output data and processing cycles in the bundled checks.'
    if plan.get('quake2_single_group'):
        audio_change+='\nThis image explicitly specializes audio to the pinned Quake2 group 0 contract.\nAll 32 voices, streams, stereo, ramps, loops and rates remain; group 0 and master\nvolumes remain dynamic. Other applications using groups 1–3 are outside this\nprofile. Default mixer source behavior remains general when the macro is absent.'
    cpu_change=('The CPU contains only the exact qualified freeze-sink Boolean factoring.\nAll sequential RTL, ISA features and processing cycles are unchanged. The old\nCPU, exact two-block transformation, extracted cones and exhaustive proof are\nbundled; verify.py reconstructs the selected CPU independently from that baseline.\nRerun the portable CPU proof (Python3/NumPy, Verilator and a C++ compiler):\n  python3 evidence/timing-next/cpu-freeze-factoring/portable/run.py --output /tmp/cpu-proof-new'
      if plan.get('cpu_profile')=='freeze-sink-factoring-v1'else 'The CPU source is unchanged from the prior audio package.')
    if plan.get('cpu_profile')=='quake2-lrsc-only-v1':
        cpu_change=('This Quake2-only CPU retains LR/SC, fences and reservation behavior but traps\n'
          'arithmetic AMOs; MISA.A is cleared. This is an ISA subset, not a general OS30\n'
          'replacement. Only the pinned app/app.elf (10581...) and app/os.bin (054abd...) pairing\n'
          'is covered; boot MIF e505... is bound inside the source archive. Use this bundled\n'
          'OS with this app/core. Future binaries require a new executable instruction\n'
          'audit. Default macro-off CPU remains unchanged. The three edits are independently\n'
          'reconstructed by verify.py from the bundled original CPU. Directed whole-CPU\n'
          'tests preserve retained outputs/cycles; they are not a universal formal proof\n'
          'or a full-game instruction-retirement trace.')
    if plan.get('sdram_profile'):
        cpu_change+='\nThe SDRAM controller adds seven registered address classes, mirroring all 65 FSM state writes.\nThe reconstructed address decoder and paired 41-state tests preserve commands,\naddresses and cycles. This is not an independent SDRAM chip timing/retention test.'
    readme=(HERE/'README.template.txt').read_text().format(candidate=trial.name,seed=seed,
      fmax=row['minimum_main_restricted_fmax_mhz'],closed=str(row['timing_closed_at_configured_100_mhz']).lower(),
      resources=json.dumps(row['resources'],sort_keys=True),fingerprint=manifest['frozen_sha256'],
      changed_sources='\n'.join('  '+n for n in changed),
      audio_change=audio_change,cpu_change=cpu_change)
    (output/'README.txt').write_text(readme)
    summary.update(exported=True,deployed=False,dock_source_unchanged=True,extra_gpu_cycles=0,
      pocket_audio_tested=False,pocket_fps_tested=False,prior_audio_source_sha256=BASE_SHA,
      unchanged_app_sha256=APP_SHA,files=inventory(output))
    save(output/'manifest.json',summary)
    summary['verification']=verify(output)
    return {key:value for key,value in summary.items()if key!='files'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial',type=Path);p.add_argument('--seed',type=int,required=True)
    p.add_argument('--evidence-plan',type=Path,default=HERE/'evidence-plan.json')
    p.add_argument('--output',type=Path,default=DESTINATION)
    p.add_argument('--export',action='store_true')
    args=p.parse_args()
    print(json.dumps(assemble(args.trial.absolute(),args.seed,args.evidence_plan.absolute(),args.output,args.export),indent=2))

if __name__=='__main__':main()
