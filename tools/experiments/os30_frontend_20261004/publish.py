#!/usr/bin/env python3
"""Prepare or publish the matched P4 source/app/core, with guarded backups.

No SD writes, commits, SDK source changes or seed sweep. A report and a
saved-seed result at least as good as the installed worker are required.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-frontend-20261004'
DEST={'core':ROOT,'sm64':ROOT.parent/'SM64'}
RELEASE=OUT/'release'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
read=lambda p:json.loads(p.read_text())


def copy_atomic(source,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent,prefix='.'+dest.name+'.',delete=False) as f:
        temporary=Path(f.name)
        with source.open('rb') as src:shutil.copyfileobj(src,f)
        f.flush();os.fsync(f.fileno())
    shutil.copymode(source,temporary)
    assert sha(temporary)==sha(source)
    os.replace(temporary,dest)


def prepare():
    report=read(OUT/'results.json')
    assert report['validation']['color_and_depth_identical']
    assert report['validation']['gpu_acceptance_checks']>=387
    assert report['mixer']['exact_pcm_and_cycle_match']
    fit=report['fit']
    # The installed 100 MHz worker itself is experimental (91.23 MHz STA).
    # This guard prevents replacing it with an even weaker timing result.
    assert fit['valid'] and fit['minimum_main_fmax_mhz']>=91.23,fit
    assert min(fit['worst'][k] for k in ('hold','recovery','removal','pulse_width'))>=0
    RELEASE.mkdir(exist_ok=False)
    rows=[]
    before_core=read(OUT/'rtl-before.json')
    before_app=read(OUT/'before.json')
    patches={'core':[],'sm64':[]}

    def add(root,rel,source,expected=None,text=False):
        dest=DEST[root]/rel
        before=sha(dest)
        if expected is not None:assert before==expected,('Destination changed',root,rel)
        frozen=RELEASE/'source'/root/rel
        frozen.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,frozen)
        after=sha(frozen)
        if before==after:return
        if text:
            patches[root].extend(difflib.unified_diff(
                dest.read_text().splitlines(True) if dest.exists() else [],
                frozen.read_text().splitlines(True),
                fromfile='a/'+rel if dest.exists() else '/dev/null',tofile='b/'+rel))
        rows.append(dict(root=root,path=rel,source=str(frozen),before=before,after=after))

    for name in ('gpu_core.v','gpu_geometry_worker.v','audio_mixer.v'):
        source=OUT/'rtl'/name
        fitted=OUT/('fit-'+report['fit_label'])/'frozen/src/fpga/common'/name
        assert sha(source)==sha(fitted)
        add('core','src/fpga/common/'+name,source,before_core[name],text=True)
    assert sha(ROOT/'src/fpga/common/gpu_edge_walker.v')==before_core['gpu_edge_walker.v']
    variant='src/fpga/targets/pocket/variants/os30.mk'
    content=(ROOT/variant).read_text()
    assert 'OS30_VERTEX_ATTRIBUTES' not in content
    marker='# Opt-in experiment, never selected by a normal production build or sweep.'
    option='''# Wider experimental geometry frontend: raw normals/colors and UVs use the
# independent worker, with compact state/uploads (GPU_STATUS bit 12).
# Requires the matching SM64 renderer. Ordinary builds remain unchanged.
# The audio ramp pipeline preserves PCM and sample cycles. See the measured
# limits and validation in tools/experiments/os30_frontend_20261004/README.txt.
# make build VARIANT=os30 OS30_GEOMETRY_WORKER=1 OS30_DEPTH_PREFETCH=1 OS30_VERTEX_ATTRIBUTES=1 JOB=os30-attributes
ifeq ($(OS30_VERTEX_ATTRIBUTES),1)
ifneq ($(OS30_GEOMETRY_WORKER),1)
$(error OS30_VERTEX_ATTRIBUTES=1 requires OS30_GEOMETRY_WORKER=1)
endif
DEFS += INCLUDE_GPU_VERTEX_ATTRIBUTES INCLUDE_MIXER_RAMP_PIPE
endif

'''
    assert content.count(marker)==1
    if report['physical_optimization']:
        source=Path(__file__).parent/'physical.qsf'
        fitted=OUT/('fit-'+report['fit_label'])/'frozen/src/fpga/targets/pocket/variants/os30.qsf'
        assert sha(source)==sha(fitted)
        rel='src/fpga/targets/pocket/variants/os30-attributes.qsf'
        assert not (ROOT/rel).exists()
        add('core',rel,source,text=True)
        option=option.replace('JOB=os30-attributes','VARIANT_QSF=variants/os30-attributes.qsf JOB=os30-attributes')
    staged_variant=RELEASE/'os30.mk'
    staged_variant.write_text(content.replace(marker,option+marker))
    add('core',variant,staged_variant,text=True)
    for rel,digest in report['source_sha256'].items():
        source=OUT/'SM64'/rel;assert sha(source)==digest,rel
        if rel in before_app:assert sha(DEST['sm64']/rel)==before_app[rel],rel
        else:assert not (DEST['sm64']/rel).exists(),rel
        add('sm64',rel,source,text=True)
    diag=OUT/'SM64/.obj/sm64-perfdiag/app.elf'
    normal=OUT/'SM64/.obj/sm64/app.elf'
    assert sha(diag)==report['diagnostic_app_sha256']
    assert sha(normal)==report['normal_app_sha256']
    add('sm64','.obj/sm64-perfdiag/app.elf',diag)
    add('sm64','.obj/sm64/app.elf',normal)
    add('sm64','build/pocket/sm64/Assets/sm64/common/sm64.elf',diag)
    rbf=Path(fit['directory'])/'output_files/ap_core.rbf'
    sof=Path(fit['directory'])/'output_files/ap_core.sof'
    assert sha(rbf)==fit['hashes']['rbf'] and sha(sof)==fit['hashes']['sof']
    reverse=bytes(int(f'{x:08b}'[::-1],2) for x in range(256))
    bitstream=RELEASE/'os30.rbf_r'
    bitstream.write_bytes(rbf.read_bytes().translate(reverse))
    assert bitstream.read_bytes().translate(reverse)==rbf.read_bytes()
    # Update the selected runtime artifact too: make copy otherwise restores
    # an older core from runtime even after the packaged file was replaced.
    add('sm64','runtime/pocket/os30.rbf_r',bitstream)
    add('sm64','runtime/pocket/ap_core.sof',sof)
    add('sm64','build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r',bitstream)
    for root,patch in patches.items():(RELEASE/(root+'-changes.patch')).write_text(''.join(patch))
    protected={}
    for rel in ('runtime/pocket/os.bin','build/pocket/sm64/Assets/sm64/common/os.bin'):
        digest=sha(DEST['sm64']/rel)
        assert digest=='054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
        protected[rel]=digest
    (RELEASE/'plan.json').write_text(json.dumps(dict(files=rows,protected=protected,
        report_sha256=sha(OUT/'results.json'),timing=fit),indent=2)+'\n')
    print('Prepared matched source/app/core:',RELEASE)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if not RELEASE.exists():prepare()
    plan=read(RELEASE/'plan.json')
    assert sha(OUT/'results.json')==plan['report_sha256']
    for row in plan['files']:
        assert sha(Path(row['source']))==row['after']
        expected=row['after'] if args.verify else row['before']
        assert sha(DEST[row['root']]/row['path'])==expected,('Destination changed',row['root'],row['path'])
    for rel,digest in plan['protected'].items():assert sha(DEST['sm64']/rel)==digest,rel
    if args.verify:
        assert read(OUT/'published.json')['complete']
        print('PASS: matched source, diagnostic/normal apps, runtime and packaged core hashes; OS unchanged.')
        return
    if not args.apply:
        print('Ready to publish',len(plan['files']),'local files. No SD deployment.')
        return
    backup=OUT/'publication-backup';backup.mkdir(exist_ok=False)
    for row in plan['files']:
        if row['before'] is not None:
            dest=backup/row['root']/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(DEST[row['root']]/row['path'],dest)
            assert sha(dest)==row['before']
    journal=dict(complete=False,pocket_sd_updated=False,backup=str(backup),files=plan['files'])
    (OUT/'published.json').write_text(json.dumps(journal,indent=2)+'\n')
    for row in plan['files']:
        copy_atomic(Path(row['source']),DEST[row['root']]/row['path'])
        assert sha(DEST[row['root']]/row['path'])==row['after']
    journal['complete']=True
    (OUT/'published.json').write_text(json.dumps(journal,indent=2)+'\n')
    print('Published matched P4 source, apps and os30 runtime/package. No SD deployment.')


if __name__=='__main__':main()
