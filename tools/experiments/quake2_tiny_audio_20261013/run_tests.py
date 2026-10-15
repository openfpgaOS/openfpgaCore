#!/usr/bin/env python3
"""Run packaged owned tests against a verified staged source, outside the bundle."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
sys.dont_write_bytecode=True
import package


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('suite',choices=['app','mixer','cache','integration','contention','worker','write-path'])
    p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--core-source',action='store_true',help='Use the unqualified core-source archive.')
    a=p.parse_args();source=package.unlinked(a.source);out=package.unlinked(a.output)
    if a.core_source and a.suite=='app':p.error('--core-source does not apply to app tests')
    component='app' if a.suite=='app' else 'core-source' if a.core_source else 'core'
    package.check_stage(source,component)
    if out.exists():raise ValueError('New test output required')
    out.parent.mkdir(parents=True,exist_ok=True)
    here=package.HERE
    if a.suite=='app':
        out.mkdir()
        # Native6's unchanged host fixture predates the optional mixer MMIO.
        # Overlay only its expanded test into a fresh test copy, never sources.
        work=out/'source';shutil.copytree(source,work)
        shutil.copy2(here/'tests/app/quake2_soundbackend_test.c',work/'tools/tests/quake2_soundbackend_test.c')
        for name,args in [('renderer',[]),('geometry',[]),('textures',[str(source)])]:
            with (out/(name+'.log')).open('w')as log:
                subprocess.run([sys.executable,str(work/'tools'/('check_quake2_'+name+'.py')),*args],cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True)
    elif a.suite=='mixer':
        subprocess.run([sys.executable,str(here/'tests/mixer/run.py'),str(source/'src/fpga/common/os30/audio_mixer.v'),str(out)],check=True)
    elif a.suite=='cache':
        harness=out.with_name(out.name+'-harness')
        if harness.exists():raise ValueError('Cache harness destination exists')
        shutil.copytree(here/'tests/cache',harness)
        shutil.copy2(source/'src/fpga/common/os30/audio_sample_cache.v',harness/'audio_sample_cache.v')
        subprocess.run([sys.executable,str(harness/'run.py'),str(out)],check=True)
    elif a.suite=='write-path':
        fixture=here/'tests/write-path'
        # The staged core is already verified against the selected archive;
        # the replay separately checks every recorded fixture input hash.
        subprocess.run([sys.executable,str(fixture/'replay.py'),str(fixture),str(out)],check=True)
    elif a.suite in ('integration','worker'):
        subprocess.run([sys.executable,str(here/'tests'/a.suite/'run_portable.py'),'--core',str(source),'--output',str(out)],check=True)
    else:
        subprocess.run([sys.executable,str(here/'tests/contention/build.py'),str(source),str(out)],check=True)
        subprocess.run([sys.executable,str(here/'tests/contention/analyze.py'),str(out)],check=True)


if __name__=='__main__':main()
