#!/usr/bin/env python3
"""Collect paired RTL evidence without converting kernel gains into game FPS."""
import hashlib
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT / 'docs/measurements/os30-dual-20261004/validation'
LABELS = ['baseline128', 'single64', 'stock64', 'lean64', 'immediate64']

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--profiles',nargs='+',default=LABELS)
    ap.add_argument('--output',default='summary.json')
    ap.add_argument('--references',nargs='+',default=['baseline128','single64'])
    args=ap.parse_args();labels=args.profiles
    report = dict(scope=__doc__, profiles={}, comparisons={})
    for label in labels:
        path = OUT / label / 'results.json'
        rows = json.loads(path.read_text())['results']
        assert len(rows) == 6, label
        profile = report['profiles'][label] = dict(results_sha256=sha(path),
            build_manifest_sha256=sha(OUT / label / 'build-manifest.json'), modes={})
        for mode in ['quiet','scanout']:
            r = {x['program']:x for x in rows if x['mode']==mode}
            assert r['i2f']['hash']=='71a4bfd3'
            assert r['stress']['interrupts']>=200
            kernels=r['kernels']['kernels']
            assert len(kernels)==14
            first,second=kernels[:7],kernels[7:]
            assert [(x['name'],x['hash']) for x in first]==[(x['name'],x['hash']) for x in second]
            profile['modes'][mode]=dict(interrupts=r['stress']['interrupts'],
                i2f_hash=r['i2f']['hash'],
                kernel_cycles={x['name']:[x['cycles'],y['cycles']] for x,y in zip(first,second)},
                kernel_hashes={x['name']:x['hash'] for x in second})
        icache_path=OUT/label/'icache-results.json'
        if icache_path.exists():
            profile['instruction_capacity']=json.loads(icache_path.read_text())
        for extra in ['frontend','decode-traps','fp-legality']:
            extra_path=OUT/label/(extra+'-results.json')
            if extra_path.exists():profile[extra]=json.loads(extra_path.read_text())
    for reference in args.references:
        compared=report['comparisons'][reference]={}
        for label in labels:
            if label==reference:continue
            compared[label]={}
            for mode in ['quiet','scanout']:
                a=report['profiles'][reference]['modes'][mode]
                b=report['profiles'][label]['modes'][mode]
                assert a['kernel_hashes']==b['kernel_hashes']
                compared[label][mode]={name:{'cycle_change_percent':100*(b['kernel_cycles'][name][1]/cycles[1]-1),
                    'throughput_change_percent':100*(cycles[1]/b['kernel_cycles'][name][1]-1)}
                    for name,cycles in a['kernel_cycles'].items()}
    base_frontend=report['profiles']['baseline128'].get('frontend')
    if base_frontend:
        report['frontend_comparisons']={}
        for label in labels:
            frontend=report['profiles'][label].get('frontend')
            if not frontend:continue
            assert frontend['binary_sha256']==base_frontend['binary_sha256']
            modes=report['frontend_comparisons'][label]={}
            for a,b in zip(base_frontend['results'],frontend['results']):
                assert a['mode']==b['mode'] and a['hash']==b['hash']
                assert b['interrupts']>=50
                modes[a['mode']]=dict(cycles=b['cycles'],interrupts=b['interrupts'],
                    throughput_change_percent=100*(a['cycles']/b['cycles']-1))
    base_traps=report['profiles']['baseline128'].get('decode-traps')
    if base_traps:
        for label in labels:
            traps=report['profiles'][label].get('decode-traps')
            if traps:
                assert traps['binary_sha256']==base_traps['binary_sha256']
                assert len(traps['results'])==2 and all(x['traps']==48 for x in traps['results'])
    base_fp=report['profiles']['baseline128'].get('fp-legality')
    if base_fp:
        for label in labels:
            fp=report['profiles'][label].get('fp-legality')
            if fp:
                assert fp['binary_sha256']==base_fp['binary_sha256']
                assert len(fp['results'])==2 and all(x['traps']==164 and x['cases']==264 for x in fp['results'])
    report['instruction_capacity_comparisons']={}
    base_icache=report['profiles']['baseline128'].get('instruction_capacity')
    if base_icache:
        for label in labels:
            icache=report['profiles'][label].get('instruction_capacity')
            if not icache:continue
            assert icache['binary_sha256']==base_icache['binary_sha256']
            modes=report['instruction_capacity_comparisons'][label]={}
            for a,b in zip(base_icache['results'],icache['results']):
                assert a['mode']==b['mode']
                assert [(x['region_bytes'],x['hash']) for x in a['rows']]==[(x['region_bytes'],x['hash']) for x in b['rows']]
                modes[a['mode']]={x['region_bytes']:dict(cycles=y['cycles'],
                    throughput_change_percent=100*(x['cycles']/y['cycles']-1))
                    for x,y in zip(a['rows'][2:],b['rows'][2:])}
    report['passed']=True
    report['firmware_manifest_sha256']=sha(OUT/'firmware/manifest.json')
    report['workload_origins']=json.loads((OUT/'firmware/workload-origins.json').read_text())
    report['notes']=[
        'CPU, AXI fabric, Pocket SDRAM controller, timer and scanout use RTL. No real FPGA clock/timing effects, audio traffic or active GPU drawing.',
        'Two repeats use identical input/output; the second pass is reported for comparison. Cache contents are not globally flushed between kernels.',
        'The SM64 vector function uses deterministic synthetic vertices. Triangle setup is a private exact software implementation tested against captured scene packets, not current production CPU work.',
        'Cache96 is deliberately larger than D64; its result illustrates a capacity cliff and is not a game FPS estimate.',
        'Benchmark timing uses existing MMIO cycle counter; optional rdcycle CSR is absent from production os30.'
    ]
    (OUT/args.output).write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS: {len(labels)} CPU configurations, {6*len(labels)} RTL program/mode runs, identical checked outputs')
    for name,cycles in report['profiles']['baseline128']['modes']['quiet']['kernel_cycles'].items():
        values=[f'{label} {report["profiles"][label]["modes"]["quiet"]["kernel_cycles"][name][1]}' for label in labels]
        print(name+': '+', '.join(values))

if __name__=='__main__':main()
