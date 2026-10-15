#!/usr/bin/env python3
"""Compare complete, hash-bound native intervals; never infer device FPS."""
import argparse, bisect, csv, hashlib, json, struct, subprocess
from pathlib import Path
T=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser();p.add_argument('labels',nargs='+');p.add_argument('--out',type=Path,required=True);a=p.parse_args()
inputs={str(Path(__file__)):sha(Path(__file__))};runs={}
for label in a.labels:
    root=T/label;run=root/'run';build=json.loads((root/'build.json').read_text())
    elf=Path(build['elf']);inputs[str(elf)]=sha(elf)
    for n,h in build['inputs_sha256'].items():assert sha(Path(n))==h,n
    def read(name):
        f=run/name;inputs[str(f)]=sha(f);return f.read_bytes()
    status=json.loads(read('result.json'))
    bounds=[list(map(int,line.split())) for line in read('boundaries.tsv').decode().splitlines()]
    assert status['status']==0 and len(bounds)==3
    intervals=[dict(cycles=y[1]-x[1],retired=y[2]-x[2],upload_words=y[3]-x[3]) for x,y in zip(bounds,bounds[1:])]
    names=[];starts=[];sizes=[]
    for line in subprocess.check_output(['riscv64-elf-nm','--defined-only','-S','-n',str(elf)],text=True).splitlines():
        cols=line.split()
        if len(cols)==4 and cols[2] in ('t','T'):
            starts.append(int(cols[0],16));sizes.append(int(cols[1],16));names.append(cols[3])
    functions={}
    for row in csv.DictReader(read('pc-phase-2.csv').decode().splitlines()):
        pc=int(row['pc']);i=bisect.bisect_right(starts,pc)-1
        name=names[i] if i>=0 and pc<starts[i]+sizes[i] else 'unattributed'
        bucket=functions.setdefault(name,dict(retired=0,retirement_gap_cycles=0))
        bucket['retired']+=int(row['retired']);bucket['retirement_gap_cycles']+=int(row['cycles_since_previous_retirement'])
    profile=json.loads(read('gpu-profile.json'));w=profile['windows']['2']
    result=dict(intervals=intervals,errors={key:status[key] for key in ['write_errors','scan_errors','cram_errors']},
                gpu_partition_warm=dict(zip(profile['partition_names'],w['partition'])),
                functions_warm=dict(sorted(functions.items(),key=lambda x:-x[1]['retirement_gap_cycles'])[:50]))
    for prefix in ['import','front']:
        data=read(prefix+'-stats.bin');result[prefix+'_stats_u32']=list(struct.unpack('<'+'I'*(len(data)//4),data))
    runs[label]=result
base=next(iter(runs.values()))['intervals']
for name,r in runs.items():
    r['cycle_change_percent']=[100*(v['cycles']/b['cycles']-1) for v,b in zip(r['intervals'],base)]
out=dict(runs=runs,caveats=['Two fixed intervals with synthetic display/audio and bounded services; not sustained hardware FPS.',
    'Function retirement gaps are attribution, not independent wall-time savings.', 'Same-ELF hardware comparison; physical area/timing require separate qualification.'],inputs_sha256=inputs)
a.out.write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({name:{'intervals':r['intervals'],'cycle_change_percent':r['cycle_change_percent'],'front_stats':r['front_stats_u32']} for name,r in runs.items()},indent=2))
