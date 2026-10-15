from pathlib import Path
import shutil,subprocess,json,hashlib,os,re,concurrent.futures
W=Path(__file__).resolve().parent/'passive-counts'
V=W.parents[2]/'validation'
W.mkdir(exist_ok=False)
shutil.copytree(V/'qsim',W/'qsim',ignore=shutil.ignore_patterns('build','qsim','*.o'))
p=W/'qsim/prof.c';s=p.read_text();mark='    size_t sd_first = (size_t)(cpu->sd_dc - cpu->dc_base);'
assert s.count(mark)==1
s=s.replace(mark,mark+'''
    /* Passive export of existing per-PC execution counts; no guest changes. */
    fprintf(stderr,"WORLD_COUNTS reserve=%" PRIu64 " fans=%" PRIu64 "\\n",
        cpu->prof->cnt[sd_first + ((0x104071f2u-cpu->sdram_base)>>1)],
        cpu->prof->cnt[sd_first + ((0x10407220u-cpu->sdram_base)>>1)]);
''');p.write_text(s)
with (W/'build.log').open('w') as o: subprocess.run(['make','-j4'],cwd=W/'qsim',stdout=o,stderr=subprocess.STDOUT,check=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def run(work):
    evidence=[];counts={}
    for phase in ['startup','full']:
        old=V/'runs/world-v1'/work
        data=json.loads((old/(phase+'-command.json')).read_text())
        cmd=data['argv'];cmd[0]=str(W/'qsim/qsim');cmd[cmd.index('--out')+1]=str(W/work/phase)
        env=os.environ.copy();env.update(data['environment'])
        if phase=='full':
            env['QSIM_COMMAND_TRACE']=str(W/work/'commands.bin');env['QSIM_FRAME_PIXELS']=str(W/work/'pixels.bin')
        (W/work).mkdir(exist_ok=True)
        with (W/work/(phase+'.stdout.log')).open('w') as so,(W/work/(phase+'.stderr.log')).open('w') as se:
            subprocess.run(cmd,cwd=data['cwd'],env=env,stdout=so,stderr=se,check=True,timeout=300)
        prior=json.loads((old/phase/'summary.json').read_text());new=json.loads((W/work/phase/'summary.json').read_text())
        for field in ['totals','oracle','gpu_sync','frames','elf_sha256']:assert prior[field]==new[field],(work,phase,field)
        for f in ['frames.csv','profile.tsv','profile_calls.tsv']:
            assert sha(old/phase/f)==sha(W/work/phase/f),(work,phase,f)
        if phase=='full':
            for f in ['commands.bin','pixels.bin']:assert sha(old/f)==sha(W/work/f),(work,f)
        matches=re.findall(r'WORLD_COUNTS reserve=(\d+) fans=(\d+)',(W/work/(phase+'.stderr.log')).read_text())
        assert matches
        counts[phase]=dict(zip(['reserve','fans'],map(int,matches[-1])))
        evidence.append({'phase':phase,'exact_totals_frames_profiles_oracle_sync':True,'exact_commands_pixels':phase=='full','counts':counts[phase]})
    n=340 if work=='main' else 100
    steady={k:counts['full'][k]-counts['startup'][k] for k in ['reserve','fans']}
    return {'workload':work,'steady_frames':n,'steady':steady,'reserves_per_frame':steady['reserve']/n,'fans_per_frame':steady['fans']/n,'reserve_reduction_percent':100*(1-steady['reserve']/steady['fans']),'verification':evidence}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as e: results=list(e.map(run,['main','sound','live']))
(W/'result.json').write_text(json.dumps({'observer_only':'exports existing two perPC counters; all instructions/cycles/output retained','addresses':{'reserve':'104071f2','fan_memcpy':'10407220'},'elf_sha256':'661df2c2d20963c1793878baa2e5abab0931b03add01afd956d01d5dc2ac942a','results':results},indent=2)+'\n')
print(json.dumps(results,indent=2))
