#!/usr/bin/env python3
"""Independent exact-source/storage audit of framebuffer payload packing."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'texture-offer-v1/gpu_core.v'
PROP=ART/'fbwq-payload-pack-v1'
CAND=PROP/'gpu_core.v'
OUT=ART/'fbwq-payload-pack-emitted-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(BASE)=='2d4870c5b1c687cb51a3e60aef0fa0d31372bcea4c4dd521a0c825dd8ba24ec7'
assert sha(CAND)=='e7a1b8aacf523f87add3e030bbb2212b347cab29c35720e8f0a3d985d3fb3bba'
old,new=BASE.read_text(),CAND.read_text()
old_decl='reg [31:0] fbwq_data [0:FBWQ_DEPTH-1];\n(* ramstyle = "MLAB, no_rw_check" *) reg [3:0]  fbwq_strb [0:FBWQ_DEPTH-1];'
new_decl='(* ramstyle = "M10K" *) reg [35:0] fbwq_payload [0:FBWQ_DEPTH-1];'
old_read='                m_wr_wdata   <= fbwq_data[fbwq_rd_ptr];\n                m_wr_wstrb   <= fbwq_strb[fbwq_rd_ptr];'
new_read='                {m_wr_wstrb, m_wr_wdata} <= fbwq_payload[fbwq_rd_ptr];'
edits=[(old_decl,new_decl,1),(old_read,new_read,3)]
for k in ['req','stage']:
    edits.append((f'                fbwq_data[fbwq_wr_ptr] <= fbwq_{k}_data;\n                fbwq_strb[fbwq_wr_ptr] <= fbwq_{k}_strb;',
                  f'                fbwq_payload[fbwq_wr_ptr] <= {{fbwq_{k}_strb, fbwq_{k}_data}};',1))
expected=old
for a,b,count in edits:
    assert expected.count(a)==count
    expected=expected.replace(a,b)
assert expected==new
restored=new
for a,b,count in reversed(edits):
    assert restored.count(b)==count
    restored=restored.replace(b,a)
assert restored==old
assert 'localparam FBWQ_DEPTH = 16;' in old
assert len(re.findall(r'\bfbwq_(?:data|strb)\s*\[[^\n;]+;',old))==12
assert len(re.findall(r'\bfbwq_payload\s*\[',new))==6
assert not re.search(r'\bfbwq_(data|strb)\s*\[',new)
for signal,width in [('m_wr_wdata',31),('m_wr_wstrb',3)]:
    assert re.search(r'\['+str(width)+r':0\]\s+'+signal+r'\b',old)
    assert len(re.findall(r'\b'+signal+r'\s*<=',old))==3

# Extract the complete original read/write priority fragment and prove that
# the harness carries those exact branch orders and every memory statement.
proof=(PROP/'proof.v').read_text()
mods={name:re.search(r'module '+name+r'\(.*?endmodule',proof,re.S)[0]
      for name in ['reference','candidate']}
for name,is_new in [('reference',False),('candidate',True)]:
    src=mods[name]
    read=new_read if is_new else old_read
    assert src.count(read)==3
    assert (new_decl if is_new else old_decl) in src
    body=src[src.index('end else if(run_enable) begin'):src.index('for(genvar')]
    # Only the same two memory writes occur outside initialization.
    expected_body='end else if(run_enable) begin\n'
    for i,cond in enumerate(['fbwq_start_now','fbwq_continue_now','fbwq_w_chain_start']):
        expected_body+=('  if' if i==0 else '  end else if')+'('+cond+') begin\n'+read+'\n'
    expected_body+='  end\n'
    for i,cond in enumerate(['fbwq_swap_now','fbwq_stage_drain_now']):
        expected_body+=('  if' if i==0 else '  end else if')+'('+cond+') begin\n'+edits[i+2][int(is_new)]+'\n'
    expected_body+='  end\n end\nend\n'
    assert body==expected_body
    assert 'input [3:0] fbwq_rd_ptr,fbwq_wr_ptr' in src
    assert 'output [575:0] memory,output [35:0] output_word' in src
    assert 'assign output_word={m_wr_wstrb,m_wr_wdata};' in src

# Same-address nonblocking read captures the old 36-bit word in both forms.
# There is no data-dependent indexing/control, arithmetic, partial write, or
# reset/initial write added to the actual design. Each unaffected memory cell
# keeps its own arbitrary old value; the one written cell and read output are
# the only state updates. This makes the per-bit inductive reduction exact.
OUT.mkdir(exist_ok=False)
r=subprocess.run([str(PROP/'obj/Vpacking_proof')],check=True,capture_output=True,text=True)
(OUT/'proof-rerun.log').write_text(r.stdout+r.stderr)
assert 'PASS 819200 exact sequential transitions' in r.stdout
manifest=dict(candidate_sha256=sha(CAND),reference_sha256=sha(BASE),exact_source_reconstruction_and_reversal=True,
    complete_memory_reference_census=True,actual_declarations_and_statements_in_harness=True,
    exact_original_branch_priority_and_unreset_hold_preserved=True,all_other_bytes_unchanged=True,
    total_storage_bits=576,output_bits=36,arbitrary_unaffected_memory_cells_preserved_by_identity=True,
    bit_induction_scope='Data-independent full-word copies and muxes; no arithmetic/cross-bit/control dependence.',
    all_control_and_pointer_cases_including_collision=True,packed_no_rw_check=False,
    proof_rerun_pass=True,proof_transitions=819200,
    qualification='Binary memory-state refinement; hardware mapping and retained rendering are separate.',
    hashes={str(p.relative_to(ROOT)):sha(p) for p in [BASE,CAND,PROP/'proof.v',PROP/'proof.cpp',PROP/'recipe.json',PROP/'manifest.json']})
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
