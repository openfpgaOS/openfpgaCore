#!/usr/bin/env python3
"""Test real geometry capability discovery and packet emitters with a bounded ring."""
import os
from pathlib import Path
import subprocess
import tempfile
from check_quake2_textures import function

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'src/quake2/openfpga/of_emit_q2.c').read_text()
with tempfile.TemporaryDirectory(prefix='quake2-geometry-') as temp:
    work = Path(temp)
    texture_names = ['ofq2_native_epoch', 'ofq2_texture_route_sdram', 'OFQ2_Init', 'OFQ2_TexInit',
                     'OFQ2_TexReset', 'OFQ2_TexCreate', 'OFQ2_TexBindDomain', 'OFQ2_Shutdown']
    geometry_names = ['OFQ2_Supports', 'OFQ2_DrawGeometryFan', 'OFQ2_DrawGeometryPackets', 'OFQ2_DrawClipIndexed',
                      'OFQ2_NativeReserve', 'OFQ2_NativeUpload', 'OFQ2_DrawNativeMesh',
                      'OFQ2_NativeReset', 'OFQ2_NativeGeneration']
    for name, functions in [('texture', texture_names), ('geometry', geometry_names)]:
        (work / ('extracted_' + name + '_functions.inc')).write_text(
            '\n'.join(function(source, name) for name in functions))
    exe = work / 'geometry-test'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O1', '-g',
        '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
        '-I' + str(work), '-I' + str(ROOT / 'src/sdk/include'),
        '-I' + str(ROOT / 'src/quake2/openfpga'),
        str(ROOT / 'tools/tests/quake2_geometry_test.c'), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True, env=dict(os.environ,
        ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1'))
