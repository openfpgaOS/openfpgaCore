#!/usr/bin/env python3
"""Reuse pinned passive observers; prove complete commands and RGB565 bytes."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-batch-memory-20261008'
P3=R/'docs/measurements/quake2-comprehensive-20261008'
S=R/'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
sys.path.insert(0,str(R/'tools/experiments/quake2_comprehensive_20261008'))
import clear_trace as ct
import frame_pixels_v2 as fp
sha,put=ct.sha,ct.put
BASE='thorough_combined6'
OBSERVER=P3/'frame-pixels-v2'
PIN='a458b651273b7bd10d8351b1689c2d60971cd170f06e4b5c26ea5d41bb52fcc2'

def fnv(data):
    h=0xcbf29ce484222325
    for b in data:h=((h^b)*0x100000001b3)&0xffffffffffffffff
    return f'{h:016x}'

def run(label):
    assert label.replace('_','').replace('-','').isalnum()
    report=json.loads((S/(label+'-result.json')).read_text())
    assert report['passed'] and sha(S/(label+'.elf'))==report['elf_sha256']
    prep=json.loads((OBSERVER/'prepared.json').read_text())
    assert sha(OBSERVER/'qsim/qsim')==prep['observer_qsim_sha256']==PIN
    for name,value in prep['source_sha256'].items():assert sha(OBSERVER/'qsim'/name)==value
    out=P/'full-semantics-v1'/label;out.mkdir(parents=True,exist_ok=False)
    command=[str(OBSERVER/'qsim/qsim'),str(S/(label+'.elf')),'--out',str(out/'qsim'),
        '--app',str(S/'quake2.app'),'--data-dir',str(S/'data'),'--profile','--frames','362',
        '--max-insns','4e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp','--label',label]
    env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_'))}
    env['QSIM_COMMAND_TRACE']=str(out/'commands.bin');env['QSIM_FRAME_PIXELS']=str(out/'pixels.bin')
    put(out/'command.json',dict(command=command,environment={k:v for k,v in env.items() if k.startswith('QSIM_')}))
    with (out/'run.log').open('w') as log:subprocess.run(command,cwd=S,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    assert (out/'qsim/frames.csv').read_bytes()==(S/label/'frames.csv').read_bytes()
    before=json.loads((S/label/'summary.json').read_text());after=json.loads((out/'qsim/summary.json').read_text())
    for v in (before,after):
        del v['wall_seconds'],v['guest_mips'];v['env'].pop('gpuvec_captured')
    assert before==after
    exact_files=[]
    for old in (S/label).iterdir():
        if old.name.startswith('profile') or old.name in ('console.log','geometry.csv','scene.csv'):
            assert (out/'qsim'/old.name).read_bytes()==old.read_bytes(),old.name
            exact_files.append(old.name)
    put(out/'observer-result.json',dict(passed=True,elf_sha256=report['elf_sha256'],observer_sha256=PIN,
        all_original_frame_csv_columns_exact=True,all_original_guest_summary_fields_exact=True,
        excluded_summary_fields=['wall_seconds','guest_mips','env.gpuvec_captured'],additional_exact_outputs=exact_files,
        commands_sha256=sha(out/'commands.bin'),pixels_sha256=sha(out/'pixels.bin')))
    compare(label)

def compare(label):
    out=P/'full-semantics-v1'/label
    ob=json.loads((out/'observer-result.json').read_text());assert ob['passed']
    for name in ('commands','pixels'):assert sha(out/(name+'.bin'))==ob[name+'_sha256']
    bc=P3/'command-trace-v1';bp=P3/'frame-pixels-v2'
    cr=json.loads((bc/(BASE+'-result.json')).read_text());pr=json.loads((bp/(BASE+'-result.json')).read_text())
    assert cr['passed'] and pr['passed'] and cr['elf_sha256']==pr['elf_sha256']==sha(S/(BASE+'.elf'))
    assert sha(bc/(BASE+'.commands'))==cr['trace_sha256']
    assert sha(bp/(BASE+'.pixels'))==pr['pixel_capture_sha256']
    commands={BASE:ct.trace(bc/(BASE+'.commands')),label:ct.trace(out/'commands.bin')}
    # Includes commands after the final presented frame, so late publication
    # cannot silently drop a trailing command from this equality gate.
    assert commands[BASE]==commands[label], 'commands/order/frame assignments differ'
    pixels={BASE:fp.frames(bp/(BASE+'.pixels')),label:fp.frames(out/'pixels.bin')}
    assert pixels[BASE].keys()==pixels[label].keys()
    counts={};proof=[]
    for name in (BASE,label):
        grouped={}
        for frame,cmd in commands[name]:grouped.setdefault(frame,bytearray()).extend(struct.pack('<'+'I'*len(cmd),*cmd))
        rows=list(csv.DictReader((S/name/'frames.csv').open()));assert len(rows)==362
        for row in rows:
            n=int(row['frame']);stream=grouped.get(n,b'')
            assert len(stream)//4==int(row['gpu_cmd_words']) and fnv(stream)==row['hash_cmd'],(name,n,'commands')
            header,data=pixels[name][n]
            assert header[1]==int(row['buffer']) and header[2:]==(320,240,640,3,640)
            assert fnv(b''.join(data[y*640:y*640+320] for y in range(240)))==row['hash_px']
            if name==label:
                assert (header,data)==pixels[BASE][n],(n,'complete pixels')
                proof.append(dict(frame=n,buffer=header[1],rgb565_sha256=hashlib.sha256(data).hexdigest()))
        counts[name]=len(commands[name])
    assert len(pixels[BASE])==len(pixels[label])==362
    last=pixels[label][int(rows[-1]['frame'])][1];mutated=bytearray(last);mutated[639]^=1
    assert bytes(mutated)!=last and bytes(mutated[:320])==last[:320]
    put(out/'result.json',dict(passed=True,baseline=BASE,candidate=label,baseline_elf_sha256=cr['elf_sha256'],
        candidate_elf_sha256=ob['elf_sha256'],frames=362,command_counts=counts,
        all_commands_order_and_frame_assignments_exact_including_tail=True,
        command_hash_and_count_bound_to_all_original_frames=True,
        full_visible_rgb565_bytes_exact=True,bytes_per_frame=153600,
        complete_framebuffer_right_half_negative_control=True,frame_pixel_hashes=proof,
        limitation='Functional qsim does not rasterize Quake2 triangles. These are complete CPU framebuffer and command proofs; actual RTL and timed producer validation are separate gates.'))
    shutil.copy2(__file__,out/'recipe.py')
    evidence=[out/'command.json',out/'observer-result.json',out/'result.json',out/'commands.bin',out/'pixels.bin',out/'recipe.py',
        out/'qsim/frames.csv',out/'qsim/summary.json',OBSERVER/'prepared.json',OBSERVER/'source.patch',OBSERVER/'qsim/qsim',
        bc/(BASE+'-result.json'),bc/(BASE+'.commands'),bp/(BASE+'-result.json'),bp/(BASE+'.pixels'),
        S/(label+'-result.json'),S/(label+'.elf'),S/(BASE+'-result.json'),S/(BASE+'.elf')]
    put(out/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
    print('PASS',label,'362 exact complete command and full RGB565 frames')

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('action',choices=['run','compare']);a.add_argument('--label',required=True);v=a.parse_args()
    globals()[v.action](v.label)
