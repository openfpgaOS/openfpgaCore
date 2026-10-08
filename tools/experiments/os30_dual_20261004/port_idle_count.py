#!/usr/bin/env python3
"""Private CRAM port counter-clear factoring; never modify production RTL."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'port-idle-count-v1')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    src = ROOT / 'src/fpga/common/cpu_target_port_per.v'
    frozen = json.loads((ART / 'fit-single128-v1/frozen-sources.json').read_text())
    assert digest(src) == frozen[str(src.relative_to(ROOT))]
    original = src.read_text()
    old = '''        WR_IDLE: begin
            m_awvalid <= 1'b0;
            m_wvalid  <= 1'b0;
            if (wr_grant) begin
                wr_burst_len   <= per_awlen;
                wr_burst_count <= 8'b0;
'''
    new = '''        WR_IDLE: begin
            m_awvalid <= 1'b0;
            m_wvalid  <= 1'b0;
            // The count is unobserved in IDLE; every grant starts at zero.
            // Clear from local state, without the upstream grant/address cone.
            wr_burst_count <= 8'b0;
            if (wr_grant) begin
                wr_burst_len   <= per_awlen;
'''
    assert original.count(old) == 1
    candidate = original.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == original
    # Entire source is bound above. These are all count writers/readers.
    assert len(re.findall(r'wr_burst_count\s*<=', original)) == 4
    assert len(re.findall(r'\bwr_burst_count\b', original)) == 8
    assert len(re.findall(r'\bwr_beat_is_last\b', original)) == 3
    assert original.count('wire wr_beat_is_last = (wr_burst_count == wr_burst_len);') == 1
    assert original.count('if (wr_beat_is_last)') == 2
    path = out / src.name
    path.write_text(candidate)
    (out / 'reference.v').write_text(original)
    # Relational induction: counts agree outside IDLE, arbitrary in IDLE.
    # The only count observation is in AW/W. IDLE always leaves with equal
    # zero counts on a grant; without a grant it remains unobserved in IDLE.
    states = 0
    for state in range(4):
        for count in range(256):
            for other in (range(256) if state == 0 else (count,)):
                for grant in range(2):
                    for accepted in range(2):
                        a = 0 if state == 0 and grant else ((count + 1) & 255) if state in (1, 2) and accepted else count
                        b = 0 if state == 0 else ((other + 1) & 255) if state in (1, 2) and accepted else other
                        if state != 0 or grant:
                            assert a == b
                        states += 1
    # Execute the retained bus regression against both actual modules, and
    # extend it to all AXI burst lengths and independent AW/W stall orders.
    test = ROOT / 'src/fpga/test/tb_cpu_target_port_main.cpp'
    cpp = test.read_text()
    assert cpp.count('cycle < 250') == 1
    assert cpp.count('for (int words : {1, 2, 4, 16})') == 1
    cpp = cpp.replace('cycle < 250', 'cycle < 2400').replace(
        'for (int words : {1, 2, 4, 16})', 'for (int words = 1; words <= 256; ++words)')
    harness = out / 'port_all_bursts.cpp'
    harness.write_text(cpp)
    logs = []
    for label, rtl in [('reference', out / 'reference.v'), ('candidate', path)]:
        obj = out / ('obj_' + label)
        command = ['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal',
                   '--top-module', 'cpu_target_port_per', '--Mdir', str(obj),
                   '-CFLAGS', '-DTEST_PER_ONLY', str(rtl), str(harness)]
        with (out / (label + '-build.log')).open('w') as log:
            subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        logpath = out / (label + '-runtime.log')
        with logpath.open('w') as log:
            subprocess.run([str(obj / 'Vcpu_target_port_per')], stdout=log, stderr=subprocess.STDOUT, check=True)
        logs.append(logpath)
    assert logs[0].read_bytes() == logs[1].read_bytes()
    assert logs[0].read_text().count('PASS peripheral') == 3072
    manifest = dict(reference=str(src), reference_sha256=digest(src), candidate_sha256=digest(path),
                    exact_single_block_replacement=True, relational_induction_states=states,
                    source_regression_sha256=digest(test), harness_sha256=digest(harness),
                    full_burst_cases_per_source=3072, runtime_logs_identical=True,
                    runtime_logs={str(p): digest(p) for p in logs},
                    scope='All interface cycles unchanged; counter differs only while WR_IDLE, where no observer reads it.',
                    physical_benefit='Unmeasured; targets late posted-write grant to count clear')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
