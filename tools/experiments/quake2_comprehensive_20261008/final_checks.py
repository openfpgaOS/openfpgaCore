#!/usr/bin/env python3
"""Validate the selected normal ELF, source regressions and build isolation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-comprehensive-20261008'
name = sys.argv[1]
assert name.replace('_', '').isalnum()
W = P / 'work' / name
O = P / ('final-checks-' + name)
E = P / ('build-' + name) / 'app.elf'
O.mkdir(exist_ok=False)

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def write(p, v):
    p.write_text(json.dumps(v, indent=2) + '\n')

def run(label, cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    (O / (label + '.log')).write_text(result.stdout + result.stderr)
    write(O / (label + '.json'), dict(command=cmd, returncode=result.returncode))
    assert result.returncode == 0, label
    return result.stdout

inputs = {str(p.relative_to(W)): sha(p) for d in ['src', 'tools']
          for p in sorted((W / d).rglob('*')) if p.is_file()}
elf_hash = sha(E)
assert sha(W / '.obj/quake2/app.elf') == elf_hash
write(O / 'prepared.json', dict(sources=inputs, elf_sha256=elf_hash, recipe_sha256=sha(Path(__file__))))
with ThreadPoolExecutor(max_workers=2) as pool:
    a = pool.submit(run, 'renderer', ['python3', str(W / 'tools/check_quake2_renderer.py')])
    b = pool.submit(run, 'textures', ['python3', str(W / 'tools/check_quake2_textures.py'),
                                      str(W), '--report', str(O / 'textures-result.json')])
    renderer, texture = a.result(), b.result()
assert 'PASS: MD2 direct/cached/fallback parity' in renderer
assert json.loads((O / 'textures-result.json').read_text())['passed']
symbols = run('symbols', ['llvm-nm', '--defined-only', str(E)])
assert not any(s in symbols for s in ['ofq2_profile_', 'gpu_wait_prof', '__gcov', 'gcov_info'])
assembly = run('assembly', ['llvm-objdump', '-d', '--mattr=+m,+a,+f,+c', '--no-show-raw-insn',
    '--disassemble-symbols=ofgl_project_cvert,ofgl_emit_ctri.isra.0', str(E)])
project = assembly.split('<ofgl_project_cvert>:', 1)[1].split('<ofgl_emit_ctri.isra.0>:', 1)[0]
emit = assembly.split('<ofgl_emit_ctri.isra.0>:', 1)[1]
counts = dict(project_fmin_s=len(re.findall(r'\bfmin\.s\s', project)),
              project_fmax_s=len(re.findall(r'\bfmax\.s\s', project)),
              uv_fcvt_rdn=len(re.findall(r'fcvt\.w\.s[^\n]*\brdn\b', emit)))
assert counts == dict(project_fmin_s=3, project_fmax_s=3, uv_fcvt_rdn=2), counts
assert '<floorf>' not in emit and '<memcpy>' not in emit
assert 'ofgl_report_big_tri.lto_priv.0' in emit
makefile = W / 'src/quake2/Makefile'
assert sha(makefile) == sha(P / 'work/base/src/quake2/Makefile')
assert sha(W / 'src/quake2/openfpga/of_gpu_profile_q2.h') == sha(P / 'work/base/src/quake2/openfpga/of_gpu_profile_q2.h')
dry = run('diagnostic-clean-dryrun', ['make', '-n', '-C', str(W / 'src/quake2'), 'GPU_PROFILE=1', 'clean'])
assert 'build/diagnostics/quake2-gpuprofile.elf' in dry and 'build/pocket/quake2' not in dry
bad = subprocess.run(['make', '-n', '-C', str(W / 'src/quake2'), 'GPU_PROFILE=2', 'clean'], capture_output=True, text=True)
assert bad.returncode and 'GPU_PROFILE must be 0 or 1' in bad.stderr
(O / 'invalid-mode.log').write_text(bad.stdout + bad.stderr)
size = run('size', ['llvm-size', str(P / 'work/base/.obj/quake2/app.elf'), str(E)])
assert sha(E) == elf_hash
for rel, expected in inputs.items():
    assert sha(W / rel) == expected, rel
write(O / 'result.json', dict(passed=True, elf_sha256=elf_hash, sources_unchanged=len(inputs),
    renderer_suites=['renderer', 'aliascache', 'aliasmesh', 'light', 'image', 'models', 'warp', 'sound'],
    sanitizers=['ASan', 'UBSan'], texture_test_passed=True, target_operation_counts=counts,
    triangle_floor_and_memcpy_calls_absent=True, diagnostic_isolation=True,
    no_build_flag_or_hardware_changes=True))
write(O / 'manifest.json', dict(passed=True, evidence_sha256={str(p.relative_to(R)): sha(p)
    for p in sorted(O.rglob('*')) if p.is_file()}))
print((O / 'result.json').read_text())
