#!/usr/bin/env python3
"""Bounded 3-window, 2-schedule comparison; preserves completed/failed runs."""
import concurrent.futures,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
def run(job):
    name,elf,start,traffic=job
    label=f'{name}{"" if name=="baseline" else "-"}{start}-t{traffic}'
    directory=OUT/('run-'+label)
    if directory.exists():
        assert json.loads((directory/'inputs.json').read_text())['success'],directory
        return
    subprocess.run([sys.executable,str(Path(__file__).with_name('run.py')),label,'--elf',str(elf),
        '--start',str(start),'--frames','8','--traffic',str(traffic)],check=True)
def main():
    inputs={'baseline':CAMP/'phase7/combined-v1/app.elf',
            'kick128':CAMP/'phase8/kick128/app.elf','kick128-tail':CAMP/'phase8/kick128-tail/app.elf'}
    jobs=[(name,elf,start,t) for start in [360,500,600] for t in [1,2] for name,elf in inputs.items()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(run,jobs))
if __name__=='__main__':main()
