#!/usr/bin/env python3
"""Compose three disjoint measured timing fixes and freeze their exact proof scopes."""
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
PATHS=dict(base=ART/'fp-last-combined-v1/immediate/VexiiRiscv.v',
    dual=ART/'pointer-packer-combined-v1/immediate/VexiiRiscv.v',
    rs3=ART/'fp-rs3-priority-groups-v1/immediate/VexiiRiscv.v')
HASHES=dict(base='9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377',
    dual='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa',
    rs3='186a99f36f0c7e3afaa1f6bb832f58b95c4e2cc6373725c5f4b6669d370de704')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd,log,**kwargs):
    with log.open('w') as f: subprocess.run(cmd,check=True,stdout=f,stderr=subprocess.STDOUT,**kwargs)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ART/'pointer-packer-rs3-combined-v1/immediate')
    args=ap.parse_args()
    for key,path in PATHS.items(): assert sha(path)==HASHES[key]
    source={key:path.read_text() for key,path in PATHS.items()}
    dest='execute_ctrl1_down_float_RS3_lane0'
    old=re.findall(r'^  assign '+dest+r' = [^;]+;',source['base'],re.M)
    new=re.findall(r'^  \(\* keep = "true" \*\) wire \[31:0\] fp_rs3_priority_early_data;\n.*?^  assign '+dest+r' = [^;]+;',source['rs3'],re.M|re.S)
    assert len(old)==len(new)==1
    old,new=old[0],new[0]
    assert source['base'].replace(old,new,1)==source['rs3']
    assert source['dual'].count(old)==1
    candidate=source['dual'].replace(old,new,1)
    assert candidate.replace(new,old,1)==source['dual']
    seq=lambda s:re.findall(r'^  always @\(posedge clk.*?^  end',s,re.M|re.S)
    assert seq(candidate)==seq(source['dual'])
    checker_path=PATHS['rs3'].parent/'check_vexii_os20_timing.py'
    assert sha(checker_path)=='80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    spec=importlib.util.spec_from_file_location('actual_cones',checker_path)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    normalized=candidate
    for suffix in ('early','late'):
        decl=f'  (* keep = "true" *) wire [31:0] fp_rs3_priority_{suffix}_data;'
        assert normalized.count(decl)==1
        normalized=normalized.replace(decl,decl.replace('(* keep = "true" *) ',''),1)
    inputs=['execute_lane0_bypasser_float_RS3_bypassEnables']+[
        f'execute_ctrl{i}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for i in range(4,13)]+['execute_lane0_bypasser_float_RS3_port_data']
    actual,width=checker.cone(normalized,'candidate',inputs,['execute_lane0_bypasser_float_RS3_sel',dest])
    assert width==42 and actual==(PATHS['rs3'].parent/'candidate.v').read_text()
    # All original proof statements from the packer and pointer components
    # must still be present, in addition to exact RS3 cone extraction.
    packer=ART/'packer-round-mode-v1/immediate'
    pointer=ART/'pointer-unconditional-d-v1/immediate'
    for fname in ('candidate.v','special-candidate.v'):
        statements=re.findall(r'^  assign \w+\s*=.*?;\n|^  always @\(\*\) begin\n.*?^  end\n',(packer/fname).read_text(),re.M|re.S)
        for statement in statements: assert candidate.count(statement)==1
    assert (pointer/'candidate-block.v').read_text().strip() in candidate
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    target=out/'VexiiRiscv.v';target.write_text(candidate)
    patches=dict(pointer=pointer/'pointer-d.patch',packer=packer/'packer-round-mode.patch',rs3=PATHS['rs3'].parent/'fp-rs3-priority-groups.patch')
    # External zero-fuzz patching establishes all six orders, rather than
    # merely assuming the independently extracted edit scopes commute.
    for order in itertools.permutations(patches):
        name='-'.join(order);replay=out/('order-'+name);replay.mkdir();(replay/target.name).write_text(source['base'])
        for key in order:
            run(['patch','--batch','--fuzz=0','-p1','-i',str(patches[key])],out/('order-'+name+'-'+key+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==candidate
        for key in reversed(order):
            run(['patch','--batch','--fuzz=0','-R','-p1','-i',str(patches[key])],out/('reverse-'+name+'-'+key+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==source['base']
    for key in PATHS:
        patch=out/('from-'+key+'.patch')
        patch.write_text(''.join(difflib.unified_diff(source[key].splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
        replay=out/('replay-'+key);replay.mkdir();(replay/target.name).write_text(candidate)
        for label,extra,expected in [('reverse',['-R'],source[key]),('replay',[],candidate)]:
            run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(patch)],out/(key+'-'+label+'.log'),cwd=replay)
            assert (replay/target.name).read_text()==expected
    for key,binary in [('pointer',pointer/'obj/Vtb'),('packer',packer/'obj/Vtb'),('rs3',PATHS['rs3'].parent/'obj/Vfp_rs3_proof')]:
        run([str(binary)],out/(key+'-proof-rerun.log'))
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(target)],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copyfile(__file__,out/Path(__file__).name)
    manifest=dict(reference_sha256=HASHES['base'],component_sha256=HASHES,candidate_sha256=sha(target),
        exact_scope='One ROUNDING_INCR assign; original pointer FF equivalent D equation and local option; only RS3 data mux/two kept combinational words.',
        all_six_original_patch_orders_identical=True,all_six_reverse_orders_restore_base=True,
        reversals_restore_dual_and_rs3_parents=True,actual_rs3_full_cone_identical=True,
        actual_packer_and_pointer_proof_statements_unchanged=True,all_three_focused_proofs_rerun=True,
        new_register_bits=0,new_cycles=0,new_keep_words=2,full_netlist_lint=True,
        whole_cpu_qualification='Pending retained14-case suite with unchanged skid/pingpong observation monitor.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out/Path(__file__).name),source_hashes=HASHES,
        candidate_sha256=sha(target),component_manifest_sha256={key:sha(path) for key,path in dict(pointer=pointer/'manifest.json',packer=packer/'manifest.json',rs3=PATHS['rs3'].parent/'manifest.json').items()},
        patch_sha256={p.name:sha(p) for p in out.glob('*.patch')}),indent=2)+'\n')
    (out/'README.txt').write_text('''Final pointer, packer and RS3 composition
========================================

All three individually proved changes address measured paths and touch
disjoint equations. All six ORIGINAL patch orders produce identical bytes;
all reverse orders restore9fa. Separate reverse/replay also restores the
two-fix parent and RS3 parent. The complete extracted RS3 cone remains exact,
as do every packer proof statement and the actual pointer FF block. All three
focused proof binaries rerun. Full-netlist lint passes.

No new state, pipeline cycle or functional feature change. RS3 adds only two
kept combinational words; the existing pointer FF has an equivalent D equation
and a local clock-enable-recognition option. The packer drops only a redundant
mode gate. Timing and actual pointer D-versus-ENA mapping require full fitting.
The final14-case CPU qualification is recorded separately when complete.

Reproduce with generator/compose_pointer_packer_rs3.py --output <new-directory>.
''')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
