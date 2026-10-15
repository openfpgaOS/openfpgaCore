#!/usr/bin/env python3
"""Disposable all30 source-only assembly smoke; never touches real sweep outputs."""
import hashlib,importlib.util,json,shutil,subprocess,sys,tarfile,tempfile
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PORTABLE=ROOT/'tools/experiments/quake2_tiny_audio_20261013'
REAL=HERE/'sweep-final-ring-audio'
sys.path.insert(0,str(PORTABLE))
import package,audit_sweep,timing_results

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def main():
    sys.path.insert(0,str(HERE/'sweep-tooling'))
    fixture=module('export_timing_fixture',HERE/'sweep-tooling/test_sweep.py')
    assembler=module('source_export_assembler',HERE/'assemble_package.py')
    checks=[]
    def rejects(name,action):
        try:action()
        except(ValueError,FileNotFoundError):checks.append(name);return
        raise AssertionError('Unexpected acceptance:'+name)
    with tempfile.TemporaryDirectory(prefix='tiny-audio-source-export-')as temp:
        t=Path(temp);sweep=t/'synthetic-sweep';sweep.mkdir();dest=t/'bundle'
        shutil.copytree(PORTABLE,dest)
        for name in ['manifest.json','source.tar.gz','baseline-manifest.json']:
            shutil.copyfile(REAL/name,sweep/name)
        for name in ['frozen','recipes']:shutil.copytree(REAL/name,sweep/name)
        manifest=package.load(sweep/'manifest.json');rows=[]
        for seed in range(1,31):
            d=sweep/'seeds'/f'seed_{seed:02}';attempt=d/'attempt_01';attempt.mkdir(parents=True)
            project=t/'scratch';project.mkdir();fixture.fit(project)
            fixture.timing(attempt/'timing',fmax=88,setup=-1.36,hold=-.1 if seed==1 else .1)
            pins={}
            with tarfile.open(attempt/'quartus-reports.tar.gz','w:gz')as archive:
                for p in sorted((project/'output_files').iterdir()):
                    n='src/fpga/targets/pocket/output_files/'+p.name;archive.add(p,arcname=n);pins[n]=package.sha(p)
            measured=timing_results.parse(project,attempt/'timing');shutil.rmtree(project)
            for name in ['ap_core.rbf','os30.rbf_r','ap_core.sof']:(attempt/name).write_bytes(b'SYNTHETIC TEST ONLY')
            row=dict(measured,seed=seed,frozen_sha256=manifest['frozen_sha256'],state='complete',valid_reports=True,quartus_report_sha256=pins)
            if seed==7:row.update(state='failed',valid_reports=False,eligible_above_90_mhz_estimate=False)
            row['evidence_sha256']=package.inventory(attempt)
            save(attempt/'receipt.json',row);save(d/'result.json',row);save(d/'latest.json',dict(attempt='attempt_01'));rows.append(row)
        selection=timing_results.select(rows);assert selection['selected_seed']is None
        save(sweep/'results.json',rows);save(sweep/'selection.json',selection)
        save(sweep/'status.json',dict(state='complete',seeds_completed=30))
        for name in ['map-receipt.json','run-configuration.json']:save(sweep/name,dict(synthetic_test=True))
        (sweep/'map.log').write_text('SYNTHETIC TEST: no Quartus executed\n')
        assert audit_sweep.audit(sweep,None,source_only=True)==selection
        checks.append('all30-unqualified-results-reparse-with-failed-seed-and-bad-hold')
        rejects('default-qualified-audit-still-rejects-null-selection',lambda:audit_sweep.audit(sweep,None))
        assembler.OUT=dest
        plan=HERE/'package-evidence-plan.json'
        rejects('default-final-assembler-still-rejects-null-selection',lambda:assembler.final(sweep,plan,True))
        save(sweep/'status.json',dict(state='running',seeds_completed=29))
        rejects('source-only-refuses-incomplete-sweep',lambda:assembler.final(sweep,plan,True,source_only=True))
        save(sweep/'status.json',dict(state='complete',seeds_completed=30))
        assembler.final(sweep,plan,True,source_only=True)
        assembler.final(sweep,plan,source_only=True)
        m=package.verify(dest);assert m['state']=='source-only-unqualified' and m['selected_seed']is None and not m['qualified']
        assert not(dest/'core').exists() and not[p for p in dest.rglob('*')if p.suffix in('.sof','.rbf','.rbf_r')]
        checks.append('actual-source-only-assembly-has-null-selection-no-release-core-no-fpga-binaries')
        staged=t/'staged';package.stage('core-source',staged,dest)
        assert package.inventory(staged)==manifest['source_sha256'];checks.append('all128-source-files-stage-exactly')
        r=subprocess.run([sys.executable,str(dest/'build.py'),'core-source',str(staged),str(t/'prepare'),'--prepare-only'],capture_output=True,text=True)
        assert r.returncode==0,(r.stdout,r.stderr);checks.append('portable-source-only-prepare-command-passes')
        r=subprocess.run([sys.executable,str(dest/'build.py'),'core-source',str(staged),str(t/'forbidden')],capture_output=True,text=True)
        assert r.returncode!=0 and not(t/'forbidden').exists();checks.append('portable-source-only-build-cannot-produce-release-artifact')
        assert package.load(dest/'selection.json')==selection
        checks.append('all30-selection-and-raw-evidence-retained-and-verified')
    receipt=dict(passed=True,checks=checks,real_sweep_modified=False,quartus_launched=False,scope='Synthetic timing outcomes with actual frozen128 source and app, assembled only in disposable temporary directories.',recipe_sha256=package.sha(Path(__file__)),assembler_sha256=package.sha(HERE/'assemble_package.py'),toolkit_sha256={n:package.sha(PORTABLE/n)for n in ['package.py','audit_sweep.py','build.py','run_tests.py','timing_results.py']})
    save(HERE/'package-source-export-smoke.json',receipt);print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
