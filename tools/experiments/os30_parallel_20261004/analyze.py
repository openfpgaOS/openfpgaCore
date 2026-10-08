#!/usr/bin/env python3
"""Audit the parallel-geometry feasibility evidence; never publish a bitstream."""
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-parallel-20261004'
PRIOR=ROOT/'docs/measurements/os30-frontend-20261004'
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def fit_report(path):
    result=read(path/'results/pocket-os30-s2/result.json');assert result['valid']
    rpt=(path/'results/pocket-os30-s2/ap_core.fit.rpt').read_text(errors='replace')
    result['labs']=int(re.search(r'Total LABs:.*?;\s*([\d,]+) /',rpt)[1].replace(',',''))
    result['dsps']=int(re.search(r'Total DSP Blocks\s*;\s*([\d,]+) /',rpt)[1].replace(',',''))
    sta=(Path(result['directory'])/'output_files/ap_core.sta.rpt').read_text(errors='replace')
    clocks=[]
    for line in sta.splitlines():
        c=[x.strip() for x in line.split(';')]
        if len(c)>4 and c[3]=='ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk' and re.fullmatch(r'[0-9.]+ MHz',c[1]):
            clocks.append(float(c[1].split()[0]))
    assert clocks;result['minimum_main_fmax_mhz']=min(clocks)
    lines=rpt.splitlines();start=next(i for i,l in enumerate(lines) if l.startswith('; Fitter Resource Utilization by Entity'))
    resources=[]
    for line in lines[start+5:]:
        if line.startswith('+'):break
        c=[x.strip() for x in line.split(';')][1:-1]
        if len(c)>=17:
            resources.append(dict(hierarchy=c[14],alms=float(c[1].split()[0]),
                                  m10ks=int(c[10].split()[0]),dsps=int(c[11].split()[0])))
    result['hierarchy']=resources
    result['report_sha256']=sha(path/'results/pocket-os30-s2/ap_core.fit.rpt')
    return result


def frame_ms(p):
    rs=[r for r in csv.DictReader((p/'counters.csv').open()) if r['kind']=='submit']
    assert len(rs)==8
    return (int(rs[-1]['cycle'])-int(rs[0]['cycle']))/700000


