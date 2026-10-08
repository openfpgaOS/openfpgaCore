#!/usr/bin/env python3
"""Private normal-generator proposal for the qualified registered RVC CPU."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
O = P / 'cpu-cache64-rvcpipe-repro-v1'
BASE = P / 'cpu-cache64-timing-repro-v2'
C = P / 'cpu-cache64-rvcpipe-v1'
Q = P / 'cpu-queues-v1'
V = Path('src/fpga/vendor/vexriscv')
EXPECTED = 'aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2); f.write('\n')


def replace(text, before, after):
    assert text.count(before) == 1, (before[:80], text.count(before))
    return text.replace(before, after)


def prepare():
    assert sha(C / 'VexiiRiscv.v') == EXPECTED
    for stage in [BASE, C]:
        manifest = json.loads((stage / 'manifest.json').read_text())
        for rel, digest in manifest.get('files', {}).items():
            assert sha(stage / rel) == digest, rel
    O.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'proposed', O / 'proposed')
    shutil.copytree(BASE / 'controls', O / 'controls')
    shutil.copy2(C / 'raw-emitted.v', O / 'controls/raw-emitted.v')
    vendor = O / 'proposed' / V
    patch = vendor / 'os30-rvc-pipeline.patch'
    shutil.copy2(C / 'generator.patch', patch)
    audit = json.loads((C / 'helper-audit/compatibility.json').read_text())
    assert audit['passed'] and audit['candidate_sha256'] == EXPECTED
    chosen = [row for row in audit['qualified_literal_hunks'] if row['selected_for_preservation']]
    assert [row['old_token'] for row in chosen] == ['OLD', 'OLD2', 'INTWBOLD', 'RS2OLD']
    fp = vendor / 'factor_os30_fp_rs3.pl'
    text = fp.read_text()
    text = re.sub(r"my \$before='[0-9a-f]{64}';", "my $before='" + chosen[0]['input_sha256'] + "';", text, count=1)
    text = re.sub(r"my \$after='[0-9a-f]{64}';", "my $after='" + chosen[0]['output_sha256'] + "';", text, count=1)
    fp.write_text(text)
    frontend = vendor / 'factor_os30_frontend.pl'
    old = frontend.read_text()
    head = old[:old.index('{\n my $old=')]
    head = re.sub(r"my \$before='[0-9a-f]{64}';", "my $before='" + chosen[1]['input_sha256'] + "';", head, count=1)
    head = re.sub(r"my \$after='[0-9a-f]{64}';", "my $after='" + EXPECTED + "';", head, count=1)
    head = head.replace('# os30-only zero-cycle frontend factoring.', '# os30-only literal scanner/INTWB/RS2 factoring for the registered RVC profile.')
    blocks = re.findall(r"my \$old=<<'([^']+)';\n(.*?)\n\1\n\s*my \$new=<<'([^']+)';\n(.*?)\n\3\n", old, re.S)
    retained = []
    for ot, before, nt, after in blocks:
        if ot not in ['OLD2', 'INTWBOLD', 'RS2OLD']:
            continue
        retained.append(ot)
        head += "{\n my $old=<<'" + ot + "';\n" + before + '\n' + ot + "\n my $new=<<'" + nt + "';\n" + after + '\n' + nt + '''
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified registered-profile block\n";
 $source=~s/\Q$old\E/$new/;
}
'''
    assert retained == ['OLD2', 'INTWBOLD', 'RS2OLD']
    footer = old[old.index('sha256_hex($source) eq $after or die "Unexpected transformed frontend hash'):]
    frontend.write_text(head + footer)
    assert not any(x in frontend.read_text() for x in ['rvc_instruction_bit', 'dispatch_ready_early_can', 'decode_ctrls_2_up_LANE_SEL_0'])
    config = vendor / 'configs/os30.cfg'
    text = config.read_text()
    text = replace(text, 'if [ "$VARIANT" = os30 ]; then\n', '''if [ "$VARIANT" = os30 ]; then
# Registered RAW -> RVC -> ISA decode, preserving single-issue semantics.
# This source patch is applied only inside a private elaboration copy.
OS30_RVC_PIPELINE=1
DISPATCHER_AT=3
EXTRA_FLAGS="$EXTRA_FLAGS --rvc-decompress-at=1 --decoder-at=2"
''')
    config.write_text(text)
    generator = vendor / 'generate_vexii.sh'
    text = generator.read_text()
    text = replace(text, 'EXTRA_FLAGS=""\n', '''EXTRA_FLAGS=""
DISPATCHER_AT=2         # Preserve every existing variant unless explicitly selected.
OS30_RVC_PIPELINE=0     # os30-only isolated Scala source overlay.
''')
    text = replace(text, '--dispatcher-at=2 \\\n', '--dispatcher-at=$DISPATCHER_AT \\\n')
    prep = json.loads((C / 'prepared.json').read_text())
    scala = ['src/main/scala/vexiiriscv/Param.scala', 'src/main/scala/vexiiriscv/decode/AlignerPlugin.scala']
    source_before = '\n'.join(prep['original_scala'][n] + '  ' + n for n in scala)
    source_after = '\n'.join(prep['candidate_scala'][n] + '  ' + n for n in scala)
    isolated = '''# The optional RVC pipeline must never patch the shared generator tree:
# other variants (including os30_worker) can elaborate concurrently. A killed
# process can leave only its private temporary copy, never changed source.
GENERATION_DIR="$VEXII_DIR"
if [ "$OS30_RVC_PIPELINE" = 1 ]; then
    [ "$VARIANT" = os30 ] || { echo "RVC pipeline is qualified only for os30" >&2; exit 1; }
    GENERATION_DIR=$(mktemp -d "$SCRIPT_DIR/.os30-rvc.XXXXXXXX")
    cleanup_os30_source() { rm -rf -- "$GENERATION_DIR"; }
    trap cleanup_os30_source EXIT
    # Preserve all build inputs, but never reuse compiled classes or outputs.
    (set -o pipefail; tar --exclude=.git --exclude=target --exclude='VexiiRiscv*.v' \\
        --exclude=vexii_active.v -cf - . | tar -xf - -C "$GENERATION_DIR")
    cd "$GENERATION_DIR"
    sha256sum -c <<'OS30_SOURCE_BEFORE'
''' + source_before + '''
OS30_SOURCE_BEFORE
    git apply --check "$SCRIPT_DIR/os30-rvc-pipeline.patch"
    git apply "$SCRIPT_DIR/os30-rvc-pipeline.patch"
    sha256sum -c <<'OS30_SOURCE_AFTER'
''' + source_after + '''
OS30_SOURCE_AFTER
fi

'''
    text = replace(text, '# sbt always emits the bare name VexiiRiscv.v here;', isolated + '# sbt always emits the bare name VexiiRiscv.v here;')
    text = replace(text, 'rm -f "$VEXII_DIR/VexiiRiscv.v"', 'rm -f "$GENERATION_DIR/VexiiRiscv.v"')
    text = replace(text, 'GENERATED="$VEXII_DIR/VexiiRiscv.v"', 'GENERATED="$GENERATION_DIR/VexiiRiscv.v"')
    generator.write_text(text)
    makefile = O / 'proposed/src/fpga/targets/pocket/Makefile'
    makefile.write_text(replace(makefile.read_text(),
        '$(VEXII_DIR)/../factor_os30_fp_rs3.pl $(VEXII_DIR)/../factor_os30_frontend.pl)',
        '$(VEXII_DIR)/../factor_os30_fp_rs3.pl $(VEXII_DIR)/../factor_os30_frontend.pl $(VEXII_DIR)/../os30-rvc-pipeline.patch)'))
    # Keep test fixtures aware of the new os30-only source prerequisite.
    test = O / 'proposed/tools/tests/test_pocket_cpu_freshness.py'
    text = test.read_text()
    anchor = '"factor_os30_frontend.pl",'
    if anchor in text:
        text = replace(text, anchor, anchor + '\n        "os30-rvc-pipeline.patch",')
    else:
        anchor = "'factor_os30_frontend.pl',"
        assert anchor in text
        text = replace(text, anchor, anchor + "\n        'os30-rvc-pipeline.patch',")
    test.write_text(text)
    patch_text = ''
    for path in sorted(p for p in (O / 'proposed').rglob('*') if p.is_file()):
        rel = path.relative_to(O / 'proposed')
        before = (R / rel).read_text() if (R / rel).exists() else ''
        patch_text += ''.join(difflib.unified_diff(before.splitlines(True), path.read_text().splitlines(True),
                         fromfile='a/' + str(rel) if before else '/dev/null', tofile='b/' + str(rel)))
    (O / 'candidate.patch').write_text(patch_text)
    save(O / 'prepared.json', dict(candidate_cpu_sha256=EXPECTED,
         candidate_manifest_sha256=sha(C / 'manifest.json'), parent_proposal_sha256=sha(BASE / 'manifest.json'),
         retained_hunks=chosen, exact_flags=prep['flags'], source_before={n: prep['original_scala'][n] for n in scala},
         source_after={n: prep['candidate_scala'][n] for n in scala}, isolated_os30_source_copy=True,
         original_source_tree_never_modified=True, old_rvc_and_dispatch2_hunks_omitted=True,
         production_guard=json.loads((BASE / 'prepared.json').read_text())['source_hashes'],
         proposed_sha256={str(p.relative_to(O / 'proposed')): sha(p) for p in (O / 'proposed').rglob('*') if p.is_file()},
         actual_regeneration_pending=True, recipe_sha256=sha(Path(__file__))))
    shutil.copy2(__file__, O / 'recipe.py')
    print('PREPARED', O, sha(O / 'prepared.json'))


def check():
    prep = json.loads((O / 'prepared.json').read_text())
    for rel, digest in prep['proposed_sha256'].items(): assert sha(O / 'proposed' / rel) == digest
    d = O / 'checks'; d.mkdir(exist_ok=False)
    vendor = d / 'vendor'; vendor.mkdir()
    for path in (R / V).glob('*.pl'): shutil.copy2(path, vendor / path.name)
    shutil.copytree(O / 'controls/configs', vendor / 'configs')
    for name in ['generate_vexii.sh', 'factor_os30_fp_rs3.pl', 'factor_os30_frontend.pl', 'os30-rvc-pipeline.patch', 'configs/os30.cfg']:
        shutil.copy2(O / 'proposed' / V / name, vendor / name)
    src = vendor / 'VexiiRiscv'; src.mkdir()
    for rel, digest in prep['source_before'].items():
        path = src / rel; path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Q / 'source' / rel, path); assert sha(path) == digest
    stub = d / 'bin'; stub.mkdir()
    (stub / 'sbt').write_text('''#!/bin/bash
printf '%s\\n' "$@" > "$REPLAY_ARGS"
pwd > "$REPLAY_CWD"
if [ "${REPLAY_FAIL:-0}" = 1 ]; then exit 77; fi
cp "$REPLAY_RAW" VexiiRiscv.v
''')
    (stub / 'sbt').chmod(0o755)
    env = dict(os.environ, PATH=str(stub) + os.pathsep + os.environ['PATH'],
               REPLAY_ARGS=str(d / 'args.txt'), REPLAY_CWD=str(d / 'cwd.txt'), REPLAY_RAW=str(O / 'controls/raw-emitted.v'))
    def run(label, variant='os30', fail=False):
        e = dict(env, REPLAY_FAIL='1' if fail else '0')
        r = subprocess.run(['bash', str(vendor / 'generate_vexii.sh'), variant], env=e, text=True, capture_output=True)
        (d / (label + '.log')).write_text(r.stdout + r.stderr)
        assert not list(vendor.glob('.os30-rvc.*')), 'private checkout cleanup'
        for rel, digest in prep['source_before'].items(): assert sha(src / rel) == digest, 'shared source changed'
        return r
    result = run('captured-raw-flow')
    assert result.returncode == 0, result.stderr
    output = src / 'VexiiRiscv_os30.v'; assert sha(output) == EXPECTED
    flags = next(x for x in (d / 'args.txt').read_text().splitlines() if x.startswith('Test/runMain ')).split()[2:]
    assert flags == prep['exact_flags']
    assert '/.os30-rvc.' in (d / 'cwd.txt').read_text()
    shutil.copy2(output, d / 'exact-replayed.v')
    # Emitter failure and final hash failure never replace prior good output.
    output.write_text('prior CPU\n')
    assert run('emitter-failure', fail=True).returncode == 77 and output.read_text() == 'prior CPU\n'
    bad = d / 'bad-raw.v'; bad.write_bytes((O / 'controls/raw-emitted.v').read_bytes() + b'\n// unqualified\n')
    env['REPLAY_RAW'] = str(bad)
    assert run('helper-failure').returncode != 0 and output.read_text() == 'prior CPU\n'
    # Flags and shared-source selection remain exact for every other config.
    original = d / 'original'; original.mkdir()
    shutil.copy2(O / 'controls/generate_vexii.sh', original / 'generate_vexii.sh')
    shutil.copytree(O / 'controls/configs', original / 'configs'); (original / 'VexiiRiscv').mkdir()
    variants = {}
    for cfg in sorted((original / 'configs').glob('*.cfg')):
        variant = cfg.stem
        if variant == 'os30': continue
        before_args = d / 'old.args'
        r = subprocess.run(['bash', str(original / 'generate_vexii.sh'), variant],
             env=dict(env, REPLAY_FAIL='1', REPLAY_ARGS=str(before_args)), capture_output=True, text=True)
        assert r.returncode == 77
        assert run(variant + '-capture', variant, True).returncode == 77
        assert before_args.read_bytes() == (d / 'args.txt').read_bytes(), variant
        assert (d / 'cwd.txt').read_text().strip() == str(src)
        variants[variant] = dict(flags_unchanged=True, shared_unmodified_source=True)
    # Full helper guards, including idempotence and variant rejection.
    for name, input_path, expected in [
            ('factor_os30_fp_rs3.pl', C / 'helper-audit/05-normalized.v', prep['retained_hunks'][0]['output_sha256']),
            ('factor_os30_frontend.pl', None, EXPECTED)]:
        probe = d / (name + '.v')
        if input_path: shutil.copy2(input_path, probe)
        else: shutil.copy2(d / 'factor_os30_fp_rs3.pl.v', probe)
        for _ in range(2):
            r = subprocess.run(['perl', str(vendor / name), 'os30', str(probe)], capture_output=True, text=True)
            assert r.returncode == 0 and sha(probe) == expected, r.stderr
        before = sha(probe)
        r = subprocess.run(['perl', str(vendor / name), 'os25', str(probe)], capture_output=True, text=True)
        assert r.returncode != 0 and sha(probe) == before
    save(d / 'result.json', dict(passed=True, flags_exact=True, replay_cpu_sha256=EXPECTED,
         source_isolation_success_and_failures=True, atomic_publication_preserved=True,
         variant_arguments=variants, helper_hash_guards=True, actual_scala_executed=False))
    print('CHECK PASS')


def seal():
    prep = json.loads((O / 'prepared.json').read_text())
    assert json.loads((O / 'checks/result.json').read_text())['passed']
    for rel, digest in prep['proposed_sha256'].items(): assert sha(O / 'proposed' / rel) == digest
    for rel, digest in prep['production_guard'].items(): assert sha(R / rel) == digest
    names = [p for p in O.rglob('*') if p.is_file() and '/checks/vendor/' not in str(p) and '/checks/original/' not in str(p)]
    save(O / 'manifest.json', dict(passed=True, candidate_cpu_sha256=EXPECTED,
         files={str(p.relative_to(O)): sha(p) for p in names}, actual_regeneration_pending=True,
         production_unchanged=True, no_fit=True))
    print('SEALED', sha(O / 'manifest.json'))


if __name__ == '__main__': globals()[sys.argv[1]]()
