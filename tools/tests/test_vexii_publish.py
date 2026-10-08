#!/usr/bin/env python3
"""A failed generator transform must not publish a partial CPU netlist."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    for file in ['generate_vexii.sh', 'retime_fetch_reads.pl', 'factor_fetch_ready.pl',
                 'retime_dcache_writes.pl', 'retime_fpu_matches.pl',
                 'retime_fetch_pipeline.pl', 'optimize_os20_timing.pl', 'factor_os30_fp_rs3.pl', 'factor_os30_frontend.pl', 'factor_os30_load_forward.pl', 'factor_os30_control_paths.pl', 'factor_os30_parallel_paths.pl', 'factor_os30_endpoint_paths.pl']:
        shutil.copy2(ROOT / 'src/fpga/vendor/vexriscv' / file, root / file)
    (root / 'configs').mkdir()
    (root / 'VexiiRiscv').mkdir()
    (root / 'bin').mkdir()
    fake = root / 'bin/sbt'
    fake.write_text("#!/bin/sh\nprintf 'module VexiiRiscv(); endmodule\\n' > VexiiRiscv.v\n")
    fake.chmod(0o755)
    cpu = root / 'VexiiRiscv/VexiiRiscv_os30.v'
    cpu.write_text('previous CPU\n')
    config = root / 'configs/os30.cfg'
    config.write_text('FETCH_READ_HOLD=1\n')
    command = ['bash', str(root / 'generate_vexii.sh'), 'os30']
    env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'])
    run = subprocess.run(command, env=env, capture_output=True)
    assert run.returncode != 0
    assert cpu.read_text() == 'previous CPU\n'
    cpu.unlink()
    run = subprocess.run(command, env=env, capture_output=True)
    assert run.returncode != 0 and not cpu.exists()
    config.write_text('FETCH_READY_FACTOR=1\n')
    cpu.write_text('previous CPU\n')
    run = subprocess.run(command, env=env, capture_output=True)
    assert run.returncode != 0 and cpu.read_text() == 'previous CPU\n'
    cpu.unlink()
    run = subprocess.run(command, env=env, capture_output=True)
    assert run.returncode != 0 and not cpu.exists()
    for option in ['FPU_MATCH_REG', 'FETCH_PIPELINE_HOLD', 'OS20_TIMING', 'OS30_FP_RS3_GROUPED', 'OS30_FRONTEND_FLAT']:
        config.write_text(option + '=1\n')
        cpu.write_text('previous CPU\n')
        run = subprocess.run(command, env=env, capture_output=True)
        assert run.returncode != 0 and cpu.read_text() == 'previous CPU\n'
        cpu.unlink()
        run = subprocess.run(command, env=env, capture_output=True)
        assert run.returncode != 0 and not cpu.exists()
    config.write_text('LSU_WRITE_REG=1\n')
    cpu.write_text('previous CPU\n')
    run = subprocess.run(command, env=env, capture_output=True)
    assert run.returncode != 0 and cpu.read_text() == 'previous CPU\n'
    cpu.unlink()
    config.write_text('FETCH_READ_HOLD=0\n')
    subprocess.run(command, env=env, capture_output=True, check=True)
    assert cpu.read_text() == 'module VexiiRiscv(); endmodule\n'
    assert not (root / 'VexiiRiscv/VexiiRiscv.v').exists()
print('PASS: failed CPU transforms preserve the old output; success publishes atomically')
