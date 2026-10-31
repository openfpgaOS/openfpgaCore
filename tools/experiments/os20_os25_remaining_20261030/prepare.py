#!/usr/bin/env python3
"""Freeze one os20/os25 candidate for the existing isolated Quartus runner."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
from sweep_target_matrix import inventory


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--variant', choices=['os20', 'os25'], required=True)
    p.add_argument('--cpu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seeds', nargs='+', type=int, required=True)
    p.add_argument('--audio-bram', action='store_true')
    p.add_argument('--wbuf-bram', action='store_true')
    p.add_argument('--row-track', action='store_true')
    p.add_argument('--nonstream-bypass', action='store_true')
    p.add_argument('--clamp-pipe', action='store_true')
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen = out / 'frozen'
    ignore = shutil.ignore_patterns('bld', 'db', 'incremental_db', 'output_files',
        'simulation', '*.rpt', '*.summary', '*.log', '*.sof', '*.rbf', '*.rbf_r',
        '*.qws', '*.smsg', 'firmware.mif.tmp')
    for name in ['common', 'targets/pocket']:
        shutil.copytree(ROOT / 'src/fpga' / name, frozen / 'src/fpga' / name, ignore=ignore)
    cpu = frozen / ('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_' + args.variant + '.v')
    cpu.parent.mkdir(parents=True)
    shutil.copyfile(args.cpu, cpu)
    row, = [r for r in inventory() if r['target'] == 'pocket' and r['variant'] == args.variant]
    row['cpu_sha256'] = sha(cpu)
    row['boot_sha256'] = sha(frozen / 'src/fpga/targets/pocket/firmware.mif')
    if args.audio_bram:
        path = frozen / 'src/fpga/common/audio_mixer.v'
        old = '(* ramstyle = "MLAB" *) reg [21:0] pos_latch [0:31];'
        text = path.read_text()
        assert text.count(old) == 1
        text = text.replace(old, '`ifdef INCLUDE_AUDIO_POS_M10K\n' +
                            old.replace('MLAB', 'M10K') + '\n`else\n' + old + '\n`endif')
        path.write_text(text)
        row['defs'] += ' INCLUDE_AUDIO_POS_M10K'
    if args.wbuf_bram:
        path = frozen / ('src/fpga/targets/pocket/variants/' + args.variant + '.qsf')
        text = path.read_text() if path.exists() else ''
        text += '\n# Keep the registered SDRAM write buffer in block RAM.\n'
        text += 'set_instance_assignment -name RAMSTYLE_ATTRIBUTE M10K -to *sdram_axi_slave|wbuf*\n'
        path.write_text(text)
    if args.row_track and 'INCLUDE_BANK_ROW_TRACK' not in row['defs'].split():
        row['defs'] += ' INCLUDE_BANK_ROW_TRACK'
    if args.nonstream_bypass and 'INCLUDE_NONSTREAM_BYPASS' not in row['defs'].split():
        row['defs'] += ' INCLUDE_NONSTREAM_BYPASS'
    if args.clamp_pipe and 'INCLUDE_GPU_CLAMP_PIPE' not in row['defs'].split():
        row['defs'] += ' INCLUDE_GPU_CLAMP_PIPE'
    (frozen / 'tools').mkdir()
    timing = (ROOT / 'tools/report_core_timing.tcl').read_text()
    marker = '    set temperature [get_operating_conditions_info $corner -temperature]\n'
    assert timing.count(marker) == 1
    timing = timing.replace(marker, marker +
        '    report_clock_fmax_summary -file [file join $destination "${model}_${temperature}_fmax.rpt"]\n')
    (frozen / 'tools/report_core_timing.tcl').write_text(timing)
    save(out / 'inventory.json', [row])
    save(out / 'config.json', dict(jobs=1, processors=4, seed_offsets=[0],
                                  seeds=args.seeds, variants=1, candidates=len(args.seeds)))
    save(out / 'frozen-sources.json', {str(f.relative_to(frozen)): sha(f)
         for f in sorted(frozen.rglob('*')) if f.is_file()})
    save(out / 'candidate.json', dict(variant=args.variant, cpu_source=str(args.cpu.resolve()),
         cpu_sha256=sha(cpu), audio_bram=args.audio_bram, wbuf_bram=args.wbuf_bram,
         row_track=args.row_track, nonstream_bypass=args.nonstream_bypass,
         clamp_pipe=args.clamp_pipe,
         seeds=args.seeds, configured_mhz=100,
         production_modified=False, deployed=False))
    print(out)


if __name__ == '__main__':
    main()
