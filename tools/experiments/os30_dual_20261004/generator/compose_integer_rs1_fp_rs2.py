#!/usr/bin/env python3
"""Compose measured integer RS1 2+7 and FP RS2 2+8 forwarding groups."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
PATHS=dict(base=ART/'pointer-packer-combined-v1/immediate/VexiiRiscv.v',
    integer=ART/'integer-rs1-priority-groups-v2/immediate/VexiiRiscv.v',
    fp=ART/'fp-rs2-priority-2plus8-v1/immediate/VexiiRiscv.v')
HASHES=dict(base='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa',
    integer='b182970b0f7a7e24dbafc5a6f7dc4c4008b14b62daf215dcb8b0e67578481dbf',
    fp='6c3560a5211bd184e7ca52f50cb6d9ab79c2f0eb54db0a7aa834fd961b70b3f5')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd,log,**kwargs):
    with log.open('w') as f: subprocess.run(cmd,check=True,stdout=f,stderr=subprocess.STDOUT,**kwargs)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ART/'integer-rs1-fp-rs2-combined-v1/immediate')
    args=ap.parse_args()
    for k,p in PATHS.items(): assert sha(p)==HASHES[k]
    texts={k:p.read_text() for k,p in PATHS.items()}
    intdest='_zz_execute_ctrl1_down_integer_RS1_lane0_1'
    fpdest='execute_ctrl1_down_float_RS2_lane0'
    old_int=re.findall(r'^  always @\(\*\) begin\n    '+intdest+r' = [^;]+;\n  end\n',texts['base'],re.M)
    new_int=re.findall(r'^  \(\* keep = "true" \*\) wire \[31:0\] integer_rs1_priority_early_data;\n.*?^  always @\(\*\) begin\n    '+intdest+r' = [^;]+;\n  end\n',texts['integer'],re.M|re.S)
    old_fp=re.findall(r'^  assign '+fpdest+r' = [^;]+;',texts['base'],re.M)
    new_fp=re.findall(r'^  \(\* keep = "true" \*\) wire \[31:0\] fp_rs2_priority_early_data;\n.*?^  assign '+fpdest+r' = [^;]+;',texts['fp'],re.M|re.S)
    assert len(old_int)==len(new_int)==len(old_fp)==len(new_fp)==1
    old_int,new_int,old_fp,new_fp=old_int[0],new_int[0],old_fp[0],new_fp[0]
    assert texts['base'].replace(old_int,new_int,1)==texts['integer']
    assert texts['base'].replace(old_fp,new_fp,1)==texts['fp']
    candidate=texts['integer'].replace(old_fp,new_fp,1)
    assert candidate==texts['fp'].replace(old_int,new_int,1)
    assert candidate.replace(new_fp,old_fp,1)==texts['integer']
    assert candidate.replace(new_int,old_int,1)==texts['fp']
    assert candidate.replace(new_fp,old_fp,1).replace(new_int,old_int,1)==texts['base']
    seq=lambda s:re.findall(r'^  always @\(posedge clk.*?^  end',s,re.M|re.S)
    assert seq(candidate)==seq(texts['base'])
    check=PATHS['integer'].parent/'check_vexii_os20_timing.py'
    assert sha(check)=='80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    spec=importlib.util.spec_from_file_location('actual_cones',check)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    normalized=candidate
    keep='  (* keep , syn_keep *) reg        [31:0]   '+intdest+' /* synthesis syn_keep = 1 */ ;'
    assert normalized.count(keep)==1
    normalized=normalized.replace(keep,'  reg [31:0] '+intdest+';',1)
    for operand in ('integer_rs1','fp_rs2'):
        for group in ('early','late'):
            decl=f'  (* keep = "true" *) wire [31:0] {operand}_priority_{group}_data;'
            assert normalized.count(decl)==1
            normalized=normalized.replace(decl,decl.replace('(* keep = "true" *) ',''),1)
    int_inputs=['execute_lane0_bypasser_integer_RS1_bypassEnables']+[
        f'execute_ctrl{s}_down_lane{l}_integer_WriteBackPlugin_logic_DATA_lane{l}' for s in range(2,6) for l in range(2)]+['execute_lane0_bypasser_integer_RS1_port_data']
    fp_inputs=['execute_lane0_bypasser_float_RS2_bypassEnables']+[
        f'execute_ctrl{s}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for s in range(4,13)]+['execute_lane0_bypasser_float_RS2_port_data']
    for key,inputs,outputs,width in [('integer',int_inputs,['execute_lane0_bypasser_integer_RS1_sel',intdest],41),('fp',fp_inputs,['execute_lane0_bypasser_float_RS2_sel',fpdest],42)]:
        actual,w=checker.cone(normalized,'candidate',inputs,outputs)
        assert w==width and actual==(PATHS[key].parent/'candidate.v').read_text()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    target=out/'VexiiRiscv.v';target.write_text(candidate)
    for key in PATHS:
        patch=out/('from-'+key+'.patch')
        patch.write_text(''.join(difflib.unified_diff(texts[key].splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
        replay=out/('replay-'+key);replay.mkdir();(replay/target.name).write_text(candidate)
        for label,extra,expected in [('reverse',['-R'],texts[key]),('replay',[],candidate)]:
            run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(patch)],out/(key+'-'+label+'.log'),cwd=replay)
            assert (replay/target.name).read_text()==expected
    patches=dict(integer=PATHS['integer'].parent/'integer-rs1-priority-groups.patch',fp=PATHS['fp'].parent/'fp-rs2-priority-2plus8.patch')
    for order in [('integer','fp'),('fp','integer')]:
        name='-'.join(order);replay=out/('order-'+name);replay.mkdir();(replay/target.name).write_text(texts['base'])
        for key in order:
            run(['patch','--batch','--fuzz=0','-p1','-i',str(patches[key])],out/('order-'+name+'-'+key+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==candidate
        for key in reversed(order):
            run(['patch','--batch','--fuzz=0','-R','-p1','-i',str(patches[key])],out/('reverse-'+name+'-'+key+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==texts['base']
    for key,binary in [('integer','Vinteger_rs1_proof'),('fp','Vfp_rs2_proof')]:
        run([str(PATHS[key].parent/'obj'/binary)],out/(key+'-proof-rerun.log'))
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(target)],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copyfile(__file__,out/Path(__file__).name)
    manifest=dict(reference_sha256=HASHES['base'],component_sha256=HASHES,candidate_sha256=sha(target),
        exact_scope='Only integerRS1 data block/two kept words and FP RS2 data assignment/two kept words.',
        original_integer_output_keep_preserved=True,all_original_selectors_and_live_gates_unchanged=True,
        all_sequential_source_unchanged=True,new_register_bits=0,new_cycles=0,new_kept_combinational_words=4,
        source_and_original_patch_orders_identical=True,reverse_restores_each_parent=True,
        fresh_actual_cones_identical_to_proved_components=True,focused_proofs_rerun=True,full_netlist_lint=True,
        integer_actual_cone_cases=262144,fp_actual_cone_cases=1048576,
        whole_cpu_qualification='Pending independent component/composition review then final14-case suite.',
        physical_limitation='Group boundaries can improve late priority paths but consume packing/routing resources; only complete-core timing/area qualifies benefit.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out/Path(__file__).name),source_hashes=HASHES,candidate_sha256=sha(target),
        component_recipes={k:sha(PATHS[k].parent/'recipe.json') for k in ('integer','fp')},
        patches={p.name:sha(p) for p in out.glob('*.patch')}),indent=2)+'\n')
    (out/'README.txt').write_text('''Integer RS1 and FP RS2 priority grouping on the measured5209 pair
===============================================================

Integer RS1 uses groups2+7; FP RS2 uses2+8 so its measured source0-to-source3
priority veto crosses the group boundary. Both changes preserve every original
selector and live validity/write-enable/match gate, and the integer result KEEP.
Exactly four kept combinational words are added; no state or cycle changes.

Both source orders and both original external patch orders produce identical
bytes. Both reversals restore each parent and the5209 reference. Fresh extracted
full selector/data cones equal the individually proved components; their
262144 and1048576 exhaustive bit-pattern case binaries rerun. Full lint passes.
The selected final composition receives14 retained CPU tests with the existing
skid/ping-pong monitor after independent review. Physical benefit remains a fit
question, particularly on this capacity-constrained device.

Reproduce with generator/compose_integer_rs1_fp_rs2.py --output <new-directory>.
''')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
