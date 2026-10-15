#!/usr/bin/env python3
"""Rebuild one staged component into a new output; no sweep or deployment."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
import package


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('component',choices=['app','core','core-source']);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--prepare-only',action='store_true');p.add_argument('--quartus-bin',type=Path)
    a=p.parse_args();source=package.unlinked(a.source);output=package.unlinked(a.output)
    if a.component=='core-source' and not a.prepare_only:
        p.error('Unqualified core-source supports --prepare-only; no release build is selected')
    manifest=package.check_stage(source,a.component)
    if output.exists() or output==source or source in output.parents:raise ValueError('New output outside source required')
    output.mkdir(parents=True);work=output/'source';shutil.copytree(source,work)
    package.check_stage(work,a.component)
    receipt=dict(component=a.component,component_manifest_sha256=package.sha(package.HERE/a.component/'manifest.json'),
                 source_sha256={n:v['sha256']for n,v in manifest['source'].items()},deployed=False,commands=[],returncodes={})
    if a.component!='app':receipt['synthesis_settings']=manifest['synthesis_settings']
    def save(): (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    save()
    if a.prepare_only:return
    env=dict(os.environ)
    if a.component=='app':
        version=subprocess.check_output(['riscv64-elf-gcc','--version'],text=True).splitlines()[0]
        if version!=manifest['compiler']:raise ValueError('Compiler differs: '+version)
        env['SOURCE_DATE_EPOCH']=str(manifest['source_date_epoch'])
        obj=output/'obj';target=obj/'app.elf'
        phases=[('app',['make','-C',str(work/'src/quake2'),'-j4','USE_SDK_CONTAINER=0',f'OBJ_DIR={obj}',str(target)],work)]
    else:
        if a.quartus_bin is None:p.error('--quartus-bin is required for a core build')
        version=subprocess.check_output([str(a.quartus_bin/'quartus_sh'),'--version'],text=True)
        if 'Version 25.1std.0 Build 1129' not in version:raise ValueError('Quartus version differs')
        project=work/'src/fpga/targets/pocket';qsf=project/'ap_core.qsf'
        text,count=re.subn(r'^set_global_assignment -name SEED \d+\s*$',f"set_global_assignment -name SEED {manifest['selected_seed']}",qsf.read_text(),flags=re.M)
        if count!=1:raise ValueError('Expected one saved seed')
        qsf.write_text(text);receipt['seed_override']=manifest['selected_seed'];receipt['build_qsf_sha256']=package.sha(qsf)
        phases=[(name,[str(a.quartus_bin/('quartus_'+name)),'ap_core'],project)for name in ['map','fit','asm','sta']]
        phases.append(('corners',[str(a.quartus_bin/'quartus_sta'),'-t',str(package.HERE/'sweep/recipes/report_sweep_timing.tcl'),'ap_core',str(output/'timing')],project))
        target=output/'os30.rbf_r'
    receipt['compiler']=version;save()
    for phase,command,cwd in phases:
        receipt['commands'].append(dict(phase=phase,argv=command,cwd=str(cwd)));save()
        with (output/(phase+'.log')).open('w') as log:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
        receipt['returncodes'][phase]=result.returncode;save()
        if a.component=='core' and phase=='asm' and result.returncode==0:
            table=bytes(int(f'{n:08b}'[::-1],2)for n in range(256))
            target.write_bytes((project/'output_files/ap_core.rbf').read_bytes().translate(table))
        if result.returncode:
            receipt['note']='Nonzero timing is a failure at configured100MHz even if selected seed estimate exceeds90MHz.'
            save();raise SystemExit(result.returncode)
    receipt['artifact_sha256']=package.sha(target)
    receipt['matches_recorded_artifact']=receipt['artifact_sha256']==manifest['expected_elf_sha256' if a.component=='app' else 'expected_artifact_sha256']
    save()
    if a.component=='app' and not receipt['matches_recorded_artifact']:raise ValueError('Clean app ELF differs')


if __name__=='__main__':main()
