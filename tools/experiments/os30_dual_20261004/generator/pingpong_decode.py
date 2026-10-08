#!/usr/bin/env python3
"""Reuse the skid and decode1 payload storage as a two-bank rotating buffer."""
from pathlib import Path
import argparse
import difflib
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT/'docs/measurements/os30-dual-20261004'
REF = ART/'raw-hold-load-v1/immediate/VexiiRiscv.v'
SHA = '1bce3882b721c09e4a5091cc3ef00360366df9b1b0b79ffb0b1938b820105569'
MAP = ART/'generator-v9/skid-structure.json'
H = 'decode_ctrls_1_up_valid'
S = 'decode_skid_rValid'
U = 'decode_ctrls_0_down_isValid'
R = 'decode_skid_gate_down_isReady'
D = 'decode_ctrls_1_up_isReady'
F = 'decode_logic_skid_flushIt'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART/'decode-pingpong-v1/immediate')
    args = parser.parse_args()
    src = REF.read_text()
    assert sha(src.encode()) == SHA
    fields = json.loads(MAP.read_text())['payload']
    masks = fields[:2]
    data = fields[2:]
    assert len(data) == 30 and sum(x['bits'] for x in data) == 276
    assert all('LANE_SEL' in x['input'] and x['bits'] == 1 for x in masks)
    changes = []

    def replace(old, new):
        assert src.count(old) == 1, (old, src.count(old))
        changes.append((old, new))

    # Pin all original metadata writers. They remain byte-identical; in
    # particular a blocked flush keeps H=1 while permanently clearing its lanes.
    metadata = '''      if(decode_skid_gate_down_isReady) begin
        decode_ctrls_1_up_valid <= decode_skid_gate_down_isValid;
      end
      if(decode_skid_gate_down_isReady) begin
        decode_ctrls_1_up_LANE_SEL_0 <= decode_skid_gate_down_LANE_SEL_0;
        decode_ctrls_1_up_LANE_SEL_1 <= decode_skid_gate_down_LANE_SEL_1;
      end
      if(when_DecodePipelinePlugin_l95) begin
        decode_ctrls_1_up_LANE_SEL_0 <= 1'b0;
      end
      if(when_DecodePipelinePlugin_l95_1) begin
        decode_ctrls_1_up_LANE_SEL_1 <= 1'b0;
      end
'''
    occupancy = '''      if(decode_ctrls_0_down_isValid) begin
        decode_skid_rValid <= 1'b1;
      end
      if(decode_skid_gate_up_ready) begin
        decode_skid_rValid <= 1'b0;
      end
      if(decode_skid_gate_up_forgetOne) begin
        decode_skid_rValid <= 1'b0;
      end
'''
    for block in [metadata, occupancy]:
        assert src.count(block) == 1
    assertions = [
        '  assign decode_ctrls_0_down_ready = (! decode_skid_rValid);',
        '  assign decode_skid_gate_up_valid = (decode_ctrls_0_down_isValid || decode_skid_rValid);',
        '  assign decode_skid_gate_up_ready = decode_skid_gate_down_isReady;',
        '  assign decode_skid_gate_down_isReady = decode_skid_gate_down_ready;',
        '  assign decode_skid_gate_down_isValid = decode_skid_gate_down_valid;',
        '  assign decode_skid_gate_up_forgetOne = (|decode_skid_gate_throwWhen_DecodePipelinePlugin_l80);',
        '  assign decode_skid_gate_throwWhen_DecodePipelinePlugin_l80 = decode_logic_skid_flushIt;',
        '  assign when_CtrlLink_l198_2 = (|decode_skid_gate_throwWhen_DecodePipelinePlugin_l80);',
        '  assign when_DecodePipelinePlugin_l95 = ((! decode_ctrls_1_up_isReady) && decode_ctrls_1_lane0_upIsCancel);',
        '  assign when_DecodePipelinePlugin_l95_1 = ((! decode_ctrls_1_up_isReady) && decode_ctrls_1_lane1_upIsCancel);',
        '  assign decode_ctrls_1_lane0_upIsCancel = decode_logic_flushes_1_onLanes_0_doIt;',
        '  assign decode_ctrls_1_lane1_upIsCancel = decode_logic_flushes_1_onLanes_1_doIt;',
    ]
    for text in assertions:
        assert src.count(text) == 1, text
    valid_block = '''  always @(*) begin
    decode_skid_gate_down_valid = decode_skid_gate_up_valid;
    if(when_CtrlLink_l198_2) begin
      decode_skid_gate_down_valid = 1'b0;
    end
  end
'''
    assert src.count(valid_block) == 1
    # All four lane-cancel expressions are literally the same whole-frame flush.
    flush_expr = re.findall(r'^  assign decode_logic_skid_flushIt = (.*);$', src, re.M)
    assert len(flush_expr) == 1
    for stage in [0, 1]:
        for lane in [0, 1]:
            assert src.count(f'  assign decode_logic_flushes_{stage}_onLanes_{lane}_doIt = {flush_expr[0]};') == 1
    for lane in [0, 1]:
        assert src.count(f'  assign decode_skid_gate_LANE_SEL_{lane}_bypass = (decode_skid_gate_up_LANE_SEL_{lane} && (! decode_logic_skid_flushIt));') == 1
    # R is the already exhaustively proved ready-only rewrite of (!H || D).
    # Guard that source block and its literal first !H term here. Both reference
    # and candidate retain it; the refinement uses that exact ready contract.
    ready_proof = ART/'decode-ready-v1/immediate'
    ready_src = (ready_proof/'VexiiRiscv.v').read_text()
    ready_start = '  wire decode_load_pending0;\n'
    ready_end = '  assign when_StageLink_l71_2 = (! decode_ctrls_1_up_isValid);\n'
    ready_block = src[src.index(ready_start):src.index(ready_end)]
    assert ready_block == ready_src[ready_src.index(ready_start):ready_src.index(ready_end)]
    assert 'assign decode_load_without_lane0 = !decode_ctrls_1_up_valid ||' in ready_block

    head_block = '    if(decode_skid_gate_down_isReady) begin\n'
    for field in data:
        field['head'] = field['input'].replace('decode_ctrls_0_down_', 'decode_ctrls_1_up_', 1)
        head_block += f"      {field['head']} <= {field['output']};\n"
    head_block += '    end\n'
    replace(head_block, '')
    # Remove the 24 tail-capture blocks, including two four-counter blocks.
    tail_blocks = re.findall(r'    if\(decode_ctrls_0_down_ready\) begin\n(?:      [^\n]+\n)+?    end\n', src)
    removed_tail = []
    expected = {x['saved']:x['input'] for x in data}
    observed = {}
    for block in tail_blocks:
        writers = re.findall(r'^      (\w+) <= (\w+);$', block, re.M)
        if any(name in expected for name, _ in writers):
            assert all(expected.get(name) == value for name, value in writers)
            assert not set(observed) & {name for name, _ in writers}
            observed.update(writers)
            removed_tail.append(block)
            replace(block, '')
    assert observed == expected and len(removed_tail) == 24
    for field in data:
        for name in [field['head'], field['saved']]:
            decl = re.findall(r'^  reg\s+(?:\[\d+:0\]\s+)?' + name + r';$', src, re.M)
            assert len(decl) == 1, name
            replace(decl[0], decl[0].replace('reg', 'wire', 1))
            assert len(re.findall(r'\b'+name+r'\s*<=', src)) == 1
        # Original tail only enters the S-controlled bypass or occupancy-gated
        # aggregate trap. No unqualified consumer may observe invalid tail data.
        references = re.findall(r'^.*\b' + field['saved'] + r'\b.*$', src, re.M)
        expected_refs = 4 if '_TRAP_' in field['saved'] else 3
        assert len(references) == expected_refs, (field['saved'], references)
        mux_head = field['output'].replace('decode_skid_gate_down_', 'decode_skid_gate_up_')
        mux = f'''  always @(*) begin
    if(decode_skid_rValid) begin
      {mux_head} = {field['saved']};
    end else begin
      {mux_head} = {field['input']};
    end
  end
'''
        assert src.count(mux) == 1
    assert src.count('assign decode_hold_buffered_trap = decode_skid_rValid &&') == 1

    added = '''  // The original occupancy and lane-mask FFs retain their flush semantics.
  // Only payload storage rotates; wide write enables use registered state.
  reg decode_pingpong_read_bank;
  reg [275:0] decode_pingpong_bank0;
  reg [275:0] decode_pingpong_bank1;
  wire decode_pingpong_write_bank;
  wire decode_pingpong_write0;
  wire decode_pingpong_write1;
  wire [275:0] decode_pingpong_input;
  wire [275:0] decode_pingpong_head;
  wire [275:0] decode_pingpong_tail;
  assign decode_pingpong_write_bank = decode_pingpong_read_bank ^ decode_ctrls_1_up_valid;
  assign decode_pingpong_write0 = !decode_skid_rValid && !decode_pingpong_write_bank;
  assign decode_pingpong_write1 = !decode_skid_rValid && decode_pingpong_write_bank;
  assign decode_pingpong_head = decode_pingpong_read_bank ? decode_pingpong_bank1 : decode_pingpong_bank0;
  assign decode_pingpong_tail = decode_pingpong_read_bank ? decode_pingpong_bank0 : decode_pingpong_bank1;
'''
    added += '  assign decode_pingpong_input = {' + ','.join(x['input'] for x in data) + '};\n'
    added += '  assign {' + ','.join(x['head'] for x in data) + '} = decode_pingpong_head;\n'
    added += '  assign {' + ','.join(x['saved'] for x in data) + '} = decode_pingpong_tail;\n'
    storage = '''  always @(posedge clk or posedge reset) begin
    if(reset) begin
      decode_pingpong_read_bank <= 1'b0;
    end else if(decode_ctrls_1_up_valid && decode_skid_gate_down_isReady) begin
      decode_pingpong_read_bank <= !decode_pingpong_read_bank;
    end
  end
  always @(posedge clk) begin
    if(decode_pingpong_write0) begin
      decode_pingpong_bank0 <= decode_pingpong_input;
    end
    if(decode_pingpong_write1) begin
      decode_pingpong_bank1 <= decode_pingpong_input;
    end
  end
'''
    added += storage
    anchor = '  assign decode_ctrls_0_down_ready = (! decode_skid_rValid);\n'
    replace(anchor, added + anchor)
    candidate = src
    for old, new in changes:
        candidate = candidate.replace(old, new, 1)
    reverse = candidate
    for old, new in reversed(changes):
        assert reverse.count(new) == 1 if new else True
        if new:
            reverse = reverse.replace(new, old, 1)
    # Empty deletions cannot be reversed by textual substitution; a unified
    # reverse patch is checked after emission below instead.
    for block in [metadata, occupancy, valid_block, ready_block]:
        assert candidate.count(block) == 1
    for field in masks:
        mask_capture = f"    if(decode_ctrls_0_down_ready) begin\n      {field['saved']} <= {field['input']};\n    end\n"
        assert src.count(mask_capture) == candidate.count(mask_capture) == 1
    for field in data:
        assert not re.search(r'\b('+field['head']+'|'+field['saved']+r')\s*<=', candidate)
    assert not re.search(r'\bdecode_pingpong_', src)
    # Reorder-free packing: each logical field occupies the same bit interval
    # in input, head and tail concatenations. No bit arithmetic is introduced.
    cursor = 276
    for field in data:
        cursor -= field['bits']
        field['packed_lsb'] = cursor
        field['packed_msb'] = cursor + field['bits'] - 1
    assert cursor == 0

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    contracts = dict(reference_sha256=SHA, data_fields=data, mask_fields=masks,
                     ready_block=ready_block, metadata_block=metadata,
                     occupancy_block=occupancy, old_head_block=head_block,
                     old_tail_blocks=removed_tail, new_bank_block=added,
                     unchanged_flush_expression=flush_expr[0])
    (out/'source-contracts.json').write_text(json.dumps(contracts, indent=2)+'\n')
    # The transition proof deliberately includes arbitrary payload/valid/masks,
    # stalls, flushes and reset. Only S=>H and R=(!H||D) are structural invariants.
    # All payload operators are copies, slices and muxes, identically per bit;
    # exhaust one independent bit, including all four old/new stored values.
    # Control and mask rules below are token-renamed literal source blocks.
    names = {
        H:'h_next', S:'s_next', 'decode_ctrls_1_up_LANE_SEL_0':'hm0_next',
        'decode_ctrls_1_up_LANE_SEL_1':'hm1_next',
    }
    controls = metadata + occupancy
    for name, target in names.items():
        controls = re.sub(r'\b'+name+r'\s*<=', target+' =', controls)
    controls = controls.replace('<=', '=')
    # Preserve RHS names; the proof wrapper aliases them to current inputs.
    declarations = f'''
  wire {U} = u;
  wire {R} = r;
  wire decode_skid_gate_down_isValid = (u || s) && !f;
  wire decode_skid_gate_down_LANE_SEL_0 = (s ? tm0 : im0) && !f;
  wire decode_skid_gate_down_LANE_SEL_1 = (s ? tm1 : im1) && !f;
  wire when_DecodePipelinePlugin_l95 = !d && f;
  wire when_DecodePipelinePlugin_l95_1 = !d && f;
  wire decode_skid_gate_up_ready = r;
  wire decode_skid_gate_up_forgetOne = f;
'''
    trans = '''module transition(
 input wire h,s,p,d,u,f,z,hd,td,b0,b1,di,
 input wire hm0,hm1,tm0,tm1,im0,im1,
 output reg h_next,s_next,hm0_next,hm1_next,
 output reg tm0_next,tm1_next,old_head_next,old_tail_next,
 output reg p_next,b0_next,b1_next,
 output wire new_head_next,new_tail_next);
 wire r = !h || d;
'''
    trans += declarations
    trans += ''' always @(*) begin
  h_next=h;s_next=s;hm0_next=hm0;hm1_next=hm1;
  if(z) begin
   h_next=0;s_next=0;hm0_next=0;hm1_next=0;
  end else begin
'''+controls+'''  end
  tm0_next=tm0;tm1_next=tm1;
  if(!s) begin tm0_next=im0;tm1_next=im1;end
  old_head_next=hd;old_tail_next=td;
  if(r) old_head_next=s ? td : di;
  if(!s) old_tail_next=di;
  p_next=p;b0_next=b0;b1_next=b1;
  if(z) p_next=0;
  else if(h && r) p_next=!p;
  if(!s && !(p ^ h)) b0_next=di;
  if(!s && (p ^ h)) b1_next=di;
 end
 assign new_head_next=p_next ? b1_next : b0_next;
 assign new_tail_next=p_next ? b0_next : b1_next;
endmodule
'''
    tb = trans + '''module tb;
 reg h,s,p,d,u,f,z,hd,td,b0,b1,di;
 reg hm0,hm1,tm0,tm1,im0,im1;
 wire hn,sn,hm0n,hm1n,tm0n,tm1n,oh,ot,pn,b0n,b1n,nh,nt;
 integer checked=0,reset_cases=0,blocked_flush=0,invalid_head=0;
 transition dut(h,s,p,d,u,f,z,hd,td,b0,b1,di,hm0,hm1,tm0,tm1,im0,im1,
                hn,sn,hm0n,hm1n,tm0n,tm1n,oh,ot,pn,b0n,b1n,nh,nt);
 initial begin
  for(integer i=0;i<(1<<18);i++) begin
   {h,s,p,d,u,f,z,hd,td,b0,b1,di,hm0,hm1,tm0,tm1,im0,im1}=18'(i);
   // Inductive relation: active head/tail data agree. Invalid original payload
   // can be arbitrary; this includes arbitrary values immediately after reset.
   if((!s || h) && (!h || hd==(p?b1:b0)) && (!s || td==(p?b0:b1))) begin
    #1;checked++;reset_cases+=z;blocked_flush+=h&&!d&&f&&!z;invalid_head+=!h&&!z;
    if(sn && !hn) $fatal(1,"tail full without head");
    if(!z && nh!==oh) $fatal(1,"head payload differs even after nonreset advance i=%x",i);
    if(hn && nh!==oh) $fatal(1,"active head payload differs i=%x",i);
    if(sn && nt!==ot) $fatal(1,"active tail payload differs i=%x",i);
    if(z && (hn||sn||hm0n||hm1n)) $fatal(1,"reset metadata differs");
    if(f&&!z&&h&&!d && (!hn||sn||hm0n||hm1n)) $fatal(1,"blocked flush tombstone lost");
    if(!z&&pn!==(p^(h&&d))) $fatal(1,"pointer changed without consuming head");
    if(hn!==(z?1'b0:((!h||d)?((u||s)&&!f):h))) $fatal(1,"head occupancy mismatch");
    if(sn!==(z?1'b0:(!(!h||d)&&!f&&(s||u)))) $fatal(1,"tail occupancy mismatch");
   end
  end
  $display("PASS: %0d exhaustive inductive transitions; reset=%0d blocked-flush=%0d invalid-head=%0d; all payload bits reduce to the guarded identical one-bit mux",checked,reset_cases,blocked_flush,invalid_head);
  $finish;
 end
endmodule
'''
    (out/'transition.sv').write_text(trans)
    (out/'tb.sv').write_text(tb)
    with (out/'proof-build.log').open('w') as log:
        subprocess.run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal',
                        '--Mdir',str(out/'proof-obj'),'-j','2',str(out/'tb.sv')],
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (out/'proof-build.log').read_text())
    with (out/'proof.log').open('w') as log:
        subprocess.run([str(out/'proof-obj/Vtb')],stdout=log,stderr=subprocess.STDOUT,check=True)
    print((out/'proof.log').read_text(),end='',flush=True)
    (out/'VexiiRiscv.v').write_text(candidate)
    patch = ''.join(difflib.unified_diff(src.splitlines(True),candidate.splitlines(True),
                                       fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v'))
    (out/'pingpong.patch').write_text(patch)
    replay = out/'reverse-replay'
    replay.mkdir()
    (replay/'VexiiRiscv.v').write_text(candidate)
    subprocess.run(['patch','--batch','--fuzz=0','-R','-p1','-i',str(out/'pingpong.patch')],cwd=replay,check=True)
    assert (replay/'VexiiRiscv.v').read_text() == src
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(out/'pingpong.patch')],cwd=replay,check=True)
    assert (replay/'VexiiRiscv.v').read_text() == candidate
    with (out/'lint.log').open('w') as log:
        subprocess.run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(out/'VexiiRiscv.v')],
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out/'lint.log').read_text())
    manifest = dict(reference=str(REF.relative_to(ROOT)),reference_sha256=SHA,
                    candidate_sha256=sha(candidate.encode()),payload_bits_before=556,
                    payload_bits_after=556,extra_control_ffs=1,extra_pipeline_cycles=0,
                    data_bank_bits=276,preserved_lane_mask_ffs=4,fields=data,
                    metadata_source_unchanged=True,invalid_tail_payload_unobservable=True,
                    head_payload_exact_after_every_nonreset_edge=True,
                    reset_exception='Head payload unreset/unobserved while reset clears its frame/lane metadata; first nonreset edge restores exact payload mapping.',
                    refinement_proof_passed=True,full_netlist_lint_passed=True,
                    exact_reverse_and_replay_passed=True,whole_cpu_validation='pending')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'pingpong_decode.py').write_text(Path(__file__).read_text())
    print('READY',out,'SHA256',manifest['candidate_sha256'],flush=True)


if __name__ == '__main__':
    main()
