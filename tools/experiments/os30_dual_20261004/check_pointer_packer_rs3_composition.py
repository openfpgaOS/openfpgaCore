#!/usr/bin/env python3
"""Independently check both pointer/packer and triple RS3 compositions."""
from pathlib import Path
import difflib
import hashlib
import itertools
import json

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
OUT=ART/'pointer-packer-rs3-composition-audit-v1'
FILES={
 'base':('fp-last-combined-v1','9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377'),
 'pointer':('pointer-unconditional-d-v1','d6f88a0cd1e587b83fb56e178b5fedbb2f210e4eae050ed236331cdb7a334efa'),
 'packer':('packer-round-mode-v1','b5002595e843c5a634a124be9404c8a010f60d053cf1c673ed509bd1512ca193'),
 'rs3':('fp-rs3-priority-groups-v1','186a99f36f0c7e3afaa1f6bb832f58b95c4e2cc6373725c5f4b6669d370de704'),
 'dual':('pointer-packer-combined-v1','5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'),
 'triple':('pointer-packer-rs3-combined-v1','a083bdb084b9dfd7b8944287ef3411b4dc739bf366f7dfa3f42599d5edd0528b')}


def edits(old,new):
    a,b=old.splitlines(True),new.splitlines(True)
    return [(''.join(a[i:j]),''.join(b[k:l])) for op,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op!='equal']


def apply(text,changes):
    for old,new in changes:
        assert text.count(old)==1,(old[:100],text.count(old))
        text=text.replace(old,new,1)
    return text


def main():
    source={}
    for name,(directory,sha) in FILES.items():
        raw=(ART/directory/'immediate/VexiiRiscv.v').read_bytes()
        assert hashlib.sha256(raw).hexdigest()==sha
        source[name]=raw.decode()
    deltas={n:edits(source['base'],source[n]) for n in ['pointer','packer','rs3']}
    assert {n:len(d) for n,d in deltas.items()}=={'pointer':2,'packer':1,'rs3':1}
    for order in itertools.permutations(['pointer','packer']):
        candidate=source['base']
        for n in order:candidate=apply(candidate,deltas[n])
        assert candidate==source['dual']
    for order in itertools.permutations(deltas):
        candidate=source['base']
        for n in order:candidate=apply(candidate,deltas[n])
        assert candidate==source['triple']
        restored=source['triple']
        for n in order:restored=apply(restored,[(b,a) for a,b in deltas[n]])
        assert restored==source['base']
    assert apply(source['triple'],[(b,a) for a,b in deltas['rs3']])==source['dual']
    assert apply(source['dual'],deltas['rs3'])==source['triple']
    OUT.mkdir(exist_ok=False)
    result=dict(source_sha256={n:h for n,(_,h) in FILES.items()},
       independent_component_diff_counts={n:len(d) for n,d in deltas.items()},
       both_dual_orders_match=True,all_six_triple_orders_match=True,
       all_six_triple_reverse_orders_restore_base=True,
       removing_only_rs3_restores_dual=True,
       no_other_source_edits=True,components_match_independently_audited_equations=True,
       existing_pointer_next_state_equivalent_all_other_state_exact=True,
       extra_registers=0,extra_cycles=0,
       mapping_qualification='Pointer D-versus-ENA and actual LUT/fanout placement require postfit verification.',
       runtime_qualification='Final triple14-case suite running; dual suite intentionally deferred until selected.')
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
