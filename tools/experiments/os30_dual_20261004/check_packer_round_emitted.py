#!/usr/bin/env python3
"""Independent emitted packer rounding-mode consumer and proof audit."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
CAND=ART/'packer-round-mode-v1/immediate'
OUT=ART/'packer-round-emitted-audit-v1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    ref=ART/'fp-last-combined-v1/immediate/VexiiRiscv.v'
    target=CAND/'VexiiRiscv.v'
    assert sha(ref)=='9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377'
    assert sha(target)=='b5002595e843c5a634a124be9404c8a010f60d053cf1c673ed509bd1512ca193'
    old,new=ref.read_text(),target.read_text()
    P='FpuPackerPlugin_logic_pip_node_1_';S='FpuPackerPlugin_logic_s2_'
    round=P+'s1_ROUNDING_INCR';mode=P+'s0_VALUE_mode'
    before=f'  assign {round} = (({mode} == FloatMode_NORMAL) && _zz_{round});'
    after=f'  assign {round} = _zz_{round};'
    assert old.count(before)==1 and old.replace(before,after,1)==new
    assert new.replace(after,before,1)==old
    # Four Boolean mode encodings are complete; NORMAL makes the removed
    # predicate literally true. There is no arithmetic approximation here.
    for label,encoding in [('ZERO',0),('INF',1),('NAN',2),('NORMAL',3)]:
        assert f"localparam FloatMode_{label} = 2'd{encoding};" in old
    decls=list(re.finditer(r'^  (?:wire|reg)\s+(?:\[[^\n]+?\]\s+)?(\w+);$',old,re.M))
    combs=list(re.finditer(r'^  assign \w+\s*=.*?;\n|^  always @\(\*\) begin\n.*?^  end\n',old,re.M|re.S))
    names={m[1] for m in decls}
    tokens=[set(re.findall(r'\b\w+\b',m[0]))&names for m in combs]
    written=[set(re.findall(r'\b(\w+)\s*(?:\[[^;\n]*?\])?\s*=(?!=)',m[0]))&names for m in combs]
    flags=['NX','UF','OF','DZ','NV']
    terminals={S+'fwb_value',*('FpuPackerPlugin_logic_flagsWb_flags_'+f for f in flags)}
    reached=set();pending=[round];edges={}
    while pending:
        node=pending.pop()
        if node in reached:continue
        reached.add(node)
        if node in terminals:continue
        consumers=[i for i,words in enumerate(tokens) if node in words and node not in written[i]]
        edges[node]=[combs[i][0] for i in consumers]
        allowed=[m.span() for m in decls if m[1]==node]
        allowed += [m.span() for i,m in enumerate(combs) if node in tokens[i]]
        for occurrence in re.finditer(r'\b'+re.escape(node)+r'\b',old):
            assert any(a<=occurrence.start()<b for a,b in allowed),node
        for i in consumers:pending.extend(written[i])
    assert len(edges[round])==3
    assert reached&terminals=={S+'fwb_value',*('FpuPackerPlugin_logic_flagsWb_flags_'+f for f in ['NX','UF','OF'])}
    recorded=json.loads((CAND/'dependency-audit.json').read_text())
    assert edges==recorded['internal_consumers'] and sorted(reached)==recorded['reached']
    # Re-extract all actual 62-bit visible boundaries from the full source.
    # The interface lists are metadata only; the dependency source is rebuilt.
    recipe=load(CAND/'remove_packer_mode_gate.py','packer_recipe')
    checker_path=CAND/'check_vexii_os20_timing.py'
    assert sha(checker_path)=='80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    checker=load(checker_path,'packer_checker')
    constants='\n'.join(re.findall(r'^  localparam (?:FloatMode|FpuRoundMode)_.*?;',old,re.M))
    assert len(constants.splitlines())==9
    for name,source,inputs,file in [('reference',old,recipe.FULL,'reference.v'),('candidate',new,recipe.FULL,'candidate.v'),('special_reference',old,recipe.CUT,'special-reference.v'),('special_candidate',new,recipe.CUT,'special-candidate.v')]:
        cone,width=checker.cone(source,name,list(inputs),recipe.OUTS)
        cone=cone.replace('\noutput wire ','\n'+constants+'\noutput wire ',1)
        assert width==62 and cone==(CAND/file).read_text()
        assert (CAND/'tb.sv').read_text().count(cone)==1
    # After arithmetic is abstracted freely, the two special cones are exact.
    special=(CAND/'special-reference.v').read_text()
    assert special.replace('module special_reference(','module special_candidate(',1)==(CAND/'special-candidate.v').read_text()
    initial=f'    {S}fwb_value[31 : 0] = {{{{{P}s0_VALUE_sign,{P}s2_EXP[7 : 0]}},{P}s1_MAN_RESULT[22 : 0]}};'
    assert special.count(initial)==1
    for node in [P+'s2_EXP',P+'s1_MAN_RESULT']:
        assert len(re.findall(r'\b'+re.escape(node)+r'\b',special))==3
    # Sign, NaN quiet/positive overrides, all five input flags and metadata
    # remain present in the observed interface and identical outside ROUND.
    assert recipe.OUTS[:6]==[S+'fwb_value']+['FpuPackerPlugin_logic_flagsWb_flags_'+f for f in flags]
    assert len(recipe.OUTS)==13
    for name in recipe.OUTS[6:]:
        stmt=re.findall(r'^  assign '+re.escape(name)+r' = [^;]+;',old,re.M)
        assert len(stmt)==1 and stmt[0] in new
        assert not(set(re.findall(r'\b\w+\b',stmt[0]))&reached)
    for dest in [f'FpuPackerPlugin_wb_at_{n}_payload' for n in [4,1,6,9]]+[S+'fpWriter_payload_data']:
        line=f'  assign {dest} = {S}fwb_value;'
        assert old.count(line)==new.count(line)==1
    OUT.mkdir(exist_ok=False)
    with (OUT/'actual-proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)
    assert 'cases=417792 metadata=576 full actual-cone boundary/random checks=1524288' in (OUT/'actual-proof-rerun.log').read_text()
    result=dict(reference_sha256=sha(ref),candidate_sha256=sha(target),exact_one_assignment_changed=True,
        normal_mode_literal_equation_identity=True,all_four_boolean_mode_encodings_covered=True,
        independent_whole_source_consumer_census_matches=True,round_consumers=3,affected_nodes=len(reached),
        no_affected_internal_state_or_instance_escape=True,
        complete_packed32_five_flags_valid_destination_uop_boundaries=True,
        special_arithmetic_cutpoints_independent=True,whole_data_word_lift_verified_by_bitwise_constant_overrides=True,
        sign_nan_quiet_and_positive_overrides_preserved=True,
        actual_full_and_cut_cones_freshly_match_artifacts=True,
        actual_special_proof_cases=417792,metadata_cases=576,supplemental_raw_cases=1524288,
        exact_source_no_new_state_cycles_or_keep=True,
        scope_limit='Boolean hardware equivalence, not unknown/X mode simulation equivalence; unchanged startup valid gating.',
        qualification='Final combined CPU runtime and physical mapping remain separate.')
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
