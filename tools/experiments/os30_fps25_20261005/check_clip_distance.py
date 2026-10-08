#!/usr/bin/env python3
"""Reuse the exact clip-output fixture in private current-baseline storage."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
PHASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase2'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    candidate_dir = PHASE / ('clip-distance-v'+str(args.version))
    OUT = candidate_dir / 'function-checks'
    old_script = ROOT / 'tools/experiments/os30_simple_20261005/check_clip.py'
    spec = importlib.util.spec_from_file_location('prior_clip_check', old_script)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    parent_path = PHASE / 'baseline/gfx_pc.c'
    candidate_path = candidate_dir / 'gfx_pc.c'
    parent = parent_path.read_text()
    candidate = candidate_path.read_text()
    OUT.mkdir(exist_ok=False)
    def structure(name):
        begin = parent.index('struct '+name+' {')
        return parent[begin:parent.index('\n};',begin)+3]
    helpers = parent[parent.index('static inline rspv_t rspv_lerp('):
                     parent.index('static inline bool gfx_clip_triangle(')]
    reference = old.recipe.extract(parent)[2].replace(
        'static inline bool gfx_clip_triangle(',
        'static __attribute__((noinline)) bool reference_clip(')
    current = old.recipe.extract(candidate)[2].replace(
        'static inline bool gfx_clip_triangle(',
        'static __attribute__((noinline)) bool candidate_clip(')
    tests = old.TEST.replace('int main(void) {',
                            'int main(void) {\n    setvbuf(stdout,NULL,_IONBF,0);')
    tests = tests.replace('All distance expressions stay unchanged.',
                          'Compare the simplified distance against the unchanged baseline.')
    source = OUT/'functions.c'
    source.write_text(old.PRE+structure('RGBA')+'\n'+structure('LoadedVertex')+'\n'+
                      old.SINK+helpers+reference+'\n'+current+'\n'+tests)
    variants = [('strict',['-O2','-ffp-contract=off']),
                ('fast',['-O3','-ffast-math','-fno-math-errno','-fno-trapping-math',
                         '-fsingle-precision-constant']),
                ('sanitized',['-O1','-ffp-contract=off','-fsanitize=address,undefined',
                              '-fno-omit-frame-pointer'])]
    results=[]
    for name,flags in variants:
        exe=OUT/name
        command=['gcc','-std=c11',*flags,str(source),'-lm','-o',str(exe)]
        subprocess.run(command,check=True)
        env=os.environ.copy()
        if name=='sanitized':env['ASAN_OPTIONS']='detect_leaks=0'
        run=subprocess.run([str(exe)],capture_output=True,text=True,env=env)
        (OUT/(name+'.log')).write_text(run.stdout+run.stderr)
        results.append(dict(name=name,command=command,returncode=run.returncode,
                            output=run.stdout.strip(),binary_sha256=sha(exe)))
        print(name,run.returncode,run.stdout.strip(),flush=True)
        if run.returncode:break
    passed=len(results)==3 and all(x['returncode']==0 for x in results)
    record={'passed':passed,'parent_sha256':sha(parent_path),
            'candidate_sha256':sha(candidate_path),'harness_sha256':sha(source),
            'reused_harness_recipe_sha256':sha(old_script),'script_sha256':sha(Path(__file__)),
            'runs':results,
            'comparison':'Exact return value, fan count/order, all seven float payload bits and four color bytes. Only unused scratch clip_rej and padding excluded.',
            'coverage':'32,172 finite-input triangles:20,000 random,7,500 plane/positive-negative-zero W/signed-zero cases,2,048 arbitrary clip masks and degenerates,2,624 finite powers-of-two scale cases. All64 clip masks reached.',
            'target_status':'Pending SDK RNE code generation review. Host checks do not establish reduced-FMA hardware equivalence or performance.'}
    (OUT/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    if not passed:raise SystemExit(1)


if __name__=='__main__':
    main()
