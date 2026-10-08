#!/usr/bin/env python3
"""Compile the freestanding fixture with actual app flags and bundled musl."""
import sys,os,subprocess,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import *
out=OUT/'cpu-kernel';fw=out/'firmware';sdk=OUT.parent/'SM64/src/sdk'
container=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}','-v',f'{ROOT}:{ROOT}','-w',str(fw),'openfpgaos-firmware']
flags=['-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-O3','-Wall','-ffunction-sections','-fdata-sections','-falign-functions=64','-falign-loops=8','-fno-tree-loop-distribute-patterns','-fcommon','-fno-strict-aliasing','-fwrapv','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-mtune=sifive-7-series','-nostdinc','-isystem',str(sdk/'musl/include'),'-nostdlib','-nostartfiles','-Wl,-T,link.ld','-Wl,--no-relax']
cmd=[*container,'riscv64-unknown-elf-gcc',*flags,'start.S','kernel.c',str(sdk/'musl/lib/libc.a'),'-lgcc','-o','kernel.elf']
with (out/'firmware-build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
subprocess.run([*container,'riscv64-unknown-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],check=True)
with (out/'kernel.asm').open('w') as f:subprocess.run([*container,'riscv64-unknown-elf-objdump','-d','kernel.elf'],stdout=f,check=True)
m=read(out/'manifest.json');m['compiler_command']=cmd;m['libc_sha256']=sha(sdk/'musl/lib/libc.a');m['firmware_sha256']={p.name:sha(p) for p in fw.iterdir() if p.is_file()};m['assembly_sha256']=sha(out/'kernel.asm');(out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
print('BUILT',sha(fw/'kernel.elf'))
