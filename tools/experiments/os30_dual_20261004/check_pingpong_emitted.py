#!/usr/bin/env python3
"""Audit the emitted two-bank decode storage and its inductive refinement."""
from pathlib import Path
from itertools import product
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'raw-hold-load-v1/immediate/VexiiRiscv.v'
CAND = ART / 'decode-pingpong-v1/immediate'
OUT = ART / 'pingpong-emitted-audit-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    old = BASE.read_text()
    new = (CAND / 'VexiiRiscv.v').read_text()
    assert digest(BASE) == '1bce3882b721c09e4a5091cc3ef00360366df9b1b0b79ffb0b1938b820105569'
    assert digest(CAND / 'VexiiRiscv.v') == 'd471c5da61b3f5f67ce3c78eb0bf34d98cb828407ba0da15b63ac1044ed50f86'
    contracts = json.loads((CAND / 'source-contracts.json').read_text())
    fields = contracts['data_fields']
    assert len(fields) == 30
    reconstructed = old
    old_head = contracts['old_head_block']
    assert old.count(old_head) == 1
    head_writers = re.findall(r'^      (\w+) <= (\w+);$', old_head, re.M)
    assert head_writers == [(f['head'], f['output']) for f in fields]
    assert all(f['head'] == f['input'].replace('decode_ctrls_0_down_', 'decode_ctrls_1_up_', 1)
               and f['output'] == f['input'].replace('decode_ctrls_0_down_', 'decode_skid_gate_down_', 1)
               for f in fields)
    reconstructed = reconstructed.replace(old_head, '', 1)
    tail_writers = []
    for block in contracts['old_tail_blocks']:
        assert old.count(block) == 1
        assert block.startswith('    if(decode_ctrls_0_down_ready) begin\n')
        tail_writers += re.findall(r'^      (\w+) <= (\w+);$', block, re.M)
        reconstructed = reconstructed.replace(block, '', 1)
    assert tail_writers == [(f['saved'], f['input']) for f in fields]
    bit_cursor = 276
    for field in fields:
        widths = []
        for name in (field['head'], field['saved']):
            declarations = re.findall(r'^  reg\s+(?:\[(\d+):(\d+)\]\s+)?' + name + r';$', old, re.M)
            assert len(declarations) == 1
            hi, lo = declarations[0]
            widths.append(int(hi) - int(lo) + 1 if hi else 1)
            declaration = re.search(r'^  reg\s+(?:\[\d+:\d+\]\s+)?' + name + r';$', old, re.M)[0]
            reconstructed = reconstructed.replace(declaration, declaration.replace('reg', 'wire', 1), 1)
            assert len(re.findall(r'\b' + name + r'\s*<=', old)) == 1
            assert not re.search(r'\b' + name + r'\s*<=', new)
        assert widths == [field['bits'], field['bits']]
        bit_cursor -= field['bits']
        assert field['packed_lsb'] == bit_cursor
        assert field['packed_msb'] == bit_cursor + field['bits'] - 1
    assert bit_cursor == 0
    added = contracts['new_bank_block']
    assert new.count(added) == 1
    for key, name in [('input', 'decode_pingpong_input'), ('head', 'decode_pingpong_head'), ('saved', 'decode_pingpong_tail')]:
        packed = ','.join(f[key] for f in fields)
        text = f'  assign {name} = {{{packed}}};' if key == 'input' else f'  assign {{{packed}}} = {name};'
        assert added.count(text) == 1
    expected_logic = [
        'assign decode_pingpong_write_bank = decode_pingpong_read_bank ^ decode_ctrls_1_up_valid;',
        'assign decode_pingpong_write0 = !decode_skid_rValid && !decode_pingpong_write_bank;',
        'assign decode_pingpong_write1 = !decode_skid_rValid && decode_pingpong_write_bank;',
        'assign decode_pingpong_head = decode_pingpong_read_bank ? decode_pingpong_bank1 : decode_pingpong_bank0;',
        'assign decode_pingpong_tail = decode_pingpong_read_bank ? decode_pingpong_bank0 : decode_pingpong_bank1;',
        "    if(reset) begin\n      decode_pingpong_read_bank <= 1'b0;\n    end else if(decode_ctrls_1_up_valid && decode_skid_gate_down_isReady) begin\n      decode_pingpong_read_bank <= !decode_pingpong_read_bank;\n    end",
        '    if(decode_pingpong_write0) begin\n      decode_pingpong_bank0 <= decode_pingpong_input;\n    end',
        '    if(decode_pingpong_write1) begin\n      decode_pingpong_bank1 <= decode_pingpong_input;\n    end',
    ]
    for line in expected_logic:
        assert added.count(line) == 1
    assert len(re.findall(r'\bdecode_pingpong_\w+\s*<=', new)) == 4
    anchor = '  assign decode_ctrls_0_down_ready = (! decode_skid_rValid);\n'
    reconstructed = reconstructed.replace(anchor, added + anchor, 1)
    assert reconstructed == new  # No other RTL edits, including all masks/control/reset.
    for key in ('ready_block', 'metadata_block', 'occupancy_block'):
        assert old.count(contracts[key]) == new.count(contracts[key]) == 1
    # Every tail consumer is either its S-controlled bypass, or the explicitly
    # S-qualified held-trap aggregate. All other original source is identical.
    for f in fields:
        name = f['saved']
        references = re.findall(r'^.*\b' + name + r'\b.*$', old, re.M)
        assert len(references) == (4 if '_TRAP_' in name else 3)
        mux_name = f['output'].replace('decode_skid_gate_down_', 'decode_skid_gate_up_')
        expected_mux = f"    if(decode_skid_rValid) begin\n      {mux_name} = {name};\n    end else begin\n      {mux_name} = {f['input']};\n    end"
        assert expected_mux in old and expected_mux in new
    assert 'assign decode_hold_buffered_trap = decode_skid_rValid &&' in new
    # Independently exhaust one-bit transition refinement, using arbitrary
    # unused payload/banks and all input-valid/downstream-ready/flush values.
    checked = 0
    for h, s, p, u, d, flush, di, hd, td, b0, b1 in product(range(2), repeat=11):
        banks = [b0, b1]
        if s and not h or h and hd != banks[p] or s and td != banks[1-p]:
            continue
        ready = int(not h or d)
        hn = int((u or s) and not flush) if ready else h
        sn = int((s or u) and not ready and not flush)
        old_hn = (td if s else di) if ready else hd
        old_tn = di if not s else td
        candidate_banks = banks.copy()
        if not s:
            candidate_banks[p ^ h] = di
        pn = p ^ (h and ready)
        assert not sn or hn
        assert candidate_banks[pn] == old_hn
        assert not sn or candidate_banks[1-pn] == old_tn
        checked += 1
    assert checked == 896
    # Check the extracted proof literally includes the unchanged metadata and
    # that the full saved wrapper is the one compiled for the independent rerun.
    trans = (CAND / 'transition.sv').read_text()
    tb = (CAND / 'tb.sv').read_text()
    assert tb.startswith(trans)
    controls = contracts['metadata_block'] + contracts['occupancy_block']
    for name, target in {'decode_ctrls_1_up_valid': 'h_next', 'decode_skid_rValid': 's_next',
                         'decode_ctrls_1_up_LANE_SEL_0': 'hm0_next',
                         'decode_ctrls_1_up_LANE_SEL_1': 'hm1_next'}.items():
        controls = re.sub(r'\b' + name + r'\s*<=', target + ' =', controls)
    assert controls.replace('<=', '=') in trans
    OUT.mkdir(exist_ok=False)
    with (OUT / 'proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND / 'proof-obj/Vtb')], stdout=log, stderr=subprocess.STDOUT, check=True)
    assert 'PASS: 114688 exhaustive inductive transitions' in (OUT / 'proof-rerun.log').read_text()
    report = dict(reference_sha256=digest(BASE), candidate_sha256=digest(CAND / 'VexiiRiscv.v'),
                  actual_fields=30, actual_bank_bits=276, preserved_mask_ffs=4,
                  added_pointer_ffs=1, exact_scope_reconstruction=True,
                  unchanged_ready_occupancy_masks_flush_reset=True,
                  all_field_widths_and_pack_order_verified=True,
                  independent_one_bit_inductive_cases=checked,
                  extracted_metadata_proof_rerun_cases=114688,
                  head_payload_exact_after_nonreset_edge=True,
                  tail_payload_only_observed_when_occupied=True,
                  qualification='Source/refinement review only; whole-CPU regression and timing qualification are separate.',
                  proof_sha256={name: digest(CAND / name) for name in ['transition.sv', 'tb.sv', 'source-contracts.json']})
    (OUT / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
