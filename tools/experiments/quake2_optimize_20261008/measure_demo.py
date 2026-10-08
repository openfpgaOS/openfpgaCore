#!/usr/bin/env python3
"""Private, paired Quake2 qsim software screen; never publishes a product file."""
from pathlib import Path
import argparse, csv, hashlib, json, os, shutil, subprocess, time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-optimize-20261008/demo-screen-v1'
OLD = Path('/tmp/quake2-repair')

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def write(p, value):
    p.write_text(json.dumps(value, indent=2) + '\n')

def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}

def prepare():
    OUT.mkdir(parents=True, exist_ok=False)
    q = OUT / 'qsim'; q.mkdir()
    sources = sorted([*(OLD/'qsim').glob('*.c'), *(OLD/'qsim').glob('*.h'), OLD/'qsim/Makefile'])
    for p in sources: shutil.copy2(p, q / p.name)
    (OUT/'data').mkdir()
    for name in ('pak0.pak', 'config.cfg'):
        shutil.copy2(OLD/'data'/name, OUT/'data'/name)
    shutil.copy2(OLD/'quake2.app', OUT/'quake2.app')
    shutil.copy2(OLD/'quake2.ini', OUT/'quake2.ini')
    shutil.copy2(Path('/tmp/quake2-review/analyze-steady.py'), OUT/'analyze-steady.py')
    shutil.copy2(__file__, OUT/'recipe.py')
    before = inventory(OUT)
    start = time.monotonic()
    with (OUT/'qsim-build.log').open('w') as log:
        r = subprocess.run(['make', '-j4'], cwd=q, stdout=log, stderr=subprocess.STDOUT)
    assert r.returncode == 0
    write(OUT/'prepared.json', dict(recipe_sha256=sha(__file__), inputs=before,
        qsim_sha256=sha(q/'qsim'), build_seconds=time.monotonic()-start,
        workload='Public q2demo1.dm2, fixed 30 Hz, sound disabled, 362 presented frames; steady indices 20..359',
        capability_hw_flags='0x2913775b', geometry_status_bit10=False,
        cpu_model='Inherited OS25-derived scalar model, 256 I-cache sets / 1024 D-cache sets, 2 ways, 64-byte lines',
        limits=['Model cycles are not actual selected Vexii cycles or Pocket FPS.',
                'Functional GPU model captures 3D commands but does not rasterize them.',
                'No real kernel/audio/CPU-GPU contention; fixed clock isolates deterministic software work.',
                'Historical replay omitted CPU framebuffer snapshots; separate sampled RTL validation is required.']))

def run(label, elf):
    p = json.loads((OUT/'prepared.json').read_text())
    assert sha(__file__) == p['recipe_sha256']
    assert sha(OUT/'qsim/qsim') == p['qsim_sha256']
    for rel, h in p['inputs'].items(): assert sha(OUT/rel) == h, rel
    assert label.replace('-', '').replace('_','').isalnum()
    elf = elf.resolve(); frozen = OUT/(label+'.elf')
    assert not frozen.exists()
    shutil.copy2(elf, frozen)
    # Neither public ELF nor guest instructions are patched or instrumented.
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(('SM64_', 'QSIM_')): del env[k]
    commands = []
    for suffix, frames in (('-startup',20),('',362)):
        dest = OUT/(label+suffix)
        assert not dest.exists()
        cmd = [str(OUT/'qsim/qsim'), str(frozen), '--out',str(dest),
            '--app',str(OUT/'quake2.app'),'--data-dir',str(OUT/'data'),
            '--profile','--frames',str(frames),'--max-insns','4e9',
            '--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp',
            '--label',label+suffix]
        if not suffix: cmd += ['--gpuvec','20,80,180,300']
        with (OUT/(label+suffix+'.log')).open('w') as log:
            r = subprocess.run(cmd,cwd=OUT,env=env,stdout=log,stderr=subprocess.STDOUT)
        assert r.returncode == 0, (cmd,r.returncode)
        summary = json.loads((dest/'summary.json').read_text())
        assert summary['frames'] == frames and summary['stop_reason'] == 'frame limit'
        assert summary['elf_sha256'] == sha(frozen)
        assert summary['env']['unmapped_mmio'] == 0
        assert summary['gpu_sync']['ring_overflows'] == 0
        assert 'GEOMETRY=0' in (dest/'console.log').read_text()
        commands.append(cmd)
    subprocess.run(['python3',str(OUT/'analyze-steady.py'),'--root',str(OUT),
        '--label',label,'--bench',str(frozen)],check=True,stdout=(OUT/(label+'-profile.log')).open('w'))
    rows=list(csv.DictReader((OUT/label/'frames.csv').open()))[20:360]
    write(OUT/(label+'-result.json'), dict(passed=True,elf_sha256=sha(frozen),
        source_elf=str(elf),source_elf_unchanged=sha(elf)==sha(frozen),
        prepared_sha256=sha(OUT/'prepared.json'),commands=commands,steady_frames=len(rows),
        modeled_cycles_per_frame=sum(int(r['est_cycles']) for r in rows)/len(rows),
        instructions_per_frame=sum(int(r['insns']) for r in rows)/len(rows),
        command_words_per_frame=sum(int(r['gpu_cmd_words']) for r in rows)/len(rows),
        limitations=p['limits']))
    print((OUT/(label+'-result.json')).read_text())

if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('action',choices=['prepare','run']); ap.add_argument('--label');ap.add_argument('--elf',type=Path)
    a=ap.parse_args()
    if a.action=='prepare': prepare()
    else:
        assert a.label and a.elf
        run(a.label,a.elf)
