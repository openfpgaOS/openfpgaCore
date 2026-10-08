#!/usr/bin/env python3
"""Run the retained fourteen CPU fixtures for the sampled lane1 RS1 equality."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
OUT = ART / 'validation'
LABEL = 'rvc_skid_immediate_pingpong_integer_rs1_sample_lane1'
REFERENCE = 'rvc_skid_immediate_pingpong_integer_rs1_reuse'
CPU_DIR = ART / 'integer-rs1-sample-lane1-v2/immediate'
CPU = CPU_DIR / 'VexiiRiscv.v'
DIGEST = '27ed01763d52de9d31611e2959f0d4c9007d3fd21bcb979274d075a5b9cecd17'
AUDIT = ART / 'integer-rs1-sample-lane1-emitted-audit-v1/manifest.json'
MAP = OUT / 'skid-pingpong-integer-rs1-sample-lane1-map.json'
PROGRAMS = ['stress', 'i2f', 'kernels', 'icache', 'frontend', 'decode-traps', 'fp-legality']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, data):
    assert not path.exists(), path
    path.write_text(json.dumps(data, indent=2) + '\n')

def command(args, log):
    with log.open('w') as handle:
        subprocess.run([sys.executable, *map(str, args)], check=True,
                       stdout=handle, stderr=subprocess.STDOUT, cwd=ROOT)

def check():
    assert sha(CPU) == DIGEST
    review = json.loads(AUDIT.read_text())
    assert review['candidate_sha256'] == DIGEST
    for key in ['exact_allowed_diff_reconstructed', 'all_other_source_bytes_unchanged',
                'current_RD_ENABLE_and_LANE_SEL_preserved', 'shared_13_stage_advance']:
        assert review[key]

def build():
    check()
    mapping = json.loads((OUT / 'skid-pingpong-integer-rs1-reuse-map.json').read_text())
    mapping['derived_from_cpu_sha256'] = mapping['source_sha256']
    mapping['source_sha256'] = DIGEST
    mapping['derivative_scope'] = 'One new preselected integer RS1 index5 match FF; original live qualifications and all monitored transition equations are unchanged.'
    write(MAP, mapping)
    import run
    run.build(LABEL, CPU, MAP)
    out = OUT / LABEL
    for name in ['tb_irq.v', 'main.cpp']:
        assert (out / name).read_bytes() == (OUT / REFERENCE / name).read_bytes(), name
    write(out / 'driver-manifest.json', dict(driver=str(Path(__file__).resolve()),
          driver_sha256=sha(Path(__file__)), candidate_sha256=DIGEST,
          independent_audit_sha256=sha(AUDIT), reference=REFERENCE,
          monitor_map_sha256=sha(MAP), exact_reference_testbench=True))

def run_all():
    check()
    out = OUT / LABEL
    for script in ['run.py', 'instruction_capacity.py', 'frontend_hazards.py',
                   'decode_traps.py', 'fp_legality.py']:
        command([HERE / script, 'run', '--label', LABEL], out / (script + '.driver.log'))
        print(script, 'PASS', flush=True)
    command([HERE / 'skid_coverage.py', '--label', LABEL], out / 'skid-coverage.driver.log')
    collect()

def collect():
    check()
    out = OUT / LABEL
    records = []
    totals = {}
    pingpong = []
    for program in PROGRAMS:
        for mode in ['quiet', 'scanout']:
            name = f'{program}-{mode}.log'
            path = out / name
            reference = OUT / REFERENCE / name
            assert path.read_bytes() == reference.read_bytes(), name
            text = path.read_text()
            match = re.findall(r'^PINGPONG COVERAGE (.+)$', text, re.M)
            assert len(match) == 1, name
            counters = {key: int(value) for key, value in re.findall(r'(\w+)=(\d+)', match[0])}
            assert counters['errors'] == 0
            for key, value in counters.items():
                totals[key] = totals.get(key, 0) + value
            pingpong.append(dict(log=name, counters=counters))
            records.append(dict(log=name, sha256=sha(path), byte_identical_to={REFERENCE: True}))
    assert totals == json.loads((OUT / REFERENCE / 'pingpong-coverage.json').read_text())['totals']
    write(out / 'pingpong-coverage.json', dict(passed=True, candidate_cpu_sha256=DIGEST,
          totals=totals, runs=pingpong))
    write(out / 'cycle-equivalence.json', dict(reference=REFERENCE, candidate=LABEL,
          candidate_cpu_sha256=DIGEST, identical_program_mode_logs=14,
          extra_pipeline_cycles=0, extra_registers=1, results=records))
    qualification = dict(passed=True, candidate_cpu_sha256=DIGEST,
          fourteen_cases_byte_identical_to=[REFERENCE],
          source_recipe_sha256=sha(CPU_DIR / 'recipe.json'),
          source_proof_manifest_sha256=sha(CPU_DIR / 'manifest.json'),
          independent_audit=str(AUDIT.relative_to(ROOT)), independent_audit_sha256=sha(AUDIT),
          test_build_manifest_sha256=sha(out / 'build-manifest.json'),
          driver_manifest_sha256=sha(out / 'driver-manifest.json'),
          skid_coverage_sha256=sha(out / 'skid-coverage.json'),
          pingpong_coverage_sha256=sha(out / 'pingpong-coverage.json'),
          cycle_equivalence_sha256=sha(out / 'cycle-equivalence.json'),
          note='Whole-CPU fixtures establish retained behavior and cycles; these are not game FPS measurements.')
    write(out / 'qualification.json', qualification)
    write(CPU_DIR / 'qualification.json', qualification)
    print(json.dumps(dict(passed=True, cases=14, monitored_cycles=totals['cycles'])), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'run', 'collect'])
    args = parser.parse_args()
    {'build': build, 'run': run_all, 'collect': collect}[args.action]()
