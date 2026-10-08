#!/usr/bin/env python3
"""Private portable two-TU PGO handoff. Never writes production or devices."""
import argparse,difflib,json,os,re,shlex,shutil,subprocess,time,zipfile
from collections import Counter
from pathlib import Path
from phase10_experiment import ROOT,CAMP,INPUTS,sha,write
P=CAMP/'phase10'; TEST=P/'renderer-pgo-v1'; SELECTED=P/'load-submit-overlap-v1'
BASE=CAMP/'phase9/checks-state-f11'; OUT=P/'renderer-pgo-delivery-v7'; REBUILD=P/'renderer-pgo-rebuild-v7'
TREE=REBUILD/'SM64'; EXPECTED='fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b'
# An opaque GCC auxiliary identity, not an accessed host path. Exact trained IDs.
PROFILE_KEY = str(TEST/'work/.obj/sm64/sm64/src/pc/gfx')+'/'
BLOCK='''# Frozen edge profiles apply only to the qualified normal Pocket renderer.
# GCC hashes its auxiliary filename into static-function profile IDs. Preserve
# this original identity only as an opaque lookup key below the local pgo tree;
# -MF keeps dependency outputs local. No file at the key's absolute path is used.
ifeq ($(PROFILE)$(PERF_DIAG)$(GEOMETRY)$(GEOMETRY_REFERENCE),0010)
SM64_RENDERER_PGO_DIR := $(CURDIR)/pgo
SM64_RENDERER_PGO_KEY := @KEY@
SM64_RENDERER_PGO_OBJS := $(foreach name,gfx_pc gfx_gpu,$(call sdk_app_object,$(SRC)/pc/gfx/$(name).c))
$(SM64_RENDERER_PGO_OBJS): CFLAGS += -fprofile-use=$(SM64_RENDERER_PGO_DIR) -dumpdir $(SM64_RENDERER_PGO_KEY) -MF $(@:.o=.d) -fprofile-partial-training -Werror=coverage-mismatch -Werror=missing-profile -fdump-ipa-profile-details=$(@:.o=.profile.txt)
$(SM64_RENDERER_PGO_OBJS): $(foreach name,gfx_pc gfx_gpu,$(SM64_RENDERER_PGO_DIR)$(SM64_RENDERER_PGO_KEY)$(name).gcda)
endif

'''.replace('@KEY@',PROFILE_KEY)


def prepare():
    assert sha(TEST/'use/app.elf')==EXPECTED
    for stage in(TEST,P/'renderer-pgo-coupled-v1'):
        d=json.loads((stage/'manifest.json').read_text())
        for n,h in d['files'].items():assert sha(stage/n)==h,n
    OUT.mkdir(exist_ok=False);REBUILD.mkdir(exist_ok=False)
    subprocess.run(['cp','-a','--reflink=auto',str(P/'load-submit-rebuild-v1/SM64'),str(TREE)],check=True)
    # Only private cloned generated object trees are removed for the clean build.
    shutil.rmtree(TREE/'.obj/sm64')
    assert not (TREE/'.obj/sm64').exists()
    sources={}
    for name,rel in INPUTS.items():
        assert sha(TREE/rel)==sha(SELECTED/name)
        text=(TREE/rel).read_bytes()
        if name=='Makefile.sm64':
            anchor=b'# Goddard\'s float sine/cosine kernel needs strict IEEE evaluation:'
            assert text.count(anchor)==1;text=text.replace(anchor,BLOCK.encode()+anchor)
        target=OUT/'sources'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(text)
        (TREE/rel).write_bytes(text)
        sources[rel]=dict(before_sha256=sha(BASE/name),after_sha256=sha(target),flat_name=name)
    profiles={}
    for n in('gfx_pc','gfx_gpu'):
        original=list((TEST/'profile-data').rglob(n+'.gcda'));assert len(original)==1
        rel='src/sm64/pgo'+PROFILE_KEY+n+'.gcda'
        for base in(OUT/'sources',TREE):
            target=base/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original[0],target)
        profiles[rel]=dict(sha256=sha(original[0]),source=str(original[0].relative_to(ROOT)))
    patch=''
    for rel,info in sources.items():
        a=(BASE/info['flat_name']).read_text();b=(OUT/'sources'/rel).read_text()
        patch+=''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='a/'+rel,tofile='b/'+rel))
    (OUT/'integration.patch').write_text(patch)
    write(OUT/'prepared.json',dict(expected_elf_sha256=EXPECTED,sources=sources,profiles=profiles,
        source_changes=[r for r,v in sources.items()if v['before_sha256']!=v['after_sha256']],
        explicit_normal=dict(PROFILE=0,PERF_DIAG=0),only_two_objects_profiled=True,private_only=True,
        evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in[TEST/'manifest.json',P/'renderer-pgo-coupled-v1/manifest.json',P/'renderer-pgo-source-review-v1/manifest.json',P/'load-submit-delivery-v1/manifest.json']}))
    shutil.copy2(__file__,OUT/'recipe-prepare.py');print(OUT)

