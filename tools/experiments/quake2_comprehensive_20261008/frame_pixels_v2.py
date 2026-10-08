#!/usr/bin/env python3
"""Passive complete visible-row capture, closing qsim's RGB565 hash-width gap."""
import argparse
import csv
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import clear_trace as parent

R, P, S = parent.R, parent.P, parent.S
O = P / 'frame-pixels-v2'
sha, put = parent.sha, parent.put

def prepare():
    prep=json.loads((S/'prepared.json').read_text())
    assert sha(S/'qsim/qsim')==prep['qsim_sha256']
    O.mkdir(exist_ok=False)
    for rel,expected in prep['inputs'].items():
        if rel.startswith('qsim/'):
            assert sha(S/rel)==expected
            target=O/rel;target.parent.mkdir(exist_ok=True);shutil.copy2(S/rel,target)
    source=O/'qsim/env.c'
    # Reuse the pinned passive command hook as well, so supplemental workloads
    # can collect complete commands and pixels in one otherwise identical run.
    command_prep=json.loads((parent.O/'prepared.json').read_text())
    command_source=parent.O/'qsim/env.c'
    assert sha(command_source)==command_prep['sources']['env.c']
    old=command_source.read_text()
    anchor='''    uint8_t *fb = E->scan + ((uint32_t)idx << 20);
    uint64_t hp = FNV64_INIT;'''
    assert old.count(anchor)==1
    new=old.replace(anchor,'''    uint8_t *fb = E->scan + ((uint32_t)idx << 20);
    /* Passive host output: preserve the original guest model and its hashes.
     * In particular its old mode_w-byte hash under-covers RGB565 rows. */
    static FILE *pixel_capture;
    static int pixel_checked;
    if (!pixel_checked) {
        const char *path = getenv("QSIM_FRAME_PIXELS");
        if (path) {
            pixel_capture = fopen(path, "wb");
            if (!pixel_capture) { perror("pixel capture"); exit(2); }
        }
        pixel_checked = 1;
    }
    if (pixel_capture) {
        uint32_t rowbytes = w;
        if (E->mode_color == 1) rowbytes = (w + 1) >> 1;
        else if (E->mode_color == 2) rowbytes = (w + 3) >> 2;
        else if (E->mode_color >= 3 && E->mode_color <= 5) rowbytes = w * 2;
        uint32_t header[6] = {(uint32_t)idx, w, h, st, E->mode_color, rowbytes};
        if (rowbytes > st || (uint64_t)st * h > 0x100000u ||
            fwrite(&seq, 8, 1, pixel_capture) != 1 ||
            fwrite(header, 4, 6, pixel_capture) != 6) {
            fprintf(stderr, "pixel capture header/size error\\n"); exit(2);
        }
        for (uint32_t y = 0; y < h; y++) {
            if (fwrite(fb + y * st, 1, rowbytes, pixel_capture) != rowbytes) {
                perror("pixel capture write"); exit(2);
            }
        }
    }
    uint64_t hp = FNV64_INIT;''',1)
    source.write_text(new)
    (O/'source.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/env.c',tofile='b/env.c')))
    with (O/'build.log').open('w') as log: subprocess.run(['make','-j2'],cwd=O/'qsim',stdout=log,stderr=subprocess.STDOUT,check=True)
    shutil.copy2(__file__,O/'recipe.py')
    put(O/'prepared.json',dict(parent_prepared_sha256=sha(S/'prepared.json'),parent_qsim_sha256=sha(S/'qsim/qsim'),
        observer_qsim_sha256=sha(O/'qsim/qsim'),recipe_sha256=sha(Path(__file__)),
        inherited_command_observer_source_sha256=sha(command_source),
        source_sha256={p.name:sha(p) for p in (O/'qsim').iterdir() if p.suffix in ('.c','.h')},
        format='Little-endian uint64 frame, six uint32 buffer,width,height,stride,mode,rowbytes, then height tightly packed visible rows.',
        scope='Read-only host framebuffer dump at the existing frame event. No guest memory/model/timing writes.'))
    print('prepared',sha(O/'qsim/qsim'))

