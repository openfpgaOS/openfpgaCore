#!/usr/bin/env python3
"""Freeze an isolated os30 save-only or paired-PSRAM project; no production publish.

Requires the selected generated CPU and firmware built with
OF_EXPERIMENT_SDRAM_STAGING=1. Runs only the currently saved fitter seed.
"""
import argparse, hashlib, json, shutil, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
POCKET=ROOT/'src/fpga/targets/pocket'
HERE=Path(__file__).resolve().parent
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=['sdram','paired'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--compile',action='store_true')
    args=parser.parse_args()
    out=args.output.resolve()
    if out.exists():raise SystemExit(f'Refusing to replace existing build: {out}')
    if not out.is_relative_to(ROOT):raise SystemExit('Build must be inside this worktree for container isolation')
    fw=ROOT/'src/firmware/os/bld/pocket-sdram-staging/firmware.mif'
    assert fw.is_file() and (ROOT/CPU).is_file()
    assert '50533201' in (ROOT/'src/firmware/os/hal/regs.h').read_text()
    job='psram32-template'
    subprocess.run(['make','-C',str(POCKET),'VARIANT=os30',f'JOB={job}',
        '-o','../../vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v',f'bld/{job}/ap_core.qsf'],check=True)
    out.mkdir(parents=True)
    frozen=out/'frozen'
    shutil.copytree(ROOT/'src/fpga',frozen/'src/fpga',
        ignore=shutil.ignore_patterns('bld','.git','db','incremental_db','output_files','*.qws'))
    target=frozen/'src/fpga/targets/pocket'
    shutil.copy2(fw,target/'firmware.mif')
    # Firmware and application ABI evidence accompanies each independent fit.
    shutil.copy2(ROOT/'src/firmware/os/bld/pocket-sdram-staging/os.bin',out/'os.bin')
    bdir=out/'build';bdir.mkdir()
    shutil.copy2(POCKET/'ap_core.qpf',bdir/'ap_core.qpf')
    qsf=(POCKET/f'bld/{job}/ap_core.qsf').read_text().replace(str(ROOT)+'/',str(frozen)+'/')
    ex=frozen/'src/fpga/experimental/psram32'
    qsf+='\nset_global_assignment -name VERILOG_MACRO OF_EXPERIMENT_SDRAM_STAGING\n'
    qsf+=f'set_global_assignment -name SEARCH_PATH {ex}\n'
    modules=['bridge_sdram_apf.sv','bridge_sdram_command_gate.sv','psram32_axi_error.sv']
    if args.kind=='paired':
        qsf+='set_global_assignment -name VERILOG_MACRO OF_EXPERIMENT_PSRAM32\n'
        modules+=['psram32_controller.sv','psram32_axi_adapter.sv']
    for module in modules:qsf+=f'set_global_assignment -name SYSTEMVERILOG_FILE {ex/module}\n'
    (bdir/'ap_core.qsf').write_text(qsf)
    manifest=dict(kind=args.kind,configured_mhz=100,seed=(POCKET/'seeds/os30.seed').read_text().strip(),
        cpu_sha256=sha(ROOT/CPU),firmware_sha256=sha(fw),os_sha256=sha(out/'os.bin'),
        sources={str(p.relative_to(frozen)):sha(p) for p in sorted(frozen.rglob('*')) if p.is_file()},
        qsf_sha256=sha(bdir/'ap_core.qsf'),limits=['No hardware qualification','No full seed sweep','Framebuffer remains SDRAM'])
    (out/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(bdir,flush=True)
    if args.compile:
        with (out/'compile.log').open('x') as log:
            result=subprocess.run(['bash',str(ROOT/'tools/quartus-container.sh'),str(bdir)],stdout=log,stderr=subprocess.STDOUT)
        print(f'Quartus exit {result.returncode}: {out}/compile.log',flush=True)
        raise SystemExit(result.returncode)

if __name__=='__main__':main()
