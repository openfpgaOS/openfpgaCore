#!/usr/bin/env python3
"""Independent emitted-scope/control audit of the second decoded buffer draft."""
from pathlib import Path
import hashlib
import itertools
import json
import re

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
DIR = ART / 'decode-second-skid-draft-v1/immediate'
OUT = ART / 'decode-second-skid-emitted-audit-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert sha(BASE) == '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
    assert sha(DIR / 'VexiiRiscv.v') == 'f703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5'
    old = BASE.read_text()
    new = (DIR / 'VexiiRiscv.v').read_text()
    changes = json.loads((DIR / 'changed-source-blocks.json').read_text())
    assert len(changes) == 101
    captures = re.findall(r'^      (decode_ctrls_3_up_\w+) <= (decode_ctrls_2_down_\w+);$', old, re.M)
    captures = [(q,d) for q,d in captures if 'LANE_SEL' not in q and q != 'decode_ctrls_3_up_valid']
    assert len(captures) == 90
    fields = []
    at = 0
    for i, (q,d) in enumerate(captures):
        assert d == q.replace('_3_up_', '_2_down_')
        m = re.search(r'^  reg\s+(?:\[(\d+):0\]\s+)?' + q + ';$', old, re.M)
        assert m
        width = int(m[1]) + 1 if m[1] else 1
        declaration = m[0]
        assert changes[i] == [declaration, declaration.replace('reg', 'wire', 1)]
        assert re.findall(r'\b' + q + r'\s*<=\s*([^;]+);', old) == [d]
        assert not re.search(r'\b' + q + r'\s*<=', new)
        fields.append(dict(head=q, input=d, bits=width, lsb=at, msb=at+width-1))
        at += width
    assert at == 380 and fields == json.loads((DIR / 'payload-map.json').read_text())
    capture_block = '    if(decode_ctrls_2_down_isReady) begin\n' + ''.join(f'      {q} <= {d};\n' for q,d in captures) + '    end\n'
    assert changes[90] == [capture_block, '']
    expected_control = '''      if(decode_dispatch_head_ready) begin
        decode_ctrls_3_up_valid <= ((decode_dispatch_skid_full || decode_ctrls_2_down_isValid) && !decode_dispatch_flush);
      end
      if(decode_dispatch_head_ready) begin
        decode_ctrls_3_up_LANE_SEL_0 <= (decode_dispatch_skid_full ? decode_dispatch_tail_mask[0] : decode_ctrls_2_down_LANE_SEL_0) && !decode_dispatch_flush;
        decode_ctrls_3_up_LANE_SEL_1 <= (decode_dispatch_skid_full ? decode_dispatch_tail_mask[1] : decode_ctrls_2_down_LANE_SEL_1) && !decode_dispatch_flush;
      end'''
    assert changes[91][1] == expected_control
    assert changes[92][1] == '''  always @(*) begin
    decode_ctrls_2_down_ready = !decode_dispatch_skid_full;
  end'''
    assert changes[93][1] == '  assign decode_load_without_lane0 = !decode_ctrls_1_up_valid || !decode_ctrls_2_up_valid || !decode_dispatch_skid_full;\n'
    assert changes[94][1] == "  assign decode_load_needs_lane0 = 1'b0;\n"
    assert changes[95][1] == '''      if(decode_dispatch_up_forget) begin
        decode_ctrls_2_up_valid <= 1'b0;
      end
''' + changes[95][0]
    assert changes[95][0] == '''      if(decode_ctrls_1_down_isReady) begin
        decode_ctrls_2_up_valid <= decode_ctrls_1_down_isValid;
      end'''
    assert changes[96][1] == '  assign decode_ctrls_2_up_isMoving = (decode_ctrls_2_up_isValid && (decode_ctrls_2_up_isReady || decode_dispatch_up_cancel));'
    assert changes[97][1] == '  assign decode_ctrls_2_up_isCanceling = decode_ctrls_2_up_isValid && decode_dispatch_up_cancel;'
    for i, lhs in [(98,'decode_logic_trapPending[0]'), (99,'TrapPlugin_logic_harts_0_trap_fsm_inflightTrap')]:
        assert changes[i][0].startswith('  assign ' + lhs + ' = ')
        assert changes[i][1] == changes[i][0][:-1] + ' || decode_dispatch_tail_trap_pending;'
    assert changes[100][0] == '  wire decode_load_pending0;'
    extra = changes[100][1].removesuffix('\n  wire decode_load_pending0;')
    expected_header = '''  // PRIVATE DRAFT: age1025 decoded S2M + StageLink rotating payload.
  reg decode_dispatch_skid_full;
  (* altera_attribute = "-name AUTO_CLOCK_ENABLE_RECOGNITION OFF" *) reg decode_dispatch_read_bank;
  reg [379:0] decode_dispatch_bank0;
  reg [379:0] decode_dispatch_bank1;
  reg [1:0] decode_dispatch_tail_mask;
  wire decode_dispatch_flush = (|{early0_EnvPlugin_logic_flushPort_valid,CsrAccessPlugin_logic_flushPort_valid,early0_BranchPlugin_logic_flushPort_valid,LsuPlugin_logic_flushPort_valid});
  wire decode_dispatch_head_ready = !decode_ctrls_3_up_valid || decode_ctrls_3_up_isReady;
  wire decode_dispatch_write_bank = decode_dispatch_read_bank ^ decode_ctrls_3_up_valid;
  wire decode_dispatch_up_cancel = decode_dispatch_flush && !decode_dispatch_skid_full;
  wire decode_dispatch_up_forget = decode_dispatch_flush && !decode_dispatch_skid_full;
  wire [379:0] decode_dispatch_head = decode_dispatch_read_bank ? decode_dispatch_bank1 : decode_dispatch_bank0;
  wire [379:0] decode_dispatch_tail = decode_dispatch_read_bank ? decode_dispatch_bank0 : decode_dispatch_bank1;
'''
    expected = expected_header + '  wire [379:0] decode_dispatch_input = {' + ','.join(x['input'] for x in reversed(fields)) + '};\n'
    for f in fields:
        index = str(f['lsb']) if f['bits'] == 1 else f"{f['msb']}:{f['lsb']}"
        expected += f"  assign {f['head']} = decode_dispatch_head[{index}];\n"
    traps = [next(f['lsb'] for f in fields if f['head'] == 'decode_ctrls_3_up_TRAP_' + str(i)) for i in range(2)]
    expected += f'''  wire decode_dispatch_tail_trap_pending = decode_dispatch_skid_full && !decode_dispatch_flush &&
      ((decode_dispatch_tail_mask[0] && decode_dispatch_tail[{traps[0]}]) ||
       (decode_dispatch_tail_mask[1] && decode_dispatch_tail[{traps[1]}]));
  always @(posedge clk) begin
    if(!decode_dispatch_skid_full) begin
      if(decode_dispatch_write_bank) decode_dispatch_bank1 <= decode_dispatch_input;
      else decode_dispatch_bank0 <= decode_dispatch_input;
      decode_dispatch_tail_mask <= {{decode_ctrls_2_down_LANE_SEL_1,decode_ctrls_2_down_LANE_SEL_0}};
    end
  end
  always @(posedge clk or posedge reset) begin
    if(reset) begin
      decode_dispatch_skid_full <= 1'b0;
      decode_dispatch_read_bank <= 1'b0;
    end else begin
      if(decode_ctrls_2_down_isValid) decode_dispatch_skid_full <= 1'b1;
      if(decode_dispatch_head_ready) decode_dispatch_skid_full <= 1'b0;
      if(decode_dispatch_flush) decode_dispatch_skid_full <= 1'b0;
      decode_dispatch_read_bank <= decode_dispatch_read_bank ^ (decode_ctrls_3_up_valid && decode_dispatch_head_ready);
    end
  end
'''
    assert extra == expected
    rebuilt = old
    for a,b in changes:
        assert rebuilt.count(a) == 1
        rebuilt = rebuilt.replace(a,b,1)
    assert rebuilt == new
    # Old per-lane tombstone clearing and partially sent dispatch state survive.
    guards = []
    for lane in range(2):
        expr = f"(|{{(early0_EnvPlugin_logic_flushPort_valid && 1'b1),{{(CsrAccessPlugin_logic_flushPort_valid && 1'b1),{{(early0_BranchPlugin_logic_flushPort_valid && 1'b1),(LsuPlugin_logic_flushPort_valid && 1'b1)}}}}}})"
        guards.append(f'  assign decode_logic_flushes_3_onLanes_{lane}_doIt = {expr};')
        guards.append(f'  assign when_DecodePipelinePlugin_l95_{lane+4} = ((! decode_ctrls_3_up_isReady) && decode_ctrls_3_lane{lane}_upIsCancel);')
        guards.append(f'''      if(when_DecodePipelinePlugin_l95_{lane+4}) begin
        decode_ctrls_3_up_LANE_SEL_{lane} <= 1'b0;
      end''')
    for guard in guards:
        assert old.count(guard) == new.count(guard) == 1, guard
    names = ['DispatchPlugin_logic_feeds_0_sent','DispatchPlugin_logic_feeds_1_sent',
             'DecoderPlugin_logic_harts_0_uopId','DecoderPlugin_logic_interrupt_buffered',
             'decode_ctrls_2_up_LANE_SEL_0_regNext','decode_ctrls_2_up_LANE_SEL_1_regNext',
             'decode_ctrls_2_up_LANE_SEL_0_regNext_1','decode_ctrls_2_up_LANE_SEL_1_regNext_1']
    for name in names:
        assert re.findall(r'^.*\b' + name + r' <=[^;]+;',old,re.M) == re.findall(r'^.*\b' + name + r' <=[^;]+;',new,re.M)
    # Independent truth functions for the ready helper and cancellation adapter.
    controls = 0
    for h1,h2,h3,s,d,u,flush in itertools.product(range(2),repeat=7):
        if s and not h3:
            continue
        r2 = not s
        r1 = r2 or not h2
        first_capture = r1 or not h1
        assert first_capture == ((not h1) or (not h2) or (not s))
        cancel = flush and not s
        assert not cancel or r2
        assert (u and (r2 or cancel)) == (u and r2)
        assert ((not u) or r2 or (u and cancel)) == ((not u) or r2)
        controls += 1
    OUT.mkdir(exist_ok=False)
    manifest = dict(passed=True, scope='Independent emitted scope, mapping and control review of draft; actual-source sequential/whole-CPU qualification remains separate.',
        reference_sha256=sha(BASE),candidate_sha256=sha(DIR/'VexiiRiscv.v'),
        exact_complete_source_reconstruction=True,payload_fields=90,payload_bits=380,
        all_380_packed_positions_and_sole_writers_verified=True,
        first_ready_helper_cases=controls,exact_four_older_flush_sources=True,
        existing_partial_sent_and_per_lane_cancel_controls_preserved=True,
        original_uop_interrupt_and_transaction_spawn_writers_preserved=True,
        both_trap_aggregates_include_only_live_unflushed_stored_tail=True,
        no_added_unstalled_stage=True,new_elastic_capacity_requires_architectural_testing=True,
        pending=['actual-source sequential harness','observation-only second-buffer monitor','retained CPU fixtures with architectural and measured cycle comparison','full-core capacity/timing'])
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))

if __name__ == '__main__':
    main()