def portable_probe():
    p=OUT/'portability-probe-v2';p.mkdir(exist_ok=False)
    record=json.loads((TEST/'commands.json').read_text());old=str(TEST/'work')
    for name in('gfx_pc','gfx_gpu'):
        cmd=[x.replace(old,str(TREE))for x in record['compile'][name]]
        obj=Path(cmd[cmd.index('-o')+1]);obj.parent.mkdir(parents=True,exist_ok=True)
        profile=TREE/'src/sm64/pgo'
        cmd += ['-fprofile-use='+str(profile),'-dumpdir',PROFILE_KEY,'-MF',str(obj.with_suffix('.d')),
                '-fprofile-partial-training','-Werror=coverage-mismatch','-Werror=missing-profile','-fdump-ipa-profile-details='+str(obj.with_suffix('.profile.txt'))]
        full=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
              '-v',f'{REBUILD}:{REBUILD}','-w',str(TREE/'src/sm64'),'--entrypoint',cmd[0],'openfpgaos-firmware',*cmd[1:]]
        with(p/(name+'.log')).open('x')as f:
            f.write(shlex.join(full)+'\n');f.flush();r=subprocess.run(full,stdout=f,stderr=subprocess.STDOUT)
        assert r.returncode==0,('profile lookup failed; preserve log',name)
        shutil.copy2(obj,p/(name+'.o'))
    write(p/'result.json',dict(passed=True,objects_byte_exact=all(sha(p/(n+'.o'))==sha(TEST/'use'/(n+'.o'))for n in('gfx_pc','gfx_gpu')),
        objects={n:sha(p/(n+'.o'))for n in('gfx_pc','gfx_gpu')},normalization='Original auxiliary identity retained only as opaque key; actual profile reads are package-local and dependency writes are explicit current object paths'))
    # Require the subsequent ordinary build to rebuild these too.
    shutil.rmtree(TREE/'.obj/sm64');print((p/'result.json').read_text())

def build():
    assert json.loads((OUT/'portability-probe-v2/result.json').read_text())['passed']
    assert not(TREE/'.obj/sm64').exists()
    cmd=['make','-j4','PROFILE=0','PERF_DIAG=0',str(TREE/'.obj/sm64/app.elf')]
    start=time.monotonic()
    with(REBUILD/'build-normal.log').open('x')as f:r=subprocess.run(cmd,cwd=TREE/'src/sm64',stdout=f,stderr=subprocess.STDOUT)
    rec=dict(command=cmd,cwd=str(TREE/'src/sm64'),returncode=r.returncode,seconds=time.monotonic()-start,
             helper_sha256=sha(Path(__file__)),clean_object_tree=True)
    if not r.returncode:
        shutil.copy2(TREE/'.obj/sm64/app.elf',REBUILD/'app.elf');rec['elf_sha256']=sha(REBUILD/'app.elf')
    write(REBUILD/'build-normal.json',rec)
    assert r.returncode==0,'Preserve failed ordinary build'
    print(dict(rec,exact_selected_elf=rec['elf_sha256']==EXPECTED))

