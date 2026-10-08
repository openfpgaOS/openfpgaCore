#!/usr/bin/env python3
"""Seal actual CRAM1 source upper-bound screen; no hardware FPS prediction."""
import csv,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cram-source-coupled-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rows(path):return list(csv.DictReader(path.open()))
def write(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
def profile(path):
    old={r['function']:int(r['self_cycles']) for r in csv.DictReader((path/'profile-0360.tsv').open(),delimiter='\t')}
    return {r['function']:(int(r['self_cycles'])-old.get(r['function'],0))/800000
        for r in csv.DictReader((path/'profile.tsv').open(),delimiter='\t')}
def main():
    dirs={n:OUT/('run-'+n+'-8') for n in ('sdram','cram')}
    original=P/'load-alias-coupled-v1/run-candidate-8'
    build=json.loads((OUT/'model/build.json').read_text())
    assert sha(OUT/'model/obj/Vtb_gpu')==build['simulator_sha256']
    prep=json.loads((OUT/'inputs.json').read_text())
    for name,digest in prep['sources'].items():assert sha(OUT/name)==digest,name
    summary={};outputs=[]
    for name,d in dirs.items():
        invocation=json.loads((d/'inputs.json').read_text())
        assert invocation['success'] and invocation['frames']==8
        assert invocation['environment']['CRAM_VERTEX_ROUTE']==str(int(name=='cram'))
        assert invocation['build_sha256']==sha(OUT/'model/build.json')
        for n,h in invocation['results_sha256'].items():assert sha(d/n)==h,n
        assert 'FATAL' not in (d/'run.log').read_text()
        status=json.loads((d/'coupled.json').read_text());geom=json.loads((d/'geometry.json').read_text())
        physical=json.loads((d/'cram-occupancy.json').read_text());ops=rows(d/'geometry-operations.csv')
        assert status['frames']==status['presented']==8 and status['cram1_errors']==0
        assert len(ops)==geom['loads']==2608
        assert all(int(r['operation'])==1 and int(r['status'])&6==6 and not int(r['status'])&0xe9 for r in ops)
        assert geom['r_beats']==194976 and geom['ar_bursts']==13240
        counter=rows(d/'counters.csv')[-1]
        summary[name]=dict(modeled_frame_ms=status['cpu_cycles']/800000,
            load_wait_ms=sum(int(r['cycles']) for r in ops)/800000,
            source_ar_wait_ms=geom['ar_wait']/800000,source_ar_to_last_r_ms=physical['gd_response_cycles']/800000,
            physical_cram_ce_ms=physical['ce_cycles']/800000,
            physical_cram_ce_fraction_of_complete_window=physical['ce_cycles']/status['cycles'],
            cram_controller_burst_busy_ms=physical['burst_busy_cycles']/800000,
            texture_request_wait_ms=int(counter['all_texture_request_wait'])/800000,
            gpu_busy_ms=int(counter['all_gpu_busy'])/800000,
            fragment_stall_ms=int(counter['all_fragment_stall'])/800000,
            rdptr_polls=int(counter['rdptr_polls']),publications=int(counter['publications']),
            cpu_self_profile_ms=profile(d),coupled=status,geometry=geom,physical_cram=physical)
    for f in sorted(dirs['sdram'].glob('frame_*.bin')):
        b=dirs['cram']/f.name;o=original/f.name
        assert f.read_bytes()==b.read_bytes()==o.read_bytes() and f.stat().st_size==307200
        outputs.append(dict(frame=int(f.stem.split('_')[1]),sha256=sha(f),color_exact=True,depth_exact=True))
    assert len(outputs)==8
    for name in ('scene.csv','geometry.csv','culling.csv'):
        assert (dirs['sdram']/name).read_bytes()==(dirs['cram']/name).read_bytes()==(original/name).read_bytes()
    assert json.loads((dirs['sdram']/'coupled.json').read_text())==json.loads((original/'coupled.json').read_text())
    old,new=summary['sdram']['modeled_frame_ms'],summary['cram']['modeled_frame_ms']
    f11=json.loads((P/'load-alias-coupled-v1/run-baseline-8/coupled.json').read_text())['cpu_cycles']/800000
    result=dict(passed=True,decision='Reject performance avenue: ideal source remap saves only0.81% over LOD4, before implementation overhead.',
        model_scope='Actual LOAD+BRAM alias+GPU+SDRAM+CRAM1 controller/PHY/chip RTL; native-FMA CPU instruction/cache timing is modeled.',
        frames=8,first_frame=360,clock_hz=100000000,
        matched_sdram_control_cycle_identical_to_prior=True,all368_scene_geometry_culling_exact=True,
        actual_render_outputs=outputs,summary=summary,
        frame_ms_saved=old-new,time_reduction_pct=100*(1-new/old),throughput_increase_pct=100*(old/new-1),
        retained_f11_modeled_ms=f11,total_vs_f11_time_reduction_pct=100*(1-new/f11),
        immutable_pool=json.loads((OUT/'source-pool.json').read_text()),
        limitations=[
            'Pool preload and address remapping cost excluded. Immutable source bytes are relocated before timing and rechecked on every LOAD; CPU executes unchanged driver ELF.',
            'Only observed immutable pool and eight fixed-step Bob-omb Battlefield frames; no all-world residency/allocation proof.',
            'No actual CPU RTL runs in this coupled test. Non-alias CPU accesses use estimated cache timing and synthetic SDRAM background traffic.',
            'Both route controls use identical arbiter RTL. SDRAM control exactly matches prior coupled cycles, geometry counters and eight actual buffers.',
            'Color/depth equality applies to these eight actual rendered frames. 368 scene/geometry/cull records are software-state comparisons.',
            'Physical CRAM CE occupancy counts either die selected outside initialization, including upload traffic; denominator is complete timed window through final presentation.',
            'No full-core fit, timing qualification, production source changes, SD changes or Pocket FPS claim.'])
    write(OUT/'result.json',result)
    report=f'''CRAM1 immutable vertex-source screen: rejected as a substantial speed solution.

Same LOD4 app and same CPU model, actual LOAD/GPU/SDRAM/CRAM1 RTL:
  SDRAM source: {old:.8f} ms/frame
  CRAM1 source: {new:.8f} ms/frame
  Saving: {old-new:.8f} ms ({100*(1-new/old):.6f}% time).
  Retained F11 baseline: {f11:.8f} ms; combined ideal LOD4+CRAM is {100*(1-new/f11):.6f}% lower time.

LOAD wait falls3.92918000 ->2.30900000 ms/frame. Source AR queue wait falls
1.68038000 ->0.05028625 ms/frame. Accepted AR through final response remains
0.75180125 ->0.75224000 ms/frame: the chip is not saturated.
Physical CE active time0.70557750 ->1.36094750 ms/frame,1.88154% ->3.62919%
of complete timed window. Texture request wait0.03663875 ->0.06381500 ms/frame.

Most earlier CPU progress reaches a full GPU command ring. Read-pointer polls
increase105369 ->162837 over eight frames. CPU self time in load_alias_vertex
drops1.619985ms/frame; gfx_gpu_vtx_cache_tri grows0.8280575ms and batch submission
grows0.29365375ms. Raster/GPU busy time improves only0.122840ms/frame.
Source memory contention is real, but removing it alone exposes downstream work.

All8 actual color/depth buffers exactly match each other and prior LOD4 baseline.
All368 scene/geometry/culling records match. Same-arbiter SDRAM control is
cycle-identical to original LOD4 control.13240 source bursts/194976 source words
are checked against original immutable source bytes;2608 LOADs complete correctly.

This is an optimistic cost screen.145392 observed source bytes are preloaded in
60 merged ranges with64-byte-aligned bases (146752-byte arena atCRAM1 0x100000).
Descriptor addresses were remapped by ELF symbol and compared byte-for-byte.
Startup upload, allocator reservation and CPU remapping overhead are excluded.
Actual hardware has16MiB; this simulation safely uses the low2MiB die0 model.
CPU timing remains modeled; no native full-CPU timing, fit, deployment or FPS claim.
See result.json and source-pool.json for exact denominators and source binding.
'''
    (OUT/'REPORT.txt').write_text(report)
    write(OUT/'final-manifest.json',dict(scope=__doc__,files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts and 'build' not in p.parts},
        helper_sha256={p.name:sha(p) for p in [Path(__file__),ROOT/'tools/experiments/os30_fps25_20261005/phase10_cram_source_coupled.py',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cram_source_coupled.cpp',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cram_source_run.py']}))
    print(json.dumps({k:result[k] for k in ['decision','frame_ms_saved','time_reduction_pct','total_vs_f11_time_reduction_pct']},indent=2))
if __name__=='__main__':main()
