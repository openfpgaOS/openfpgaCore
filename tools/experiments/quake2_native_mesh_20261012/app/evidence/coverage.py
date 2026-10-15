#!/usr/bin/env python3
"""Count admitted MD2 draws from unchanged captures/profile entries, not new probes."""
from pathlib import Path
import argparse,collections,hashlib,json,struct

ROOT=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def run(label, root=ROOT):
    reports=[]
    for name in ('main','sound','live'):
        control=root/'validation/runs/world-control'/name/'result.json'
        source=root/'validation/runs'/label/name
        result=source/'result.json'
        if not result.exists():continue
        a=json.loads(control.read_text());b=json.loads(result.read_text())
        profiles=[{v['function']:v for v in x['functions']} for x in (a,b)]
        calls=lambda p:p['R_SetAliasDrawBounds']['calls']+p['R_SetAliasDrawBounds']['tailcalls']
        total,remaining=map(calls,profiles)
        path=source/'commands.bin';data=path.read_bytes();pos=0;native=0
        all_assets=set();steady_assets=set()
        while pos<len(data):
            frame,h=struct.unpack_from('<QI',data,pos);count=h&8191
            if h>>24==0x60:
                assert count==164
                words=struct.unpack_from('<164I',data,pos+12)
                asset=(words[2],words[6]);all_assets.add(asset)
                if frame>=20:native+=1;steady_assets.add(asset)
            pos+=12+4*count
        assert pos==len(data) and total==native+remaining
        reports.append(dict(workload=name,steady_frames=b['steady_frames'],drawn_alias_models=total,
            native_draws=native,CPU_draws=remaining,native_percent=100*native/total,
            native_assets_seen=len(all_assets),native_assets_seen_steady=len(steady_assets),
            exact_control_alias_bounds_calls_equal_native_plus_fallback=True,
            evidence={str(p.relative_to(root)):sha(p)for p in (control,result,path)}))
    return dict(label=label,results=reports,limits=[
        'Counts reflect these fixed demo/live windows, not every map or frame.',
        'R_SetAliasDrawBounds executes once for each CPU-prepared drawn alias; native early returns omit it.',
        'All actual R_DrawAliasModel entries also include culled objects; those are not the denominator.',
        'Native draw count and eliminated bounds-call count agree exactly without changing measured execution.',
        'A draw is not a triangle-weighted workload fraction; these percentages do not predict FPS.'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('label')
    p.add_argument('--measurement-root',type=Path,default=ROOT);a=p.parse_args()
    result=run(a.label,a.measurement_root);out=a.measurement_root/'app-evidence';out.mkdir(exist_ok=True)
    (out/(a.label+'-coverage.json')).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in x.items()if k!='evidence'}for x in result['results']],indent=2))
