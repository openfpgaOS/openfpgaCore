#!/usr/bin/env python3
"""Fixed-flip Q2 CPU benchmark; GPU rendering requires separate RTL validation."""
import argparse
import collections
import concurrent.futures
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parent
CORE = Path('/home/alberto/Repos/openfpgaCore')
OLD = CORE/'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
OBSERVER = CORE/'docs/measurements/quake2-comprehensive-20261008/frame-pixels-v2'
PIN = 'a458b651273b7bd10d8351b1689c2d60971cd170f06e4b5c26ea5d41bb52fcc2'
WARMUP = 20
WORK = {'main': 360, 'sound': 120, 'live': 120}
FIELDS = ('frame', 'host_framecount', 'buffer', 'gpu_cmd_words', 'gpu_pixels',
          'hash_cmd', 'hash_px', 'hash_z', 'hash_pal', 'mem_hazards', 'gpu_barriers', 'gpu_wait_px')
LIMITS = [
 'Scalar OS25-derived CPU/cache estimates with OS30 cache sizes; not Vexii measurements or Pocket FPS.',
 'Fixed guest clock is 5000000 + ceil(flips*1000000/30), independent of instruction counts.',
 'GPU functional model records 3D commands but does not rasterize them; full RGB565 capture covers CPU framebuffer output.',
 'Physical GPU/audio/CPU/scanout contention and kernel timing are not modeled; sound services use functional stubs.',
 'Main/sound use timedemo; live uses fixedtime33334 and +forward from demo1 spawn, not prolonged combat.',
 'Texture uploads are instantaneous; identical commands and framebuffer bytes do not alone prove texture-memory equality.',
 'Startup is excluded from CPU metrics; exact output checks include startup and trailing command records.',
 'GPU-on and GPU-off commands differ; run completion and CPU framebuffer equality are not a geometry correctness gate. Separate RTL output validation is required.'
]

def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def put(p, value):
    Path(p).write_text(json.dumps(value, indent=2)+'\n')

def rows(p, delimiter=','):
    with Path(p).open() as f:
        return list(csv.DictReader(f, delimiter=delimiter))

def prepare():
    assert not (ROOT/'prepared.json').exists()
    previous=json.loads((OBSERVER/'prepared.json').read_text())
    assert sha(OBSERVER/'qsim/qsim') == previous['observer_qsim_sha256'] == PIN
    for name, digest in previous['source_sha256'].items():
        assert sha(OBSERVER/'qsim'/name) == digest, name
    shutil.copytree(OBSERVER/'qsim', ROOT/'qsim')
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(ROOT/'qsim-worker-cap.patch')],cwd=ROOT/'qsim',check=True)
    subprocess.run(['make','-j4'],cwd=ROOT/'qsim',check=True)
    common='set ofgl_bisect 0\nset cl_showfps 0\nset logfile 0\n'
    configs={
      'main':common+'set timedemo 1\nset s_initsound 0\nalias d1 "demomap q2demo1.dm2"\n',
      'sound':common+'set timedemo 1\nset s_initsound 1\nalias d1 "demomap q2demo1.dm2"\n',
      'live':common+'set timedemo 0\nset busywait 0\nset fixedtime 33334\nset s_initsound 1\nalias d1 "map demo1; +forward"\n'}
    for name, config in configs.items():
        work=ROOT/'inputs'/name
        (work/'data').mkdir(parents=True)
        for filename in ('quake2.app','quake2.ini'):
            shutil.copy2(OLD/filename, work/filename)
        shutil.copy2(OLD/'data/pak0.pak',work/'data/pak0.pak')
        (work/'data/config.cfg').write_text(config)
    pins={str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'inputs').rglob('*')) if p.is_file()}
    pins['qsim/qsim']=sha(ROOT/'qsim/qsim')
    for filename,digest in previous['source_sha256'].items():
        pins['qsim/'+filename]=sha(ROOT/'qsim'/filename)
    put(ROOT/'prepared.json',dict(pins=pins,observer_parent=str(OBSERVER),
        observer_parent_manifest_sha256=sha(OBSERVER/'prepared.json'),frames=WORK,
        startup_frames=WARMUP,caps='hw_features=0x2b136753 FASTTEX=1; controlled Q2_BENCH_GEOMETRY=0/inline/worker advertises status10/11',limits=LIMITS,
        observer_delta='Added only Q2_GPU_WORKER_CAP to gpu.c status read; when unset status is unchanged.'))
    print('Prepared',ROOT,flush=True)

def verify():
    prepared=json.loads((ROOT/'prepared.json').read_text())
    for name,digest in prepared['pins'].items():
        assert sha(ROOT/name)==digest,name
    return prepared

def function_profile(path):
    result=collections.defaultdict(collections.Counter)
    for row in rows(path/'profile.tsv','\t'):
        name=re.sub(r'\.(?:isra|constprop|lto_priv|part)\..*$', '', row['function'])
        for key in ('self_insns','self_cycles','calls','tailcalls'):
            result[name][key]+=int(row[key])
    services=collections.Counter()
    for row in rows(path/'profile_calls.tsv','\t'):
        if row['kind']=='service': services[row['callee']]+=int(row['calls'])
    return result,services

