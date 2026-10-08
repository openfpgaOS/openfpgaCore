#!/usr/bin/env python3
"""Private checked generator proposal for the composed zero-cycle CPU factors."""
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
O = P / 'cpu-control-compose-repro-v1'
BASE = P / 'cpu-cache64-load-forward-repro-v1'
F = P / 'cpu-control-compose-v1'
C = P / 'cpu-cache64-rvcpipe-v1'
Q = P / 'cpu-queues-v1'
V = Path('src/fpga/vendor/vexriscv')
FRONTEND = 'aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
PARENT = '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
EXPECTED = '878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2); f.write('\n')


def replace(text, before, after):
    assert text.count(before) == 1, (before[:80], text.count(before))
    return text.replace(before, after)


def prepare():
    assert not (R / V / 'factor_os30_control_paths.pl').exists()
    assert sha(F / 'VexiiRiscv.v') == EXPECTED
    for rel, digest in json.loads((BASE / 'manifest.json').read_text())['files'].items():
        assert sha(BASE / rel) == digest, rel
    for rel, digest in json.loads((F / 'manifest.json').read_text())['evidence_sha256'].items():
        assert sha(R / rel) == digest, rel
    prep = json.loads((BASE / 'prepared.json').read_text())
    O.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'proposed', O / 'proposed')
    shutil.copytree(BASE / 'controls', O / 'controls')
    vendor = O / 'proposed' / V
    old_helper = (vendor / 'factor_os30_load_forward.pl').read_text()
    header = old_helper[:old_helper.index('{\n my $old=')]
    header = header.replace('factor_os30_load_forward.pl', 'factor_os30_control_paths.pl')
    header = header.replace('exact integer late-load forwarding reassociation. Apply AFTER factor_os30_frontend.pl.',
        'exact branch/LSU-sign/RS3-tag/decode-ready factors. Apply AFTER factor_os30_load_forward.pl.')
    header = re.sub(r"my \$before='[0-9a-f]{64}';", "my $before='" + PARENT + "';", header, count=1)
    header = re.sub(r"my \$after='[0-9a-f]{64}';", "my $after='" + EXPECTED + "';", header, count=1)
    header = header.replace('Unqualified os30 load-forward input', 'Unqualified os30 control-path input')
    hunks = json.loads((F / 'hunks.json').read_text())
    assert len(hunks) == 5 and len({h['component'] for h in hunks}) == 4
    # Bind the same literal composition in both directions before generating its recipe.
    composed = (P / 'cpu-load-forward-v1/VexiiRiscv.v').read_text()
    assert hashlib.sha256(composed.encode()).hexdigest() == PARENT
    text = header
    for i, hunk in enumerate(hunks):
        before, after = hunk['old'], hunk['new']
        assert before.endswith('\n') and after.endswith('\n')
        composed = replace(composed, before, after)
        tag = 'HUNK' + str(i)
        text += "{\n my $old=<<'" + tag + "OLD';\n" + before + tag + "OLD\n"
        text += " my $new=<<'" + tag + "NEW';\n" + after + tag + "NEW\n"
        text += r'''
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
'''
    assert composed == (F / 'VexiiRiscv.v').read_text()
    for hunk in reversed(hunks): composed = replace(composed, hunk['new'], hunk['old'])
    assert hashlib.sha256(composed.encode()).hexdigest() == PARENT
    footer = old_helper[old_helper.index('sha256_hex($source) eq $after or die "Unexpected transformed load-forward hash'):]
    text += footer.replace('load-forward', 'control-path')
    helper = vendor / 'factor_os30_control_paths.pl'
    helper.write_text(text); helper.chmod(0o755)
    generator = vendor / 'generate_vexii.sh'
    text = generator.read_text()
    text = replace(text, '\nmv -f "$OUTPUT" "$FINAL_OUTPUT"\n', '''
# Exact os30 registered-RVC output only; other variants retain their chain.
if [ "$OS30_RVC_PIPELINE" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_control_paths.pl" "$VARIANT" "$OUTPUT"
fi

mv -f "$OUTPUT" "$FINAL_OUTPUT"
''')
    generator.write_text(text)
    makefile = O / 'proposed/src/fpga/targets/pocket/Makefile'
    makefile.write_text(replace(makefile.read_text(),
        '$(VEXII_DIR)/../factor_os30_load_forward.pl)',
        '$(VEXII_DIR)/../factor_os30_load_forward.pl $(VEXII_DIR)/../factor_os30_control_paths.pl)'))
    test = O / 'proposed/tools/tests/test_pocket_cpu_freshness.py'
    test.write_text(replace(test.read_text(), "'factor_os30_load_forward.pl',", "'factor_os30_load_forward.pl', 'factor_os30_control_paths.pl',"))
    test = O / 'proposed/tools/tests/test_vexii_publish.py'
    test.write_text(replace(test.read_text(), "'factor_os30_load_forward.pl']:", "'factor_os30_load_forward.pl', 'factor_os30_control_paths.pl']:"))
    # Existing helpers, source isolation, configuration and flag order stay exact.
    unchanged = ['factor_os30_fp_rs3.pl', 'factor_os30_frontend.pl', 'factor_os30_load_forward.pl', 'os30-rvc-pipeline.patch', 'configs/os30.cfg']
    for name in unchanged: assert sha(vendor / name) == sha(BASE / 'proposed' / V / name)
    patch = ''
    for path in sorted(p for p in (O / 'proposed').rglob('*') if p.is_file()):
        rel = path.relative_to(O / 'proposed')
        old = (R / rel).read_text() if (R / rel).exists() else ''
        patch += ''.join(difflib.unified_diff(old.splitlines(True), path.read_text().splitlines(True),
            fromfile='a/' + str(rel) if old else '/dev/null', tofile='b/' + str(rel)))
    (O / 'candidate.patch').write_text(patch)
    prep.update(candidate_cpu_sha256=EXPECTED, parent_cpu_sha256=PARENT,
        candidate_manifest_sha256=sha(F / 'manifest.json'), parent_proposal_sha256=sha(BASE / 'manifest.json'),
        component_review_sha256={name: sha(P / name / 'manifest.json') for name in [
            'cpu-btb-balanced-source-review-v1', 'cpu-load-sign-source-review-v1',
            'cpu-fp-rs3-tag-source-review-v1', 'cpu-decode-ready-source-review-v2',
            'cpu-control-compose-source-review-v1']},
        composed_hunks_sha256=sha(F / 'hunks.json'), literal_hunks=5,
        appended_helper='factor_os30_control_paths.pl', flags_unchanged=True,
        proposed_sha256={str(p.relative_to(O / 'proposed')): sha(p) for p in (O / 'proposed').rglob('*') if p.is_file()},
        actual_regeneration_pending=True, recipe_sha256=sha(Path(__file__)))
    save(O / 'prepared.json', prep)
    shutil.copy2(__file__, O / 'recipe.py')
    print('PREPARED', O, sha(O / 'prepared.json'))


