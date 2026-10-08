#!/usr/bin/env python3
"""Independent read-only verification of the fixed endpoint physical/payload evidence."""
import csv
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
F = P / 'endpoint-control-fit-v1'
D = P / 'final-candidate-delivery-v3'
N = P / 'normal-build-endpoint-control-v1'
M = P / 'endpoint-control-map-v1'
O = P / 'endpoint-physical-delivery-review-v1'
W = P / 'native-renderer-window-v1'
E = {}


def sha(p):
    assert p.is_file() and not p.is_symlink(), p
    value = hashlib.sha256(p.read_bytes()).hexdigest()
    E[str(p.relative_to(R))] = value
    return value


def read(p):
    sha(p)
    return json.loads(p.read_text())


def check(p, digest):
    assert sha(p) == digest, p


def manifest(p, plain=False):
    d = read(p)
    tables = [(p.parent, d)] if plain else [
        (R, d.get('evidence_sha256', {})), (p.parent, d.get('files', {}))]
    for base, rows in tables:
        for name, value in rows.items():
            if isinstance(value, dict):
                check(base / name, value['sha256'])
                assert stat.S_IMODE((base / name).stat().st_mode) == value['mode']
            else:
                check(base / name, value)
    return d


check(F / 'manifest.json', '60da6979ade5e297826929cff776b935125e7ce874b7059804d1cfff6635f6c6')
check(D / 'manifest.json', '4f34548b08a0f76c2a7a9819102099d9cb9b01073aac58094ef178de61420221')
fm, dm = manifest(F / 'manifest.json'), manifest(D / 'manifest.json')
normal = manifest(N / 'manifest.json', plain=True)
manifest(M / 'manifest.json')
manifest(P / 'native-endpoint-control-check-v1/manifest.json')
for name, digest in dm['inputs_sha256'].items():
    check(R / name, digest)
result = read(F / 'result.json')
complete = read(F / 'fit-seed2/complete.json')
prep = read(F / 'prepared.json')
runprep = read(F / 'fit-seed2/prepared.json')
job = F / 'fit-seed2/job'
wd = job / 'src/fpga/targets/pocket'
assert complete['directory'] == str(wd)
assert complete['valid'] and complete['state'] == 'complete'
assert complete['returncodes'] == {'fit': 0, 'asm': 0, 'sta': 0, 'corners': 3}
assert runprep['prepared_sha256'] == sha(F / 'prepared.json')
assert runprep['review_sha256'] == sha(F / 'independent-review.json')
assert result['configured_mhz'] == 100 and result['seed'] == prep['seed'] == complete['seed'] == 2
assert not result['all_configured_timing_checks_pass']
assert result['full_fit_count'] == 1 and not result['seed_sweep']

# Independent report parsing; require the raw corner log and all 20 report files.
summary = wd / 'timing/summary.tsv'
sha(summary)
rows = list(csv.DictReader(summary.open(), delimiter='\t'))
assert rows == complete['timing'] and len(rows) == 20
keys = {(m, t, c) for m in ['slow', 'fast'] for t in ['0', '85']
        for c in ['setup', 'hold', 'recovery', 'removal', 'pulse_width']}
assert {(x['model'], x['temperature'], x['check']) for x in rows} == keys
log = (F / 'fit-seed2/corners.log').read_text()
matches = re.findall(r'^RESULT (slow|fast)_(0|85)_(\w+) (-?\d+\.\d+)$', log, re.M)
assert matches == [(x['model'], x['temperature'], x['check'], x['slack']) for x in rows]
assert '\nWhole-design timing failed; see timing\n' in log
assert [x for x in log.splitlines() if re.match('^(Error|Fatal)', x)] == [
    f'Error (23031): Evaluation of Tcl script {job}/tools/report_core_timing.tcl unsuccessful',
    'Error: Quartus Prime Timing Analyzer was unsuccessful. 1 error, 1 warning']
for row in rows:
    report = wd / ('timing/{model}_{temperature}_{check}.rpt'.format(**row))
    sha(report)
    text = report.read_text()
    if row['check'] == 'pulse_width':
        values = re.findall(r'^;\s*(-?\d+\.\d+)\s*;', text, re.M)
        actual = min(map(float, values))
    else:
        actual = float(re.search(r'Worst case slack is (-?\d+\.\d+)', text).group(1))
        assert ('Slow' if row['model'] == 'slow' else 'Fast') + ' 1100mV ' + row['temperature'] + 'C Model' in text
    assert actual == float(row['slack'])
