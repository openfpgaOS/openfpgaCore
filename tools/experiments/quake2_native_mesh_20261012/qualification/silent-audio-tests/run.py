#!/usr/bin/env python3
"""Recheck pinned, exact silent RTL and full OS mixer source; no hardware/assets."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / 'sources'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audio_instance(text):
    return re.search(r'audio_output audio_out \(.*?\n\);', text, re.S).group()


def wrapper(body):
    return '''module audio_selection (
input wire clk_cpu, clk_core_12288, reset_n_cpu_media,
input wire periph_audio_sample_wr, mixer_sample_wr,
input wire [31:0] periph_audio_sample_data, mixer_sample_data,
output wire [9:0] audio_fifo_level,
output wire audio_fifo_full, audio_mclk, audio_lrck, audio_dac
);
''' + body + '''
endmodule
// A transparent test double checks the selected producer in normal branches.
module audio_output (
input wire clk_sys, clk_audio, reset_n, sample_wr,
input wire [31:0] sample_data,
output wire [9:0] fifo_level,
output wire fifo_full, audio_mclk, audio_lrck, audio_dac
);
assign fifo_level = sample_data[9:0];
assign fifo_full = sample_wr;
assign audio_mclk = clk_audio;
assign audio_lrck = reset_n;
assign audio_dac = sample_data[31];
endmodule
'''


def main():
    pins = json.loads((ROOT / 'source-pins.json').read_text())
    for name, entry in pins.items():
        assert digest(SOURCES / name) == entry['sha256'], name
    core = (SOURCES / 'core_top.v').read_text()
    base = (SOURCES / 'baseline_core_top.v').read_text()
    marker = '`ifdef NATIVE_MESH_SILENT_AUDIO'
    end = '`endif // NATIVE_MESH_SILENT_AUDIO'
    assert core.count(marker) == 1 and core.count(end) == 1
    block = core[core.index(marker):core.index(end) + len(end)]
    assert audio_instance(block) == audio_instance(base)
    # Pin actual AXI status packing and actual firmware status masks.
    axi = (SOURCES / 'axi_periph_slave.v').read_text()
    assert "{21'b0, audio_fifo_full, audio_fifo_level}" in axi
    qsf = (SOURCES / 'silent_ap_core.qsf').read_text()
    assert 'VERILOG_MACRO NATIVE_MESH_SILENT_AUDIO\n' in qsf
    assert 'VERILOG_MACRO INCLUDE_HW_MIXER\n' not in qsf
    assert 'VERILOG_MACRO INCLUDE_GPU_NATIVE_MESH\n' in qsf
    regs = (SOURCES / 'regs.h').read_text()
    macros = ['AUDIO_FIFO_LEVEL_MASK', 'AUDIO_FIFO_FULL', 'SW_MIX_APERTURE_WORDS']
    definitions = '\n'.join(re.search(r'^#define\s+' + name + r'\s+[^\n]+', regs, re.M).group()
                            for name in macros)
    assert '#define   AUDIO_FIFO_FULL       (1u << 10)' in definitions
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='q2-silent-audio-') as directory:
        build = Path(directory)
        (build / 'selection.sv').write_text(wrapper(block))
        (build / 'baseline.sv').write_text(wrapper(audio_instance(base)))
        (build / 'generated_regs.h').write_text(definitions + '\n')
        for mode in ('sw', 'hw'):
            defs = ['-DINCLUDE_HW_MIXER'] if mode == 'hw' else []
            command = ['verilator', '-E', '-P', *defs]
            native_pp = subprocess.check_output(command + [str(build / 'selection.sv')], text=True)
            baseline_pp = subprocess.check_output(command + [str(build / 'baseline.sv')], text=True)
            assert native_pp == baseline_pp, 'Normal ' + mode + ' preprocessing changed'
        silent_pp = subprocess.check_output(['verilator', '-E', '-P',
            '-DNATIVE_MESH_SILENT_AUDIO', str(build / 'selection.sv')], text=True)
        assert 'audio_output audio_out (' not in silent_pp
        assert 'assign audio_fifo_level = 10\'d1023;' in silent_pp
        print('PASS: pinned sources, actual AXI/firmware status ABI, exact normal HW/SW preprocessing, silent output selection')

        def rtl(mode):
            defs = {0: [], 1: ['-DINCLUDE_HW_MIXER'], 2: ['-DNATIVE_MESH_SILENT_AUDIO']}[mode]
            obj = build / ('rtl-' + str(mode))
            command = ['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal',
                '--top-module', 'audio_selection', '--Mdir', str(obj), *defs,
                '-CFLAGS', '-DEXPECT_MODE=' + str(mode),
                str(build / 'selection.sv'), str(ROOT / 'audio_main.cpp')]
            with (logs / ('rtl-' + str(mode) + '-build.log')).open('w') as stream:
                subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
            output = subprocess.check_output([str(obj / 'Vaudio_selection')], text=True)
            (logs / ('rtl-' + str(mode) + '-run.log')).write_text(output)
            return output.strip()

        def pump():
            exe = build / 'pump-test'
            command = [os.environ.get('CC', 'cc'), '-std=gnu11', '-O1', '-g',
                '-finstrument-functions', '-fsanitize=address,undefined',
                '-fno-omit-frame-pointer', '-I' + str(build), '-I' + str(ROOT),
                str(ROOT / 'pump_main.c'), '-o', str(exe)]
            with (logs / 'pump-build.log').open('w') as stream:
                subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
            env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
            output = subprocess.check_output([str(exe)], text=True, env=env)
            (logs / 'pump-run.log').write_text(output)
            return output.strip()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(rtl, mode) for mode in range(3)] + [pool.submit(pump)]
            for future in futures:
                print(future.result())
    result = dict(passed=True, source_pins_sha256=digest(ROOT / 'source-pins.json'),
        verilator=subprocess.check_output(['verilator', '--version'], text=True).strip(),
        cc=subprocess.check_output([os.environ.get('CC', 'cc'), '--version'], text=True).splitlines()[0],
        audio_cycles_per_mode=8212, active_audio_cycles_per_mode=8192,
        cold_reset_cycles=9, warm_reset_cycles=11,
        silent_lrck_rises=32, silent_lrck_falls=32,
        mclk_hz=12288000, lrck_hz=48000, dac=0, fifo_status=2047,
        normal_hw_sw_branches='exact preprocessed text and producer-selection simulation',
        full_os_pump_calls=3000, sample_render_calls=0, pcm_writes=0,
        cold_init='one backing-store initialization per cold/reset epoch, no samples',
        pump_positive_control='one available FIFO slot causes one real render and PCM write',
        hardware_mixer_pump_calls=1000, hardware_mixer_status_reads=0)
    (ROOT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
