#!/usr/bin/env python3
"""Verify a targeted timing export; no compiler, deployment or source changes."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys
import tarfile
import tempfile

sys.dont_write_bytecode=True
APP_SHA='10581bdb05e98c87dcd08abb2798549267793920f6c91e1c6be964b0dd9fb636'
RESULTS_SHA='192828cad6b134c0f6cacfd484611e1ec9db5ab27cf379974bd737296d4f110b'
QSF='src/fpga/targets/pocket/ap_core.qsf'
PLACEMENT_LINE='set_global_assignment -name PLACEMENT_EFFORT_MULTIPLIER 3.0'
PLACEMENT_ADAPTER_SHA='ceae81090e2a43272dbc8be1ad68cd6181f6767f32159d9eae7010b78a35b94f'
SINGLE_GROUP_LINE='set_global_assignment -name VERILOG_MACRO QUAKE2_AUDIO_SINGLE_GROUP'
SINGLE_GROUP_MIXER_SHA='34192651c576219e351af45892e145bc4780157062cf2ac45c903f1defeb3ba7'
SINGLE_GROUP_PROOFS={
 'timing-next/mixer-single-group/qualification.json':'35c0f8f1974c21d7f826533d43af5034fa34dbfff3b43f56344f5216bec52238',
 'timing-next/mixer-single-group/quake2-contract.json':'dfce19fc3ed494052836e31548f8ad97ce09e817842b992a767acd351d5fca5f'}
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
CPU_OLD_SHA='6f50382aa82e6684551756e6c2473492d0bd915a0c94b2aeae9ac4a47e62dec4'
CPU_FREEZE_SHA='d840abe5f80a4398328874f8ff10758ab20ac5e3a3274f56737df83ca1f31111'
CPU_FREEZE_QUAL_SHA='83325db63bee54094634bf80e6dfc9f18ca3aa5886b98108c0f018c653327eb8'
CPU_FREEZE_ADAPTER_SHA='4713e50f0f6a37d026d032d638f74f1bc9f76e3e3c918214385a96f6ce73978a'
CPU_PROOF='timing-next/cpu-freeze-factoring/'

AMO_LINE='set_global_assignment -name VERILOG_MACRO QUAKE2_CPU_LRSC_ONLY'
AMO_SHA='e46b8e60e153f53fb4141401bfecbacdd81118e8d67c1d2febfa54a163a9381b'
AMO_QUAL='4f2f8831826dfd3ce334cb4b5de7f63d888d7e106b8a528179aa4737b5a11f95'
AMO_ADAPTER='bcb55bf2d414721534284175dd263ff8b5be17ce7928d5803fdd1888e321c106'
AMO_ONLY_ADAPTER='67b9f1073727490db21a489805b1e1422a71750f9b3716197f443f8e294707ff'
AMO_GROUP_ADAPTER='61d53d313f8e92a8dc27c63df44afe959c24a26a69d179cd028ae74b57590376'
AMO_PROOF='timing-next/cpu-amo-cut/'
OS_SHA='054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
BOOT='src/fpga/targets/pocket/firmware.mif'
BOOT_SHA='e505071d970e68bb7672be27db82ff304e37d9cb3a9d04180c0f9cf61992b031'
SDRAM='src/fpga/targets/pocket/os30/io_sdram.v'
SDRAM_OLD='402fbc16b1b6957001f13b5c0ba957ce4e2253d586ddb3b965464e5c017ac2fc'
SDRAM_SHA='0da798a262b12cee2d508eaf09c142baab86616f9622ed9f4e113c97e44a16bd'
SDRAM_QUAL='c43244912ae5278dfa7dbb869d976ebb7e4026ab72c9cacf989b4da026d23ec8'
SDRAM_PROOF='timing-next/sdram-address-classes/'

def has_amo_profile(text):
    settings=[s.strip()for s in text.splitlines()if 'QUAKE2_CPU_LRSC_ONLY'in s and not s.lstrip().startswith('#')]
    if settings not in ([],[AMO_LINE]):raise ValueError('Unqualified CPU subset macro setting')
    return bool(settings)

def proof_file(plan,root,name,digest=None):
    p=PurePosixPath(name)
    if p.is_absolute()or '..'in p.parts or str(p)!=name:raise ValueError('Unsafe proof path')
    expected=plan['files'].get(name)
    if not expected or digest is not None and expected!=digest:raise ValueError('Proof missing from pinned inventory: '+name)
    path=root/name
    if sha(path)!=expected:raise ValueError('Proof changed: '+name)
    return path

def literal_assignment(tree,name):
    values=[n.value for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id==name for t in n.targets)]
    if len(values)!=1:raise ValueError('Expected one literal assignment: '+name)
    return ast.literal_eval(values[0])

def cpu_adapter_name(plan):
    return 'cpu-sdram-preparation.py' if plan.get('cpu_profile')=='quake2-lrsc-only-v1' and plan.get('sdram_profile') else 'cpu-preparation.py'

def check_amo_cpu_profile(plan,files,qsf,evidence_root,cpu_text,scope,adapter):
    if plan.get('cpu_profile')!='quake2-lrsc-only-v1' or files.get(CPU)!=AMO_SHA or plan['qualified_rtl'].get(CPU)!=AMO_SHA or not has_amo_profile(qsf)or CPU not in active_qsf_sources(qsf):
        raise ValueError('CPU subset profile/source/macro mismatch')
    def pinned(name,digest=None):return proof_file(plan,evidence_root,name,digest)
    qual=json.loads(pinned(AMO_PROOF+'qualification.json',AMO_QUAL).read_text())
    if qual['status']!='PASS_DIRECTED_WORKLOAD_GATES_READY_FOR_PHYSICAL_TRIAL' or qual['candidate_sha256']!=AMO_SHA or qual['parent_sha256']!=CPU_OLD_SHA:
        raise ValueError('CPU subset qualification mismatch')
    for name,h in qual['files'].items():pinned(AMO_PROOF+name,h)
    dynamic=json.loads(pinned(AMO_PROOF+'dynamic-v6/receipt.json').read_text())
    cones=json.loads(pinned(AMO_PROOF+'cones-v1/receipt.json').read_text())
    if dynamic['status']!='PASS'or len(dynamic['results'])!=24 or not cones['pass']or not cones['default_preprocessed_token_identity']:
        raise ValueError('CPU subset gates incomplete')
    for latency in [0,3,19,63]:
        for test in ['retained','amo']:
            base=dynamic['results'][f'parent-{test}-{latency}']
            for mode in ['default']+(['cut']if test=='retained'else []):
                actual=dynamic['results'][f'{mode}-{test}-{latency}']
                if {k:v for k,v in base.items()if k!='kernel_sha256'}!={k:v for k,v in actual.items()if k!='kernel_sha256'}:
                    raise ValueError('CPU subset differential gate mismatch')
        cut=dynamic['results'][f'cut-amo-{latency}']
        if not cut['pass']or cut['failure']!=0 or cut['outputs']!=325:raise ValueError('CPU precise AMO trap gate mismatch')
    baseline=pinned(AMO_PROOF+'portable/reference/VexiiRiscv_os30.v',CPU_OLD_SHA).read_text()
    tree=ast.parse(pinned(AMO_PROOF+'prepare.py').read_text())
    loops=[n for n in tree.body if isinstance(n,ast.For)and isinstance(n.target,ast.Tuple)and [x.id for x in n.target.elts if isinstance(x,ast.Name)]==['anchor','new']]
    if len(loops)!=1:raise ValueError('CPU three-block recipe shape changed')
    replacements=ast.literal_eval(loops[0].iter)
    if len(replacements)!=3:raise ValueError('Expected exactly three CPU subset replacements')
    expected=baseline
    for before,after in replacements:
        if expected.count(before)!=1:raise ValueError('CPU subset replacement anchor is not unique')
        expected=expected.replace(before,after)
    if expected!=cpu_text:raise ValueError('CPU subset source differs from exact three-block reconstruction')
    # Also reconstruct macro-off source text independently, without running a compiler.
    default=re.sub(r'`ifdef QUAKE2_CPU_LRSC_ONLY\n(.*?)`endif',lambda m:m[1].split('`else\n',1)[1]if '`else\n'in m[1]else '',cpu_text,flags=re.S)
    if re.sub(r'\s+','',default)!=re.sub(r'\s+','',baseline):raise ValueError('CPU subset default source differs')
    audit_name='timing-next/cpu-review/atomics/receipt.json'
    audit=json.loads(pinned(audit_name,qual['static_audit']['sha256']).read_text())
    contract=qual['static_audit']
    if contract['app_sha256']!=APP_SHA or contract['os_sha256']!=OS_SHA or contract['boot_mif_sha256']!=BOOT_SHA or files.get(BOOT)!=BOOT_SHA:
        raise ValueError('CPU subset app/OS/boot pairing mismatch')
    if any(audit[key]['counts']['AMO_RMW']or audit[key]['misa_csr_instruction_count']for key in ['app','firmware']):raise ValueError('CPU subset workload uses removed operations')
    pinned(AMO_PROOF+'portable/workload/os.bin',OS_SHA)
    pinned(AMO_PROOF+'portable/workload/firmware.elf',contract['firmware_elf_sha256'])
    required=dict(path=CPU,parent_sha256=CPU_OLD_SHA,candidate_sha256=AMO_SHA,qualification_sha256=AMO_QUAL,enabled_macro='QUAKE2_CPU_LRSC_ONLY',isa_unchanged=False,retained_workload_cycles_unchanged=True)
    expected_adapter=AMO_ADAPTER if plan.get('sdram_profile')=='address-classes-v1' else AMO_ONLY_ADAPTER
    if plan.get('quake2_single_group'):
        if plan.get('sdram_profile')or files.get(SDRAM)!=SDRAM_OLD or files.get('src/fpga/common/os30/gpu_core.v')!='365a9994ec6aa814396a1219b2c02ccdf644cacdd53b7a72e0c508ab2cf7d563':raise ValueError('Unqualified AMO/group0/DSP2 composition')
        check_audio_profile(qsf,plan)
        proofs={
          'mixer-single-group/qualification.json':'35c0f8f1974c21d7f826533d43af5034fa34dbfff3b43f56344f5216bec52238',
          'mixer-single-group/quake2-contract.json':'dfce19fc3ed494052836e31548f8ad97ce09e817842b992a767acd351d5fca5f',
          'gpu-dsp2-routing/FINAL-VALIDATION.json':'39b965b51b8dc25d3ed5d5fb30efb45414935b2543e4719e86e267202c09782b',
          'candidates/corrected-cache-mixer-dsp2/FINAL-VALIDATION.json':'655bc09e4b92e50197701a2addbdbe042de79e7f6e03cc9b3dbe8891c25df087'}
        if not scope or scope.get('component_proofs')!=proofs:raise ValueError('AMO/group0/DSP2 component scope mismatch')
        for name,digest in proofs.items():pinned('timing-next/'+name,digest)
        expected_adapter=AMO_GROUP_ADAPTER
    if not scope or scope.get('cpu_transformation')!=required or scope.get('workload_contract')!=contract or scope.get('preparation_adapter_sha256')!=expected_adapter or adapter is None or sha(adapter)!=expected_adapter:
        raise ValueError('CPU subset preparation/workload scope mismatch')
    return True

def check_sdram_profile(plan,files,qsf,evidence_root,sdram_text,scope=None):
    if hashlib.sha256(sdram_text.encode()).hexdigest()!=files.get(SDRAM):raise ValueError('SDRAM text/source pin mismatch')
    if files.get(SDRAM)==SDRAM_OLD:
        if plan.get('sdram_profile')or SDRAM in plan['qualified_rtl']or scope and scope.get('sdram_transformation'):raise ValueError('SDRAM profile does not match original source')
        return False
    if plan.get('sdram_profile')!='address-classes-v1'or files.get(SDRAM)!=SDRAM_SHA or plan['qualified_rtl'].get(SDRAM)!=SDRAM_SHA or SDRAM not in active_qsf_sources(qsf):raise ValueError('Unqualified SDRAM transformation')
    def pinned(name,digest=None):return proof_file(plan,evidence_root,name,digest)
    qual=json.loads(pinned(SDRAM_PROOF+'qualification.json',SDRAM_QUAL).read_text())
    for name,h in qual['artifacts'].items():pinned(SDRAM_PROOF+name,h)
    gate=json.loads(pinned(SDRAM_PROOF+'gates-v2/receipt.json',qual['receipt_sha256']).read_text())
    for name,h in gate['artifacts'].items():pinned(SDRAM_PROOF+'gates-v2/'+name,h)
    if not qual['passed']or qual['added_cycles']!=0 or qual['mirrored_state_assignments']!=65 or not gate['passed']or not gate['exact65_paired_state_writes']or not gate['unchanged_state_and_command_logic']:
        raise ValueError('SDRAM cycle/address equivalence gate failed')
    baseline=pinned(SDRAM_PROOF+'portable/reference/io_sdram.v',SDRAM_OLD).read_text()
    tree=ast.parse(pinned(SDRAM_PROOF+'prepare.py').read_text())
    declaration=literal_assignment(tree,'DECLARATION');decoder=literal_assignment(tree,'NEW_DECODER')
    pattern=r'\bstate\s*<=\s*(ST_[A-Z0-9_]+)\s*;'
    if len(re.findall(pattern,baseline))!=65:raise ValueError('SDRAM state assignment count changed')
    expected=re.sub(pattern,lambda m:'begin state <= '+m[1]+'; address_class <= address_class_for_state('+m[1]+'); end',baseline)
    marker='    reg     [23:0]  delay_boot;'
    if expected.count(marker)!=1:raise ValueError('SDRAM declaration anchor mismatch')
    expected=expected.replace(marker,declaration+'\n'+marker)
    start=expected.index('always @(posedge controller_clk) begin\n    case (state)')
    end=expected.index('\n\nendmodule',start)
    expected=expected[:start]+decoder+expected[end:]
    if expected!=sdram_text:raise ValueError('SDRAM differs from exact65-mirror/decoder reconstruction')
    required=dict(path=SDRAM,parent_sha256=SDRAM_OLD,candidate_sha256=SDRAM_SHA,qualification_sha256=SDRAM_QUAL,added_cycles=0)
    if not scope or scope.get('sdram_transformation')!=required:raise ValueError('SDRAM preparation scope mismatch')
    return True

def check_cpu_profile(plan,files,qsf,evidence_root,cpu_text,scope=None,adapter=None):
    """Accept the original CPU or an exact pinned CPU transformation."""
    digest=files.get(CPU)
    if hashlib.sha256(cpu_text.encode()).hexdigest()!=digest:raise ValueError('CPU text/source pin mismatch')
    if digest==AMO_SHA or plan.get('cpu_profile')=='quake2-lrsc-only-v1':
        return check_amo_cpu_profile(plan,files,qsf,evidence_root,cpu_text,scope,adapter)
    if has_amo_profile(qsf):raise ValueError('CPU subset macro without qualified subset source')
    if digest==CPU_OLD_SHA:
        if plan.get('cpu_profile')or CPU in plan['qualified_rtl']:raise ValueError('CPU profile does not match original CPU')
        return False
    if digest!=CPU_FREEZE_SHA or plan.get('cpu_profile')!='freeze-sink-factoring-v1' or plan['qualified_rtl'].get(CPU)!=CPU_FREEZE_SHA or CPU not in active_qsf_sources(qsf):
        raise ValueError('Unqualified CPU source/transformation')
    def pinned(name,digest=None):
        p=PurePosixPath(name)
        if p.is_absolute()or '..'in p.parts or str(p)!=name:raise ValueError('Unsafe CPU proof path')
        expected=plan['files'].get(name)
        if not expected or (digest is not None and expected!=digest):raise ValueError('CPU proof is not in the pinned inventory')
        path=evidence_root/name
        if sha(path)!=expected:raise ValueError('CPU proof changed: '+name)
        return path
    qual=json.loads(pinned(CPU_PROOF+'qualification.json',CPU_FREEZE_QUAL_SHA).read_text())
    if not qual.get('passed')or qual['parent_sha256']!=CPU_OLD_SHA or qual['candidate_sha256']!=CPU_FREEZE_SHA or qual['added_registers']!=0 or qual['added_cycles']!=0 or not qual['isa_features_unchanged']:
        raise ValueError('CPU qualification contract mismatch')
    for name,h in qual['artifact_sha256'].items():pinned(CPU_PROOF+name,h)
    gate=json.loads(pinned(CPU_PROOF+'gates-v1/receipt.json',qual['receipt_sha256']).read_text())
    if not gate.get('passed')or not gate['exact_two_block_delta']or not gate['sequential_and_all_other_source_byte_identical']or not gate['whole_cpu_lint_passed']or gate['verilator_binary_vectors']!=262144:
        raise ValueError('CPU equivalence/lint gate did not pass')
    pinned(CPU_PROOF+'gates-v1/cone_miter.v',gate['generated_miter_sha256'])
    pinned(CPU_PROOF+'gates-v1/main.cpp',gate['harness_sha256'])
    for command in gate['commands']:pinned(CPU_PROOF+'gates-v1/'+command['log'],command['log_sha256'])
    baseline=pinned(CPU_PROOF+'portable/reference/VexiiRiscv_os30.v',CPU_OLD_SHA).read_text()
    # Independently reconstruct from literal replacement pairs. Do not execute
    # the archived preparation/build script during portable verification.
    tree=ast.parse(pinned(CPU_PROOF+'prepare.py').read_text())
    assignments=[n.value for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='REPLACEMENTS'for t in n.targets)]
    if len(assignments)!=1:raise ValueError('CPU transformation recipe shape changed')
    replacements=ast.literal_eval(assignments[0])
    if len(replacements)!=2:raise ValueError('Expected exactly two CPU combinational replacements')
    expected=baseline
    for before,after in replacements:
        if expected.count(before)!=1:raise ValueError('CPU replacement anchor is not unique')
        expected=expected.replace(before,after)
    if expected!=cpu_text:raise ValueError('CPU differs from exact two-block baseline transformation')
    required=dict(path=CPU,parent_sha256=CPU_OLD_SHA,candidate_sha256=CPU_FREEZE_SHA,
                  qualification_sha256=CPU_FREEZE_QUAL_SHA,added_cycles=0,isa_unchanged=True)
    if not scope or scope.get('cpu_transformation')!=required or scope.get('preparation_adapter_sha256')!=CPU_FREEZE_ADAPTER_SHA or adapter is None or sha(adapter)!=CPU_FREEZE_ADAPTER_SHA:
        raise ValueError('CPU preparation adapter/scope source guard mismatch')
    return True

def active_qsf_sources(text):
    # These qualified experiment files are direct literal QSF assignments.
    # Do not infer activity merely because a file is in the128-input archive.
    active=set()
    for line in text.splitlines():
        match=re.fullmatch(r'\s*set_global_assignment\s+-name\s+(?:SYSTEMVERILOG|VERILOG)_FILE\s+([^\s]+)\s*',line)
        if match:
            name=match.group(1).strip('"')
            active.add(posixpath.normpath(str(PurePosixPath(QSF).parent/name)))
    return active

def has_placement_effort3(text):
    settings=[s.strip()for s in text.splitlines()if 'PLACEMENT_EFFORT_MULTIPLIER'in s and not s.lstrip().startswith('#')]
    if settings not in ([],[PLACEMENT_LINE]):raise ValueError('Unqualified placement effort setting')
    return bool(settings)

def has_single_group(text):
    settings=[s.strip()for s in text.splitlines()if 'QUAKE2_AUDIO_SINGLE_GROUP'in s and not s.lstrip().startswith('#')]
    if settings not in ([],[SINGLE_GROUP_LINE]):raise ValueError('Unqualified single-group macro setting')
    return bool(settings)

def check_audio_profile(text,plan):
    enabled=has_single_group(text)
    if enabled!=bool(plan.get('quake2_single_group')):raise ValueError('Audio profile/macro mismatch')
    if enabled and (plan['qualified_rtl'].get('src/fpga/common/os30/audio_mixer.v')!=SINGLE_GROUP_MIXER_SHA or any(plan['files'].get(n)!=h for n,h in SINGLE_GROUP_PROOFS.items())):
        raise ValueError('Single-group source or Quake2 contract is not qualified')

def check_gpu_gate(plan,files,evidence_root):
    gate=plan['gpu_gate']
    def read(which):
        entry=gate[which];name=entry['path'];p=PurePosixPath(name)
        if p.is_absolute()or '..'in p.parts or str(p)!=name or plan['files'].get(name)!=entry['sha256']:
            raise ValueError('GPU gate is not in the pinned evidence inventory')
        path=evidence_root/name
        if sha(path)!=entry['sha256']:raise ValueError('GPU gate hash changed')
        value=json.loads(path.read_text())
        if not value.get('passed'):raise ValueError('GPU functional gate did not pass')
        return value
    replay=read('replay');read('aggregate')
    if replay.get('gpu_core_source_sha256')!=files['src/fpga/common/os30/gpu_core.v']or replay.get('worker_source_sha256')!=files['src/fpga/common/gpu_geometry_worker.v']or not replay.get('source_unchanged')or len(replay.get('frames',[]))!=8:
        raise ValueError('GPU replay gate does not match selected sources')
    for frame in replay['frames']:
        if not frame.get('passed')or set(frame.get('results',{}))!={'paced','unpaced'}or any(not result.get(key)for result in frame['results'].values()for key in ['exact_output','exact_stats','exact_fences']):
            raise ValueError('GPU replay is incomplete or differs')
    if 'write_path_binding'in gate:
        for item in read('write_path_binding')['compiled_active_inputs'].values():
            if files.get(item['source'])!=item['sha256']:raise ValueError('GPU write-path source binding mismatch')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def inventory(root):
    answer={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink(): raise ValueError('No symlinks: '+str(p))
        if p.is_file(): answer[str(p.relative_to(root))]=sha(p)
    return answer

def archive_inventory(path):
    files={}
    with tarfile.open(path)as tar:
        for member in tar:
            name=PurePosixPath(member.name)
            if name.is_absolute() or '..'in name.parts or str(name)!=member.name or not member.isfile() or member.name in files:
                raise ValueError('Unsafe/duplicate source archive member: '+member.name)
            files[member.name]=hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    return files

def parse_timing(root):
    parser=root/'recipes/results.py'
    if sha(parser)!=RESULTS_SHA: raise ValueError('Unexpected timing parser')
    spec=importlib.util.spec_from_file_location('timing_result_verifier',parser)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix='q2-targeted-timing-')as tmp:
        project=Path(tmp); (project/'output_files').mkdir()
        with tarfile.open(root/'timing/chosen/quartus-reports.tar.gz')as tar:
            for name in ['ap_core.fit.rpt','ap_core.fit.summary']:
                member=tar.getmember('src/fpga/targets/pocket/output_files/'+name)
                if not member.isfile(): raise ValueError('Invalid fitter report member')
                (project/'output_files'/name).write_bytes(tar.extractfile(member).read())
        return module.parse(project,root/'timing/chosen/timing')

def verify(root):
    root=Path(root).absolute()
    if any(p.is_symlink()for p in [root,*root.parents]):raise ValueError('Symlink root')
    manifest=json.loads((root/'manifest.json').read_text())
    actual=inventory(root);actual.pop('manifest.json')
    if actual!=manifest['files']:raise ValueError('Package file hashes changed')
    if manifest['kind']!='targeted-seed-qualification' or manifest['new_full_sweep_complete'] or manifest['full_sweep_release_selected']:
        raise ValueError('Unexpected qualification scope')
    sources=json.loads((root/'core/source-manifest.json').read_text())
    if len(sources['source_sha256'])!=128 or archive_inventory(root/'core/source.tar.gz')!=sources['source_sha256']:
        raise ValueError('Source inventory mismatch')
    plan=json.loads((root/'evidence-plan.json').read_text())
    with tarfile.open(root/'core/source.tar.gz')as tar:
        qsf=tar.extractfile(QSF).read().decode();active=active_qsf_sources(qsf)
        cpu_text=tar.extractfile(CPU).read().decode()
        sdram_text=tar.extractfile(SDRAM).read().decode()
    check_audio_profile(qsf,plan)
    cpu_scope=json.loads((root/'timing/trial-qualification-scope.json').read_text())if plan.get('cpu_profile')else None
    check_cpu_profile(plan,sources['source_sha256'],qsf,root/'evidence',cpu_text,cpu_scope,root/'timing'/cpu_adapter_name(plan))
    check_sdram_profile(plan,sources['source_sha256'],qsf,root/'evidence',sdram_text,cpu_scope)
    if plan.get('cpu_profile')=='quake2-lrsc-only-v1'and sha(root/'app/os.bin')!=OS_SHA:raise ValueError('Bundled OS pairing changed')
    if has_single_group(qsf) and any(sha(root/'evidence'/n)!=h for n,h in SINGLE_GROUP_PROOFS.items()):
        raise ValueError('Single-group contract evidence changed')
    if has_placement_effort3(qsf):
        scope=json.loads((root/'timing/trial-qualification-scope.json').read_text())
        if scope.get('placement_effort_multiplier')!=3.0 or scope.get('preparation_adapter_sha256')!=PLACEMENT_ADAPTER_SHA or sha(root/'timing/placement-preparation.py')!=PLACEMENT_ADAPTER_SHA:
            raise ValueError('Placement adapter/scope mismatch')
    for name,digest in plan['qualified_rtl'].items():
        if name not in active or sources['source_sha256'].get(name)!=digest:
            raise ValueError('Qualified RTL is not the exact active QSF source: '+name)
    check_gpu_gate(plan,sources['source_sha256'],root/'evidence')
    if sha(root/'app/app.elf')!=APP_SHA:raise ValueError('Application changed')
    q=json.loads((root/'qualification.json').read_text())
    requested=q['requested_seeds']
    if not 1<=len(requested)<=30 or any(type(n)is not int or not 1<=n<=30 for n in requested)or len(set(requested))!=len(requested)or q['seed']not in requested or not set(requested)<=set(sources['seeds']):raise ValueError('Invalid explicit targeted seed scope')
    if len(requested)>4 and not(root/'timing/trial-qualification-scope.json').exists():raise ValueError('Larger targeted scope requires explicit scope evidence')
    if (root/'timing/trial-qualification-scope.json').exists():
        explicit=json.loads((root/'timing/trial-qualification-scope.json').read_text())
        if explicit['seeds']!=requested or explicit['source']!=sources['frozen_sha256']or explicit.get('dock_intact')is not True:raise ValueError('Targeted scope/source pairing changed')
    measured=parse_timing(root)
    if not measured['eligible_above_90_mhz_estimate'] or measured['minimum_main_restricted_fmax_mhz']<=90:
        raise ValueError('Selected image does not pass >90MHz and complete used timing checks')
    if any(q['measured'][key]!=value for key,value in measured.items()):raise ValueError('Timing values differ from raw report reparse')
    if sha(root/'core/os30.rbf_r')!=q['bitstream_sha256']:raise ValueError('Bitstream changed')
    return dict(passed=True,seed=q['seed'],fmax=measured['minimum_main_restricted_fmax_mhz'],scope=manifest['kind'])

if __name__=='__main__':
    print(json.dumps(verify(Path(sys.argv[1])if len(sys.argv)>1 else Path('.')),indent=2))
