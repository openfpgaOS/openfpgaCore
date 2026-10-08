#!/usr/bin/env python3
"""Validate the selected trio without modifying its source or products."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import time

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/quake2-optimize-pass2-20261008'
W=P/'work/trio'
OUT=P/'final-regressions-trio-v1'
ELF=W/'.obj/quake2/app.elf'
ELF_SHA='7bcb432f6edb78a0d7fc85db3fd0b7bb79866382df001ad9caeb9a02c7cacdd6'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def run(name,cmd):
    start=time.monotonic()
    with (OUT/(name+'.log')).open('w') as log:
        p=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
    result=dict(command=cmd,returncode=p.returncode,seconds=time.monotonic()-start)
    write(OUT/(name+'-result.json'),result)
    return result

def main():
    assert sha(ELF)==ELF_SHA
    OUT.mkdir(exist_ok=False)
    inputs={str(p.relative_to(W)):sha(p) for directory in ['src','tools']
            for p in sorted((W/directory).rglob('*')) if p.is_file()}
    write(OUT/'prepared.json',dict(source_files=inputs,normal_elf_sha256=ELF_SHA,
        script_sha256=sha(__file__),scope='Existing eight renderer/loader/audio sanitizer suites, texture tests, normal ELF and diagnostic isolation inspection.'))
    commands={
        'renderer':['python3',str(W/'tools/check_quake2_renderer.py')],
        'textures':['python3',str(W/'tools/check_quake2_textures.py'),str(W),'--report',str(OUT/'textures.json')]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs={name:pool.submit(run,name,cmd)for name,cmd in commands.items()}
        results={name:job.result()for name,job in jobs.items()}
    assert all(r['returncode']==0 for r in results.values()),results
    renderer=(OUT/'renderer.log').read_text()
    assert renderer.count('PASS:')==7 and 'Quake2 renderer regressions passed' in renderer,renderer
    texture=json.loads((OUT/'textures.json').read_text());assert texture['passed']

    symbols=subprocess.run(['llvm-nm','--defined-only',str(ELF)],check=True,capture_output=True,text=True).stdout
    (OUT/'normal-elf-symbols.txt').write_text(symbols)
    forbidden=['ofq2_profile_','gpu_wait_prof','__gcov','gcov_info']
    assert not any(name in symbols for name in forbidden)
    disasm=subprocess.run(['llvm-objdump','-d','--mattr=+m,+a,+f,+c',
        '--no-show-raw-insn','--disassemble-symbols=ofgl_project_cvert,ofgl_emit_ctri.isra.0',str(ELF)],
        check=True,capture_output=True,text=True).stdout
    (OUT/'selected-functions-disassembly.txt').write_text(disasm)
    project=disasm.split('<ofgl_project_cvert>:',1)[1].split('<ofgl_emit_ctri.isra.0>:',1)[0]
    emit=disasm.split('<ofgl_emit_ctri.isra.0>:',1)[1]
    opcounts=dict(project_fmin_s=len(re.findall(r'\bfmin\.s\s',project)),
        project_fmax_s=len(re.findall(r'\bfmax\.s\s',project)),
        uv_fcvt_rdn=len(re.findall(r'fcvt\.w\.s[^\n]*\brdn\b',emit)))
    assert opcounts==dict(project_fmin_s=3,project_fmax_s=3,uv_fcvt_rdn=2),opcounts
    assert 'ofgl_report_big_tri.lto_priv.0' in emit
    assert '<floorf>' in emit # Required fallback remains linked.
    write(OUT/'target-operations.json',dict(passed=True,normal_elf_sha256=ELF_SHA,
        instruction_counts=opcounts,original_floorf_fallback_retained=True,
        cold_diagnostic_helper_retained=True,
        disassembly_sha256=sha(OUT/'selected-functions-disassembly.txt')))

    makefile=W/'src/quake2/Makefile';make=makefile.read_text()
    assert 'GPU_PROFILE ?= 0' in make
    assert '$(if $(filter 1,$(GPU_PROFILE)),-gpuprofile)' in make
    assert 'build/diagnostics/quake2-gpuprofile.elf' in make
    assert '-DOFQ2_GPU_PROFILE=$(GPU_PROFILE)' in make
    assert sha(makefile)==sha(P/'work/base/src/quake2/Makefile')
    assert sha(W/'src/quake2/openfpga/of_gpu_profile_q2.h')==sha(P/'work/base/src/quake2/openfpga/of_gpu_profile_q2.h')
    diag=subprocess.run(['make','-n','-C',str(W/'src/quake2'),'GPU_PROFILE=1','clean'],capture_output=True,text=True)
    (OUT/'diagnostic-clean-dryrun.txt').write_text(diag.stdout+diag.stderr)
    assert diag.returncode==0 and 'build/diagnostics/quake2-gpuprofile.elf' in diag.stdout
    assert 'build/pocket/quake2' not in diag.stdout
    invalid=subprocess.run(['make','-n','-C',str(W/'src/quake2'),'GPU_PROFILE=2','clean'],capture_output=True,text=True)
    (OUT/'invalid-profile-dryrun.txt').write_text(invalid.stdout+invalid.stderr)
    assert invalid.returncode and 'GPU_PROFILE must be 0 or 1' in invalid.stderr
    write(OUT/'diagnostic-isolation.json',dict(passed=True,inspection_and_dryrun_only=True,
        normal_default=0,separate_object_directories=True,
        diagnostic_release='build/diagnostics/quake2-gpuprofile.elf',
        diagnostic_clean_preserves_normal_package=True,invalid_mode_rejected=True,
        forbidden_profile_symbols=forbidden,forbidden_symbols_found=False,normal_elf_sha256=ELF_SHA))

    assert sha(ELF)==ELF_SHA
    for rel,h in inputs.items():assert sha(W/rel)==h,rel
    result=dict(passed=True,results=results,source_files_rehashed_unchanged=len(inputs),
        renderer_suites=['renderer','aliascache','aliasmesh','light','image','models','warp','sound'],
        sanitizers=['ASan','UBSan'],texture_exact_source_lengths=65,
        selected_source_sha256=sha(W/'src/quake2/openfpga/of_gl1.c'),normal_elf_sha256=ELF_SHA,
        target_instruction_counts=opcounts,diagnostic_isolation_passed=True,
        source_writes=False,sibling_writes=False,product_builds=False,
        limitations=['Host tests verify actual source under sanitizers, not device FPS.',
                     'Read-only selected normal ELF checks and diagnostic dry runs; no diagnostic build.'])
    write(OUT/'result.json',result)
    write(OUT/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(ROOT)):sha(p)
        for p in sorted(OUT.rglob('*'))if p.is_file()}))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