def captures(dest, csvrows):
    data=(dest/'pixels.bin').read_bytes();pos=0;hashes={}
    for row in csvrows:
        header=struct.unpack_from('<QIIIIII',data,pos);pos+=32
        assert header==(int(row['frame']),int(row['buffer']),320,240,640,3,640),header
        frame=data[pos:pos+153600];pos+=153600
        assert len(frame)==153600
        hashes[row['frame']]=hashlib.sha256(frame).hexdigest()
    assert pos==len(data)
    # Outside the legacy 160-column RGB565 oracle, inside the complete capture.
    mutated=bytearray(frame);mutated[639]^=1
    assert mutated!=frame and mutated[:320]==frame[:320]
    data=(dest/'commands.bin').read_bytes();pos=0;counts=collections.Counter();ncmd=0
    while pos<len(data):
        frame,header=struct.unpack_from('<QI',data,pos)
        count=(header&8191)+1
        pos+=8+4*count
        assert pos<=len(data)
        counts[frame]+=count;ncmd+=1
    assert pos==len(data)
    for row in csvrows:
        assert counts[int(row['frame'])]==int(row['gpu_cmd_words']),row['frame']
    return dict(pixel_sha256=sha(dest/'pixels.bin'),command_sha256=sha(dest/'commands.bin'),
                command_count=ncmd,full_frame_sha256=hashes,right_half_negative_control=True)

def run_work(label, elf, workload):
    source=ROOT/'inputs'/workload
    dest=ROOT/'runs'/label/workload
    dest.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_','Q2_GPU_'))}
    mode=os.environ.get('Q2_BENCH_GEOMETRY','0')
    assert mode in ('0','inline','worker')
    if mode!='0': env['SM64_GEOMETRY_CAP']='1'
    if mode=='worker': env['Q2_GPU_WORKER_CAP']='1'
    summaries=[]
    for suffix,frames in (('startup',WARMUP),('full',WORK[workload])):
        out=dest/suffix
        command=[str(ROOT/'qsim/qsim'),str(elf),'--out',str(out),'--app',str(source/'quake2.app'),
            '--data-dir',str(source/'data'),'--profile','--frames',str(frames),
            '--max-insns','4e9','--gpu-sched','eager','--mem','flat',
            '--no-normalize-build-stamp','--label',label+'-'+workload+'-'+suffix]
        capture_env=dict(env)
        if suffix=='full':
            capture_env.update(QSIM_COMMAND_TRACE=str(dest/'commands.bin'),QSIM_FRAME_PIXELS=str(dest/'pixels.bin'))
        put(dest/(suffix+'-command.json'),dict(argv=command,cwd=str(source),
            environment={k:v for k,v in capture_env.items() if k.startswith(('QSIM_','SM64_','Q2_GPU_'))}))
        with (dest/(suffix+'.stdout.log')).open('w') as stdout,(dest/(suffix+'.stderr.log')).open('w') as stderr:
            subprocess.run(command,cwd=source,env=capture_env,stdout=stdout,stderr=stderr,timeout=300,check=True)
        summary=json.loads((out/'summary.json').read_text())
        console=(out/'console.log').read_text()
        assert summary['frames']==frames and summary['stop_reason']=='frame limit',summary
        assert summary['elf_sha256']==sha(elf)
        assert summary['env']['unmapped_mmio']==summary['gpu_sync']['ring_overflows']==0
        assert ('FASTTEX=1 GEOMETRY='+str(int(mode!='0'))) in console and '0x2b136753' in console
        summaries.append(summary)
    full=rows(dest/'full/frames.csv');steady=full[WARMUP:]
    assert len(full)==WORK[workload]
    proof=captures(dest,full)
    before,services0=function_profile(dest/'startup')
    after,services1=function_profile(dest/'full')
    functions={k:{metric:after[k][metric]-before[k][metric] for metric in
        ('self_insns','self_cycles','calls','tailcalls')} for k in sorted(set(before)|set(after))}
    cycles=sum(int(row['est_cycles']) for row in steady)/len(steady)
    profile=sorted((dict(function=k,cycles_per_frame=v['self_cycles']/len(steady),
        percent=100*v['self_cycles']/len(steady)/cycles,**v) for k,v in functions.items()),
        key=lambda row:row['self_cycles'],reverse=True)
    result=dict(passed=True,label=label,workload=workload,geometry_mode=mode,elf_sha256=sha(elf),
        frames=len(full),steady_frames=len(steady),steady_frame_indices=f'{WARMUP}..{len(full)-1}',
        cycles_per_frame=cycles,instructions_per_frame=sum(int(row['insns']) for row in steady)/len(steady),
        words_per_frame=sum(int(row['gpu_cmd_words']) for row in steady)/len(steady),
        functions=profile,services={k:services1[k]-services0[k] for k in set(services0)|set(services1)},
        captures=proof,limits=LIMITS)
    put(dest/'result.json',result)
    print(json.dumps({k:result[k] for k in ('label','workload','frames','steady_frames','cycles_per_frame','words_per_frame')}),flush=True)
    return result