worst = {c: min(float(x['slack']) for x in rows if x['check'] == c)
         for c in ['setup', 'hold', 'recovery', 'removal', 'pulse_width']}
assert worst == result['worst_timing'] == complete['worst']
assert all(worst[c] > 0 for c in worst if c != 'setup')
sta = (wd / 'output_files/ap_core.sta.rpt').read_text()
freq = {}
clock = 'ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk'
for temperature in ['0', '85']:
    section = sta.split('; Slow 1100mV ' + temperature + 'C Model Fmax Summary', 1)[1].split('Model Setup Summary', 1)[0]
    lines = [line for line in section.splitlines() if clock in line and 'MHz' in line]
    assert len(lines) == 1
    columns = [x.strip() for x in lines[0].split(';')][1:-1]
    assert columns[0] == columns[1] and columns[2] == clock
    freq[temperature] = float(columns[0].removesuffix(' MHz'))
assert freq == result['slow_main_clock_fmax_mhz'] == {'0': 91.75, '85': 90.33}
assert min(freq.values()) > 90
fit = (wd / 'output_files/ap_core.fit.rpt').read_text(encoding='latin-1')
resource_labels = {'needed_alms': 'ALMs needed [=A-B+C]',
    'placed_alms': '[A] ALMs used in final placement [=a+b+c+d]',
    'labs': 'Total LABs:  partially or completely used', 'm10ks': 'M10K blocks', 'dsps': 'Total DSP Blocks'}
resources = {}
for key, label in resource_labels.items():
    line = next(line for line in fit.splitlines() if len(line.split(';')) > 2 and line.split(';')[1].strip() == label) if key != 'labs' else next(line for line in fit.splitlines() if re.search(r';\s*Total LABs:\s*partially or completely used\s*;', line))
    resources[key] = int(re.match(r'[\d,]+', line.split(';')[2].strip()).group().replace(',', ''))
assert resources == result['resources'] == dict(needed_alms=18006, placed_alms=18071, labs=1842, m10ks=304, dsps=45)
budgets = dict(needed_alms=18480, placed_alms=18480, labs=1848, m10ks=308, dsps=66)
assert all(0 < resources[k] <= v for k, v in budgets.items())

# Mapped inventory was copied without old fit/timing files; selected source and
# QSF/SDC bytes remain identical after all four physical phases.
assert not any('/timing/' in n or n.endswith(('.fit.rpt', '.sta.rpt')) for n in prep['mapped_job_sha256'])
for n, h in prep['mapped_job_sha256'].items():
    check(F / 'job' / n, h)
for n, h in prep['source_input_sha256'].items():
    check(job / n, h)
qsf = wd / 'ap_core.qsf'
assert sha(qsf) == prep['qsf_sha256'] == read(N / 'checks/selected-binding.json')['mapped_qsf_sha256']
assert qsf.read_bytes() == (M / 'map-core/job/src/fpga/targets/pocket/ap_core.qsf').read_bytes()
text = qsf.read_text()
assert re.findall(r'^set_global_assignment -name SEED (\d+)$', text, re.M) == ['2']
locations = [x for x in text.splitlines() if x.startswith('set_location_assignment ')]
assert len(locations) == 224 and all('PIN_' in x for x in locations)

# The raw assembler output, bit-reversed payload and the exact two ZIP members
# are independently compared byte for byte. No converter/build is executed.
raw = wd / 'output_files/ap_core.rbf'
check(raw, complete['hashes']['rbf'])
assert sha(raw) == dm['raw_sha256'] == sha(D / 'raw/ap_core.rbf')
reverse = D / 'payload/Cores/ThinkElastic.SM64/os30.rbf_r'
def rev8(v):
    v = ((v & 0x55) << 1) | ((v >> 1) & 0x55)
    v = ((v & 0x33) << 2) | ((v >> 2) & 0x33)
    return ((v & 0x0f) << 4) | ((v >> 4) & 0x0f)
