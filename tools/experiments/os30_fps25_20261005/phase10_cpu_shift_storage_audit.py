#!/usr/bin/env python3
"""Read-only one-M10K CPU shift-storage feasibility; no generation or map."""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OLD = ROOT / 'docs/measurements/os30-worker-20261003'
OUT = P / 'cpu-shift-storage-audit-v1'
CPU = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    assert not OUT.exists()
    evidence = {}
    states = {}
    verified = 0
    for label in ('fit-dc64_queues', 'fit-dc64_queues_shifts'):
        base = OLD / label
        inventory = read(base / 'frozen-sources.json')
        for name, digest in inventory.items():
            assert sha(base / 'frozen' / name) == digest, name
            verified += 1
        fit_path = base / 'results/pocket-os30-s2/ap_core.fit.rpt'
        fit = fit_path.read_text(encoding='latin1')
        row = next(l for l in fit.splitlines() if '|VexiiRiscv:cpu|' in l and '; VexiiRiscv ' in l)
        columns = [x.strip() for x in row.split(';')]
        states[label] = dict(inventory=inventory,
                            cpu_needed_alms=float(columns[2].split()[0]),
                            cpu_placed_alms=float(columns[3].split()[0]),
                            cpu_registers=int(columns[8].split()[0]),
                            whole=read(base / 'results.json')[0])
        for path in (base / 'frozen-sources.json', base / 'frozen' / CPU,
                     base / 'results.json', fit_path):
            evidence[str(path.relative_to(ROOT))] = sha(path)
    before, after = states.values()
    differences = [p for p in before['inventory'] if before['inventory'][p] != after['inventory'][p]]
    assert len(before['inventory']) == len(after['inventory']) == 103 and differences == [CPU]
    marker = '(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\n'
    original = (OLD / 'fit-dc64_queues/frozen' / CPU).read_text()
    inferred = (OLD / 'fit-dc64_queues_shifts/frozen' / CPU).read_text()
    assert original.count(marker) == 1 and original.replace(marker, '') == inferred
    assert before['cpu_needed_alms'] == 4494.1 and after['cpu_needed_alms'] == 4470.8
    assert before['whole']['alms'] == 17893 and after['whole']['alms'] == 17902
    assert before['whole']['m10ks'] == 246 and after['whole']['m10ks'] == 249
    log = OLD / 'fit-dc64_queues_shifts/results/pocket-os30-s2/map.log'
    bank_matches = re.findall(r'Inferred altshift_taps.*?"([^"\n]+)"\n\s*Info[^\n]+NUMBER_OF_TAPS set to (\d+)\n\s*Info[^\n]+TAP_DISTANCE set to (\d+)\n\s*Info[^\n]+WIDTH set to (\d+)', log.read_text())
    assert [(int(t), int(d), int(w)) for _, t, d, w in bank_matches] == [(1, 5, 8), (1, 3, 51)]
    banks = [dict(name=n, taps=int(t), distance=int(d), width=int(w)) for n, t, d, w in bank_matches]
    current = P / 'compact-lod5-core-v7/frozen' / CPU
    source = current.read_text()
    assert sha(current) == '8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
    assert marker in source
    fields = dict(mode=2, quiet=1, sign=1, exponent=9, mantissa=23)
    for stage in (3, 4, 5):
        for field, width in fields.items():
            name = f'execute_ctrl{stage}_up_FpuUnpack_RS3_RS_lane0_{field}'
            assert len(re.findall(r'\b' + name + r'\s*<=', source)) == 1, name
            if field != 'exponent':
                assert f'{name} <= execute_ctrl{stage-1}_down_FpuUnpack_RS3_RS_lane0_{field};' in source
            else:
                assert f'{name} <= _zz_{name};' in source
                assert f'assign _zz_{name} = execute_ctrl{stage-1}_down_FpuUnpack_RS3_RS_lane0_exponent;' in source
    for stage in range(2, 12):
        assert f'assign execute_ctrl{stage}_down_ready = execute_ctrl{stage+1}_up_ready;' in source
    for stage in range(3, 13):
        assert f'assign execute_ctrl{stage}_up_ready = execute_ctrl{stage}_down_isReady;' in source
        assert f'assign execute_ctrl{stage}_down_isReady = execute_ctrl{stage}_down_ready;' in source
    assert 'assign execute_ctrl12_down_ready = (! execute_freeze_valid);' in source
    vendor = Path('/home/alberto/altera_lite/25.1std/quartus/libraries/megafunctions/altshift_taps.tdf')
    vendor_text = vendor.read_text()
    assert 'CONSTANT RAM_WIDTH = WIDTH * NUMBER_OF_TAPS;' in vendor_text
    assert 'ASSERT (TAP_DISTANCE > 2)' in vendor_text
    paths = [log, current, P / 'compact-lod5-core-v7/prepared.json',
             P / 'compact-lod5-core-v7/map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt',
             ROOT / 'src/fpga/vendor/vexriscv/generate_vexii.sh',
             ROOT / 'tools/experiments/sm64_cpu_20260924/README.md',
             ROOT / 'tools/experiments/sm64_srw_20260923/README.md',
             ROOT / 'tools/experiments/os30_worker_20261003/fit.py', Path(__file__)]
    for path in paths:
        evidence[str(path.relative_to(ROOT))] = sha(path)
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    result = dict(
        read_only_audit_complete=True, credible_measured_40_alm_single_ram_candidate=False,
        no_shift_taps_global_disable_with_one_spare_allowed=False,
        original_pair_rehashed_files=verified, original_pair_only_change=CPU,
        historical_banks=banks,
        historical_cpu_needed_alms=[4494.1, 4470.8],
        historical_whole_needed_alms=[17893, 17902], historical_m10ks=[246, 249],
        prospective_terminal_tuple=dict(name='execute_ctrl3..5 FpuUnpack_RS3_RS',
                                        declared_bits_per_stage=36, stages=3,
                                        declared_ff_bits=108, clock_enable='!execute_freeze_valid',
                                        ram_blocks_expected=1, area_saving_measured=False),
        vendor_source=dict(path=str(vendor), sha256=sha(vendor)),
        cpu_generation_or_map_run=False, production_changes=False,
        evidence_sha256=evidence)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text('''One-spare-M10K CPU shift-storage audit: no evidence-backed 40-ALM option.

The strongest historical comparison is retained, not reconstructed: the
os30-worker-20261003 dc64_queues and dc64_queues_shifts fits have 103 frozen
inputs each, with only the module AUTO_SHIFT_REGISTER_RECOGNITION OFF annotation
removed. Both inventories rehash exactly. This changes CPU needed ALMs from
4494.1 to 4470.8 (-23.3), CPU placed ALMs from 4570.4 to 4529.0, and CPU fitted
registers from 5376 to 5217. Whole-core needed ALMs move 17893->17902 and RAM 246->249.
These are old 64 KiB CPU/worker fits, not a new v7 performance or placement claim.

The two actual inferred banks are:
* One final tap, width 8, distance 5, 40 stored bits, 1 M10K. Its fitted subtree
  costs 6.8 ALMs and 6 fabric registers for pointer/control logic.
* One final tap, width 51, distance 3, 153 stored bits, 2 M10Ks. Its subtree costs
  4.0 ALMs and 6 fabric registers. The representative name is FpuAddSharedPlugin
  node 3 rs2_quiet, but that name does not prove all 51 constituent signal bits.
Simply disabling NO_SHIFT_TAPS globally therefore exceeds the one-block budget.
The known one-block inferred bank is too small to credibly free 40 ALMs.

The largest straightforward coherent current-v7 tuple identified is FMA RS3:
execute_ctrl3_up_FpuUnpack_RS3_RS_lane0 through ctrl5, with mode 2, quiet 1, sign 1,
exponent 9, mantissa 23 (36 bits per stage, 108 declared FFs). There are no architectural
intermediate consumers: ctrl3/4 copies feed the next stage; only ctrl5 feeds the
FMA add-port rs2. Debug strings are unconsumed. Every stage advances on the same
!execute_freeze_valid and has no reset assignment. An explicitly bounded
single-tap, three-enabled-cycle RAM delay could preserve this ownership and
latency in principle, using one block. No implementation or proof run was done.

Do not quote 108 FFs as 54 ALMs: the current map already merges mantissa bit 22 with
quiet and exponent bit 7 with bit 8 at all three stages, and mode encoding changes
the physical FF representation. Registers also pack with surrounding logic.
The historical result for all 193 nominal delay bits is only 23.3 CPU needed ALMs
after three RAMs and controls, so no >=40-ALM saving is supported for a subset.
A custom 40-bit subset of the larger bank is conceivable, but would be a new
explicit storage design, not a demonstrated one-line inference win.

Wider-looking alternatives are not a single identical-CE terminal shift:
predicted target PC crosses decode and execute stall domains; store-buffer data
starts in an independently ready stream; float writeback DATA is modified by
packer merges and consumed for bypass at intermediate stages. Wide FPU values
also have early arithmetic consumers. Adding taps widens the RAM: the installed
altshift_taps definition uses RAM_WIDTH=WIDTH*NUMBER_OF_TAPS and requires tap
distance>=3. Capacity alone cannot supply extra parallel tap ports.

NO_SHIFT_TAPS also has a recorded timing motive: RAM outputs on FPU/writeback
bypass paths had a -0.83ns path. That historical number is not current-v7 STA.
Any selected future subset needs exact freeze/flush/first-valid-cycle testing,
same-cycle output proof and a measured map/fit result; no current timing or ISA
qualification is inferred from source feasibility. No CPU generation, map,
simulation, fit, production change, or new seed trial occurred in this audit.
''')
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()},
        evidence_sha256=evidence), indent=2) + '\n')
    print(json.dumps(dict(manifest_sha256=sha(OUT / 'manifest.json'), rehashed=verified)))


if __name__ == '__main__':
    main()