def closure_checks():
    prep=json.loads((OUT/'prepared.json').read_text())
    d=OUT/'closure-checks';d.mkdir(exist_ok=False)
    # Only the new checkout is exposed; opaque old key must not name a real file.
    cmd=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
         '-v',f'{REBUILD}:{REBUILD}','-w',str(TREE/'src/sm64'),'--entrypoint','sh','openfpgaos-firmware',
         '-c','test ! -e "$1" && test -f "$2" && printf "original-directory-absent; packaged-profile-present\\n"',
         'profile-path-check',str(TEST/'work'),str(TREE/next(iter(prep['profiles'])))]
    with(d/'filesystem-independence.log').open('x')as f:
        f.write(shlex.join(cmd)+'\n');f.flush();subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    modes=[]
    for profile,diag,sub in[(0,0,'sm64'),(0,1,'sm64-perfdiag'),(1,0,'sm64-profile')]:
        obj=TREE/'.obj'/sub/'sm64/src/pc/gfx/gfx_gpu.o'
        cmd=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
             '-v',f'{REBUILD}:{REBUILD}','-w',str(TREE/'src/sm64'),'--entrypoint','make','openfpgaos-firmware',
             '-nB','OF_SDK_IN_CONTAINER=1',f'PROFILE={profile}',f'PERF_DIAG={diag}',str(obj)]
        log=d/f'make-profile{profile}-diag{diag}.log'
        with log.open('x')as f:
            f.write(shlex.join(cmd)+'\n');f.flush();subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
        lines=[l for l in log.read_text().splitlines()if l.startswith('riscv64-unknown-elf-gcc ')and l.endswith('gfx_gpu.c')]
        assert len(lines)==1,lines
        assert ('-fprofile-use='in lines[0])==(profile==diag==0)
        modes.append(dict(PROFILE=profile,PERF_DIAG=diag,pgo_enabled=profile==diag==0,compile=lines[0]))
    write(d/'result.json',dict(passed=True,original_training_directory_inaccessible=True,modes=modes))

