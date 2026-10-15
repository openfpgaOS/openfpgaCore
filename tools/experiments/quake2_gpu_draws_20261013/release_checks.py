"""Shared selection checks. These authorize assembly, never deployment."""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
import sys

NORMAL = '46d2206d655e663aab7c15b56ff0c88b7a1c547bb6b3516e921780d795c9b753'
PROFILE = '75db4eca10e3b7412771e977620770d0450047b283e9be68e33c64cbc512b0c7'
INSTALLER = '1313665b6de91fa316fa3c64c00d33036469314f15e64d326f6187d9f83e33e2'
COMPATIBILITY = {
    'Assets/quake2/common/os.bin': '054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7',
    'Cores/thinkelastic.quake2/loader.bin': 'fc1f8d37eb0fb322006b4428f0b59d2801c9b74978bd3f24103f5f9bbfca3c99',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_name(name):
    p = PurePosixPath(name)
    require(bool(name) and not p.is_absolute() and '..' not in p.parts and str(p) == name,
            'Unsafe relative path: ' + name)
    return name


def sha(path):
    require(path.is_file() and not path.is_symlink(), 'Missing/linked file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def inventory(root, exclude_manifest=False):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'Symlink in package: ' + str(path))
        name = path.relative_to(root).as_posix()
        if path.is_file() and not (exclude_manifest and name == 'manifest.json'):
            result[name] = sha(path)
    return result


def verify_child(path):
    subprocess.run([sys.executable, '-B', str(path / 'package.py'), 'verify'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def number(value, label):
    result = float(value)
    require(math.isfinite(result), 'Non-finite ' + label)
    return result


def select(core, app, physical, validation, clean, appearance):
    """Check evidence relationships, then return reproducible selection summary."""
    require(core['configured_mhz'] == physical['configured_clock_MHz'] == 100,
            'Unexpected configured clock')
    require(physical['fit_successful'] is True, 'Physical fit did not succeed')
    require(all(physical['phase_returncodes'].get(k) == 0 for k in ('map', 'fit', 'asm', 'sta')),
            'Incomplete physical build')
    require(physical['phase_returncodes'].get('corners') in (0, 3), 'Incomplete corner checks')
    fmax = physical['main_clock_restricted_Fmax_MHz']
    require(set(fmax) == {'slow_0', 'slow_85'} and
            all(number(v, 'Fmax') > 90 for v in fmax.values()), 'Both slow Fmax values must exceed 90 MHz')
    checks = {}
    for c in physical['all_corner_checks']:
        key = (c['model'], str(c['temperature']), c['check'])
        require(key not in checks, 'Duplicate corner check')
        checks[key] = number(c['slack'], 'corner slack')
    expected = {(m, t, k) for m in ('slow', 'fast') for t in ('0', '85')
                for k in ('setup', 'hold', 'recovery', 'removal', 'pulse_width')}
    require(set(checks) == expected, 'Missing or unexpected corner checks')
    require(all(v > 0 for k, v in checks.items() if k[2] != 'setup'),
            'Every hold/recovery/removal/pulse-width check must be positive')
    require(physical['worst_setup_ns'] == min(v for k, v in checks.items() if k[2] == 'setup'),
            'Setup summary differs from corners')
    require(physical['constrained_timing_passed'] == all(v >= 0 for v in checks.values()),
            'Timing qualification contradicts corner results')
    require(core['artifact_sha256'] == physical['bitstream_sha256'], 'Core/physical bitstream mismatch')
    require(core['additive_color_combiner'] is False, 'This release is the Quake2 no-combine experiment')
    require(app['optimized_elf_sha256'] == validation['normal_elf_sha256'] == NORMAL, 'Normal ELF mismatch')
    require(validation['profile_smoke']['passed'] is True and
            validation['profile_smoke']['elf_sha256'] == PROFILE, 'Profile ELF smoke mismatch')
    require(clean['passed'] is True and clean['elf_sha256'] == NORMAL, 'Clean rebuild not proven')
    require(validation['simulation_qualified'] is True, 'Simulation not qualified')
    gates = validation['gates']
    for name in ('canonical_hardware_vs_integer_reference',
                 'old_core_command_and_cpu_framebuffer_parity', 'old_core_rendering_parity'):
        require(gates[name]['passed'] is True, 'Failed validation gate: ' + name)
    pins = core['source_sha256']
    for source, tested in (
        ('src/fpga/common/gpu_geometry_worker.v', 'rtl/common/gpu_geometry_worker.v'),
        ('src/fpga/common/os30/gpu_core.v', 'rtl/common/gpu_core.v'),
    ):
        require(pins[source] == validation['rtl_source_pins'][tested], 'Physical/validation source mismatch: ' + source)
    audit = validation['configuration_audit']
    require(audit['macros_exact'] is True and audit['combine_parameter'] == 0 and
            pins['src/fpga/targets/pocket/ap_core.qsf'] == audit['qsf_sha256'] and
            sorted(core['verilog_macros']) == sorted(audit['macros']), 'Physical/validation QSF mismatch')
    samples = validation['actual_gpu_samples']
    require(len(samples) >= 8 and all(s['canonical_complete_output_exact'] is True and
            s['paced_vs_unpaced_exact'] is True for s in samples), 'Incomplete actual GPU equality checks')
    busy = [s for s in samples if s['workload'] == 'main' and s['frame'] in (92, 94, 148)]
    require(len(busy) == 3 and {s['frame'] for s in busy} == {92, 94, 148}, 'Missing busy samples')
    for sample in samples:
        b, c = sample['baseline_whole_frame_cycles'], sample['candidate_whole_frame_cycles']
        require(b > 0 and c > 0, 'Invalid frame cycles')
        require(abs((b / c - 1) * 100 - sample['modeled_throughput_gain_percent']) < 1e-9,
                'Sample throughput does not match measured cycles')
    gain = (sum(s['baseline_whole_frame_cycles'] for s in busy) /
            sum(s['candidate_whole_frame_cycles'] for s in busy) - 1) * 100
    require(gain >= 20 and abs(gain - gates['performance']['busy_three_sample_aggregate_gain_percent']) < 1e-9,
            'Busy aggregate must reach 20 percent and match cycle evidence')
    require(appearance['decision'].startswith('Accept ') and appearance['legacy_pixel_exact'] is False and
            appearance['maximum_changed_pixels'] == gates['legacy_new_path_appearance']['max_changed_pixels'],
            'Explicit appearance review is absent or inconsistent')
    return {
        'status': 'READY_FOR_DEVICE_TRIAL', 'deployed': False,
        'configured_clock_MHz': 100, 'timing_qualified': physical['constrained_timing_passed'],
        'restricted_slow_Fmax_MHz': fmax, 'worst_setup_ns': physical['worst_setup_ns'],
        'minimum_nonsetup_slack_ns': {k: min(v for c, v in checks.items() if c[2] == k)
                                    for k in ('hold', 'recovery', 'removal', 'pulse_width')},
        'resources': {k: physical[k] for k in ('ALMs', 'LABs', 'M10Ks', 'DSPs')},
        'normal_elf_sha256': NORMAL, 'profile_elf_sha256': PROFILE,
        'bitstream_sha256': physical['bitstream_sha256'],
        'modeled_busy_aggregate_gain_percent': gain,
        'modeled_sample_gain_range_percent': [min(s['modeled_throughput_gain_percent'] for s in samples),
                                              max(s['modeled_throughput_gain_percent'] for s in samples)],
        'physical_Pocket_tested': False, 'physical_FPS_improvement_established': False,
        'legacy_pixel_exact': False,
        'appearance': {**gates['legacy_new_path_appearance'], 'decision': appearance['decision']},
        'appearance_review_decision': appearance['decision'],
        'scope': 'Quake2 only; 320x240 RGB565; native particle projection; no additive color combiner; silent audio.',
        'evidence_scope': 'Complete app/core sources and selected evidence summaries, logs and pins; full raw capture archives are not included.',
    }


def check_deployment(path, manifest):
    require(manifest['compatibility'] == COMPATIBILITY, 'OS/loader compatibility changed')
    require(set(manifest['files']) == {'quake2.elf', 'quake2-gpuprofile.elf', 'os30.rbf_r', 'core.json'},
            'Unexpected deployment payload')
    require(sha(path / 'install.py') == INSTALLER, 'Native5 installer changed')
    for name, spec in manifest['files'].items():
        require(sha(path / name) == spec['sha256'] and (path / name).stat().st_size == spec['bytes'],
                'Deployment payload mismatch: ' + name)
    require(read(path / 'core.json')['core']['metadata']['version'] == '1.0.6-native6', 'Wrong core version')
