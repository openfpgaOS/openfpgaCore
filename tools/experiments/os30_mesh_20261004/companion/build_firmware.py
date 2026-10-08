#!/usr/bin/env python3
"""Build the isolated cacheless RV32IMF worker benchmark, never production."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
def main():
    p=argparse.ArgumentParser();p.add_argument('--label',default='companion-triangle-fw-v1')
    p.add_argument('--source',default='triangle_firmware.c');a=p.parse_args()
    out=ROOT/'docs/measurements/os30-mesh-20261004'/a.label;out.mkdir(exist_ok=False)
    frozen=out/'source';frozen.mkdir()
    for name in (a.source,'start.S','link.ld'):(frozen/name).write_bytes((HERE/name).read_bytes())
    command=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
             '-v',f'{ROOT}:{ROOT}','--tmpfs','/tmp:exec','-e','CPATH=/usr/lib/picolibc/riscv64-unknown-elf/include',
             '-w',str(ROOT),'openfpgaos-firmware','riscv64-unknown-elf-gcc',
             '-march=rv32imf','-mabi=ilp32f','-O3','-ffreestanding','-fno-builtin',
             '-fno-common','-fno-pic','-ffunction-sections','-fdata-sections','-msmall-data-limit=0',
             '-nostdlib','-static','-Wl,--gc-sections','-Wl,--no-relax',
             '-T',str(frozen/'link.ld'),str(frozen/'start.S'),str(frozen/a.source),
             '-lgcc','-o',str(out/'worker.elf')]
    with (out/'build.log').open('w') as log:subprocess.run(command,check=True,stdout=log,stderr=subprocess.STDOUT)
    result=dict(elf_sha256=hashlib.sha256((out/'worker.elf').read_bytes()).hexdigest(),
                sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen.iterdir()},command=command)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(out/'worker.elf')
if __name__=='__main__':main()
