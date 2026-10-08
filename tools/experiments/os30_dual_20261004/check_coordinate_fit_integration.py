#!/usr/bin/env python3
"""Bind the completed coordinate/5209 fit to exact component qualification."""
from pathlib import Path
import csv,hashlib,json,re
ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
FIT=ART/'fit-rvc-skid-pointer-packer-coord-effort2-v1'
OUT=ART/'coordinate-fit-integration-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def main():
    e=read(FIT/'experiment.json'); inv=read(FIT/'inventory.json'); sources=read(FIT/'frozen-sources.json')
    r=read(FIT/'results.json')[0];job=Path(r['directory']).parents[3];frozen=FIT/'frozen'
    assert len(inv)==1 and inv[0]['variant']=='os30' and inv[0]['seed']==inv[0]['saved_seed']==2
    assert e['saved_seed']==e['placement_seed']==2 and not e['seed_sweep']
    assert e['clock_mhz']==100 and e['placement_effort']==2.0 and not e['production_modified']
    overrides={'cpu':'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v','gpu':'src/fpga/common/gpu_core.v',
        'texture_cache':'src/fpga/common/gpu_tex_cache.v','render_cache':'src/fpga/common/gpu_color_depth_cache.sv',
        'cpu_system':'src/fpga/common/cpu_system.v','audio_mixer':'src/fpga/common/audio_mixer.v','peripheral_slave':'src/fpga/common/axi_periph_slave.v'}
    for k,p in overrides.items():assert sources[p]==e[k+'_sha256']==sha(Path(e[k+'_source']))
    assert e['cpu_sha256']=='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
    assert e['gpu_sha256']=='8e47122870ad71aea38f761691b252173fdc351b22255f7a591207f4ac245185'
    qsf='src/fpga/targets/pocket/ap_core.qsf';changed=[]
    assert len(sources)==104
    for p,h in sources.items():
        assert sha(frozen/p)==h,p
        if p!=qsf:assert sha(job/p)==h,p
        if sha(ROOT/p)!=h:changed.append(p)
    assert set(changed)==set(overrides.values())|{qsf}
    expected=(ROOT/qsf).read_text().replace('set_instance_assignment -name RAMSTYLE_ATTRIBUTE MLAB -to *sdram_axi_slave|wbuf*','set_instance_assignment -name RAMSTYLE_ATTRIBUTE M10K -to *sdram_axi_slave|wbuf*')
    expected+='\nset_global_assignment -name OPTIMIZATION_MODE BALANCED\nset_global_assignment -name OPTIMIZATION_TECHNIQUE AREA\nset_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF\n'
    expected+='\nset_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC_FOR_AREA ON\n'
    expected+='\nset_global_assignment -name PLACEMENT_EFFORT_MULTIPLIER 2.0\n'
    assert (frozen/qsf).read_text()==expected
    defs=(ROOT/'src/fpga/targets/pocket/variants/os30.mk').read_text().split('DEFS := ',1)[1].split('\n\n',1)[0].replace('\\\n',' ').split()
    assert defs.count('INCLUDE_TEX_QUEUE_MLAB')==1;defs.remove('INCLUDE_TEX_QUEUE_MLAB')
    assert defs==inv[0]['defs'].split()
    actual=(job/qsf).read_text().splitlines();base=expected.splitlines()
    assert set(x for x in base if x not in actual)=={'set_global_assignment -name SEED 6','set_global_assignment -name NUM_PARALLEL_PROCESSORS ALL'}
    added=['set_global_assignment -name SEED 2','set_global_assignment -name NUM_PARALLEL_PROCESSORS 4']
    added+=['set_global_assignment -name VERILOG_MACRO '+d for d in defs]
    added+=['set_global_assignment -name VERILOG_FILE ../../vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
    assert [x for x in actual if x not in base]==added
    assert e['area'] and e['combo_area'] and e['texture_queues_bram'] and e['write_buffer_bram']
    for key in ['cpu_speed','combo_speed','register_duplication','register_retiming','decode_ready_fanout','stage2_ready_global','sdram_rx_labs','register_packing','router_effort','physical_effort','stage2_ready_fanout_limit']:assert not e[key],key
    assert e['full_triangle_gpu'] and e['texture_memory'] and not e['geometry_worker']
    timing_sources={p:h for p,h in sources.items() if p.endswith('.sdc') or 'pll' in Path(p).name.lower()}
    for p,h in timing_sources.items():assert sha(ROOT/p)==h
    pll=(frozen/'src/fpga/targets/pocket/megafunctions/mf_pllram_133.v').read_text()
    assert '.output_clock_frequency0("100.000000 MHz")' in pll and '.phase_shift1("6000 ps")' in pll
    assert {'INCLUDE_VERT_TRI','INCLUDE_TEX_MEM','INCLUDE_GPU_RENDER_CACHE','INCLUDE_HW_MIXER'}<=set(defs)
    assert not {'INCLUDE_CLK90','INCLUDE_CLK96','INCLUDE_OS30_SDRAM166','INCLUDE_GPU_GEOMETRY_WORKER'}&set(defs)
    cpuparent=Path(e['cpu_source']).parent;cpudir=ART/'validation/rvc_skid_immediate_pingpong_pointer_packer'
    cpuq=read(cpuparent/'qualification.json');ce=read(cpudir/'cycle-equivalence.json');cb=read(cpudir/'build-manifest.json')
    assert cpuq['passed'] and cpuq['candidate_cpu_sha256']==e['cpu_sha256']
    assert cb['sources'][cb['netlist']]==e['cpu_sha256'] and sha(Path(cb['netlist']))==e['cpu_sha256']
    assert cpuq['test_build_manifest_sha256']==sha(cpudir/'build-manifest.json')
    assert cpuq['cycle_equivalence_sha256']==sha(cpudir/'cycle-equivalence.json')
    assert len(ce['results'])==14
    for item in ce['results']:
        assert sha(cpudir/item['log'])==item['sha256']
        for label,identical in item['byte_identical_to'].items():assert identical and sha(ART/'validation'/label/item['log'])==item['sha256']
    for name in ['skid','pingpong']:
        p=cpudir/(name+'-coverage.json');cov=read(p)
        assert sha(p)==cpuq[name+'_coverage_sha256'] and cov['totals']['errors']==0 and cov['totals']['cycles']==33363543
    for label,h in cpuq['component_audits'].items():assert sha(ART/label/'manifest.json')==h
    assert cpuq['source_recipe_sha256']==sha(cpuparent/'recipe.json')
    recipe=read(cpuparent/'recipe.json')
    assert sha(cpuparent/'compose_pointer_packer.py')==recipe['script_sha256']
    for p,h in recipe['patch_hashes'].items():assert sha(cpuparent/p)==h
    gpudir=ART/'validation/gpu-coordinate-stage-v1';gpuq=read(gpudir/'qualification.json');gm=read(gpudir/'rendering/manifest.json')
    assert gpuq['passed'] and gpuq['candidate_sha256']==e['gpu_sha256']
    assert gpuq['texture_cache_sha256']==e['texture_cache_sha256'] and gpuq['render_cache_sha256']==e['render_cache_sha256']
    assert gm['variant']['defs'].split()==defs and gpuq['selected_os30_bram_queue_configuration_exercised']
    for p,h in gpuq['evidence_sha256'].items():assert sha(ROOT/p)==h,p
    for item in gpuq['whole_gpu_logs']:
        assert item['entire_logs_identical']
        for side in ['baseline','candidate']:assert sha(gpudir/'rendering'/side/(item['mode']+'.log'))==item['sha256']
    posted=read(ART/'posted-write-factor-v1/qualification.json')
    assert posted['status']=='passed' and posted['candidate_sha256']==e['cpu_system_sha256']
    audio=ART/'audio-ramp-balance-v1';am=read(audio/'manifest.json');ar=read(audio/'recipe.json')
    assert am['candidate_sha256']==e['audio_mixer_sha256']
    for p,h in ar['files_sha256'].items():assert sha(audio/p)==h,p
    for p in ['run.log','throughput.csv']:assert (audio/'reference'/p).read_bytes()==(audio/'candidate'/p).read_bytes()
    mm=read(ART/'mmio-read-boundary-v1/manifest.json')
    assert mm['candidate_sha256']==e['peripheral_slave_sha256']
    assert Path(e['peripheral_slave_source']).read_text().replace('(* keep = "true" *) reg [31:0] sysreg_rdata;','reg [31:0] sysreg_rdata;',1)==(ROOT/mm['reference']).read_text()
    assert r['state']=='complete' and r['valid'] and all(r['worst'][k]>0 for k in ['hold','recovery','removal','pulse_width'])
    assert r['alms']==18142 and r['m10ks']==308
    fr=(Path(r['directory'])/'output_files/ap_core.fit.rpt').read_text(errors='replace')
    assert re.search(r'; Total LABs:\s+partially or completely used\s*;\s*1,848 / 1,848',fr)
    assert re.search(r'; Placement Effort Multiplier\s*; 2.0\s*;',fr)
    clocks=list(csv.DictReader((FIT/'results/pocket-os30-s2/timing/clocks.tsv').open(),delimiter='\t'))
    mainclock='ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk'
    other=min(float(x['slack']) for x in clocks if x['check']=='setup' and x['clock']!=mainclock and x['slack']!='no_paths')
    assert other>0
    OUT.mkdir(exist_ok=False)
    evidence=[FIT/'frozen-sources.json',FIT/'experiment.json',FIT/'results.json',job/qsf,cpuparent/'qualification.json',gpudir/'qualification.json',ART/'posted-write-factor-v1/qualification.json',audio/'manifest.json',ART/'mmio-read-boundary-v1/manifest.json']
    report=dict(passed=True,fit=FIT.name,all_104_frozen_and_job_sources_verified=True,only_source_overrides=overrides,
        qsf_changes='Only write-bufferBRAM, AREA/BALANCED, duplicationOFF, physical-areaON, placement2.0; job adds savedseed2/macros/four workers/privateCPU.',
        clocks_sdc_pll_phases_unchanged=timing_sources,full_triangle_gpu_texture_cache_audio_and_cpu_cache_geometry_preserved=True,
        cpu_sha256=e['cpu_sha256'],cpu_retained_cases=14,cpu_monitored_cycles=33363543,cpu_logs_byte_identical=True,
        gpu_sha256=e['gpu_sha256'],gpu_cases=114,gpu_logs_byte_identical=True,gpu_monitor_cycles=42548619,
        component_qualifications_bound=True,new_full_cpu_gpu_simulation_claimed=False,game_fps_claimed=False,
        main_mhz=88.95,configured_main_mhz=100,above_90mhz_goal_met=False,alms=r['alms'],labs=1848,m10ks=r['m10ks'],
        all_corner_checks=r['worst'],other_clock_setup_minimum_ns=other,rbf_sha256=r['hashes']['rbf'],
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence},script_sha256=sha(Path(__file__)))
    (OUT/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
