#!/usr/bin/env python3
"""Add the proved FP RS3 group to the qualified integer-RS1/FP-RS2 composition."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import itertools
import json
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
PATHS=dict(base=ART/'pointer-packer-combined-v1/immediate/VexiiRiscv.v',
    parent=ART/'integer-rs1-fp-rs2-combined-v1/immediate/VexiiRiscv.v',
    integer=ART/'integer-rs1-priority-groups-v2/immediate/VexiiRiscv.v',
    rs2=ART/'fp-rs2-priority-2plus8-v1/immediate/VexiiRiscv.v',
    rs3base=ART/'fp-last-combined-v1/immediate/VexiiRiscv.v',
    rs3=ART/'fp-rs3-priority-groups-v1/immediate/VexiiRiscv.v')
HASHES=dict(base='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa',
    parent='fb18b1ca44bdb6ae500583c094d0c8859e3a7184457194fb40b738fe09337fdb',
    integer='b182970b0f7a7e24dbafc5a6f7dc4c4008b14b62daf215dcb8b0e67578481dbf',
    rs2='6c3560a5211bd184e7ca52f50cb6d9ab79c2f0eb54db0a7aa834fd961b70b3f5',
    rs3base='9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377',
    rs3='186a99f36f0c7e3afaa1f6bb832f58b95c4e2cc6373725c5f4b6669d370de704')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd,log,**kwargs):
    with log.open('w') as f: subprocess.run(cmd,check=True,stdout=f,stderr=subprocess.STDOUT,**kwargs)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ART/'all-operand-groups-v1/immediate')
    args=ap.parse_args()
    for k,p in PATHS.items(): assert sha(p)==HASHES[k]
    texts={k:p.read_text() for k,p in PATHS.items()}
    dest='execute_ctrl1_down_float_RS3_lane0'
    old=re.findall(r'^  assign '+dest+r' = [^;]+;',texts['rs3base'],re.M)
    new=re.findall(r'^  \(\* keep = "true" \*\) wire \[31:0\] fp_rs3_priority_early_data;\n.*?^  assign '+dest+r' = [^;]+;',texts['rs3'],re.M|re.S)
    assert len(old)==len(new)==1
    old,new=old[0],new[0]
    assert texts['rs3base'].replace(old,new,1)==texts['rs3']
    assert texts['parent'].count(old)==1
    candidate=texts['parent'].replace(old,new,1)
    assert candidate.replace(new,old,1)==texts['parent']
    seq=lambda s:re.findall(r'^  always @\(posedge clk.*?^  end',s,re.M|re.S)
    assert seq(candidate)==seq(texts['parent'])
    check=PATHS['integer'].parent/'check_vexii_os20_timing.py'
    assert sha(check)=='80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    spec=importlib.util.spec_from_file_location('actual_cones',check)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    normalized=candidate
    intdest='_zz_execute_ctrl1_down_integer_RS1_lane0_1'
    keep='  (* keep , syn_keep *) reg        [31:0]   '+intdest+' /* synthesis syn_keep = 1 */ ;'
    assert normalized.count(keep)==1
    normalized=normalized.replace(keep,'  reg [31:0] '+intdest+';',1)
    for operand in ('integer_rs1','fp_rs2','fp_rs3'):
        for group in ('early','late'):
            decl=f'  (* keep = "true" *) wire [31:0] {operand}_priority_{group}_data;'
            assert normalized.count(decl)==1
            normalized=normalized.replace(decl,decl.replace('(* keep = "true" *) ',''),1)
    inputs_int=['execute_lane0_bypasser_integer_RS1_bypassEnables']+[
        f'execute_ctrl{s}_down_lane{l}_integer_WriteBackPlugin_logic_DATA_lane{l}' for s in range(2,6) for l in range(2)]+['execute_lane0_bypasser_integer_RS1_port_data']
    cones={}
    cones['integer'],width=checker.cone(normalized,'candidate',inputs_int,['execute_lane0_bypasser_integer_RS1_sel',intdest])
    assert width==41
    for rs in (2,3):
        inputs=[f'execute_lane0_bypasser_float_RS{rs}_bypassEnables']+[
            f'execute_ctrl{s}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for s in range(4,13)]+[f'execute_lane0_bypasser_float_RS{rs}_port_data']
        cones[f'rs{rs}'],width=checker.cone(normalized,'candidate',inputs,[f'execute_lane0_bypasser_float_RS{rs}_sel',f'execute_ctrl1_down_float_RS{rs}_lane0'])
        assert width==42
    for key,actual in cones.items(): assert actual==(PATHS[key].parent/'candidate.v').read_text()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    target=out/'VexiiRiscv.v';target.write_text(candidate)
    for key in ('base','parent'):
        patch=out/('from-'+key+'.patch')
        patch.write_text(''.join(difflib.unified_diff(texts[key].splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
        replay=out/('replay-'+key);replay.mkdir();(replay/target.name).write_text(candidate)
        for label,extra,expected in [('reverse',['-R'],texts[key]),('replay',[],candidate)]:
            run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(patch)],out/(key+'-'+label+'.log'),cwd=replay)
            assert (replay/target.name).read_text()==expected
    patches=dict(integer=PATHS['integer'].parent/'integer-rs1-priority-groups.patch',rs2=PATHS['rs2'].parent/'fp-rs2-priority-2plus8.patch',rs3=PATHS['rs3'].parent/'fp-rs3-priority-groups.patch')
    for order in itertools.permutations(patches):
        name='-'.join(order);replay=out/('order-'+name);replay.mkdir();(replay/target.name).write_text(texts['base'])
        for key in order:
            run(['patch','--batch','--fuzz=0','-p1','-i',str(patches[key])],out/('order-'+name+'-'+key+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==candidate
        for key in reversed(order):
            run(['patch','--batch','--fuzz=0','-R','-p1','-i',str(patches[key])],out/('reverse-'+name+'-'+key+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==texts['base']
    # Applying the already-frozen combined parent patch after the RS3 patch
    # is another direct commutation check against the selected fb18 parent.
    replay=out/'rs3-before-parent';replay.mkdir();(replay/target.name).write_text(texts['base'])
    for key,patch in [('rs3',patches['rs3']),('parent',PATHS['parent'].parent/'from-base.patch')]:
        run(['patch','--batch','--fuzz=0','-p1','-i',str(patch)],out/('rs3-before-parent-'+key+'.log'),cwd=replay)
    assert (replay/target.name).read_text()==candidate
    for key,binary in [('integer','Vinteger_rs1_proof'),('rs2','Vfp_rs2_proof'),('rs3','Vfp_rs3_proof')]:
        run([str(PATHS[key].parent/'obj'/binary)],out/(key+'-proof-rerun.log'))
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(target)],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copyfile(__file__,out/Path(__file__).name)
    manifest=dict(reference_sha256=HASHES['parent'],component_sha256=HASHES,candidate_sha256=sha(target),
        exact_scope_vs_parent='Only prior proved FP RS3 data assignment and its two kept combinational words.',
        group_sizes=dict(integer_rs1=[2,7],fp_rs2=[2,8],fp_rs3=[4,6]),
        all_original_selectors_live_gates_output_keep_and_sequential_source_unchanged=True,
        new_register_bits=0,new_cycles=0,new_kept_words_vs_parent=2,
        all_six_original_component_patch_orders_identical=True,all_six_reversals_restore_5209=True,
        reversing_only_rs3_restores_fb18=True,rs3_and_parent_patch_commute=True,
        all_three_fresh_actual_cones_identical_to_proved_components=True,all_three_proofs_rerun=True,
        exhaustive_cases=dict(integer=262144,rs2=1048576,rs3=1048576),full_netlist_lint=True,
        whole_cpu_qualification='Pending independent review then selected final14-case suite.',
        physical_qualification='Root will measure seed2 with scoped stage2-ready fanout hint; source equivalence does not imply timing benefit.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out/Path(__file__).name),source_hashes=HASHES,candidate_sha256=sha(target),
        component_recipes={key:sha(PATHS[key].parent/'recipe.json') for key in ('integer','rs2','rs3')},
        patches={p.name:sha(p) for p in out.glob('*.patch')}),indent=2)+'\n')
    (out/'README.txt').write_text('''All selected operand groups on the qualified fb18 parent
=======================================================

Only the independently proved RS3 4+6 data grouping is added to fb18. Existing
integer RS1 2+7 and FP RS2 2+8 groups remain byte-identical. All selectors, live
match/write-enable/lane-valid gates, original output KEEP and FFs stay unchanged.
Two kept combinational words are added, with no state or cycle.

All six original component patch orders on5209 produce the same final source;
all six reversals recover5209. Reversing only RS3 restores fb18, and applying
RS3 before the entire original fb18 patch also gives the same result. Fresh
actual full selector/data cones equal all three qualified proof artifacts.
Their262144/1048576/1048576 exhaustive-case binaries rerun; full lint passes.

The new candidate gets one final14-case CPU suite with the retained monitor
after independent review. Fitting will establish physical area/timing; the
intended scoped ready-fanout option is a separate synthesis constraint owned
by the parent and does not alter this frozen RTL.

Reproduce with generator/compose_all_operand_groups.py --output <new-directory>.
''')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