table = bytes(map(rev8, range(256)))
assert bytes(range(256)).translate(table).translate(table) == bytes(range(256))
assert raw.read_bytes().translate(table) == reverse.read_bytes()
assert reverse.read_bytes().translate(table) == raw.read_bytes()
assert sha(reverse) == dm['reversed_sha256']
app = D / 'payload/Assets/sm64/common/sm64.elf'
assert sha(app) == dm['elf_sha256'] == 'fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b'
archive = D / 'sm64-incremental-local.zip'
members = ['Cores/ThinkElastic.SM64/os30.rbf_r', 'Assets/sm64/common/sm64.elf']
with zipfile.ZipFile(archive) as z:
    assert z.namelist() == members and z.testzip() is None
    assert all(z.read(n) == (D / 'payload' / n).read_bytes() for n in members)
    assert all(stat.S_ISREG(i.external_attr >> 16) for i in z.infolist())

# Payload source closure, captured preimages and native artifacts are still
# exact. Integration itself remains a separate parent-owned action.
proposal = read(N / 'prepared.json')['proposed_sha256']
assert len(proposal) == len(dm['entries']) == 38
assert set(proposal) == {x['path'] for x in dm['entries']}
for row in dm['entries']:
    n = row['path']
    check(D / 'hardware' / n, proposal[n])
    assert row['after']['sha256'] == proposal[n]
    target = R / n
    if row['before'] is None:
        assert not target.exists() and not target.is_symlink(), target
    else:
        check(target, row['before']['sha256'])
        assert stat.S_IMODE(target.stat().st_mode) == row['before']['mode']
for n, h in dm['production_guards'].items():
    check(R / n, h)
native = read(P / 'native-endpoint-control-check-v1/result.json')
assert native['passed'] and native['cycle_change'] == 0 and native['exact_artifact_count'] == 17
assert native['total_renderer_entry_interval_cycles'] == 7869037
for n, h in native['exact_artifact_sha256'].items():
    check(W / 'run-endpoint-control-v1' / n, h)
    check(W / 'run-parallel-control-v1' / n, h)
selected = {'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v': '6f50382aa82e6684551756e6c2473492d0bd915a0c94b2aeae9ac4a47e62dec4',
    'src/fpga/common/os30/gpu_core.v': '5e9dd586e64034ba7b9f2bb97d9f4b8cba20c772da21d263f38ad74049269e5a',
    'src/fpga/common/os30/gpu_color_depth_cache.sv': 'ab1f1aeeb135cd2f80e44adb39616c35f28f1e164ab639b2f5645fbb447000ae'}
for n, h in selected.items():
    check(D / 'hardware' / n, h)
assert not (D / 'integration').exists()
assert dm['private_only'] and not dm['integrated']

O.mkdir(exist_ok=False)
output = dict(passed=True, passed_for_parent_selected_integration=True,
    no_cad_or_simulation_run=True, production_modified=False, payload_members=members,
    raw_rbf_sha256=sha(raw), reversed_rbf_sha256=sha(reverse), zip_sha256=sha(archive),
    elf_sha256=sha(app), hardware_files=38, payload_files=len(dm['files']),
    mapped_files_checked=len(prep['mapped_job_sha256']), source_inputs_checked=len(prep['source_input_sha256']),
    normal_flat_bindings_checked=len(normal), timing_rows=rows, slow_main_fmax_mhz=freq,
    worst_timing=worst, resources=resources, resource_limits=budgets,
    bit_reversal_and_involution_exact=True, zip_contents_exact=True,
    native_artifacts_exact=17, native_interval_cycles=7869037,
    configured_mhz=100, configured_100mhz_timing_closed=False,
    limitations=['Both slow main-clock Fmax values exceed the requested 90MHz threshold; this is not 100MHz setup closure.',
        'Native evidence remains two bounded renderer intervals with inherited guest/audio/scanout limitations; no sustained Pocket FPS claim.',
        'The reviewed ZIP contains the SM64 app and core only. This review does not install files, modify sibling repositories, or promote sources.',
        'The integration helper was read, not executed here. Its exact38-file preimage/backup/journal/rollback logic is retained. Parent must select integration separately.'])
(O / 'result.json').write_text(json.dumps(output, indent=2, sort_keys=True) + '\n')
sha(O / 'result.json')
sha(Path(__file__))
(O / 'manifest.json').write_text(json.dumps(dict(passed=True, evidence_sha256=E), indent=2, sort_keys=True) + '\n')
print('PASS', hashlib.sha256((O / 'manifest.json').read_bytes()).hexdigest())
print(json.dumps({k: output[k] for k in ['slow_main_fmax_mhz', 'worst_timing', 'resources', 'raw_rbf_sha256', 'reversed_rbf_sha256', 'zip_sha256']}))
