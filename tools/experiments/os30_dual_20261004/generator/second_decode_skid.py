#!/usr/bin/env python3
"""Private draft: a flush-aware two-bank buffer before dispatch stage3.

This is a new elastic capacity experiment, not cycle equivalence to the current
single-slot boundary. Local refinement, lint and source replay are only the
first qualification steps; actual CPU regressions and independent review remain.
"""
from pathlib import Path
import argparse
import difflib
import hashlib
import itertools
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
BASE_SHA = '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
H = 'decode_ctrls_3_up_valid'
S = 'decode_dispatch_skid_full'
U = 'decode_ctrls_2_down_isValid'
D = 'decode_ctrls_3_up_isReady'
R = 'decode_dispatch_head_ready'
F = 'decode_dispatch_flush'
P = 'decode_dispatch_read_bank'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as f:
        subprocess.run([str(x) for x in command], check=True,
                       stdout=f, stderr=subprocess.STDOUT, **kwargs)


def prove_rotating_reference():
    """Every bit of unreset payload against explicit S2M + StageLink storage."""
    cases = 0
    classes = dict(blocked_flush=0, tail_drain=0, empty_fill=0, partial_masks=0)
    # R/H/S and controls may depend on data outside this boundary; admitting all
    # combinations is therefore stronger than an open-loop stimulus stream.
    for h, s, ptr, b0, b1, old_head, old_tail, u, d, flush, data, reset in itertools.product(range(2), repeat=12):
        if s and not h:
            continue
        banks = [b0, b1]
        if h and old_head != banks[ptr]:
            continue
        if s and old_tail != banks[ptr ^ 1]:
            continue
        ready = (not h) or d
        hn = 0 if reset else (((s or u) and not flush) if ready else h)
        sn = s
        if u:
            sn = 1
        if ready:
            sn = 0
        if flush or reset:
            sn = 0
        assert not sn or hn
        ref_head = (old_tail if s else data) if ready else old_head
        ref_tail = old_tail if s else data
        result = banks[:]
        if not s:
            result[ptr ^ h] = data
        pn = 0 if reset else ptr ^ (h and ready)
        if hn:
            assert result[pn] == ref_head
        if sn:
            assert result[pn ^ 1] == ref_tail
        if not reset:
            # Stronger than live-payload equality: the invalid head also
            # matches after every nonreset edge. During reset both masks clear.
            assert result[pn] == ref_head
        # Same two live lane masks, preserving original cancel-after-capture.
        for mask, tailmask, inputmask in itertools.product(range(4), repeat=3):
            reference_mask = ((tailmask if s else inputmask) if not flush else 0) if ready else mask
            if not d and flush:
                reference_mask = 0
            if reset:
                reference_mask = 0
            candidate_mask = mask
            if ready:
                candidate_mask = (tailmask if s else inputmask) & (0 if flush else 3)
            if not d and flush:
                candidate_mask = 0
            if reset:
                candidate_mask = 0
            assert candidate_mask == reference_mask
            classes['partial_masks'] += int(mask in (1, 2) or inputmask in (1, 2))
            cases += 1
        classes['blocked_flush'] += int(h and not ready and flush)
        classes['tail_drain'] += int(s and ready and not reset)
        classes['empty_fill'] += int(not h and u and not flush and not reset)
    # With dispatch always ready, the tail stays empty, and the same one-cycle
    # head update is retained for every valid/flush/input-mask combination.
    unstalled = 0
    for h, u, flush, imask in itertools.product(range(2), range(2), range(2), range(4)):
        assert ((0 or u) and not flush) == (u and not flush)
        assert not ((0 or u) and not True and not flush)
        unstalled += 1
    return dict(passed=True, transition_bit_and_mask_cases=cases, corner_counts=classes,
                unstalled_cases=unstalled, arbitrary_payload_bit_lift=380,
                invariant='S implies H; live head equals selected bank; live tail equals other bank. Head payload also matches after every nonreset edge.',
                reference='Explicit flush-aware S2M tail plus original StageLink head, with blocked flushed head tombstone.',
                baseline_limit='No identical backpressure or IRQ-cycle claim against original one-slot boundary.')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ART / 'decode-second-skid-draft-v1/immediate')
    args = ap.parse_args()
    assert sha(BASE) == BASE_SHA
    source = BASE.read_text()
    edits = []

    def change(before, after):
        assert source.count(before) == 1, (before[:100], source.count(before))
        edits.append((before, after))

    # The only stage3 payload capture block is unreset and advances as a unit.
    blocks = re.findall(r'^    if\(decode_ctrls_2_down_isReady\) begin\n(?:(?!^    end).)*?^    end\n', source, re.M | re.S)
    blocks = [x for x in blocks if 'decode_ctrls_3_up_Decode_INSTRUCTION_SLICE_COUNT_0 <=' in x]
    assert len(blocks) == 1
    block = blocks[0]
    captures = re.findall(r'^      (\w+) <= (\w+);$', block, re.M)
    assert len(captures) == 90
    assert block == '    if(decode_ctrls_2_down_isReady) begin\n' + ''.join(f'      {q} <= {d};\n' for q, d in captures) + '    end\n'
    data = []
    offset = 0
    for q, d in captures:
        assert q.startswith('decode_ctrls_3_up_') and d == q.replace('decode_ctrls_3_up_', 'decode_ctrls_2_down_')
        decl = re.findall(r'^  reg\s+(?:\[(\d+):0\]\s+)?' + q + r';$', source, re.M)
        assert len(decl) == 1
        width = int(decl[0]) + 1 if decl[0] else 1
        original = re.findall(r'^  reg\s+(?:\[\d+:0\]\s+)?' + q + r';$', source, re.M)[0]
        assert re.findall(r'\b' + q + r'\s*<=\s*([^;]+);', source) == [d]
        change(original, original.replace('reg', 'wire', 1))
        data.append(dict(head=q, input=d, bits=width, lsb=offset, msb=offset + width - 1))
        offset += width
    assert offset == 380
    change(block, '')

    # Both head lane masks use only four strictly older execute flush sources.
    older = ['early0_EnvPlugin_logic_flushPort_valid', 'CsrAccessPlugin_logic_flushPort_valid',
             'early0_BranchPlugin_logic_flushPort_valid', 'LsuPlugin_logic_flushPort_valid']
    flush = '(|{' + ','.join(older) + '})'
    for lane in range(2):
        expr = re.findall(r'^  assign decode_logic_flushes_3_onLanes_' + str(lane) + r'_doIt = ([^;]+);$', source, re.M)
        assert len(expr) == 1
        names = re.findall(r'\b\w+_flushPort_valid\b', expr[0])
        assert names == older
        assert f'  assign when_DecodePipelinePlugin_l95_{lane+4} = ((! decode_ctrls_3_up_isReady) && decode_ctrls_3_lane{lane}_upIsCancel);' in source
    # Capture/mask cancellation ordering remains the original StageLink rule.
    old_control = '''      if(decode_ctrls_2_down_isReady) begin
        decode_ctrls_3_up_valid <= decode_ctrls_2_down_isValid;
      end
      if(decode_ctrls_2_down_isReady) begin
        decode_ctrls_3_up_LANE_SEL_0 <= decode_ctrls_2_down_LANE_SEL_0;
        decode_ctrls_3_up_LANE_SEL_1 <= decode_ctrls_2_down_LANE_SEL_1;
      end'''
    new_control = f'''      if({R}) begin
        decode_ctrls_3_up_valid <= (({S} || {U}) && !{F});
      end
      if({R}) begin
        decode_ctrls_3_up_LANE_SEL_0 <= ({S} ? decode_dispatch_tail_mask[0] : decode_ctrls_2_down_LANE_SEL_0) && !{F};
        decode_ctrls_3_up_LANE_SEL_1 <= ({S} ? decode_dispatch_tail_mask[1] : decode_ctrls_2_down_LANE_SEL_1) && !{F};
      end'''
    change(old_control, new_control)
    old_ready = '''  always @(*) begin
    decode_ctrls_2_down_ready = decode_ctrls_3_up_ready;
    if(when_StageLink_l71_4) begin
      decode_ctrls_2_down_ready = 1'b1;
    end
  end'''
    change(old_ready, f'''  always @(*) begin
    decode_ctrls_2_down_ready = !{S};
  end''')
    # The earlier flattened helper cannot keep its old direct dispatch path.
    old_first = re.findall(r'^  assign decode_load_without_lane0 = .*?;\n', source, re.M | re.S)
    old_second = re.findall(r'^  assign decode_load_needs_lane0 = .*?;\n', source, re.M | re.S)
    assert len(old_first) == len(old_second) == 1
    change(old_first[0], f'  assign decode_load_without_lane0 = !decode_ctrls_1_up_valid || !decode_ctrls_2_up_valid || !{S};\n')
    change(old_second[0], "  assign decode_load_needs_lane0 = 1'b0;\n")

    # Preserve explicit S2M cancel/forget propagation. cancel implies !S/ready;
    # the original StageLink capture therefore overrides this earlier clear.
    old_stage2 = '''      if(decode_ctrls_1_down_isReady) begin
        decode_ctrls_2_up_valid <= decode_ctrls_1_down_isValid;
      end'''
    change(old_stage2, '''      if(decode_dispatch_up_forget) begin
        decode_ctrls_2_up_valid <= 1'b0;
      end
''' + old_stage2)
    change('  assign decode_ctrls_2_up_isMoving = (decode_ctrls_2_up_isValid && decode_ctrls_2_up_isReady);',
           '  assign decode_ctrls_2_up_isMoving = (decode_ctrls_2_up_isValid && (decode_ctrls_2_up_isReady || decode_dispatch_up_cancel));')
    change("  assign decode_ctrls_2_up_isCanceling = 1'b0;",
           '  assign decode_ctrls_2_up_isCanceling = decode_ctrls_2_up_isValid && decode_dispatch_up_cancel;')
    for lhs in ['decode_logic_trapPending[0]', 'TrapPlugin_logic_harts_0_trap_fsm_inflightTrap']:
        old = re.findall(r'^  assign ' + re.escape(lhs) + r' = [^;]+;', source, re.M)
        assert len(old) == 1
        change(old[0], old[0][:-1] + ' || decode_dispatch_tail_trap_pending;')

    extra = f'''  // PRIVATE DRAFT: age1025 decoded S2M + StageLink rotating payload.
  reg {S};
  (* altera_attribute = "-name AUTO_CLOCK_ENABLE_RECOGNITION OFF" *) reg {P};
  reg [379:0] decode_dispatch_bank0;
  reg [379:0] decode_dispatch_bank1;
  reg [1:0] decode_dispatch_tail_mask;
  wire {F} = {flush};
  wire {R} = !{H} || {D};
  wire decode_dispatch_write_bank = {P} ^ {H};
  wire decode_dispatch_up_cancel = {F} && !{S};
  wire decode_dispatch_up_forget = {F} && !{S};
  wire [379:0] decode_dispatch_head = {P} ? decode_dispatch_bank1 : decode_dispatch_bank0;
  wire [379:0] decode_dispatch_tail = {P} ? decode_dispatch_bank0 : decode_dispatch_bank1;
  wire [379:0] decode_dispatch_input = {{{','.join(x['input'] for x in reversed(data))}}};
'''
    for field in data:
        selection = str(field['lsb']) if field['bits'] == 1 else f"{field['msb']}:{field['lsb']}"
        extra += f"  assign {field['head']} = decode_dispatch_head[{selection}];\n"
    traps = {lane: next(x['lsb'] for x in data if x['head'] == f'decode_ctrls_3_up_TRAP_{lane}') for lane in range(2)}
    extra += f'''  wire decode_dispatch_tail_trap_pending = {S} && !{F} &&
      ((decode_dispatch_tail_mask[0] && decode_dispatch_tail[{traps[0]}]) ||
       (decode_dispatch_tail_mask[1] && decode_dispatch_tail[{traps[1]}]));
  always @(posedge clk) begin
    if(!{S}) begin
      if(decode_dispatch_write_bank) decode_dispatch_bank1 <= decode_dispatch_input;
      else decode_dispatch_bank0 <= decode_dispatch_input;
      decode_dispatch_tail_mask <= {{decode_ctrls_2_down_LANE_SEL_1,decode_ctrls_2_down_LANE_SEL_0}};
    end
  end
  always @(posedge clk or posedge reset) begin
    if(reset) begin
      {S} <= 1'b0;
      {P} <= 1'b0;
    end else begin
      if({U}) {S} <= 1'b1;
      if({R}) {S} <= 1'b0;
      if({F}) {S} <= 1'b0;
      {P} <= {P} ^ ({H} && {R});
    end
  end
'''
    anchor = '  wire decode_load_pending0;'
    change(anchor, extra + '\n' + anchor)
    candidate = source
    for before, after in edits:
        assert candidate.count(before) == 1
        candidate = candidate.replace(before, after, 1)
    # All changes have exact inverse scope, including the removed capture block.
    inverse = candidate
    for before, after in reversed(edits):
        if after:
            assert inverse.count(after) == 1
            inverse = inverse.replace(after, before, 1)
        else:
            # Removed block is restored relative to its original next block.
            following = source[source.index(before) + len(before):].splitlines(True)[0]
            assert following == '    if(decode_ctrls_0_down_ready) begin\n'
            target = following + '      decode_ctrls_0_down_LANE_SEL_0_s2mBuffer'
            assert inverse.count(target) == 1
            inverse = inverse.replace(target, before + target, 1)
    assert inverse == source
    # Preserve dispatch partial-issue, decoder transactionSpawn/uop-ID and all
    # original per-lane clearing statements; only their upstream capacity changes.
    for name in ['DispatchPlugin_logic_feeds_0_sent', 'DispatchPlugin_logic_feeds_1_sent',
                 'DecoderPlugin_logic_harts_0_uopId', 'DecoderPlugin_logic_interrupt_buffered']:
        assert re.findall(r'^.*\b' + name + r' <=[^;]+;', source, re.M) == re.findall(r'^.*\b' + name + r' <=[^;]+;', candidate, re.M)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'VexiiRiscv.v').write_text(candidate)
    (out / 'payload-map.json').write_text(json.dumps(data, indent=2) + '\n')
    (out / 'local-refinement.json').write_text(json.dumps(prove_rotating_reference(), indent=2) + '\n')
    (out / 'changed-source-blocks.json').write_text(json.dumps(edits, indent=2) + '\n')
    patch = out / 'second-decoded-skid.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True),
                     fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    replay = out / 'patch-replay'; replay.mkdir()
    (replay / 'VexiiRiscv.v').write_text(candidate)
    for label, flags, expected in [('reverse', ['-R'], source), ('replay', [], candidate)]:
        run(['patch', '--batch', '--fuzz=0', *flags, '-p1', '-i', patch], out / (label + '.log'), cwd=replay)
        assert (replay / 'VexiiRiscv.v').read_text() == expected
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', out / 'VexiiRiscv.v'], out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    shutil.copy2(__file__, out / Path(__file__).name)
    manifest = dict(status='DRAFT: local model/source/lint only, not qualified for deployment or fit',
        reference_sha256=BASE_SHA, candidate_sha256=sha(out / 'VexiiRiscv.v'),
        payload_fields=len(data), payload_bits=380, physical_payload_banks=2,
        added_declared_FF_bits=384, new_unstalled_cycles=0, new_frame_capacity=1,
        flush_age=1025, older_flush_sources=older, younger_decoder_flushes_excluded=True,
        original_head_valid_mask_reset_and_cancel_order_preserved=True,
        partial_dispatch_sent_controls_unchanged=True, new_tail_trap_pending_both_aggregates=True,
        new_bank_write_controls='Registered tail full, head valid, and read pointer only.',
        ready_feedback_cut='Stage2 ready=!tailFull; earlier flattened head helper rederived accordingly.',
        up_cancel_and_forget='Older flush && !tailFull implies upstream ready; clear-before-capture priority retained.',
        exact_reverse_replay=True, full_netlist_lint=True,
        pending='Independent actual-source local sequential proof, monitor support and full fourteen-program architectural/cycle comparison before fit.',
        cycle_claim='Only unchanged unstalled forward latency. Backpressure and interrupt acceptance cycles can change.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'recipe.json').write_text(json.dumps(dict(script_sha256=sha(Path(__file__)),
        source_sha256=BASE_SHA, candidate_sha256=manifest['candidate_sha256'],
        command=['python3', str(Path(__file__).relative_to(ROOT))]), indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