def check():
    prep = json.loads((O / 'prepared.json').read_text())
    for rel, digest in prep['proposed_sha256'].items(): assert sha(O / 'proposed' / rel) == digest
    d = O / 'checks'; d.mkdir(exist_ok=False)
    vendor = d / 'vendor'; vendor.mkdir()
    for path in (R / V).glob('*.pl'): shutil.copy2(path, vendor / path.name)
    shutil.copytree(O / 'controls/configs', vendor / 'configs')
    for name in ['generate_vexii.sh', 'factor_os30_fp_rs3.pl', 'factor_os30_frontend.pl', 'factor_os30_load_forward.pl', 'factor_os30_control_paths.pl', 'os30-rvc-pipeline.patch', 'configs/os30.cfg']:
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
            ('factor_os30_frontend.pl', None, FRONTEND),
            ('factor_os30_load_forward.pl', d / 'factor_os30_frontend.pl.v', PARENT),
            ('factor_os30_control_paths.pl', d / 'factor_os30_load_forward.pl.v', EXPECTED)]:
        probe = d / (name + '.v')
        if input_path: shutil.copy2(input_path, probe)
        else: shutil.copy2(d / 'factor_os30_fp_rs3.pl.v', probe)
        for _ in range(2):
            r = subprocess.run(['perl', str(vendor / name), 'os30', str(probe)], capture_output=True, text=True)
            assert r.returncode == 0 and sha(probe) == expected, r.stderr
        before = sha(probe)
        r = subprocess.run(['perl', str(vendor / name), 'os25', str(probe)], capture_output=True, text=True)
        assert r.returncode != 0 and sha(probe) == before
        qualified = probe.read_bytes()
        probe.write_bytes(qualified + b'\n// rejected drift\n')
        before = sha(probe)
        r = subprocess.run(['perl', str(vendor / name), 'os30', str(probe)], capture_output=True, text=True)
        assert r.returncode != 0 and sha(probe) == before
        # Restore the qualified output needed by the next pass.
        probe.write_bytes(qualified)
    save(d / 'result.json', dict(passed=True, flags_exact=True, replay_cpu_sha256=EXPECTED,
         source_isolation_success_and_failures=True, atomic_publication_preserved=True,
         variant_arguments=variants, helper_hash_guards=True, actual_scala_executed=False))
    print('CHECK PASS')


def seal():
    assert not (R / V / 'factor_os30_control_paths.pl').exists()
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