def seal():
    prep=json.loads((OUT/'prepared.json').read_text());build=json.loads((REBUILD/'build-normal.json').read_text())
    assert build['returncode']==0 and sha(REBUILD/'app.elf')==EXPECTED
    assert json.loads((OUT/'closure-checks/result.json').read_text())['passed']
    for rel,v in prep['sources'].items():assert sha(OUT/'sources'/rel)==sha(TREE/rel)==v['after_sha256']
    for rel,v in prep['profiles'].items():assert sha(OUT/'sources'/rel)==sha(TREE/rel)==sha(ROOT/v['source'])==v['sha256']
    changed=prep['source_changes'];assert len(changed)==4
    check=OUT/'patch-check'
    if not check.exists():
        for rel,v in prep['sources'].items():
            q=check/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/v['flat_name'],q)
        with(OUT/'patch-check.log').open('x')as f:subprocess.run(['patch','--batch','--forward','-p1','-i',str(OUT/'integration.patch')],cwd=check,stdout=f,stderr=subprocess.STDOUT,check=True)
    for rel,v in prep['sources'].items():assert sha(check/rel)==v['after_sha256']
    logs=(REBUILD/'build-normal.log').read_text().splitlines()
    compiles=[l for l in logs if l.startswith('riscv64-unknown-elf-gcc ')and' -c 'in l]
    pgo=[l for l in compiles if'-fprofile-use='in l];assert len(pgo)==2 and all(any(l.endswith('/'+n+'.c')for n in('gfx_pc','gfx_gpu'))for l in pgo)
    assert not any('error:'in l for l in logs)
    clean=lambda line:re.sub(r'\x1b\[[0-9;]*[mK]','',line)
    warnings=Counter(clean(l)for l in logs if'warning:'in l)
    oldlogs=(P/'load-submit-rebuild-v1/build-normal.log').read_text().splitlines()
    assert warnings==Counter(clean(l)for l in oldlogs if'warning:'in l),warnings
    assert not any('gfx_pc' in l or 'gfx_gpu' in l for l in warnings)
    objects={str(f.relative_to(TREE)):sha(f)for f in(TREE/'.obj/sm64').rglob('*.o')}
    original=json.loads((TEST/'prepared.json').read_text())['baseline_objects']
    assert set(objects)==set(original)
    deltas=[n for n,h in objects.items()if original[n]!=h]
    assert sorted(Path(n).stem for n in deltas)==['gfx_gpu','gfx_pc']
    for n in('gfx_pc','gfx_gpu'):assert sha(TREE/'.obj/sm64/sm64/src/pc/gfx'/f'{n}.o')==sha(TEST/'use'/f'{n}.o')
    target=OUT/'payload/Assets/sm64/common/sm64.elf';target.parent.mkdir(parents=True);shutil.copy2(REBUILD/'app.elf',target)
    write(OUT/'rebuild-proof.json',dict(passed=True,elf_identical=True,elf_sha256=EXPECTED,all_objects=objects,only_two_changed=deltas,
        exact_clean_build_command=build['command'],cwd=build['cwd'],pgo_compiles=pgo,ordinary_compile_count=len(compiles),unchanged_warnings=dict(warnings),
        source_closure=prep['sources'],profile_closure=prep['profiles'],normal_only=True,
        evidence_sha256={str(f.relative_to(ROOT)):sha(f)for f in[REBUILD/'build-normal.log',REBUILD/'build-normal.json',REBUILD/'app.elf',OUT/'closure-checks/result.json']}))
    (OUT/'README.txt').write_text('''Private portable SM64 renderer-PGO delivery

Qualified normal ELF: fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b
The payload is byte-identical to the app tested in renderer-pgo-v1 and
renderer-pgo-coupled-v1. No hardware, kernel, assets or old diagnostic ELF is
included or overwritten. Requires the matching LOD5 core for acceleration;
CPU fallback source remains unchanged. Hardware qualification is separate.

Apply to a pinned F11 checkout only after checking the source preimage hashes
in prepared.json. Apply integration.patch from its root, then copy sources/src/
sm64/pgo into src/sm64/pgo preserving the complete nested directory structure.
The patch changes the same three renderer files as load-submit-delivery-v1,
plus one normal-only Makefile block. Eight pinned source inputs and two raw
GCC profile files are included in sources/. The gcda files are byte-identical
to the original real measured data; no counters, IDs or summaries were edited.

From the checkout root, the exact ordinary normal build is:
  make -C src/sm64 -j4 PROFILE=0 PERF_DIAG=0 "$(pwd)/.obj/sm64/app.elf"
A clean .obj/sm64 tree was used for the private proof. The original SDK Docker
build path, GCC13.2, ISA/FP/source flags and linker order remain in use. Only
gfx_pc.c and gfx_gpu.c consume profile feedback. Other modes, including the
existing PROFILE/diagnostic builds, receive no PGO flags. Missing or mismatched
profiles are compile errors; changing these source functions requires new
qualified profiles, not suppressing the errors.

The long original training path in SM64_RENDERER_PGO_KEY is an opaque GCC
auxiliary identity, NOT a required host directory. GCC13 hashes this identity
into static-function IDs. -fprofile-use resolves it BELOW the checkout-local
pgo directory; -MF and the explicit IPA dump filename keep generated outputs
in the current object tree. The build container could not access the original
training directory. This new checkout reproduced both objects and the full
ELF exactly. Keep -fdump-ipa-profile-details: although diagnostic in intent,
omitting it changed this compiler's inlining; restoring the exact original
option restored the qualified bytes. Dumps are local build artifacts only.

Qualification: all721 scene/geometry/culling records and command counts,
eight ordered renderer streams, and16 warm/cold actual RTL color/depth buffers
match. PGO saves0.62382375ms warm and1.09880875ms cold. Original matched warm
37.82757375 ->34.02665625ms is10.0480% less modeled time. CPU/cache timings are
modeled with actual GPU/LOAD/memory RTL, not Pocket FPS or a25FPS claim.
Training and evaluation use the same deterministic Bob-omb workload; this is
in-sample. GCC freestanding data retains runs=0,sum_max=0 and hotness threshold0;
these metadata are documented, never invented or patched. The optimized ELF
has no training instrumentation. Other levels and fault modes have inherited
source/component evidence, not a new full-game equivalence proof.

Earlier failed portability attempts are preserved separately. This local
app-only ZIP is not deployment or public redistribution. Production checkouts,
siblings, SD cards and the older selected diagnostic ELF were not modified.
''')
    archive=OUT/'sm64-renderer-pgo-app.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9)as z:
        info=zipfile.ZipInfo('Assets/sm64/common/sm64.elf',(2026,10,7,0,0,0));info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,target.read_bytes())
    with zipfile.ZipFile(archive)as z:assert z.namelist()==['Assets/sm64/common/sm64.elf']and z.read(z.namelist()[0])==target.read_bytes()
    shutil.copy2(__file__,OUT/'recipe-final.py')
    proofs=[TEST/'manifest.json',P/'renderer-pgo-coupled-v1/manifest.json',P/'renderer-pgo-source-review-v1/manifest.json',P/'selected-performance-summary-v3/manifest.json',P/'load-submit-delivery-v1/manifest.json']
    write(OUT/'manifest.json',dict(passed=True,private_only=True,deployed=False,elf_sha256=EXPECTED,rebuild_exact=True,
        files={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()and'patch-check'not in f.parts},
        evidence_sha256={str(f.relative_to(ROOT)):sha(f)for f in proofs}))
    print(sha(OUT/'manifest.json'));print('ZIP',sha(archive))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','portable_probe','build','closure_checks','seal']);a=p.parse_args();globals()[a.action]()
