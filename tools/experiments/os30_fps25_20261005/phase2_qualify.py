#!/usr/bin/env python3
"""Qualify a private exact-output app; never publish, deploy, build or replay.

Without --write-qualified, write only the candidate's qualification.json and
qualification-report.txt after every gate passes. With that flag, also create
phase2/QUALIFIED.json last, for the separately invoked publisher. Existing
selection/evidence files are never overwritten with different contents.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import tempfile

import phase2_publish as publisher

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT/'docs/measurements/os30-fps25-20261005'
OUT, VAL = CAMPAIGN/'phase2', CAMPAIGN/'validation'
STAGE = OUT/'SM64'
PREVIOUS = ROOT/'docs/measurements/os30-simple-20261005/validation'
FRAMES = (40,120,200,360,400,500,600,720)
KEYS = {(mode,frame) for mode in (1,2) for frame in FRAMES}
SOURCES = publisher.SOURCES
COMPONENTS = {'batch_guard','viewport_dirty','lighting_word','reciprocal_bit','clip_distance'}
BASELINES = {'normal':'render-v5-normal', 'fps':'render-v6-fps'}
BASE_ELFS = {'normal':publisher.OLD_NORMAL, 'fps':publisher.OLD_FPS}
METRICS = ('insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict')


def require(condition, detail):
    if not condition:
        raise RuntimeError(str(detail))


def read(path):
    return json.loads(Path(path).read_text())


class Evidence:
    def __init__(self):
        self.files = {}

    def remember(self, path, expected=None):
        path = Path(path)
        if not path.is_absolute():
            path = ROOT/path
        publisher.regular(path, ROOT)
        digest = publisher.sha(path)
        if expected is not None:
            require(digest == expected, ('Evidence digest mismatch', str(path)))
        relative = str(path.relative_to(ROOT))
        require(relative not in self.files or self.files[relative] == digest,
                ('Evidence changed during validation', relative))
        self.files[relative] = digest
        return digest

    def bind(self, path):
        self.remember(path)
        return read(path)

    def links(self, mapping):
        require(isinstance(mapping, dict), 'Expected evidence hash map')
        for path, digest in mapping.items():
            self.remember(path, digest)

    def unchanged(self):
        for path, digest in self.files.items():
            require(publisher.sha(ROOT/path) == digest, ('Evidence changed before commit', path))


def capture(evidence, label, expected_elf):
    path = VAL/('capture-'+label)
    record = evidence.bind(path/'inputs.json')
    require(record['success'] is True, ('Incomplete capture', label))
    require(record['hw_features'] == '0x2b136753', ('Wrong core features', label))
    require(record['audio'] is True and all(record[k] is False for k in
            ('geometry_capability','command_dma','panel')), ('Wrong capture configuration', label))
    evidence.links(record['sha256'])
    for relative, digest in record['results_sha256'].items():
        evidence.remember(publisher.relative_file(path, relative), digest)
    required = {'sm64.elf','run.log','console.log','frames.csv','scene.csv','summary.json'}
    required |= {f'gpuvec/frame_{frame:05}.vec' for frame in FRAMES}
    require(required <= set(record['results_sha256']), ('Incomplete result manifest', label))
    evidence.remember(path/'sm64.elf', expected_elf)
    summary = evidence.bind(path/'summary.json')
    require(summary['elf_sha256'] == expected_elf and summary['frames'] == 721,
            ('Wrong capture ELF/frame count', label))
    require(summary['stop_reason'] == 'frame limit' and summary['gpu_sched'] == 'eager',
            ('Unexpected capture termination/schedule', label))
    require(summary['env']['unmapped_mmio'] == 0 and summary['gpu_sync']['ring_overflows'] == 0,
            ('Capture memory/ring failure', label))
    with (path/'frames.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 721 and [int(r['frame']) for r in rows] == list(range(721)),
            ('Incomplete or reordered frame counters', label))
    require({p.name for p in (path/'gpuvec').glob('*.vec')} ==
            {f'frame_{f:05}.vec' for f in FRAMES}, ('Unexpected replay frames', label))
    return dict(path=path, record=record, summary=summary, rows=rows)


def metrics(before, after, start, stop):
    result = {}
    for key in METRICS:
        old = sum(int(r[key]) for r in before[start:stop])
        new = sum(int(r[key]) for r in after[start:stop])
        result[key] = dict(baseline=old,candidate=new,
                           saving_percent=100*(1-new/old) if old else None)
    return dict(first_frame=start,last_frame=stop-1,frames=stop-start,metrics=result)


def paired_counters(evidence, before, after, result_path):
    result = evidence.bind(result_path)
    bm, cm = before['record'], after['record']
    def non_app(m):
        return {path:digest for path,digest in m['sha256'].items() if Path(path).name!='sm64.elf'}
    require(non_app(bm) == non_app(cm), 'Paired capture input mismatch')
    def normalized_command(c):
        return [str(item).replace(str(c['path']),'<CAPTURE>') for item in c['record']['command']]
    require(normalized_command(before) == normalized_command(after), 'Paired capture command mismatch')
    require((before['path']/'scene.csv').read_bytes() == (after['path']/'scene.csv').read_bytes(),
            'Paired gameplay state differs')
    require(result['exact_inputs_except_app'] is True and result['scene_identical'] is True,
            'Analysis does not certify paired scene/inputs')
    for key,c in (('baseline',before),('candidate',after)):
        require(result[key] == c['path'].name.removeprefix('capture-'), ('Wrong analysis label',key))
        require(result[key+'_elf_sha256'] == publisher.sha(c['path']/'sm64.elf'),
                ('Wrong analysis executable',key))
    x,y = before['rows'],after['rows']
    gameplay = metrics(x,y,360,721)
    windows = [metrics(x,y,f,min(721,f+60)) for f in range(360,721,60)]
    require(result['gameplay'] == gameplay and result['windows'] == windows,
            'Analysis metrics do not reproduce from raw frame counters')
    for window in [gameplay,*windows]:
        require(all(window['metrics'][key]['saving_percent'] > 0 for key in ('insns','est_cycles')),
                ('Nonpositive instruction/cycle improvement',window['first_frame'],window['last_frame']))
    require(all(a['gpu_cmd_words'] == b['gpu_cmd_words'] for a,b in zip(x,y)),
            'GPU word count changed in at least one frame')
    evidence.links(result['evidence_sha256'])
    return result


def depth_vector(data):
    """Reproduce the retained replay adapter without modifying any input file."""
    pos=0; addresses=set(); counts={}; ended=False
    def u32():
        nonlocal pos
        value=struct.unpack_from('<I',data,pos)[0];pos+=4;return value
    while pos<len(data):
        tag=data[pos:pos+4];pos+=4
        if tag==b'MEM1':
            u32();n=u32();pos+=(n+3)&~3
        elif tag==b'TLUT':pos+=32768
        elif tag==b'PALB':pos+=4
        elif tag in (b'FLSH',b'FENC',b'HASH'):pass
        elif tag==b'DUMP':pos+=8
        elif tag==b'RAND':pos+=12
        elif tag==b'CMDS':
            n=u32();words=struct.unpack_from('<'+'I'*n,data,pos);pos+=4*n;i=0
            while i<n:
                header=words[i];opcode=header>>24;size=header&8191
                require(i+1+size<=n,'Malformed captured command')
                counts[hex(opcode)]=counts.get(hex(opcode),0)+1
                if opcode==0x4a and size==17 and words[i+12]:addresses.add(words[i+12])
                i+=size+1
        elif tag==b'END.':
            require(pos==len(data),'Bytes after replay terminator');ended=True;break
        else:raise RuntimeError(('Unknown vector tag',tag,pos))
        require(pos<=len(data),'Truncated vector')
    require(ended and len(addresses)==1,'Missing terminator or unique depth buffer')
    return data[:-4]+b'DUMP'+struct.pack('<II',next(iter(addresses)),153600)+b'END.',counts


def replay_rows(evidence, label, captures, hardware_hash):
    # A root replay invocation can combine the two labels in one result file.
    matches=[]
    for path in sorted(VAL.glob('replay-results-*.json')):
        rows=read(path)
        selected=[r for r in rows if r['label']==label]
        if selected:
            evidence.remember(path);matches.extend(selected)
    require(len(matches)==16 and {(r['mode'],r['frame']) for r in matches}==KEYS,
            ('Missing, duplicate or unexpected replay rows',label,len(matches)))
    indexed={}
    for row in matches:
        mode,frame=row['mode'],row['frame']
        require(row['hardware_sha256']==hardware_hash,('Wrong GPU simulator',label,mode,frame))
        binary=VAL/'replays-single-A'/label/str(mode)/f'frame_{frame:05}.bin'
        require(row['binary']==str(binary.relative_to(ROOT)),'Unexpected replay output location')
        evidence.remember(binary);data=binary.read_bytes()
        require(len(data)==307212,'Incomplete color/depth replay output')
        require(hashlib.sha256(data[:153600]).hexdigest()==row['framebuffer_sha256'] and
                hashlib.sha256(data[153612:]).hexdigest()==row['depth_sha256'],
                'Replay hash does not match actual color/depth bytes')
        log=binary.with_suffix('.log');evidence.remember(log,row['log_sha256'])
        require({k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log.read_text())}==row['stats'],
                'Replay counters do not match actual log')
        require(row['stats']['cycles']>0 and row['stats']['busy_cycles']>0,'Empty GPU replay')
        original=captures[label]['path']/'gpuvec'/f'frame_{frame:05}.vec'
        evidence.remember(original,row['vector_sha256'])
        expected,counts=depth_vector(original.read_bytes())
        vec=binary.with_suffix('.vec');evidence.remember(vec)
        require(vec.read_bytes()==expected and row['commands']==counts,
                'Actual replay vector/commands differ from captured input plus depth dump')
        indexed[(mode,frame)]=row
    return indexed


def source_and_builds(evidence, candidate, source_hashes):
    baseline=evidence.bind(OUT/'source-baseline.json')
    evidence.remember(OUT/'source-baseline.json',publisher.BASELINE_SHA)
    require(baseline['parent']=='render-v6' and len(baseline['files'])==6747,'Wrong source baseline')
    expected=dict(baseline['files'])
    expected.update({relative:source_hashes[name] for name,relative in SOURCES.items()})
    require(publisher.inventory(STAGE)==set(expected),'Private stage source inventory differs from6747 inputs')
    for relative,digest in expected.items():evidence.remember(STAGE/relative,digest)
    current={str(p.relative_to(STAGE)):p for p in STAGE.rglob('*') if p.is_file()}
    generated=set(current)-set(expected)
    for relative in generated:
        path=Path(relative)
        require('/'.join(path.parts[:2]) in ('.obj/sm64','.obj/sm64-perfdiag') and
                (path.suffix in ('.o','.d','.elf') or path.name=='.sdk-ccid'),
                ('Unexpected stage generated file',relative))
        evidence.remember(current[relative])
    dependencies=set();bindings={}
    for mode,elf_name,objroot in (('normal','app.elf','.obj/sm64'),
                                   ('diagnostic','diagnostic.elf','.obj/sm64-perfdiag')):
        build_path=candidate/f'build-{mode}.json';record=evidence.bind(build_path)
        require(record['returncode']==0 and record['diagnostic']==(mode=='diagnostic'),
                ('Incomplete/wrong build',mode))
        require(record['cwd']==str(STAGE/'src/sm64'),'Unexpected build working directory')
        require(set(record['inputs'])==set(SOURCES),'Build does not bind all seven inputs')
        for name,info in record['inputs'].items():
            require(Path(info['source'])==candidate/name and info['sha256']==source_hashes[name],
                    ('Build source binding mismatch',mode,name))
            evidence.remember(candidate/name,info['sha256'])
        elf=candidate/elf_name
        require(record['elf']==str(elf),'Unexpected build ELF path')
        evidence.remember(elf,record['elf_sha256'])
        evidence.remember(STAGE/objroot/'app.elf',record['elf_sha256'])
        log=candidate/f'build-{mode}.log';log_hash=evidence.remember(log)
        for line in log.read_text().splitlines():
            if not line.startswith('riscv64-unknown-elf-gcc ') or ' -c ' not in line:continue
            args=shlex.split(line)
            require('-march=rv32imafc' in args and '-mabi=ilp32f' in args and '-nostdinc' in args,
                    ('Unexpected compiler flags',mode))
            require([a for a in args if a.startswith('-mtune=')]==['-mtune=sifive-5-series'],
                    ('Unexpected CPU scheduler tune',mode))
        d_files=[relative for relative in generated if relative.startswith(objroot+'/') and relative.endswith('.d')]
        require(len(d_files)==298,('Unexpected dependency-file count',mode,len(d_files)))
        for relative in d_files:
            require(current[relative].with_suffix('.o').is_file(),('Dependency lacks object',relative))
            text=current[relative].read_text().replace('\\\n',' ')
            first=text.splitlines()[0]
            for token in shlex.split(first.split(':',1)[1]):
                dep=Path(token)
                if not dep.is_absolute():dep=STAGE/'src/sm64'/dep
                dep=dep.resolve()
                require(dep.is_relative_to(STAGE),'Compiler dependency outside frozen stage')
                key=str(dep.relative_to(STAGE))
                require(key in expected,('Compiler dependency not in source closure',mode,key))
                dependencies.add(key)
        bindings[mode]=dict(record_sha256=publisher.sha(build_path),log_sha256=log_hash,
            elf_sha256=record['elf_sha256'],dependency_files=len(d_files),
            all7_input_hashes=source_hashes)
    sdk=[relative for relative in expected if '/sdk/' in relative or relative.startswith('src/sdk/')]
    require(len(sdk)==333 and all(expected[p]==baseline['files'][p] for p in sdk),'SDK input changed')
    closure=dict(source_inputs=6747,changed_inputs=sum(expected[p]!=baseline['files'][p] for p in expected),
        generated_outputs=len(generated),dependency_input_count=len(dependencies),sdk_inputs=len(sdk),
        all_compiler_dependencies_in_closed_tree=True,all_sdk_inputs_unchanged=True,
        source_baseline_sha256=publisher.BASELINE_SHA)
    return expected,closure,bindings


def qualify(candidate_name):
    require(re.fullmatch(r'[a-z0-9][a-z0-9_-]*',candidate_name),'Invalid candidate name')
    candidate=OUT/candidate_name;evidence=Evidence()
    evidence.remember(Path(__file__))
    evidence.remember(Path(publisher.__file__))
    for name in ('phase2.py','build.py','prepare_phase2_exact.py'):
        evidence.remember(Path(__file__).with_name(name))
    for name in ('capture.py','common.py','analyze.py','replay.py'):
        evidence.remember(Path(__file__).parent/'validation'/name)
    manifest=evidence.bind(candidate/'manifest.json')
    require(manifest['candidate']==candidate_name and manifest['parent']=='render-v6','Wrong candidate manifest')
    require(manifest.get('guardband_included') is False,'Guardband candidate cannot qualify')
    source_hashes={name:evidence.remember(candidate/name) for name in SOURCES}
    require(manifest['source_sha256']==source_hashes,'Candidate source manifest mismatch')
    audit=evidence.bind(candidate/'independent-review/final-source-audit.json')
    require(audit['candidate']==candidate_name and audit['passed'] is True and
            audit['source_sha256']==source_hashes,'Final source audit mismatch/incomplete')
    require(set(audit['components'])==COMPONENTS and all(c['passed'] is True for c in audit['components'].values()),
            'Required source component proof missing or failed')
    require(audit.get('guardband_included') is False,'Final audit must exclude guardband')
    require(audit['composition']['passed'] is True and
            audit['composition']['all_seven_files_reconstruct_exact_baseline'] is True and
            audit['normal_fps_source_closure']['passed'] is True,
            'Final composition or normal/FPS source closure failed')
    evidence.links(audit.get('evidence_sha256',audit.get('evidence')))
    for component in audit['components'].values():
        if 'evidence_sha256' in component:evidence.links(component['evidence_sha256'])
    for component,hashes in manifest['component_source_sha256'].items():
        require(set(hashes)==set(SOURCES),'Incomplete component source bindings')
        for name,digest in hashes.items():evidence.remember(OUT/component/name,digest)
    clip=OUT/audit['components']['clip_distance']['source_component']
    proof=evidence.bind(clip/'function-checks/manifest.json')
    require(proof['passed'] is True and {r['name'] for r in proof['runs']}=={'strict','fast','sanitized'},
            'Incomplete clipping arithmetic proof')
    evidence.remember(clip/'function-checks/functions.c',proof['harness_sha256'])
    for run in proof['runs']:
        require(run['returncode']==0 and run['output'].startswith('PASS exact clipped payload/fan order:'),
                'Clipping arithmetic proof failed')
        evidence.remember(clip/'function-checks'/run['name'],run['binary_sha256'])
        log=clip/'function-checks'/(run['name']+'.log');evidence.remember(log)
        require(log.read_text().strip()==run['output'],'Clipping proof log differs from manifest')
    codegen=evidence.bind(clip/'codegen/manifest.json')
    require(codegen['returncode']==0 and codegen['source_sha256']==proof['harness_sha256'],
            'Clipping codegen proof source/failure')
    evidence.remember(clip/'codegen/functions.o',codegen['object_sha256'])
    evidence.remember(clip/'codegen/functions.asm',codegen['assembly_sha256'])
    evidence.remember(clip/'codegen/compile.log')
    source_tree,closure,builds=source_and_builds(evidence,candidate,source_hashes)
    pins=evidence.bind(VAL/'inputs.json');require(pins['passed'] is True,'Model/configuration audit failed')
    require(pins['A']['pocket_sha256']==publisher.CORE and pins['A']['hw_features']=='0x2b136753'
            and pins['A']['geometry'] is False,'Wrong frozen A configuration')
    qsim=PREVIOUS/'qsim-observed'
    for relative,digest in pins['qsim']['sources'].items():evidence.remember(qsim/relative,digest)
    require(pins['qsim']['exe_sha256']==publisher.sha(qsim/'qsim'),'Wrong qsim executable')
    replay_root=PREVIOUS/'replay-single-A'
    replay_manifest=evidence.bind(replay_root/'manifest.json')
    hardware_hash=evidence.remember(replay_root/'obj/Vtb_gpu',replay_manifest['simulator_sha256'])
    for relative,digest in replay_manifest['sources'].items():evidence.remember(replay_root/relative,digest)
    require(replay_manifest['selected_fit']=='fit-single128-v1','Wrong replay hardware fit')
    captures={};results={};replays={}
    for mode in ('normal','fps'):
        label=f'phase2-{candidate_name}-{mode}';base=BASELINES[mode]
        elf_hash=builds['normal' if mode=='normal' else 'diagnostic']['elf_sha256']
        captures[base]=capture(evidence,base,BASE_ELFS[mode])
        captures[label]=capture(evidence,label,elf_hash)
        results[mode]=paired_counters(evidence,captures[base],captures[label],VAL/f'results-{label}.json')
        for current in (base,label):replays[current]=replay_rows(evidence,current,captures,hardware_hash)
    differences=[]
    for mode,result in results.items():
        before,after=replays[result['baseline']],replays[result['candidate']]
        comparisons=result['gpu_comparisons']
        require(len(comparisons)==16 and {(r['mode'],r['frame']) for r in comparisons}==KEYS and
                result['all_replayed_pixels_depth_exact'] is True,'Incomplete/inexact GPU analysis')
        for comparison in comparisons:
            key=(comparison['mode'],comparison['frame']);old,new=before[key],after[key]
            for channel,region in (('color',slice(0,153600)),('depth',slice(153612,None))):
                require((ROOT/old['binary']).read_bytes()[region]==(ROOT/new['binary']).read_bytes()[region],
                        ('Actual GPU color/depth mismatch',mode,key,channel))
                require(comparison[channel]['different_pixels']==0 and comparison[channel]['total_pixels']==76800,
                        'GPU analysis differs from raw output')
            require(comparison['baseline_gpu_cycles']==old['stats']['cycles'] and
                    comparison['candidate_gpu_cycles']==new['stats']['cycles'] and
                    comparison['baseline_commands']==old['commands'] and comparison['candidate_commands']==new['commands'],
                    'GPU analysis counter/command binding mismatch')
            require(old['commands']==new['commands'],'GPU opcode counts changed')
            differences.append(100*(new['stats']['cycles']/old['stats']['cycles']-1))
    # Bind the concrete rejected experiment so it cannot be represented as part
    # of the selected performance gain or silently reintroduced by publication.
    evidence.remember(OUT/'guardband-v2/review/verdict.txt')
    evidence.remember(OUT/'guardband-v2/review/seam-cause.json')
    result=dict(candidate=candidate_name,parent='render-v6',qualified_for_device_trial=True,
        target_verified=False,last_observed_device_fps=22.2,target_fps=25,
        source_sha256=source_hashes,source_tree_sha256=source_tree,
        normal_elf_sha256=builds['normal']['elf_sha256'],diagnostic_elf_sha256=builds['diagnostic']['elf_sha256'],
        evidence_sha256=evidence.files,source_input_closure=closure,build_bindings=builds,
        normal_vs_normal=results['normal']['gameplay'],fps_vs_render_v6=results['fps']['gameplay'],
        window_results={mode:r['windows'] for mode,r in results.items()},
        gpu_color_depth_exact_comparisons=32,gpu_memory_modes=[1,2],gpu_frames=list(FRAMES),
        gpu_component_cycle_change_percent_range=[min(differences),max(differences)],
        gpu_command_words_unchanged_every_frame=True,retained_core_sha256=publisher.CORE,
        guardband_included=False,guardband_rejected_reason='Known opaque shared-edge background holes; its 5.2443% model gain is excluded.',
        normal_baseline_reuse='render-v5 normal ELF is SHA-identical to render-v6 normal ELF.',
        scope='Actual guest instruction counts and modeled CPU cycles on the paired 721-frame route, with 361 gameplay frames. Separate frozen-A GPU RTL replays do not model CPU/GPU overlap. No claim of hardware FPS, 25FPS, new FPGA timing/area or seed sweep.')
    evidence.remember(CAMPAIGN/'render-v6/app.elf',BASE_ELFS['normal'])
    evidence.remember(OUT/'baseline/normal.elf',BASE_ELFS['normal'])
    evidence.remember(OUT/'baseline/diagnostic.elf',BASE_ELFS['fps'])
    publisher.local_retained()
    evidence.unchanged()
    return result


def report(result):
    normal=result['normal_vs_normal']['metrics'];fps=result['fps_vs_render_v6']['metrics']
    return (f"{result['candidate']}: qualified for a device trial; 25FPS remains unverified.\n\n"
        f"Normal vs render-v6 normal: {normal['est_cycles']['saving_percent']:.4f}% fewer modeled CPU cycles; "
        f"{normal['insns']['saving_percent']:.4f}% fewer guest instructions.\n"
        f"FPS build vs render-v6 FPS: {fps['est_cycles']['saving_percent']:.4f}% fewer modeled CPU cycles; "
        f"{fps['insns']['saving_percent']:.4f}% fewer guest instructions.\n"
        "Both matched gameplay traces are identical; every gameplay window improves both metrics.\n"
        "All 32 paired frozen-A GPU comparisons have exact color/depth; GPU words are unchanged in every frame.\n"
        "All 6747 source inputs, compiler dependencies, both builds, capture outputs, replay outputs/logs and simulators are SHA-bound.\n\n"
        "The last physical measurement was 22.2FPS on render-v6/F4. Modeled cycles are not device FPS; 25FPS is not established.\n"
        "The FPGA remains unchanged. No fit or seed sweep was run for this software qualification.\n"
        "Guardband's 5.2443% modeled gain is excluded because opaque shared edges exposed background pixels.\n"
        "This script performs no publication or SD-card writes.\n")


def create_once(path, contents):
    data=contents.encode()
    if path.exists():
        require(not path.is_symlink() and path.read_bytes()==data,('Preserve different existing record',str(path)))
        return
    # Hard-link an fsynced temporary inode: complete-file publication without
    # replacing an existing selection if another process creates it meanwhile.
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.link(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate')
    parser.add_argument('--write-qualified',action='store_true',help='Create phase2/QUALIFIED.json last, only after all gates pass.')
    args=parser.parse_args()
    result=qualify(args.candidate)
    text=report(result);serialized=json.dumps(result,indent=2)+'\n'
    candidate=OUT/args.candidate
    create_once(candidate/'qualification-report.txt',text)
    create_once(candidate/'qualification.json',serialized)
    if args.write_qualified:create_once(OUT/'QUALIFIED.json',serialized)
    print(text,end='')


if __name__=='__main__':main()
