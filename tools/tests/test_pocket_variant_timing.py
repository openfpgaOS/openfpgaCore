#!/usr/bin/env python3
"""Variant fitter settings must agree in local and frozen builds."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from sweep_target_matrix import prepare


class VariantTimingTests(unittest.TestCase):
    def test_settings_constraints_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / 'live'
            target = live / 'src/fpga/targets/pocket'
            target.mkdir(parents=True)
            shutil.copy2(ROOT / 'src/fpga/targets/pocket/Makefile', target / 'Makefile')
            (target / 'variants').mkdir()
            (target / 'seeds').mkdir()
            for variant in ('os20', 'os25'):
                (target / f'variants/{variant}.mk').write_text('DEFS := INCLUDE_PALETTE\n')
                (target / f'seeds/{variant}.seed').write_text('35\n')
            (target / 'ap_core.qpf').write_text('PROJECT_REVISION = ap_core\n')
            (target / 'ap_core.qsf').write_text(
                'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING OFF\n'
                'set_global_assignment -name SEED 1\n')
            fragment = target / 'variants/os20.qsf'
            fragment.write_text(
                'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING ON\n'
                'set_global_assignment -name SDC_FILE variants/os20.sdc\n')
            constraint = target / 'variants/os20.sdc'
            constraint.write_text('# qualified timing constraints\n')
            vendor = live / 'src/fpga/vendor/vexriscv'
            inputs = ['generate_vexii.sh', 'retime_fetch_reads.pl', 'retime_aligner_pc.pl',
                      'factor_fetch_ready.pl', 'retime_dcache_writes.pl',
                      'retime_fpu_matches.pl', 'retime_fetch_pipeline.pl',
                      'optimize_os20_timing.pl', 'configs/os20.cfg', 'configs/os25.cfg',
                      'VexiiRiscv/build.sbt', 'VexiiRiscv/ext/SpinalHDL/build.sbt']
            for name in inputs:
                path = vendor / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test input\n')
                os.utime(path, (10, 10))
            for variant in ('os20', 'os25'):
                cpu = vendor / f'VexiiRiscv/VexiiRiscv_{variant}.v'
                cpu.write_text('module VexiiRiscv(); endmodule\n')
                os.utime(cpu, (20, 20))
            frozen = root / 'frozen'
            shutil.copytree(live, frozen)

            def settings(path):
                return [line for line in path.read_text().splitlines()
                        if 'PHYSICAL_SYNTHESIS_' in line]

            def generate(variant):
                path = target / f'bld/{variant}/ap_core.qsf'
                subprocess.run(['make', '-s', f'VARIANT={variant}', str(path.relative_to(target))],
                               cwd=target, check=True, capture_output=True)
                return path

            for variant in ('os20', 'os25'):
                local_qsf = generate(variant)
                directory, project = prepare(frozen, root / variant,
                    {'target': 'pocket', 'variant': variant, 'defs': 'INCLUDE_PALETTE'}, 35, 4)
                matrix_qsf = directory / (project + '.qsf')
                self.assertEqual(settings(local_qsf), settings(matrix_qsf))
                self.assertEqual(settings(local_qsf)[-1].split()[-1],
                                 'ON' if variant == 'os20' else 'OFF')
                for qsf in (local_qsf, matrix_qsf):
                    paths = [line.split()[-1] for line in qsf.read_text().splitlines()
                             if '-name SDC_FILE ' in line]
                    self.assertEqual(len(paths), int(variant == 'os20'))
                    for path in paths:
                        resolved = Path(path) if Path(path).is_absolute() else qsf.parent / path
                        self.assertEqual(resolved.read_text(), constraint.read_text())

            qsf = target / 'bld/os20/ap_core.qsf'
            def fingerprint():
                return subprocess.check_output(
                    ['bash', '-c', '. "$1"; shift; netlist_hash "$@"', 'test',
                     str(ROOT / 'tools/netlist_hash.sh'), str(qsf), str(fragment), str(constraint)])
            original = fingerprint()
            constraint.write_text('# stricter hold margin\n')
            self.assertNotEqual(fingerprint(), original)
            before = fingerprint()
            fragment.write_text(fragment.read_text().replace('RETIMING ON', 'RETIMING OFF'))
            os.utime(fragment, (qsf.stat().st_mtime + 2,) * 2)
            status = subprocess.run(['make', '-s', '-q', 'VARIANT=os20', 'bld/os20/ap_core.qsf'],
                                    cwd=target, capture_output=True)
            self.assertEqual(status.returncode, 1, status.stderr)
            self.assertNotEqual(fingerprint(), before)
            generate('os20')
            self.assertTrue(settings(qsf)[-1].endswith(' OFF'))


if __name__ == '__main__':
    unittest.main()