def main():
    report=dict(scope=__doc__,deployable=False,fits={},headroom=[],raster_replays={},tests={})
    report['fits']['attribute_worker']=fit_report(PRIOR/'fit-mixer-serial-v7')
    for label in ['v1','v2']:
        path=OUT/('fit-backend-'+label)
        report['fits']['backend_'+label]=fit_report(path)
        frozen=path/'frozen'
        for rel,digest in read(path/'frozen-sources.json').items(): assert sha(frozen/rel)==digest,rel
        rtl=OUT/('edges-rtl' if label=='v2' else 'planes-rtl')
        for name in ['gpu_core.v','gpu_edge_walker.v','audio_mixer.v']:
            assert sha(rtl/name)==sha(frozen/'src/fpga/common'/name)
        if label=='v2':assert sha(rtl/'gpu_span_walker.v')==sha(frozen/'src/fpga/common/gpu_span_walker.v')
        inv=read(path/'inventory.json');assert len(inv)==1 and inv[0]['seed']==2
        assert inv[0]['cpu_sha256']==read(PRIOR/'fit-mixer-serial-v7/inventory.json')[0]['cpu_sha256']
    for p in sorted(OUT.glob('run-*')):
        m=read(p/'inputs.json');assert m['success'] and m['counterfactual']
        for name,digest in m['sha256'].items():assert sha(Path(name))==digest,name
        e=m['environment'];start=int(e['COUPLED_START_FRAME']);traffic=int(e['MEMORY_TRAFFIC'])
        baseline=PRIOR/f'run-release-{start}-t{traffic}'
        for frame in range(start,start+8):
            name=f'frame_{frame:05}.bin';assert (p/name).read_bytes()==(baseline/name).read_bytes()
        status=read(p/'coupled.json');assert status['frames']==status['presented']==8
        assert status['memory_errors']==0 and not status['ring_overflow']
        old,new=frame_ms(baseline),frame_ms(p)
        report['headroom'].append(dict(case=e['PARALLEL_CASE'],start=start,traffic=traffic,
            baseline_ms=old,counterfactual_ms=new,throughput_gain_percent=100*(old/new-1),
            assumed_cpu_cost_discount=read(p/'headroom.json')))
    for label,filename in [('planes','plane-checks.json'),('edges','edges-checks.json')]:
        result=read(OUT/filename);assert result['passed']
        for name,digest in result['executable_sha256'].items(): assert sha(Path(name))==digest,name
        for row in result['results']:
            folder=OUT/('edges-checks' if label=='edges' else 'plane-checks')/row['name']
            assert sha(folder/'backend.bin')==row['output_sha256']
            assert (folder/'backend.bin').read_bytes()==(folder/'reference.bin').read_bytes()
            assert sha(folder/'planes.vec')==row['planes_sha256']
        report['raster_replays'][label]=result
    report['tests']['span_walker']=read(OUT/'span-unit/result.json')
    assert report['tests']['span_walker']['passed']
    for name,digest in report['tests']['span_walker']['sources'].items():assert sha(Path(name))==digest,name
    report['limits']=[
        'No new mesh processor, DMA reader or compiled-mesh client is implemented. Raster backends cannot run current games by themselves.',
        'CPU sensitivity discounts original instruction timing without implementing the replacement work. It retains original command volume and real GPU/SDRAM RTL backpressure; it omits new DMA/processor costs.',
        'Raster inputs were extracted from the original RTL after setup. Faster replay measures ready-triangle consumption, not complete-system acceleration.',
        'CPU/cache time is estimated. Game steps are fixed and external memory traffic is synthetic. Real CPU bus-stall feedback and kernel PCM/interrupt costs are omitted.',
        'Fitted ALMs are not a complete placement budget. Backend v2 still occupies 1793 of 1848 LABs; the actual processor plus DMA must be fitted.',
        'The clock remains configured at 100 MHz. Neither backend closes all timing checks; no clock or timing exceptions were relaxed.',
        'The edge packet truncates slopes only after full 33-bit prestep calculations. A new producer must preserve this rule and the existing corrected edge coverage.',
        'Quake II source was reviewed for compatibility, but no Quake II full-game performance result is claimed.',
        'No full seed sweep, live RTL/app publication, commit or SD-card deployment.'
    ]
    (OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['OS30 PARALLEL GEOMETRY FEASIBILITY — 2026-10-04','',
           'Conclusion: replace the current geometry, vertex cache, triangle-plane',
           'derivation and edge setup with one batched geometry processor. Keep',
           'the pixel pipeline, CPU/FPU, caches, texture bus, mixer and combiner.',
           'The prepared-triangle raster backend is implemented and tested;',
           'the new processor and mesh DMA/client remain to be implemented.','',
           'Saved seed 2; complete os30 infrastructure, normal 128 KiB CPU D-cache:',
           'Design                 ALMs   LABs  M10Ks DSPs  Main Fmax  Worst setup']
    for label,f in report['fits'].items():
        lines.append(f"{label:22} {f['alms']:5d}  {f['labs']:4d}  {f['m10ks']:5d} {f['dsps']:4d}  {f['minimum_main_fmax_mhz']:8.2f}  {f['worst']['setup']:11.3f} ns")
    a,b=report['fits']['attribute_worker'],report['fits']['backend_v2']
    lines+=['',f"Measured removal: {a['alms']-b['alms']} ALMs, {a['m10ks']-b['m10ks']} M10Ks, {a['labs']-b['labs']} occupied LABs.",
            f"Remaining device capacity: {18480-b['alms']} nominal ALMs, {1848-b['labs']} wholly unused LABs, {308-b['m10ks']} M10Ks, {66-b['dsps']} DSPs.",
            'Main Fmax is the minimum reported main-clock value across slow corners.',
            'All-check worst setup also includes external SDRAM timing. Neither is timing closed.','',
            'CPU sensitivity (NOT an implemented processor speedup):',
            'Case          Start Traffic Baseline ms What-if ms Throughput change']
    for r in report['headroom']:
        lines.append(f"{r['case']:13} {r['start']:5d} {r['traffic']:7d} {r['baseline_ms']:11.4f} {r['counterfactual_ms']:10.4f} {r['throughput_gain_percent']:16.2f}%")
    lines+=['','mesh75 reduces bounds/matrix preparation, face selection and packing/upload self-time by 75%.',
            'geometry75 adds vertex-load/fallback and triangle/clipping fallback functions.',
            'renderer90 additionally reduces display-list/material dispatch, using a 90% discount.',
            'Busy-loop instructions grow under backpressure: the discounted-cycle counter is not',
            'a pure estimate of useful work transferred to the processor. Compare whole-frame times.','',
            'Prepared-raster replays (precomputed inputs; NOT game FPS):']
    for label,result in report['raster_replays'].items():
        rows=result['results'];entrance=next(r for r in rows if r['name']=='final-hidden-m1-f360')
        lines.append(f"{label}: {len(rows)} exact full-memory/fence replays, {sum(r['planes'] for r in rows)} packets; entrance GPU replay {entrance['cycles']['extract']} -> {entrance['cycles']['backend']} cycles ({entrance['raster_only_gain_percent']:.2f}% throughput).")
    lines+=['',report['tests']['span_walker']['result'].strip(),
            f"{len(report['headroom'])*8} headroom-run color/depth comparisons exact; control timestamps and counters match the retained baseline.",
            '', 'Limits:']+['  '+s for s in report['limits']]
    (OUT/'REPORT.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:20]))


if __name__=='__main__':main()
