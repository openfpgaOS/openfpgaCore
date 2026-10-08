#!/usr/bin/env python3
"""Audit frozen pointer/packer pair and RS3 triple fits; preserve coverage distinctions."""
import argparse,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
OUT=BASE/'validation/pointer-packer-integration-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--fit',required=True);ap.add_argument('--cpu-label');args=ap.parse_args()
    folder=BASE/args.fit;frozen=folder/'frozen';job=folder/'jobs/pocket-os30-s2'
    experiment=read(folder/'experiment.json');inventory=read(folder/'inventory.json');sources=read(folder/'frozen-sources.json')
    assert len(inventory)==1 and inventory[0]['variant']=='os30' and inventory[0]['seed']==2
    overrides={'cpu':'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v',
               'gpu':'src/fpga/common/gpu_core.v','render_cache':'src/fpga/common/gpu_color_depth_cache.sv',
               'audio_mixer':'src/fpga/common/audio_mixer.v','cpu_system':'src/fpga/common/cpu_system.v',
               'peripheral_slave':'src/fpga/common/axi_periph_slave.v'}
    if experiment['texture_cache_source']:
        overrides['texture_cache']='src/fpga/common/gpu_tex_cache.v'
    assert not experiment['cpu_speed']
    for key,path in overrides.items():
        assert sources[path]==experiment[key+'_sha256']==sha(Path(experiment[key+'_source']))
    qsf='src/fpga/targets/pocket/ap_core.qsf';changed=[];job_changed=[]
    for path,digest in sources.items():
        assert sha(frozen/path)==digest,path
        assert (ROOT/path).is_file(),path
        if sha(ROOT/path)!=digest:changed.append(path)
        assert (job/path).is_file(),path
        if sha(job/path)!=digest:job_changed.append(path)
    assert set(changed)==set(overrides.values())|{qsf},changed
    assert job_changed==[qsf],job_changed
    expected=(ROOT/qsf).read_text().replace(
        'set_instance_assignment -name RAMSTYLE_ATTRIBUTE MLAB -to *sdram_axi_slave|wbuf*',
        'set_instance_assignment -name RAMSTYLE_ATTRIBUTE M10K -to *sdram_axi_slave|wbuf*')
    expected+='\nset_global_assignment -name OPTIMIZATION_MODE BALANCED\nset_global_assignment -name OPTIMIZATION_TECHNIQUE AREA\nset_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF\n'
    expected+='\nset_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC_FOR_AREA ON\n'
    if experiment['cpu_speed']:
        expected+='\nset_instance_assignment -name OPTIMIZATION_TECHNIQUE SPEED -to ic|cpu|cpu\n'
    assert expected==(frozen/qsf).read_text()
    production_variant=(ROOT/'src/fpga/targets/pocket/variants/os30.mk').read_text()
    defs=production_variant.split('DEFS := ',1)[1].split('\n\n',1)[0].replace('\\\n',' ').split()
    assert defs.count('INCLUDE_TEX_QUEUE_MLAB')==1;defs.remove('INCLUDE_TEX_QUEUE_MLAB')
    assert defs==inventory[0]['defs'].split()
    job_text=(job/qsf).read_text();frozen_lines=expected.splitlines();job_lines=job_text.splitlines()
    removed=[x for x in frozen_lines if x not in job_lines];added=[x for x in job_lines if x not in frozen_lines]
    assert set(removed)=={'set_global_assignment -name SEED 6','set_global_assignment -name NUM_PARALLEL_PROCESSORS ALL'}
    expected_added=['set_global_assignment -name SEED 2','set_global_assignment -name NUM_PARALLEL_PROCESSORS 4']
    expected_added += ['set_global_assignment -name VERILOG_MACRO '+x for x in defs]
    expected_added += ['set_global_assignment -name VERILOG_FILE ../../vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
    assert added==expected_added
    assert experiment['clock_mhz']==100 and not experiment['seed_sweep'] and not experiment['production_modified']
    assert experiment['full_triangle_gpu'] and experiment['texture_memory'] and not experiment['geometry_worker']
    assert experiment['area'] and experiment['combo_area'] and experiment['write_buffer_bram'] and experiment['texture_queues_bram']
    for key in ['register_retiming','decode_ready_fanout','sdram_rx_labs','register_packing','placement_effort']:assert not experiment[key],key
    timing={path:digest for path,digest in sources.items() if path.endswith('.sdc') or 'pll' in Path(path).name.lower()}
    assert timing
    for path,digest in timing.items():assert sha(ROOT/path)==digest,path
    pll=frozen/'src/fpga/targets/pocket/megafunctions/mf_pllram_133.v'
    assert '.output_clock_frequency0("100.000000 MHz")' in pll.read_text()
    assert '.phase_shift0("0 ps")' in pll.read_text() and '.phase_shift1("6000 ps")' in pll.read_text()
    assert not any(x in defs for x in ['INCLUDE_CLK90','INCLUDE_CLK96','INCLUDE_OS30_SDRAM166','INCLUDE_GPU_GEOMETRY_WORKER'])
    assert {'INCLUDE_VERT_TRI','INCLUDE_TEX_MEM','INCLUDE_GPU_RENDER_CACHE','INCLUDE_HW_MIXER'}<=set(defs)
    core=(frozen/'src/fpga/targets/pocket/core_top.v').read_text()
    assert '.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)' in core
    # Retained early-offer GPU runtime used this exact BRAM queue macro selection.
    # Also verify its source distinction from the production MLAB option.
    gpu_source=(frozen/overrides['gpu']).read_text()
    assert {'INCLUDE_XFORM','EXCLUDE_GPU_XFORM_MAC','EXCLUDE_CLIP_TRI'}<=set(defs)
    assert 'localparam SINGLE_CLIP_VERTEX = (INCLUDE_GPU_XFORM_MAC == 0)\n                             && (INCLUDE_CLIP_TRI == 0);' in gpu_source
    queue= gpu_source.split('`ifdef INCLUDE_TEX_QUEUE_MLAB',1)[1].split('`endif',1)[0]
    mlab,m10k=queue.split('`else')
    def normalized_queue(part):
        part=re.sub(r'//[^\n]*','',part).replace('MLAB','M10K').replace('!SINGLE_CLIP_VERTEX','0')
        return ' '.join(part.split())
    assert normalized_queue(mlab)==normalized_queue(m10k)
    cpu_source=Path(experiment['cpu_source']);cpu_parent=cpu_source.parent
    cpu_manifest=read(cpu_parent/'manifest.json');cpu_recipe=read(cpu_parent/'recipe.json')
    assert cpu_manifest['candidate_sha256']==cpu_recipe['candidate_sha256']==experiment['cpu_sha256']
    is_triple=experiment['cpu_sha256']=='a083bdb084b9dfd7b8944287ef3411b4dc739bf366f7dfa3f42599d5edd0528b'
    assert is_triple or experiment['cpu_sha256']=='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
    script='compose_pointer_packer_rs3.py' if is_triple else 'compose_pointer_packer.py'
    assert sha(cpu_parent/script)==cpu_recipe['script_sha256']
    for name,digest in cpu_recipe['patch_sha256' if is_triple else 'patch_hashes'].items():assert sha(cpu_parent/name)==digest,name
    cpu_audit_path=BASE/'pointer-packer-rs3-composition-audit-v1/manifest.json';cpu_audit=read(cpu_audit_path)
    assert cpu_audit['source_sha256']['triple' if is_triple else 'dual']==experiment['cpu_sha256']
    assert cpu_audit['no_other_source_edits'] and cpu_audit['components_match_independently_audited_equations']
    assert cpu_audit['both_dual_orders_match'] and cpu_audit['all_six_triple_orders_match']
    cpu_audits=[cpu_audit_path]+[BASE/name/'manifest.json' for name in
        ['pointer-d-emitted-audit-v1','packer-round-emitted-audit-v1']+(['fp-rs3-priority-emitted-audit-v1'] if is_triple else [])]
    cpu_evidence={'completed_fourteen':False,'independent_audits_sha256':{str(p.relative_to(ROOT)):sha(p) for p in cpu_audits},
        'source_recipe_sha256':sha(cpu_parent/'recipe.json'),'source_manifest_sha256':sha(cpu_parent/'manifest.json'),
        'focused_proofs_and_exact_composition':True,'physical_D_versus_ENA_mapping_not_claimed':True}
    if is_triple:
        label='rvc_skid_immediate_pingpong_pointer_packer_rs3'
        assert args.cpu_label in [None,label]
        cpu_dir=BASE/'validation'/label;cpu_build=read(cpu_dir/'build-manifest.json')
        assert cpu_build['sources'][cpu_build['netlist']]==experiment['cpu_sha256']
        assert sha(Path(cpu_build['netlist']))==experiment['cpu_sha256']
        assert sha(cpu_dir/'obj/Vtb_system')==cpu_build['simulator_sha256']
        cp=cpu_dir/'cycle-equivalence.json';ce=read(cp)
        assert ce['candidate_cpu_sha256']==experiment['cpu_sha256'] and len(ce['results'])==14
        for item in ce['results']:
            assert sha(cpu_dir/item['log'])==item['sha256']
            for reference,identical in item['byte_identical_to'].items():
                assert identical and sha(BASE/'validation'/reference/item['log'])==item['sha256']
        cpu_q_path=cpu_parent/'qualification.json';cpuq=read(cpu_q_path)
        assert cpuq['passed'] and cpuq['candidate_cpu_sha256']==experiment['cpu_sha256']
        assert cpuq['cycle_equivalence_sha256']==sha(cp)
        assert cpuq['source_recipe_sha256']==sha(cpu_parent/'recipe.json')
        assert cpuq['test_build_manifest_sha256']==sha(cpu_dir/'build-manifest.json')
        assert sha(cpu_audit_path)==cpuq['independent_audit_sha256']
        for name,digest in cpuq['component_audits'].items():assert sha(BASE/name/'manifest.json')==digest,name
        for name in ['skid','pingpong']:
            coverage=cpu_dir/(name+'-coverage.json')
            assert sha(coverage)==cpuq[name+'_coverage_sha256']
            cov=read(coverage);assert cov['passed'] and cov['totals']['cycles']==33363543 and cov['totals']['errors']==0
        cpu_evidence.update(label=label,completed_fourteen=True,build_manifest_sha256=sha(cpu_dir/'build-manifest.json'),
            cycle_equivalence_sha256=sha(cp),qualification_sha256=sha(cpu_q_path),all_fourteen_complete_logs_verified=True,
            monitored_cycles=33363543,monitor_errors=0,qualification_gap=None)
    else:
        assert args.cpu_label is None
        assert not (cpu_parent/'qualification.json').exists()
        assert cpu_manifest['focused_proofs_rerun'] and cpu_manifest['no_new_state_or_cycles']
        assert cpu_manifest['source_orders_and_original_external_patch_orders_identical']
        cpu_evidence.update(label=None,qualification_gap='Exact pair5209 has focused proofs/composition only; no whole-CPU suite was run, intentionally per root. Triple suite is not relabeled as pair execution.')
    texture_q_path=BASE/'validation/texture-offer-v1/qualification.json';texture=read(texture_q_path)
    assert texture['passed']
    for name,digest in texture['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
    render_path=BASE/'validation/texture-offer-v1/rendering/manifest.json';render=read(render_path)
    assert render['variant']['defs'].split()==defs
    assert render['texture_queues_bram'] and render['texture_offer_monitor']
    assert texture['render_cache_sha256']==experiment['render_cache_sha256']
    if experiment['texture_cache_source']:
        assert texture['gpu_sha256']==experiment['gpu_sha256']
        assert texture['cache_sha256']==experiment['texture_cache_sha256']
        assert '.EARLY_REQ_OFFER(1)' in gpu_source and '.req_offer_a(tex_req_offer_a)' in gpu_source
        assert sources[overrides['texture_cache']]==render['texture_cache_candidate_sha256']
        gpu={'evidence':'texture-offer candidate arm','qualification_sha256':sha(texture_q_path),
             'gpu_sha256':texture['gpu_sha256'],'texture_cache_sha256':texture['cache_sha256']}
    else:
        assert texture['reference_gpu_sha256']==experiment['gpu_sha256']
        assert texture['reference_cache_sha256']==sources['src/fpga/common/gpu_tex_cache.v']
        gpu={'evidence':'texture-offer reference arm, matching selected BRAM queue macros',
             'qualification_sha256':sha(texture_q_path),'gpu_sha256':texture['reference_gpu_sha256'],
             'texture_cache_sha256':texture['reference_cache_sha256']}
    gpu.update(render_manifest_sha256=sha(render_path),actual_workloads=38,latency_modes=3,
               exact_selected_variant_macros=True,monitor_cycles=texture['gpu_monitor_totals']['cycles'],
               entire_paired_logs_identical=True)
    for variant in ['baseline','candidate']:
        for item in texture['whole_gpu_logs']:
            assert sha(render_path.parent/variant/(item['mode']+'.log'))==item['sha256']
    for path in ['src/fpga/common/gpu_tex_cache.v','src/fpga/common/gpu_color_depth_cache.sv']:
        assert path in sources
    assert 'parameter SET_BITS = 10' in (frozen/'src/fpga/common/gpu_tex_cache.v').read_text()

    posted=read(BASE/'posted-write-factor-v1/qualification.json');assert posted['candidate_sha256']==experiment['cpu_system_sha256']
    audio_dir=BASE/'audio-ramp-balance-v1';audio=read(audio_dir/'manifest.json');recipe=read(audio_dir/'recipe.json')
    assert audio['candidate_sha256']==experiment['audio_mixer_sha256']
    for name,digest in recipe['files_sha256'].items():assert sha(audio_dir/name)==digest,name
    assert (audio_dir/'reference/run.log').read_bytes()==(audio_dir/'candidate/run.log').read_bytes()
    assert (audio_dir/'reference/throughput.csv').read_bytes()==(audio_dir/'candidate/throughput.csv').read_bytes()
    assert sha(audio_dir/'patch-replay/audio_mixer.v')==experiment['audio_mixer_sha256']
    aa=read(BASE/'audio-ramp-emitted-audit-v1/manifest.json');assert aa['candidate_sha256']==experiment['audio_mixer_sha256']
    mmio_dir=BASE/'mmio-read-boundary-v1';mmio=read(mmio_dir/'manifest.json')
    mmio_reference=ROOT/mmio['reference'];mmio_candidate=Path(experiment['peripheral_slave_source'])
    assert sha(mmio_reference)==mmio['reference_sha256']
    assert sha(mmio_candidate)==mmio['candidate_sha256']==experiment['peripheral_slave_sha256']
    old='reg [31:0] sysreg_rdata;';new='(* keep = "true" *) '+old
    assert mmio_reference.read_text().count(old)==1 and mmio_candidate.read_text().count(new)==1
    assert mmio_candidate.read_text().replace(new,old,1)==mmio_reference.read_text()
    assert sha(ROOT/'src/fpga/common/axi_periph_slave.v')==mmio['reference_sha256']
    assert sha(mmio_dir/'patch-replay/axi_periph_slave.v')==mmio['candidate_sha256']
    assert sha(mmio_dir/'keep_mmio_boundary.py')==mmio['frozen_script_sha256']
    assert sha(mmio_dir/'keep-sysreg-rdata.patch')==mmio['patch_sha256']
    for path,digest in mmio['dependencies_sha256'].items():assert sha(ROOT/path)==digest,path
    assert mmio['extra_registers']==0 and mmio['extra_cycles']==0
    mmio_audit_path=BASE/'mmio-read-boundary-emitted-audit-v1/manifest.json';mmio_audit=read(mmio_audit_path)
    assert mmio_audit['candidate_sha256']==mmio['candidate_sha256']
    assert mmio_audit['reference_sha256']==mmio['reference_sha256']
    for key in ['exact_only_keep_attribute','all_behavioral_source_bytes_identical',
                'all_preprocessor_configurations_preserved','clock_cycle_state_unchanged']:
        assert mmio_audit[key],key
    mmio_evidence={str(p.relative_to(ROOT)):sha(p) for p in [mmio_dir/n for n in
        ['manifest.json','keep_mmio_boundary.py','keep-sysreg-rdata.patch','lint.log','replay.log','reverse.log','patch-replay/axi_periph_slave.v']]}
    mmio_evidence[str(mmio_audit_path.relative_to(ROOT))]=sha(mmio_audit_path)
    result={'fit':args.fit,'status':'Exact frozen integration passed; component qualification is separate from full-system/game execution.',
            'frozen_source_count':len(sources),'all_frozen_and_job_hashes_verified':True,'only_production_differences':changed,
            'source_overrides':{k:{'path':v,'sha256':sources[v]} for k,v in overrides.items()},
            'only_frozen_qsf_changes':['SDRAM write buffer MLAB->M10K','BALANCED synthesis with AREA technique','physical register duplication OFF','combinational area optimization ON'],
            'only_job_qsf_changes':['Saved seed2','four compile processors','os30 macros with only INCLUDE_TEX_QUEUE_MLAB removed','private os30 CPU file'],
            'qsf_sha256':sha(job/qsf),'variant_definitions':defs,'clock_mhz':100,'seed':2,'gpu_features_and_cache_geometry_preserved':True,
            'timing_and_pll_identities_unchanged':timing,'cpu_evidence':cpu_evidence,
            'texture_queue_macro_removal_is_only_two_ramstyle_attributes_for_actual_os30_geometry':True,
            'gpu_qualification':gpu,'posted_write_qualification_sha256':sha(BASE/'posted-write-factor-v1/qualification.json'),
            'audio_qualification':{'manifest_sha256':sha(audio_dir/'manifest.json'),'recipe_sha256':sha(audio_dir/'recipe.json'),'independent_emitted_audit_sha256':sha(BASE/'audio-ramp-emitted-audit-v1/manifest.json'),'exhaustive_inputs':audio['exhaustive_inputs'],'pcm_samples':14223,'throughput_cases':36,'identical_logs_and_throughput':True},
            'cpu_speed_instance_assignment':experiment['cpu_speed'],'cpu_speed_target':'ic|cpu|cpu' if experiment['cpu_speed'] else None,
            'mmio_attribute_only':{'exact_attribute_reversal':True,'extra_behavioral_tests':0,'reference_sha256':mmio['reference_sha256'],'candidate_sha256':mmio['candidate_sha256'],'evidence_sha256':mmio_evidence},
            'source_manifest_sha256':sha(folder/'frozen-sources.json'),'experiment_sha256':sha(folder/'experiment.json'),
            'audit_script_sha256':sha(Path(__file__)),'full_combined_simulation_claimed':False,'no_additional_simulations_run':True}
    if experiment['cpu_speed']:result['only_frozen_qsf_changes'].append('CPU instance ic|cpu|cpu OPTIMIZATION_TECHNIQUE SPEED; global AREA preserved')
    OUT.mkdir(exist_ok=True);(OUT/(args.fit+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    names=['fit-rvc-skid-pointer-packer-texoffer-v1','fit-rvc-skid-pointer-packer-rs3-texoffer-v1']
    if all((OUT/(name+'.json')).exists() for name in names):
        pair,triple=[read(BASE/name/'frozen-sources.json') for name in names]
        assert pair.keys()==triple.keys()
        differences=[path for path in pair if pair[path]!=triple[path]]
        assert differences==['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
        ea,eb=[read(BASE/name/'experiment.json') for name in names]
        changed_options=[key for key in ea if ea[key]!=eb[key]]
        assert changed_options==['cpu_source','cpu_sha256']
        audits=[read(OUT/(name+'.json')) for name in names]
        assert audits[0]['qsf_sha256']==audits[1]['qsf_sha256']
        comparison=dict(fits=names,only_changed_frozen_source=differences,unchanged_inputs=len(pair)-1,
            only_changed_experiment_keys=changed_options,job_qsf_identical=True,
            pair_whole_cpu_executed=False,triple_whole_cpu_cases=14,full_combined_system_simulation_claimed=False,
            audit_sha256={name:sha(OUT/(name+'.json')) for name in names})
        (OUT/'paired-integration.json').write_text(json.dumps(comparison,indent=2)+'\n')
    print(f'PASS {args.fit}: {len(sources)} frozen inputs, exact {len(overrides)} overrides/options, unchanged clocks/SDC/phase; CPU14={cpu_evidence["completed_fourteen"]}')
if __name__=='__main__':main()