def run(label,elf,workloads):
    verify();assert re.fullmatch(r'[A-Za-z0-9_-]+',label)
    source=Path(elf).resolve();frozen=ROOT/'elf'/(label+'.elf');frozen.parent.mkdir(exist_ok=True)
    if frozen.exists(): assert sha(frozen)==sha(source),'Label already holds a different executable'
    else: shutil.copy2(source,frozen)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tasks=[pool.submit(run_work,label,frozen,w) for w in workloads]
        result=[task.result() for task in tasks]
    assert sha(source)==sha(frozen)
    verify()
    result=[json.loads((ROOT/'runs'/label/w/'result.json').read_text()) for w in WORK
            if (ROOT/'runs'/label/w/'result.json').exists()]
    assert all(r['elf_sha256']==sha(frozen) for r in result)
    put(ROOT/'runs'/label/'result.json',dict(passed=True,label=label,elf_sha256=sha(frozen),
        results=[{k:r[k] for k in ('workload','steady_frames','cycles_per_frame','words_per_frame')} for r in result]))

def compare(candidate,baseline,workloads):
    verify();reports=[]
    for workload in workloads:
        a=ROOT/'runs'/baseline/workload;b=ROOT/'runs'/candidate/workload
        ra=json.loads((a/'result.json').read_text());rb=json.loads((b/'result.json').read_text())
        assert ra['passed'] and rb['passed']
        aa=rows(a/'full/frames.csv');bb=rows(b/'full/frames.csv');assert len(aa)==len(bb)
        differences=[dict(frame=i,field=k,baseline=x[k],candidate=y[k])
            for i,(x,y) in enumerate(zip(aa,bb)) for k in FIELDS if x[k]!=y[k]]
        cmd_same=(a/'commands.bin').read_bytes()==(b/'commands.bin').read_bytes()
        pixels_same=(a/'pixels.bin').read_bytes()==(b/'pixels.bin').read_bytes()
        af={x['function']:x for x in ra['functions']};bf={x['function']:x for x in rb['functions']}
        functions=[]
        for name in set(af)|set(bf):
            av=af.get(name,{}).get('cycles_per_frame',0);bv=bf.get(name,{}).get('cycles_per_frame',0)
            functions.append(dict(function=name,baseline=av,candidate=bv,delta=bv-av))
        functions.sort(key=lambda x:abs(x['delta']),reverse=True)
        same_sound={k:v for k,v in ra['services'].items() if 'mixer_' in k or 'audio_' in k}=={
            k:v for k,v in rb['services'].items() if 'mixer_' in k or 'audio_' in k}
        result=dict(passed=not differences and cmd_same and pixels_same and same_sound,
            workload=workload,baseline=baseline,candidate=candidate,
            baseline_elf_sha256=ra['elf_sha256'],candidate_elf_sha256=rb['elf_sha256'],
            baseline_cycles_per_frame=ra['cycles_per_frame'],candidate_cycles_per_frame=rb['cycles_per_frame'],
            change_percent=100*(rb['cycles_per_frame']/ra['cycles_per_frame']-1),
            all_commands_order_and_frame_assignments_including_tail_exact=cmd_same,
            complete_RGB565_all_frames_exact=pixels_same,sound_service_counts_exact=same_sound,
            compared_frame_fields=FIELDS,frame_mismatch_count=len(differences),first_frame_mismatches=differences[:20],
            largest_function_deltas=functions[:20],limits=LIMITS)
        reports.append(result)
        print(json.dumps({k:result[k] for k in ('workload','passed','change_percent',
          'all_commands_order_and_frame_assignments_including_tail_exact','complete_RGB565_all_frames_exact','frame_mismatch_count')}),flush=True)
    target=ROOT/'comparisons';target.mkdir(exist_ok=True)
    output=target/(candidate+'-vs-'+baseline+'.json')
    previous=json.loads(output.read_text()) if output.exists() else []
    merged={r['workload']:r for r in previous}
    merged.update({r['workload']:r for r in reports})
    put(output,[merged[w] for w in WORK if w in merged])
    if not all(r['passed'] for r in reports):raise SystemExit(1)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='action',required=True)
    sub.add_parser('prepare')
    r=sub.add_parser('run');r.add_argument('label');r.add_argument('elf',type=Path)
    r.add_argument('--workloads',nargs='+',choices=WORK,default=list(WORK))
    c=sub.add_parser('compare');c.add_argument('candidate');c.add_argument('--baseline',default='baseline')
    c.add_argument('--workloads',nargs='+',choices=WORK,default=list(WORK))
    args=ap.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='run':run(args.label,args.elf,args.workloads)
    else:compare(args.candidate,args.baseline,args.workloads)
