#!/usr/bin/env python3
"""Verify the focused residency tests reject intentional payload/lifetime faults."""
import hashlib
import json
import resource
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase7'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

for name in ['invalidation-v1', 'resident-v1', 'resident-v2']:
    directory = OUT / name
    harness = directory / 'focused-proof.c'
    text = harness.read_text()
    faults = {}
    if name.startswith('resident'):
        start = text.index('void candidate(')
        prefix, body = text[:start], text[start:]
        assert body.count('job & 0x7fffu') == 1
        faults['corrupt-index'] = prefix + body.replace('job & 0x7fffu', 'job & 0x3fffu')
        # Alters only the candidate, not the reference or test expectations.
        faults['wrong-kick-cadence'] = prefix + body.replace('++g_vc_kick >= 32u', '++g_vc_kick >= 31u')
    else:
        token = '            rdp.texture_tile.uls != vc_tex_sig.uls ||\n'
        assert text.count(token) == 1
        faults['miss-tile-origin'] = text.replace(token, '')
        token = '        if (triangle_vc_texture_ready) return false;'
        assert text.count(token) == 1
        faults['erase-external-staleness'] = text.replace(token, '        vc_slot_stale = 0;\n' + token)
    dest = directory / 'negative-controls'
    dest.mkdir(exist_ok=True)
    results = []
    for fault, bad in faults.items():
        source, exe = dest / (fault + '.c'), dest / fault
        source.write_text(bad)
        subprocess.run(['gcc', '-std=c11', '-O2', str(source), '-lm', '-o', str(exe)], check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True, preexec_fn=no_core)
        (dest / (fault + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode != 0, (name, fault, 'fault went undetected')
        results.append(dict(fault=fault, rejected=True, returncode=result.returncode,
                            source_sha256=sha(source), executable_sha256=sha(exe)))
    (directory / 'negative-controls.json').write_text(json.dumps(dict(
        passed=True, script_sha256=sha(Path(__file__)), harness_sha256=sha(harness), faults=results), indent=2)+'\n')
    print(name + ': ' + ', '.join(faults) + ' rejected')
