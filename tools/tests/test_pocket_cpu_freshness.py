#!/usr/bin/env python3
"""CPU configuration and generator edits must invalidate a Pocket netlist."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    target = root / 'src/fpga/targets/pocket'
    target.mkdir(parents=True)
    shutil.copy2(ROOT / 'src/fpga/targets/pocket/Makefile', target / 'Makefile')
    shutil.copytree(ROOT / 'src/fpga/targets/pocket/variants', target / 'variants')
    vendor = root / 'src/fpga/vendor/vexriscv'
    inputs = ['generate_vexii.sh', 'retime_fetch_reads.pl', 'retime_aligner_pc.pl',
              'factor_fetch_ready.pl', 'retime_dcache_writes.pl',
              'retime_fpu_matches.pl', 'retime_fetch_pipeline.pl',
              'optimize_os20_timing.pl', 'factor_os30_fp_rs3.pl', 'factor_os30_frontend.pl',
        'os30-rvc-pipeline.patch', 'factor_os30_load_forward.pl', 'factor_os30_control_paths.pl', 'factor_os30_parallel_paths.pl', 'factor_os30_endpoint_paths.pl',
              'configs/os30.cfg', 'patches/test.patch']
    for name in [*inputs, 'VexiiRiscv/build.sbt', 'VexiiRiscv/ext/SpinalHDL/build.sbt']:
        path = vendor / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('test input\n')
        os.utime(path, (10, 10))
    cpu = vendor / 'VexiiRiscv/VexiiRiscv_os30.v'
    cpu.write_text('generated netlist\n')
    os.utime(cpu, (20, 20))
    command = ['make', '-s', '-q', 'VARIANT=os30',
               '../../vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
    def status():
        run = subprocess.run(command, cwd=target, capture_output=True)
        assert run.returncode in (0, 1), run.stderr.decode()
        return run.returncode
    assert status() == 0
    for name in inputs:
        path = vendor / name
        os.utime(path, (30, 30))
        assert status() == 1, name + ' failed to invalidate the CPU'
        os.utime(path, (10, 10))
        assert status() == 0
print('PASS: Pocket CPU regeneration follows config, generator and patch changes')
