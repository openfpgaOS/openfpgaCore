#!/usr/bin/env python3
"""Compose disjoint packer rounding and existing-pointer D-input transforms."""
from pathlib import Path
import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT/'docs/measurements/os30-dual-20261004'
PATHS = dict(base=ART/'fp-last-combined-v1/immediate/VexiiRiscv.v',
    pointer=ART/'pointer-unconditional-d-v1/immediate/VexiiRiscv.v',
    packer=ART/'packer-round-mode-v1/immediate/VexiiRiscv.v')
HASHES = dict(base='9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377',
    pointer='d6f88a0cd1e587b83fb56e178b5fedbb2f210e4eae050ed236331cdb7a334efa',
    packer='b5002595e843c5a634a124be9404c8a010f60d053cf1c673ed509bd1512ca193')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd,log,**kwargs):
    with log.open('w') as handle:
        subprocess.run(cmd,check=True,stdout=handle,stderr=subprocess.STDOUT,**kwargs)


def apply(source,changes,reverse=False):
    for a,b in (reversed(changes) if reverse else changes):
        old,new=(b,a) if reverse else (a,b)
        assert source.count(old)==1
        source=source.replace(old,new,1)
    return source


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ART/'pointer-packer-combined-v1/immediate')
    args=ap.parse_args()
    for key,path in PATHS.items(): assert sha(path)==HASHES[key]
    source={key:path.read_text() for key,path in PATHS.items()}
    dest='FpuPackerPlugin_logic_pip_node_1_s1_ROUNDING_INCR'
    packer=[]
    for key in ('base','packer'):
        lines=re.findall(r'^  assign '+dest+r' = [^;]+;',source[key],re.M)
        assert len(lines)==1
        packer.append(lines[0])
    packer_changes=[tuple(packer)]
    assert apply(source['base'],packer_changes)==source['packer']
    ptr='decode_pingpong_read_bank'
    decls=[]; blocks=[]
    for key in ('base','pointer'):
        decl=re.findall(r'^  (?:\(\* altera_attribute = "-name AUTO_CLOCK_ENABLE_RECOGNITION OFF" \*\) )?reg '+ptr+';',source[key],re.M)
        block=[b for b in re.findall(r'^  always @\(posedge clk or posedge reset\) begin\n.*?^  end\n',source[key],re.M|re.S) if ptr+' <=' in b]
        assert len(decl)==len(block)==1
        decls.append(decl[0]); blocks.append(block[0])
    pointer_changes=[tuple(decls),tuple(blocks)]
    assert apply(source['base'],pointer_changes)==source['pointer']
    candidate=apply(source['pointer'],packer_changes)
    assert candidate==apply(source['packer'],pointer_changes)
    assert apply(candidate,packer_changes,True)==source['pointer']
    assert apply(candidate,pointer_changes,True)==source['packer']
    assert apply(apply(candidate,pointer_changes,True),packer_changes,True)==source['base']
    # Every extracted packer proof statement and every pointer proof block
    # remains identical to the independently qualified component.
    for fname in ('reference-block.v','candidate-block.v'):
        actual=(PATHS['pointer'].parent/fname).read_text().strip()
        assert actual in (source['base'] if fname.startswith('reference') else candidate)
    for fname in ('candidate.v','special-candidate.v'):
        cone=(PATHS['packer'].parent/fname).read_text()
        stmts=re.findall(r'^  assign \w+\s*=.*?;\n|^  always @\(\*\) begin\n.*?^  end\n',cone,re.M|re.S)
        assert stmts
        for statement in stmts: assert candidate.count(statement)==1
    out=args.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    target=out/'VexiiRiscv.v'; target.write_text(candidate)
    for key in PATHS:
        patch=out/('from-'+key+'.patch')
        patch.write_text(''.join(difflib.unified_diff(source[key].splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
        replay=out/('replay-'+key);replay.mkdir();(replay/target.name).write_text(candidate)
        for label,extra,expected in [('reverse',['-R'],source[key]),('replay',[],candidate)]:
            run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(patch)],out/(key+'-'+label+'.log'),cwd=replay)
            assert (replay/target.name).read_text()==expected
    patches=dict(pointer=PATHS['pointer'].parent/'pointer-d.patch',packer=PATHS['packer'].parent/'packer-round-mode.patch')
    for order in [('pointer','packer'),('packer','pointer')]:
        replay=out/('order-'+'-'.join(order));replay.mkdir();(replay/target.name).write_text(source['base'])
        for key in order:
            run(['patch','--batch','--fuzz=0','-p1','-i',str(patches[key])],out/('order-'+'-'.join(order)+'-'+key+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==candidate
    for key in ('pointer','packer'):
        run([str(PATHS[key].parent/'obj/Vtb')],out/(key+'-proof-rerun.log'))
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(target)],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copyfile(__file__,out/Path(__file__).name)
    manifest=dict(reference_sha256=HASHES['base'],component_sha256=HASHES,candidate_sha256=sha(target),
        exact_scope='One packer ROUNDING_INCR assignment plus one pointer attribute and its equivalent next-state expression.',
        source_orders_and_original_external_patch_orders_identical=True,reverse_restores_each_parent=True,
        actual_component_cones_and_proof_blocks_unchanged=True,focused_proofs_rerun=True,
        no_new_state_or_cycles=True,full_netlist_lint=True,
        physical_caveat='Pointer D-versus-ENA mapping and packer fanout/timing improvement remain unverified until fit.',
        cpu_qualification='Pending selected final combined14-case suite; possible disjoint RS3 grouping component is being prepared.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out/Path(__file__).name),reference_hashes=HASHES,
        candidate_sha256=sha(target),component_manifests={key:sha(PATHS[key].parent/'manifest.json') for key in ('pointer','packer')},
        patch_hashes={p.name:sha(p) for p in out.glob('*.patch')}),indent=2)+'\n')
    (out/'README.txt').write_text('''Pointer D-input and packer mode-gate composition
==============================================

The two independently proved source edits touch disjoint equations. Both
source orders and both original external patch orders yield identical bytes.
Reversals recover each component and the9fa baseline exactly. Every extracted
packer statement and both pointer proof blocks retain the qualified text.
Both focused proof binaries rerun successfully; full-netlist lint passes.

Only the original pointer FF next-state expression/attribute changes; reset,
occupancy, lane masks and all payload banks remain. The packer removes only
its redundant NORMAL qualifier from the raw rounding decision. No FF or cycle
is added. Any D-versus-ENA or timing improvement still requires actual fitting.
The final selected CPU composition will receive the retained14-case suite.

Reproduce with generator/compose_pointer_packer.py --output <new-directory>.
''')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