def run(label):
    prep=json.loads((O/'prepared.json').read_text())
    assert sha(O/'qsim/qsim')==prep['observer_qsim_sha256']
    for name,value in prep['source_sha256'].items():assert sha(O/'qsim'/name)==value
    assert label.replace('_','').replace('-','').isalnum()
    report=json.loads((S/(label+'-result.json')).read_text());assert report['passed']
    assert sha(S/(label+'.elf'))==report['elf_sha256']
    dest=O/label;assert not dest.exists()
    env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_'))}
    capture=O/(label+'.pixels');assert not capture.exists();env['QSIM_FRAME_PIXELS']=str(capture)
    command=[str(O/'qsim/qsim'),str(S/(label+'.elf')),'--out',str(dest),'--app',str(S/'quake2.app'),
        '--data-dir',str(S/'data'),'--profile','--frames','362','--max-insns','4e9','--gpu-sched','eager',
        '--mem','flat','--no-normalize-build-stamp','--label',label]
    with (O/(label+'.log')).open('w') as log:subprocess.run(command,cwd=S,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    assert (dest/'frames.csv').read_bytes()==(S/label/'frames.csv').read_bytes()
    before=json.loads((S/label/'summary.json').read_text());after=json.loads((dest/'summary.json').read_text())
    for value in (before,after):
        del value['wall_seconds'],value['guest_mips'];value['env'].pop('gpuvec_captured')
    assert before==after
    put(O/(label+'-result.json'),dict(passed=True,elf_sha256=report['elf_sha256'],command=command,
        prepared_sha256=sha(O/'prepared.json'),pixel_capture_sha256=sha(capture),capture_bytes=capture.stat().st_size,
        original_frame_csv_all_columns_exact=True,original_guest_summary_all_fields_exact=True,
        excluded_summary_fields=['wall_seconds','guest_mips','env.gpuvec_captured']))
    print(label,'full pixel capture; original guest behavior exact')

def frames(path):
    data=path.read_bytes();pos=0;result={}
    while pos<len(data):
        assert pos+32<=len(data)
        header=struct.unpack_from('<QIIIIII',data,pos);frame,index,w,h,stride,mode,rowbytes=header;pos+=32
        assert frame not in result and index<3 and 0<w<=800 and 0<h<=600
        expected=((w+1)//2 if mode==1 else (w+3)//4 if mode==2 else w*2 if 3<=mode<=5 else w)
        assert rowbytes==expected and stride>=rowbytes
        size=rowbytes*h;assert pos+size<=len(data)
        result[frame]=(header,data[pos:pos+size]);pos+=size
    assert pos==len(data)
    return result

def compare(candidate,baseline):
    captures={};reports={};evidence=[Path(__file__),O/'recipe.py',O/'source.patch',O/'prepared.json']
    for label in (baseline,candidate):
        reports[label]=json.loads((O/(label+'-result.json')).read_text())
        assert reports[label]['passed'] and sha(O/(label+'.pixels'))==reports[label]['pixel_capture_sha256']
        captures[label]=frames(O/(label+'.pixels'))
        evidence.extend([O/(label+'-result.json'),O/(label+'.pixels'),O/label/'frames.csv',O/label/'summary.json'])
    assert captures[baseline].keys()==captures[candidate].keys()
    rows=list(csv.DictReader((S/baseline/'frames.csv').open()));assert len(rows)==362
    proof=[]
    for row in rows:
        n=int(row['frame']);a,pixels=captures[baseline][n];b,other=captures[candidate][n]
        assert a==b and pixels==other,n
        assert a[1]==int(row['buffer']) and a[2:]==(320,240,640,3,640),(n,a)
        # Bind the left-half legacy hash to the original frame, confirming the
        # sample point and exposing exactly what the original oracle covered.
        legacy=b''.join(pixels[y*640:y*640+320] for y in range(240))
        h=0xcbf29ce484222325
        for byte in legacy:h=((h^byte)*0x100000001b3)&0xffffffffffffffff
        assert f'{h:016x}'==row['hash_px'],n
        proof.append(dict(frame=n,buffer=a[1],visible_bytes=len(pixels),full_rgb565_sha256=hashlib.sha256(pixels).hexdigest()))
    # Negative control: perturb a right-half visible byte. The old left-half
    # hash would be unchanged, while the new complete byte comparison rejects it.
    header,pixels=captures[baseline][int(rows[-1]['frame'])]
    mutated=bytearray(pixels);mutated[639]^=1
    assert bytes(mutated)!=pixels and bytes(mutated[:320])==pixels[:320]
    assert hashlib.sha256(mutated).digest()!=hashlib.sha256(pixels).digest()
    dest=O/(candidate+'-vs-'+baseline);dest.mkdir(exist_ok=False)
    put(dest/'result.json',dict(passed=True,baseline=baseline,candidate=candidate,frames=362,
        visible_bytes_per_frame=153600,full_rgb565_bytes_exact=True,legacy_hash_frame_binding=True,
        right_half_negative_control_rejected=True,frame_hashes=proof,
        limitation='Functional GPU does not rasterize Q2 triangles; this closes complete CPU overlay/framebuffer parity. Actual selected GPU color/depth replay remains separately required.'))
    evidence.append(dest/'result.json')
    put(dest/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
    print('PASS complete RGB565 bytes identical for all 362 frames; right-half control rejected')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run','compare']);p.add_argument('--label');p.add_argument('--baseline',default='pass2_trio');a=p.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='run':run(a.label)
    else:compare(a.label,a.baseline)
