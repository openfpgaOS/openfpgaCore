#!/usr/bin/env python3
"""Passive host capture of model-cycle GPU publications for sensitivity replay."""
from pathlib import Path
import argparse, csv, difflib, hashlib, json, os, shutil, struct, subprocess

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-batch-memory-20261008'
S=R/'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
O=P/'publish-trace-v1'
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b,1)

def prepare():
    original=json.loads((S/'prepared.json').read_text());O.mkdir(exist_ok=False)
    for rel,digest in original['inputs'].items():
        if not rel.startswith('qsim/'):continue
        assert sha(S/rel)==digest,rel
        target=O/rel;target.parent.mkdir(exist_ok=True);shutil.copy2(S/rel,target)
    f=O/'qsim/env.c';old=f.read_text();new=old
    anchor='static void frame_event(qenv *E, int idx, uint32_t token)\n{'
    observer=r'''/* Passive host output. Never writes guest/model state. */
static void submission_event(qenv *E, uint32_t kind, uint32_t a,
                             uint64_t aux, const uint32_t *words)
{
    static FILE *file;
    static int checked;
    if (!checked) {
        const char *path=getenv("QSIM_SUBMISSION_TRACE");
        if(path) {file=fopen(path,"wb");if(!file){perror(path);exit(2);}}
        checked=1;
    }
    if(!file)return;
    uint64_t frame=E->flips,cycle=E->cpu->model ? E->cpu->model->cyc : 0;
    if(fwrite(&kind,4,1,file)!=1 || fwrite(&a,4,1,file)!=1 ||
       fwrite(&frame,8,1,file)!=1 || fwrite(&cycle,8,1,file)!=1 ||
       fwrite(&aux,8,1,file)!=1 || (words && fwrite(words,4,a,file)!=a)) {
        perror("submission trace");exit(2);
    }
}

'''
    new=once(new,anchor,observer+anchor+'\n    submission_event(E,1,(uint32_t)idx,token,NULL);')
    new=once(new,'static void vec_begin(qenv *E)\n{',
        'static void vec_begin(qenv *E)\n{\n    submission_event(E,3,0,E->cpu->instret_live,NULL);')
    new=once(new,"            vec_u32(E, GV_TAG('C', 'M', 'D', 'S'));",
        "            submission_event(E,4,E->vec_n,E->vec_frame,E->vec_words);\n            vec_u32(E, GV_TAG('C', 'M', 'D', 'S'));")
    new=once(new,'        *val = size == 4 ? v : (v >> ((off & 3) * 8));\n        return 0;',
        '''        if(E->vec_f && ((off&~3u)==0x18 || (off&~3u)==0x10))
            submission_event(E,(off&~3u)==0x18?5:6,v,E->vec_frame,NULL);
        *val = size == 4 ? v : (v >> ((off & 3) * 8));
        return 0;''')
    new=once(new,'        if (E->gpu) gpu_wait_fence(E->gpu, a1);',
        '        if(E->vec_f) submission_event(E,7,a1,E->vec_frame,NULL);\n        if (E->gpu) gpu_wait_fence(E->gpu, a1);')
    f.write_text(new)
    (O/'source.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='env.c',tofile='env.c')))
    with (O/'build.log').open('w') as log:subprocess.run(['make','-j2'],cwd=O/'qsim',stdout=log,stderr=subprocess.STDOUT,check=True)
    put(O/'prepared.json',dict(passed=True,parent_qsim_sha256=sha(S/'qsim/qsim'),
        qsim_sha256=sha(O/'qsim/qsim'),recipe_sha256=sha(Path(__file__)),
        sources={str(x.relative_to(O)):sha(x) for x in (O/'qsim').iterdir() if x.suffix in ['.c','.h']},
        record='little endian <IIQQQ>: kind,a,flip,modelcycle,aux; kind4 followed by a command words',
        kinds={'1':'frame event','3':'vector start','4':'vector publish','5':'fence observation','6':'ring read pointer observation','7':'kernel fence wait'},
        limits=['OS25-derived estimated cycles, not actual Vexii timestamps.',
                'Eager functional GPU has no modeled GPU waits or contention; timed replay must enforce dependencies separately.']))

def records(path):
    data=path.read_bytes();pos=0;out=[]
    while pos<len(data):
        kind,a,frame,cyc,aux=struct.unpack_from('<IIQQQ',data,pos);pos+=32
        words=[]
        if kind==4:words=list(struct.unpack_from('<'+'I'*a,data,pos));pos+=4*a
        out.append(dict(kind=kind,a=a,frame=frame,cycle=cyc,aux=aux,words=words))
    assert pos==len(data)
    assert all(a['cycle']<=b['cycle'] for a,b in zip(out,out[1:]))
    return out

def run(label):
    prep=json.loads((O/'prepared.json').read_text());assert sha(Path(__file__))==prep['recipe_sha256']
    assert sha(O/'qsim/qsim')==prep['qsim_sha256']
    for rel,h in prep['sources'].items():assert sha(O/rel)==h
    baseline=json.loads((S/(label+'-result.json')).read_text());assert baseline['passed']
    dest=O/label;assert not dest.exists()
    env={k:v for k,v in os.environ.items() if not k.startswith(('QSIM_','SM64_'))}
    env['QSIM_SUBMISSION_TRACE']=str(O/(label+'.trace'))
    cmd=[str(O/'qsim/qsim'),str(S/(label+'.elf')),'--out',str(dest),'--app',str(S/'quake2.app'),
         '--data-dir',str(S/'data'),'--profile','--frames','362','--max-insns','4e9','--gpu-sched','eager',
         '--mem','flat','--no-normalize-build-stamp','--label',label,'--gpuvec','20,80,180,300']
    with (O/(label+'.log')).open('w') as log:subprocess.run(cmd,cwd=S,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    assert (dest/'frames.csv').read_bytes()==(S/label/'frames.csv').read_bytes()
    old=json.loads((S/label/'summary.json').read_text());new=json.loads((dest/'summary.json').read_text())
    for v in [old,new]:del v['wall_seconds'],v['guest_mips']
    assert old==new
    for rel in ['profile.tsv','profile_calls.tsv','console.log']:
        assert (dest/rel).read_bytes()==(S/label/rel).read_bytes(),rel
    for frame in [20,80,180,300]:
        rel=f'gpuvec/frame_{frame:05}.vec';assert sha(dest/rel)==sha(S/label/rel),rel
    events=records(O/(label+'.trace'));starts={x['frame']:x for x in events if x['kind']==3}
    stats=[]
    for frame in [20,80,180,300]:
        pubs=[x for x in events if x['kind']==4 and x['aux']==frame];assert pubs
        times=[x['cycle'] for x in pubs]
        row=dict(frame=frame,capture_start=starts[frame]['cycle'],first_publish=times[0],last_publish=times[-1],
                 batches=len(pubs),words=sum(x['a'] for x in pubs),largest_gap=max(b-a for a,b in zip(times,times[1:])),
                 publish_span=times[-1]-times[0],cross_frame_publishes=sum(x['frame']!=frame for x in pubs))
        put(O/f'{label}-frame-{frame:05}.json',dict(summary=row,events=[x for x in events if x['kind'] in [4,5,6,7] and x['aux']==frame]))
        stats.append(row)
    put(O/(label+'-result.json'),dict(passed=True,elf_sha256=baseline['elf_sha256'],command=cmd,
        prepared_sha256=sha(O/'prepared.json'),trace_sha256=sha(O/(label+'.trace')),
        original_frames_summary_profiles_console_and_vectors_exact=True,frames=stats,limits=prep['limits']))
    print(json.dumps(stats,indent=2))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run']);a.add_argument('--label',default='thorough_combined6');n=a.parse_args()
    if n.action=='prepare':prepare()
    else:run(n.label)
