#!/usr/bin/env python3
"""Publish an independently qualified F6 trial, preserving the retained A core."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase4/release'
PARENT=ROOT/'docs/measurements/os30-fps25-20261005/phase2/QUALIFIED.json'
PARENT_SHA='bc88095dc147edcc70151da53d7d62b4fbc07f0f10e4f41b30873bd294b4c2e7'
spec=importlib.util.spec_from_file_location('guarded_publisher',Path(__file__).with_name('phase2_publish.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.OUT=OUT;m.QUALIFIED=OUT/'QUALIFIED.json'
m.OLD_NORMAL='71cba6356e3f95a46d4d1993e059424bed3e6fe3b5e3046a7f19d45b8226f19b'
m.OLD_FPS='a3feb3f8415929e73f380c37eb08ed8162d05ae83e3664dc60e30c73541b1260'
m.ELF_DESTINATIONS=(
    ('app.elf','.obj/sm64/app.elf',m.OLD_NORMAL,'normal_elf_sha256'),
    ('diagnostic.elf','.obj/sm64-perfdiag/app.elf',m.OLD_FPS,'diagnostic_elf_sha256'),
    ('diagnostic.elf','build/pocket/sm64/Assets/sm64/common/sm64.elf',m.OLD_FPS,'diagnostic_elf_sha256'))


def selection():
    m.require(m.sha(PARENT)==PARENT_SHA,'F5 qualification changed')
    r=m.read(m.QUALIFIED);h=m.sha(m.QUALIFIED)
    m.require(r['qualified_for_device_trial'] is True and r['target_verified'] is False,'Wrong trial qualification state')
    m.require(r['independent_review_passed'] is True,'Independent qualification review missing')
    m.require(re.fullmatch(r'[a-z0-9_-]+',r['candidate']), 'Invalid candidate identifier')
    relative=Path(r['candidate_directory'])
    m.require(not relative.is_absolute() and '..' not in relative.parts,'Unsafe candidate directory')
    directory=ROOT/relative
    before=dict(m.read(PARENT)['source_tree_sha256']);m.require(len(before)==6747,'Unexpected F5 source closure')
    expected=dict(before)
    m.require(set(r['source_sha256'])==set(m.SOURCES),'Missing reviewed source input')
    for name,rel in m.SOURCES.items():
        m.require(m.sha(m.relative_file(directory,name))==r['source_sha256'][name],('Candidate source changed',name))
        expected[rel]=r['source_sha256'][name]
    m.require(expected==r['source_tree_sha256'],'Candidate tree differs from qualified composition')
    for name,key in [('app.elf','normal_elf_sha256'),('diagnostic.elf','diagnostic_elf_sha256')]:
        m.require(m.sha(m.relative_file(directory,name))==r[key],('Candidate executable changed',name))
    evidence=r['evidence_sha256'];m.require(evidence and evidence.get(str(PARENT.relative_to(ROOT)))==PARENT_SHA,'Parent not bound')
    m.require(evidence.get(str(Path(__file__).relative_to(ROOT)))==m.sha(Path(__file__)),'Publisher changed')
    for rel,digest in evidence.items():
        m.require(m.sha(m.relative_file(ROOT,rel))==digest,('Qualification evidence changed',rel))
    m.require(m.sha(m.QUALIFIED)==h,'Qualification changed during selection')
    m.local_retained()
    return r,h,directory,before,expected


m.selection=selection


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['plan','publish','deploy','verify']);a=p.parse_args()
    if a.action=='plan':result=m.plan()
    elif a.action=='publish':result=m.publish()
    elif a.action=='deploy':result=m.deploy_sd()
    else:
        r,h,directory,before,expected=selection();m.verify_local(r,h,directory,before,expected,record=True)
        result={'local_verified':True,'candidate':r['candidate'],'target_verified':False}
        if (OUT/'sd-deployed.json').exists():
            record=m.read(OUT/'sd-deployed.json');m.require(record['qualified_sha256']==h,'Wrong SD deployment record')
            m.card_identity(require_rw=False)
            m.require(m.sha(m.CARD/'Assets/sm64/common/sm64.elf')==r['diagnostic_elf_sha256'],'SD readback changed')
            result['sd_verified']=True
    print(json.dumps(result,indent=2))
