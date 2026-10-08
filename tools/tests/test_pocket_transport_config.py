#!/usr/bin/env python3
"""Transport benchmarks must include the implicit fast-texture SDRAM settings."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_pocket_gpu_transport import sdram_parameters
from sweep_target_matrix import inventory

expected = {'os20': (0, 736), 'os25': (1, 736), 'os30': (1, 736)}
with tempfile.TemporaryDirectory() as name:
    for row in inventory():
        if row['target'] != 'pocket':
            continue
        params = sdram_parameters({'variant': row}, Path(name))
        assert (params['BANK_ROW_TRACK'], params['REFRESH_INTERVAL']) == expected[row['variant']]
        assert params['FAST_166'] == 0
        if row['variant'] == 'os30':
            trial = dict(row, defs=row['defs'] + ' INCLUDE_OS30_SDRAM166')
            params = sdram_parameters({'variant': trial}, Path(name))
            assert (params['BANK_ROW_TRACK'], params['REFRESH_INTERVAL'], params['FAST_166']) == (1, 1222, 1)
print('PASS: all Pocket transport configurations match production bank tracking and refresh')
