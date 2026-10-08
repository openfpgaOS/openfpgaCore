#!/usr/bin/env python3
"""Seal a completed private LOD4 whole-core resource/timing screen."""
import argparse,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def main():
    ap=argparse.ArgumentParser();ap.add_argument('label');a=ap.parse_args()
    assert re.fullmatch(r'load-bram-core[-a-z0-9]+',a.label)
    out=P/a.label;fit=out/'fit-seed2';prep=read(out/'prepared.json');binding=read(out/'component-binding.json')
    for n,h in prep['source_sha256'].items():
        assert sha(out/'frozen'/n)==h,n
        assert sha(fit/'frozen'/n)==h,n
    component=ROOT/binding['component_manifest'];assert sha(component)==binding['component_manifest_sha256']
    for n,d in read(component).items():assert sha(component.parent/n)==d['sha256'],n
    tests=[]
    for r in binding['tests']:
        p=ROOT/r['path'];assert sha(p)==r['sha256'];t=read(p);assert t['passed'];tests.append(t)
    f=read(fit/'results/pocket-os30-s2/result.json');assert f['state'] in ['complete','failed']
    passed=f['returncodes'].get('fit')==0
    report=fit/'jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.fit.rpt';s=report.read_text(errors='replace')
    def number(pattern):return int(re.search(pattern,s).group(1).replace(',',''))
    metrics=dict(alms_needed=number(r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)'),
        reported_placed_estimate=number(r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)'),
        labs_required=number(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)'),
        m10ks=number(r';\s*M10K blocks\s*;\s*([\d,]+)'),dsps=number(r';\s*Total DSP Blocks\s*;\s*([\d,]+)'))
    entities={}
    for line in s.split('; Fitter Resource Utilization by Entity')[1].splitlines():
        r=[a.strip() for a in line.split(';')][1:-1]
        if len(r)>=16 and (r[-2].startswith('geometry_') or r[-2] in ['GeometryFmaProbe','raw_vtx_dma','axi_periph_slave','axi_sdram_arbiter']):
            entities[r[-2]]=dict(alms_needed_estimate=float(r[1].split()[0]),exclusive_alms_needed_estimate=float(re.search(r'\(([^)]+)\)',r[1]).group(1)),placed_estimate=float(r[2].split()[0]),aluts=int(r[6].split()[0]),registers=int(r[7].split()[0]))
    result=dict(passed_component_correctness=True,whole_core_fit_successful=passed,
        full_core_functionally_qualified=False,recommended_for_deployment=False,private_only=True,deployed=False,
        seed=2,seed_sweep=False,metrics=metrics,entities=entities,
        over_capacity=dict(alms=max(metrics['alms_needed']-18480,0),labs=max(metrics['labs_required']-1848,0)),
        component_tests=tests,baseline_cpu_gpu_and_clock_preserved=True,
        timing=f.get('timing'),worst=f.get('worst'),all_configured_timing_checks_pass=f.get('all_checks_pass'),
        limitations=['Component tests cover the frozen alias/wrapper and native arithmetic; this is not full-core functional or application performance qualification.',
            'The source-only M2 requester and cacheable read-only view retain results in BRAM; no output DMA or GPU ingress is included.',
            'No production application, core, OS or SD file was changed.',
            'Failed-fit resource and packing counts are preliminary; no successful placement, bitstream or timing result exists.' if not passed else 'Placement success alone does not establish application correctness or device performance.'])
    save(out/'result.json',result)
    lines=[f'Private LOD4 native LOAD / BRAM alias: {"placement passes" if passed else "does not fit"}',
        '',f'One saved-seed-2 run; {metrics["alms_needed"]} needed ALMs, {metrics["labs_required"]} LABs, {metrics["m10ks"]} M10Ks, {metrics["dsps"]} DSPs.',
        'Device limits: 18480 ALMs / 1848 LABs / 308 M10Ks / 66 DSPs.',
        'Original CPU, narrow GPU, clocks and constraints preserved. Hardware destination is always slot0; software copies the completed32-byte view into its intended RSP slots.',
        'The reserved1KiB read-only alias is0x13f80000 (uncached mirror0x53f80000). CPU-only reset clears local alias responses but preserves accepted external DMA/write ownership.',
        'Component v3 tests pass230 native vertices (145 independent RGB),528 synthetic vertices,15456 alias words and5315 backpressure cycles.',
        'Native tests include exact normal-memory passthrough, START/read ordering, reset handling and write rejection/draining.',
        '', 'Entity needed-ALM estimates (inclusive / exclusive):']
    lines += [f'{n}: {d["alms_needed_estimate"]} / {d["exclusive_alms_needed_estimate"]}' for n,d in entities.items()]
    lines+=['']+result['limitations'];(out/'REPORT.txt').write_text('\n'.join(lines)+'\n')
    evidence={}
    def add(p):evidence[str(p.relative_to(ROOT))]=sha(p)
    for n,h in prep['source_sha256'].items():add(out/'frozen'/n)
    for p in out.iterdir():
        if p.is_file() and p.name!='final-manifest.json':add(p)
    for p in (fit/'results').rglob('*'):
        if p.is_file():add(p)
    for n in ['frozen-sources.json','inventory.json','experiment.json','results.json']:add(fit/n)
    for p in report.parent.iterdir():
        if p.suffix in ['.rpt','.summary','.sof','.rbf']:add(p)
    add(component)
    for n in read(component):add(component.parent/n)
    for r in binding['tests']:add(ROOT/r['path'])
    for n in ['phase10_load_bram_fit.py','phase10_load_bram_report.py']+(['phase10_load_bram_area.py'] if 'area' in a.label else []):add(Path(__file__).with_name(n))
    save(out/'final-manifest.json',dict(passed_component_correctness=True,whole_core_fit_successful=passed,recommended_for_deployment=False,evidence_sha256=evidence))
    for n,h in evidence.items():assert sha(ROOT/n)==h,n
    print(json.dumps(dict(label=a.label,metrics=metrics,manifest_sha256=sha(out/'final-manifest.json'),evidence_files=len(evidence))))
if __name__=='__main__':main()
