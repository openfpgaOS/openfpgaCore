#!/usr/bin/env python3
"""Guarded local promotion. prepare is private; apply/rollback require an exact manifest.

No SD, Quake2, build invocation, clock change, seed change or source generation.
Do not edit destinations concurrently: ordinary filesystems have no compare-and-
rename primitive. Each file is rechecked immediately before atomic replacement;
the durable intent journal also covers interruption between rename and commit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
import phase10_promotion_plan_v7 as plan_recipe

ROOT=plan_recipe.ROOT; P=plan_recipe.P; SM64=plan_recipe.SM64; QUAKE=plan_recipe.QUAKE
PLAN=P/'promotion-plan-v7'; BACKUP=P/'promotion-rollback-v7'
NORMAL=P/'normal-build-integration-v9'; FIT=P/'compact-lod5-core-v12'
OUT=P/'promotion-payload-v7'; TX=P/'promotion-transaction-v7'
CPU_SHA='482e13475702808f40d5bad7f6b6e6af9331ed5c895d99d7bf4630a3b047a303'
APP_SHA='bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def require(ok, message):
    if not ok: raise RuntimeError(message)

def safe_path(path, base, missing=False):
    path=Path(path); base=Path(base)
    require(path.is_absolute() and '..' not in path.parts and path.is_relative_to(base),f'Invalid path: {path}')
    # Check the entire absolute ancestry, including the workspace anchor.
    for q in reversed([path,*path.parents]):
        if not q.exists():
            require(missing and not q.is_symlink(),f'Missing path: {q}')
            continue
        mode=q.lstat().st_mode
        require(not stat.S_ISLNK(mode),f'Symlink rejected: {q}')
        if q!=path: require(stat.S_ISDIR(mode),f'Non-directory ancestor: {q}')
    return path

def file_state(path, base, missing=False):
    p=safe_path(path,base,missing)
    if not p.exists(): return None
    require(stat.S_ISREG(p.lstat().st_mode),f'Not a regular file: {p}')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd); require(stat.S_ISREG(st.st_mode),f'Not regular: {p}')
        h=hashlib.sha256()
        while True:
            b=os.read(fd,1024*1024)
            if not b: break
            h.update(b)
        return dict(sha256=h.hexdigest(),mode=stat.S_IMODE(st.st_mode))
    finally: os.close(fd)

def expect(path, base, digest, mode=None):
    value=file_state(path,base,missing=digest is None)
    require((value is None) if digest is None else value is not None and value['sha256']==digest,
            f'File guard mismatch: {path}')
    if value is not None and mode is not None: require(value['mode']==mode,f'Mode drift: {path}')
    return value

def save(path, data):
    with path.open('x') as f:
        json.dump(data,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())

def directory_sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def flat_manifest(directory):
    m=read(directory/'manifest.json')
    for name,digest in m.items(): expect(directory/name,directory,digest)

def source_names(base):
    names={'Makefile','Mario64.z64'}
    for top in ('src','tools'):
        safe_path(base/top,base)
        for p in (base/top).rglob('*'):
            if any(v in ('.git','__pycache__') for v in p.relative_to(base).parts):continue
            safe_path(p,base)
            if p.is_file():names.add(str(p.relative_to(base)))
    return names

def destination_base(path):
    p=Path(path)
    if p.is_relative_to(ROOT):return ROOT
    require(p.is_relative_to(SM64),f'Unapproved destination: {p}')
    return SM64

def guards(before, rows=(), after=False):
    updates={r['destination']:r for r in rows} if after else {}
    require(len(before['core_production_sha256'])==132,'Unexpected core guard count')
    require(len(before['sm64_source_sha256'])==6747,'Unexpected SM64 guard count')
    for base,key in ((ROOT,'core_production_sha256'),(SM64,'sm64_source_sha256')):
        for n,digest in before[key].items():
            row=updates.get(str(base/n));expect(base/n,base,row['after_sha256'] if row else digest)
    require(source_names(SM64)==set(before['sm64_source_sha256']),'SM64 source inventory changed')
    for n,digest in before['retained_artifacts'].items():
        p=Path(n);base=SM64 if p.is_relative_to(SM64) else QUAKE
        row=updates.get(n);expect(p,base,row['after_sha256'] if row else digest)
    for n,digest in before['quake_source_sha256'].items():expect(Path(n),QUAKE,digest)
    for row in rows:
        key='after' if after else 'before'
        expect(Path(row['destination']),destination_base(row['destination']),row[key+'_sha256'],row[key+'_mode'])

def qualify_fit():
    record=read(FIT/'result.json');prep=read(FIT/'prepared.json')
    require(record['whole_core_fit_successful'] and record['seed']==2 and not record['seed_sweep'],'Fit/seed gate failed')
    require(record['configured_mhz']==100 and prep['configured_mhz']==100,'Clock changed')
    require(set(record['main_clock_slow_fmax_mhz'])=={'0','85'} and
            min(record['main_clock_slow_fmax_mhz'].values())>90,'Both slow corners must exceed90MHz')
    require(record['worst_timing']['hold']>0,'Positive hold required')
    for key in ('map','fit','asm','sta'):require(record['returncodes'][key]==0,f'{key} failed')
    require(record['prepared_sha256']==sha(FIT/'prepared.json'),'Fit source binding differs')
    review=read(FIT/'independent-review.json')
    require(review['passed_for_resource_fit'] and review['prepared_sha256']==sha(FIT/'prepared.json'),'Independent gate differs')
    evidence=read(FIT/'final-manifest.json')['evidence_sha256']
    for n,h in evidence.items():expect(ROOT/n,ROOT,h)
    for n,h in prep['source_sha256'].items():
        expect(FIT/'frozen'/n,FIT,h);expect(FIT/'fit-seed2/frozen'/n,FIT,h)
    results_path=FIT/'fit-seed2/results.json'
    require(str(results_path.relative_to(ROOT)) in evidence,'Final manifest does not bind assembler result')
    expect(results_path,FIT,evidence[str(results_path.relative_to(ROOT))])
    results=read(results_path);require(len(results)==1,'Multiple fit jobs')
    job=Path(results[0]['directory']);safe_path(job,FIT/'fit-seed2/jobs')
    require(results[0]['seed']==2 and results[0]['variant']=='os30','Wrong fitted job')
    qsf=job/'ap_core.qsf';require(re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)==['2'],'Effective seed mismatch')
    raw=job/'output_files/ap_core.rbf';file_state(raw,FIT)
    require(raw.stat().st_size>0,'Empty assembler output')
    require(sha(raw)==results[0]['hashes']['rbf'],'Assembler raw hash differs from selected result')
    asm=job/'output_files/ap_core.asm.rpt';file_state(asm,FIT)
    require(re.search(r';\s*Assembler Status\s*;\s*Successful',asm.read_text()) is not None and
            str(raw) in asm.read_text(),'Raw file absent from successful selected assembler report')
    # Re-read the actual bound STA report, rather than only trusting summary fields.
    sta=job/'output_files/ap_core.sta.rpt';expect(sta,FIT,evidence[str((FIT/'fit-seed2/results/pocket-os30-s2/ap_core.sta.rpt').relative_to(ROOT))])
    for temp in (0,85):
        part=sta.read_text().split(f'; Slow 1100mV {temp}C Model Fmax Summary',1)[1].split('Model Setup Summary',1)[0]
        match=re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]',part)
        require(match and float(match[1])==record['main_clock_slow_fmax_mhz'][str(temp)],'STA frequency differs')
    return record,raw,asm

def check_closure():
    flat_manifest(NORMAL)
    n=read(NORMAL/'prepared.json');r=read(NORMAL/'result.json');fit=read(FIT/'prepared.json')
    require(n['selected_prepared_sha256']==sha(FIT/'prepared.json'),'Normal build targets another fit')
    require(len(n['overrides'])==8 and len(n['proposed_sha256'])==31,'Normal source allowlist shape differs')
    require(r['passed'] and r['selected_map_source_exact'] and r['other_variants_unchanged'] and r['normal_and_frozen_generators_agree'],'Normal closure gate failed')
    require(r['prepared_sha256']==sha(NORMAL/'prepared.json'),'Normal result binding differs')
    for item in n['inventory']:
        require(item['selected_sha256']==fit['source_sha256'][item['selected_path']],'Mapped source differs')
        if 'proposed_path' in item:
            expect(NORMAL/'proposed'/item['proposed_path'],NORMAL,item['selected_sha256'])
    require(n['proposed_sha256']['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']==CPU_SHA,'Wrong CPU')
    return n

def prepare():
    require(not OUT.exists() and not OUT.is_symlink(),'Preserve existing payload')
    flat_manifest(PLAN);flat_manifest(BACKUP)
    plan=read(PLAN/'plan.json');before=read(PLAN/'retained-before.json');backup=read(BACKUP/'rollback.json')
    require(backup['plan_manifest_sha256']==sha(PLAN/'manifest.json'),'Rollback refers to another plan')
    require(backup['current_guard_snapshot_sha256']==sha(PLAN/'retained-before.json'),'Rollback guards differ')
    n=check_closure();fit,raw,asm=qualify_fit();guards(before)
    expected=plan['hardware_source_files']+plan['sm64_source_files']
    require(len(expected)==34 and len(backup['entries'])==38,'Wrong destination count')
    require(len({r['destination'] for r in backup['entries']})==38,'Duplicate destination')
    source_map={r['destination']:dict(r) for r in expected}
    require({str(ROOT/k) for k in n['proposed_sha256']}=={r['destination'] for r in plan['hardware_source_files']},'Hardware allowlist differs')
    for k,v in n['proposed_sha256'].items():require(source_map[str(ROOT/k)]['after_sha256']==v,'Hardware output differs')
    software=read(plan_recipe.APP/'sources.json')
    require({str(SM64/k) for k in software['changed_files']}=={r['destination'] for r in plan['sm64_source_files']},'Software allowlist differs')
    for k in software['changed_files']:
        require(source_map[str(SM64/k)]['after_sha256']==software['inputs'][k]['after_sha256'],'Software output differs')
    require(plan['artifact_destinations_after_selection']['normal_app']==[str(SM64/'.obj/sm64/app.elf'),str(SM64/'build/pocket/sm64/Assets/sm64/common/sm64.elf')],'App destinations differ')
    require(plan['artifact_destinations_after_selection']['core']==[str(SM64/'runtime/pocket/os30.rbf_r'),str(SM64/'build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r')],'Core destinations differ')
    app=Path(plan['tested_app_elf']);expect(app,plan_recipe.APP,APP_SHA)
    OUT.mkdir();shutil.copy2(__file__,OUT/'recipe.py')
    (OUT/'artifacts').mkdir();(OUT/'tools').mkdir()
    raw_copy=OUT/'artifacts/ap_core.rbf';shutil.copy2(raw,raw_copy)
    csource=ROOT/'tools/reverse_bits.c';binary=OUT/'tools/reverse_bits'
    cmd=['cc','-O2','-Wall','-Wextra','-o',str(binary),str(csource)]
    subprocess.run(cmd,check=True,capture_output=True)
    reverse=OUT/'artifacts/os30.rbf_r'
    subprocess.run([str(binary),str(raw_copy),str(reverse)],check=True,capture_output=True)
    table=bytes(int(f'{i:08b}'[::-1],2) for i in range(256))
    require(reverse.read_bytes()==raw_copy.read_bytes().translate(table),'Reverse tool/table mismatch')
    require(reverse.read_bytes().translate(table)==raw_copy.read_bytes(),'Bit-reversal involution failed')
    domain=OUT/'tools/all-bytes.bin';domain.write_bytes(bytes(range(256)));domain_out=OUT/'tools/all-bytes-reversed.bin'
    subprocess.run([str(binary),str(domain),str(domain_out)],check=True,capture_output=True)
    require(domain_out.read_bytes()==table,'Reverse tool domain mismatch')
    require(sha(raw)==sha(raw_copy),'Assembler output changed while copying')
    for kind in ('normal_app','core'):
        for dest in plan['artifact_destinations_after_selection'][kind]:
            source_map[dest]=dict(source=str(app if kind=='normal_app' else reverse),after_sha256=APP_SHA if kind=='normal_app' else sha(reverse))
    require(set(source_map)=={r['destination'] for r in backup['entries']},'Destination allowlists disagree')
    rows=[];absent_dirs=set()
    for i,old in enumerate(backup['entries']):
        dest=Path(old['destination']);base=destination_base(dest);expected_row=source_map[str(dest)]
        prior=expect(dest,base,old['before_sha256'],old['prior_mode'])
        source=Path(expected_row['source']);state=file_state(source,ROOT)
        require(state['sha256']==expected_row['after_sha256'],'Selected source drift')
        mode=prior['mode'] if prior else state['mode']
        require(mode&0o7000==0,'Special permission bits rejected')
        target=OUT/'files'/f'{i:02d}';target.parent.mkdir(exist_ok=True);shutil.copyfile(source,target);os.chmod(target,mode)
        rollback=None
        if prior:
            oldfile=BACKUP/old['rollback_file'];expect(oldfile,BACKUP,prior['sha256'],prior['mode'])
            target_old=OUT/'before'/f'{i:02d}';target_old.parent.mkdir(exist_ok=True);shutil.copy2(oldfile,target_old)
            rollback=str(target_old.relative_to(OUT))
        for parent in dest.parents:
            if parent==base:break
            if not parent.exists():absent_dirs.add(str(parent))
        rows.append(dict(id=i,destination=str(dest),payload=str(target.relative_to(OUT)),
            before_sha256=prior['sha256'] if prior else None,before_mode=prior['mode'] if prior else None,
            after_sha256=state['sha256'],after_mode=mode,rollback=rollback))
    guards(before,rows)
    artifacts={'Assets/sm64/common/sm64.elf':app,'Cores/ThinkElastic.SM64/os30.rbf_r':reverse}
    archive=OUT/'sm64-incremental-local-v7.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,source in sorted(artifacts.items()):
            info=zipfile.ZipInfo(name,(2026,10,7,0,0,0));info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,source.read_bytes())
    with zipfile.ZipFile(archive) as z:
        require(set(z.namelist())==set(artifacts),'Unexpected ZIP member')
        for name,source in artifacts.items():require(z.read(name)==source.read_bytes(),'ZIP payload differs')
    shutil.copy2(PLAN/'retained-before.json',OUT/'guards.json')
    save(OUT/'prepared.json',dict(entries=rows,absent_parent_directories=sorted(absent_dirs,key=lambda s:(len(Path(s).parts),s)),
        fit_result=fit,experimental_at_configured_100mhz=fit['worst_timing']['setup']<0,
        raw_assembler_sha256=sha(raw),reversed_sha256=sha(reverse),app_sha256=APP_SHA,
        reverse_tool=dict(command=cmd,source_sha256=sha(csource),binary_sha256=sha(binary),all256_bytes_and_involution_pass=True),
        dependencies_sha256={str(p.relative_to(ROOT)):sha(p) for p in [PLAN/'manifest.json',BACKUP/'manifest.json',NORMAL/'manifest.json',FIT/'final-manifest.json',FIT/'result.json',FIT/'prepared.json',
            P/'cpu-os30-repro-v8/manifest.json',P/'cpu-os30-regenerate-v8/manifest.json',plan_recipe.APP/'manifest.json',raw,asm,csource]},
        no_sd_or_quake_destinations=True,source_closure_qualified=True,production_writes=0))
    (OUT/'README.txt').write_text('Prepared local SM64/source transaction; not yet applied.\nExactly38 guarded outputs; ZIP has only the selected SM64 ELF and core.\nConfigured clock remains100MHz. A >90MHz comparative fit with negative100MHz setup slack is experimental, not100MHz timing closure or a Pocket FPS result.\nNo SD, Quake2, OS, loader, assets or saves are installed by this helper.\napply requires this manifest SHA256 and no concurrent destination edits. Durable intent records cover interruption; rollback restores only matching recorded outputs.\n')
    save(OUT/'manifest.json',{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()})
    # Backups must be durable before an apply transaction can be started.
    for p in OUT.rglob('*'):
        if p.is_file():
            fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
    for p in sorted([OUT,*[x for x in OUT.rglob('*') if x.is_dir()]],key=lambda p:len(p.parts),reverse=True):directory_sync(p)
    directory_sync(OUT.parent)
    print('Prepared',OUT,'manifest',sha(OUT/'manifest.json'))

def load_payload(digest):
    expect(OUT/'manifest.json',OUT,digest);flat_manifest(OUT)
    p=read(OUT/'prepared.json')
    require(len(p['entries'])==38 and len({x['destination'] for x in p['entries']})==38,'Payload shape differs')
    for n,h in p['dependencies_sha256'].items():expect(ROOT/n,ROOT,h)
    require(sha(Path(__file__))==sha(OUT/'recipe.py'),'Helper differs from frozen recipe')
    check_closure();qualify_fit()
    for row in p['entries']:
        destination_base(row['destination'])
        expect(OUT/row['payload'],OUT,row['after_sha256'],row['after_mode'])
        if row['rollback']:expect(OUT/row['rollback'],OUT,row['before_sha256'],row['before_mode'])
    return p,read(OUT/'guards.json')

def journal(tx, event, **kw):
    safe_path(tx,tx.parent)
    path=tx/'journal.jsonl';safe_path(path,tx,missing=True)
    if path.exists():file_state(path,tx)
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as f:
        f.write(json.dumps(dict(event=event,**kw),sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
    directory_sync(tx)

def atomic_file(dest, base, data, mode, expected_sha, expected_mode, tx, row_id):
    # All ancestors already exist and have been checked. Hold the parent dirfd
    # so the rename cannot follow a substituted destination symlink.
    safe_path(dest,base,missing=True)
    parentfd=os.open(dest.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    temp=None
    try:
        fd,temp=tempfile.mkstemp(prefix='.os30-promotion-',dir=dest.parent)
        with os.fdopen(fd,'wb') as f:
            os.fchmod(f.fileno(),mode);f.write(data);f.flush();os.fsync(f.fileno())
        expect(dest,base,expected_sha,expected_mode)
        os.replace(Path(temp).name,dest.name,src_dir_fd=parentfd,dst_dir_fd=parentfd)
        temp=None;os.fsync(parentfd)
    finally:
        if temp is not None:
            try:os.unlink(temp)
            except FileNotFoundError:pass
        os.close(parentfd)

def apply(digest):
    p,before=load_payload(digest);rows=p['entries'];guards(before,rows)
    require(not TX.exists() and not TX.is_symlink(),'Transaction already exists; inspect/rollback instead of reapplying')
    for name in p['absent_parent_directories']:require(not Path(name).exists() and not Path(name).is_symlink(),f'New parent already exists: {name}')
    TX.mkdir();directory_sync(TX.parent)
    save(TX/'transaction.json',dict(payload_manifest_sha256=digest,helper_sha256=sha(Path(__file__)),destination_count=38))
    journal(TX,'begin')
    try:
        for name in p['absent_parent_directories']:
            directory=Path(name);base=destination_base(directory);safe_path(directory.parent,base)
            journal(TX,'mkdir_intent',path=name);directory.mkdir(mode=0o755);directory_sync(directory.parent)
            st=directory.stat();journal(TX,'mkdir_commit',path=name,device=st.st_dev,inode=st.st_ino)
        for row in rows:
            dest=Path(row['destination']);base=destination_base(dest)
            expect(dest,base,row['before_sha256'],row['before_mode'])
            data=(OUT/row['payload']).read_bytes();require(hashlib.sha256(data).hexdigest()==row['after_sha256'],'Payload changed during apply')
            journal(TX,'replace_intent',id=row['id'],destination=str(dest),before_sha256=row['before_sha256'],after_sha256=row['after_sha256'])
            atomic_file(dest,base,data,row['after_mode'],row['before_sha256'],row['before_mode'],TX,row['id'])
            expect(dest,base,row['after_sha256'],row['after_mode']);journal(TX,'replace_commit',id=row['id'])
        guards(before,rows,after=True);check_closure()
        save(TX/'postcheck.json',dict(passed=True,all38_outputs_exact=True,full6747_source_inventory_exact=True,all132_core_guards_plus_reviewed_payload_exact=True,
            OS_loader_Quake2_unchanged=True,source_closure_exact=True,sd_writes=0,experimental_at_100mhz=p['experimental_at_configured_100mhz']))
        journal(TX,'complete');print('Applied local transaction',TX)
    except BaseException as error:
        journal(TX,'apply_failed',error=str(error))
        print('Partial transaction preserved. Run the guarded rollback command with the same manifest; do not force overwrite.',file=__import__('sys').stderr)
        raise

def rollback(digest):
    p,before=load_payload(digest)
    file_state(TX/'transaction.json',TX);file_state(TX/'journal.jsonl',TX)
    transaction=read(TX/'transaction.json')
    require(transaction['payload_manifest_sha256']==digest and transaction['helper_sha256']==sha(Path(__file__)) and transaction['destination_count']==38,'Wrong transaction binding')
    log=(TX/'journal.jsonl').read_bytes()
    # A crash during a journal write can leave an incomplete final line. No
    # action follows until that line is fsynced, so ignore only that tail.
    complete=log if log.endswith(b'\n') else log[:log.rfind(b'\n')+1]
    events=[json.loads(line) for line in complete.splitlines()]
    require(all(e.get('id') in {r['id'] for r in p['entries']} for e in events if 'id' in e),'Journal contains unknown output')
    if complete!=log:
        # Remove only the incomplete final record before appending recovery
        # records. Otherwise later JSON lines would concatenate to that tail.
        fd=os.open(TX/'journal.jsonl',os.O_WRONLY|os.O_NOFOLLOW)
        try:os.ftruncate(fd,len(complete));os.fsync(fd)
        finally:os.close(fd)
        journal(TX,'discarded_incomplete_tail',bytes=len(log)-len(complete),sha256=hashlib.sha256(log[len(complete):]).hexdigest())
    ids=[]
    for event in events:
        if event['event']=='replace_intent' and event['id'] not in ids:ids.append(event['id'])
    rows={r['id']:r for r in p['entries']}
    for i in reversed(ids):
        row=rows[i];dest=Path(row['destination']);base=destination_base(dest)
        current=file_state(dest,base,missing=True)
        old=None if row['before_sha256'] is None else dict(sha256=row['before_sha256'],mode=row['before_mode'])
        new=dict(sha256=row['after_sha256'],mode=row['after_mode'])
        if current==old:
            journal(TX,'rollback_already_before',id=i);continue
        require(current==new,f'Rollback stops on intervening edit: {dest}')
        journal(TX,'rollback_intent',id=i)
        if old is None:
            expect(dest,base,row['after_sha256'],row['after_mode']);dest.unlink();directory_sync(dest.parent)
        else:
            data=(OUT/row['rollback']).read_bytes();require(hashlib.sha256(data).hexdigest()==row['before_sha256'],'Backup drift')
            atomic_file(dest,base,data,row['before_mode'],row['after_sha256'],row['after_mode'],TX,i)
        expect(dest,base,row['before_sha256'],row['before_mode']);journal(TX,'rollback_commit',id=i)
    # Remove only directories this transaction successfully created, still the
    # same inode and empty. Never recursively delete a user directory.
    for event in reversed(events):
        if event['event']!='mkdir_commit':continue
        d=Path(event['path']);safe_path(d,destination_base(d),missing=True)
        if not d.exists():continue
        st=d.stat();require((st.st_dev,st.st_ino)==(event['device'],event['inode']),f'Changed directory: {d}')
        require(not any(d.iterdir()),f'Nonempty new directory: {d}')
        d.rmdir();directory_sync(d.parent);journal(TX,'rollback_directory',path=str(d))
    guards(before,p['entries']);journal(TX,'rollback_complete');print('Rollback verified',TX)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','apply','rollback'))
    parser.add_argument('--manifest-sha256')
    args=parser.parse_args()
    if args.action=='prepare':
        require(args.manifest_sha256 is None,'prepare has no prior payload manifest');prepare()
    else:
        require(args.manifest_sha256 is not None and re.fullmatch('[0-9a-f]{64}',args.manifest_sha256),'Exact reviewed payload manifest SHA256 required')
        globals()[args.action](args.manifest_sha256)
