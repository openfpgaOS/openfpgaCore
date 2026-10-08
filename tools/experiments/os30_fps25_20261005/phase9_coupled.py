#!/usr/bin/env python3
"""Reuse the sealed current-A coupled model while writing only phase9 runs."""
import argparse
import json
from pathlib import Path
import re
import sys

from phase9_experiment import CAMP, HERE, OUT, PARENT, ROOT, directory, module, sha, write

MODEL_CAMPAIGN = ROOT/'docs/measurements/os30-overlap-20261006/coupled'
COUPLED = OUT/'coupled'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate',help='phase9 candidate label, or baseline for retained F9')
    p.add_argument('--mode',choices=['normal','diagnostic'],default='normal')
    p.add_argument('--start',type=int,default=360)
    p.add_argument('--frames',type=int,default=8)
    p.add_argument('--traffic',type=int,choices=[1,2],default=1)
    p.add_argument('--suffix',default='')
    a = p.parse_args()
    assert re.fullmatch(r'[a-z0-9][a-z0-9_-]*',a.candidate)
    assert not a.suffix or re.fullmatch(r'[a-z0-9][a-z0-9_-]*',a.suffix)
    assert a.start >= 0 and a.frames >= 2
    source = PARENT if a.candidate == 'baseline' else directory(a.candidate)
    elf = source/('app.elf' if a.mode == 'normal' else 'diagnostic.elf')
    assert sha(elf) == json.loads((source/f'build-{a.mode}.json').read_text())['elf_sha256']
    model = MODEL_CAMPAIGN/'model'
    build = json.loads((model/'build.json').read_text())
    assert sha(model/'obj/Vtb_gpu') == build['simulator_sha256']
    inputs = json.loads((model/'inputs.json').read_text())
    for name,digest in inputs['sources'].items():
        assert sha(model/name) == digest, name
    COUPLED.mkdir(parents=True,exist_ok=True)
    link = COUPLED/'model'
    if link.is_symlink():
        assert link.resolve() == model.resolve()
    else:
        assert not link.exists()
        link.symlink_to(model,target_is_directory=True)
    label = f'{a.candidate}-{a.mode}-{a.start}-t{a.traffic}' + ('-'+a.suffix if a.suffix else '')
    target = COUPLED/('run-'+label)
    assert not target.exists(), 'Preserve every prior run; choose a new suffix'
    runner_path = ROOT/'tools/experiments/os30_overlap_20261006/run.py'
    runner = module('phase9_current_a_runner',runner_path)
    runner.OUT = COUPLED
    sys.argv = [str(runner_path),label,'--elf',str(elf),'--start',str(a.start),
                '--frames',str(a.frames),'--traffic',str(a.traffic)]
    runner.main()
    write(target/'phase9-invocation.json',dict(
        candidate=a.candidate,mode=a.mode,source_elf_sha256=sha(elf),
        wrapper_sha256=sha(Path(__file__)),runner_sha256=sha(runner_path),
        model_build_sha256=sha(model/'build.json'),model_inputs_sha256=sha(model/'inputs.json'),
        output_inputs_sha256=sha(target/'inputs.json'),sealed_model_used_read_only=True,
        qualified_for_deployment=False))


if __name__ == '__main__':main()
