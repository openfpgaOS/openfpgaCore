#!/usr/bin/env python3
"""Real mixer + optional sample cache + selected SDRAM arbiter.

This is a contention stress model, not a Pocket FPS or SDRAM-pin simulation.
The source tree is pinned in the receipt. The normal full-core QSF must select
WQ16 and unbuffered SDRAM, exactly as this wrapper instantiates the arbiter.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor


def ports(path):
    header = path.read_text().split(');', 1)[0]
    header = re.sub(r'//[^\n]*|/\*.*?\*/', '', header, flags=re.S)
    return re.findall(r'\b(input|output)\s+(?:wire|reg)?\s*(\[\d+:\d+\])?\s*(\w+)', header)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--mixer', type=Path, help='Optional isolated mixer source override; copied and pinned.')
    p.add_argument('--cases', default='90:8,90:16,90:32,100:8,100:16,100:32')
    p.add_argument('--q2-only', action='store_true')
    p.add_argument('--all22-only', action='store_true', help='Only31mono22.05kHz SFX plus44.1kHz stereo stream.')
    a = p.parse_args()
    if a.q2_only and a.all22_only:
        p.error('Choose at most one workload filter')
    source, out = a.source.resolve(), a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    inputs = out/'inputs'
    inputs.mkdir()
    for name in ['src/fpga/common/os30/audio_mixer.v',
                 'src/fpga/common/os30/audio_sample_cache.v',
                 'src/fpga/common/axi_sdram_arbiter.v',
                 'src/fpga/targets/pocket/os30/core_top.v',
                 'src/fpga/targets/pocket/ap_core.qsf']:
        f=a.mixer.resolve() if a.mixer and name.endswith('/audio_mixer.v') else source/name
        before=hashlib.sha256(f.read_bytes()).hexdigest()
        shutil.copy2(f,inputs/f.name)
        assert hashlib.sha256((inputs/f.name).read_bytes()).hexdigest()==before
        assert hashlib.sha256(f.read_bytes()).hexdigest()==before
    for name in ['build.py','slave.vh','main.cpp']:
        shutil.copy2(here/name,inputs/name)
    mix = inputs/'audio_mixer.v'
    cache = inputs/'audio_sample_cache.v'
    arb = inputs/'axi_sdram_arbiter.v'
    qsf = (inputs/'ap_core.qsf').read_text()
    macros = re.findall(r'^set_global_assignment -name VERILOG_MACRO (\S+)', qsf, re.M)
    assert 'INCLUDE_GPU_WQ16' in macros and 'INCLUDE_OS30_SDRAM166' not in macros
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:=[A-Za-z_0-9]+)?', m) for m in macros):
        raise ValueError('Unsupported QSF macro syntax')
    ap = ports(arb)
    mp = ports(mix)
    assert len(ap) > 90 and len(mp) > 25, (len(ap), len(mp))
    mixer_top = {'clk', 'reset_n', 'mixer_enable', 'voice_wr', 'voice_wr_ready',
                 'voice_field', 'voice_sel', 'voice_sel_rd', 'voice_wdata',
                 'sample_wr', 'sample_data', 'fifo_level', 'pos_readback',
                 'voice_active_mask', 'voice_end_pending'}
    top = [f'{d} wire {w} {n}' for d,w,n in mp if n in mixer_top]
    top += [f'{d} wire {w} {n}' for d,w,n in ap if n.startswith(('m0_', 'm1_', 'dbg_'))]
    top += ['input wire cache_enable', 'input wire [15:0] mem_latency', 'input wire [7:0] mem_beat_gap',
            'input wire periodic_stalls', 'output wire audio_ar_fire',
            'output wire audio_r_fire', 'output wire [7:0] audio_arlen',
            'output wire source_ar_fire', 'output wire source_r_fire',
            'output wire [4:0] fetch_voice', 'output wire fetch_cacheable']
    wires = [f'wire {w} {n};' for _,w,n in ap if n.startswith(('s_', 'm3_'))]
    wires += [f'wire {w} source_{n[2:]};' for _,w,n in mp if n.startswith('m_')]
    mixmap = {n: 'source_'+n[2:] for _,_,n in mp if n.startswith('m_')}
    mixmap.update({
              'master_vol':"8'd255", 'group_vol_0':"8'd255", 'group_vol_1':"8'd255",
              'group_vol_2':"8'd255",'group_vol_3':"8'd255",'voice_group_packed':"64'd0",
              'irq_clear_wr':"1'b0",'irq_clear':"32'd0",'voice_end_irq':''})
    mixconn = [f'.{n}({mixmap.get(n,n)})' for _,_,n in mp]
    cachemap = {'cache_enable':'cache_enable && mixer_enable',
                'request_cacheable':'fetch_cacheable', 'request_voice':'fetch_voice',
                'invalidate_valid':'voice_wr', 'invalidate_voice':'voice_sel'}
    cachemap.update({n: 'source_'+n[2:] for _,_,n in ports(cache) if n.startswith('s_')})
    cachemap.update({n: 'm3_'+n[2:] for _,_,n in ports(cache) if n.startswith('m_')})
    cacheconn = [f'.{n}({cachemap.get(n,n)})' for _,_,n in ports(cache)]
    arbconn = []
    for d,w,n in ap:
        value = n
        if n.startswith('m2_'):
            value = "'0" if d == 'input' else ''
        arbconn.append(f'.{n}({value})')
    wrapper = '`default_nettype none\nmodule tb_contention(\n'+',\n'.join(top)+'\n);\n'
    wrapper += '\n'.join(wires)
    wrapper += '\naudio_mixer mixer(\n'+',\n'.join(mixconn)+'\n);\n'
    wrapper += '\naudio_sample_cache cache(\n'+',\n'.join(cacheconn)+'\n);\n'
    wrapper += '\nassign source_ar_fire = source_arvalid && source_arready;\n'
    wrapper += 'assign source_r_fire = source_rvalid && source_rready;\n'
    wrapper += '\naxi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH(16),.BUFFERED_SLAVE(0)) arb(\n'
    wrapper += ',\n'.join(arbconn)+'\n);\n'
    wrapper += (inputs/'slave.vh').read_text()
    wrapper += '\nendmodule\n`default_nettype wire\n'
    (out/'tb_contention.v').write_text(wrapper)
    command = ['verilator', '--cc', '--exe', '--build', '-j', '4', '-Wno-fatal',
               *['+define+'+m for m in macros],
               '--top-module','tb_contention','--Mdir',str(out/'obj'),
               str(out/'tb_contention.v'),str(mix),str(cache),str(arb),str(inputs/'main.cpp'),
               '-CFLAGS','-O3']
    with (out/'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    # Independent clock/latency cases share no simulator state. Keep concurrency
    # bounded while full-core Quartus may also be running in this workspace.
    def run_case(case):
        mhz, latency = case
        path = out/f'results-{mhz}-{latency}.json'
        with path.open('w') as results:
            subprocess.run([str(out/'obj/Vtb_contention'),str(mhz),str(latency),
                            '3' if a.all22_only else '1' if a.q2_only else '2'],
                           stdout=results,check=True)
        return json.loads(path.read_text())
    with ThreadPoolExecutor(max_workers=2) as pool:
        cases=list(pool.map(run_case,[tuple(map(int,s.split(':'))) for s in a.cases.split(',')]))
    (out/'results.json').write_text(json.dumps({'cases':cases},indent=2)+'\n')
    paths=[mix,cache,arb,inputs/'main.cpp',inputs/'slave.vh',inputs/'build.py',
           inputs/'ap_core.qsf',inputs/'core_top.v',out/'tb_contention.v']
    receipt={'source':str(source),'mixer_override':str(a.mixer.resolve()) if a.mixer else None,
        'cases':a.cases,'q2_only':a.q2_only,'all22_only':a.all22_only,'qsf_macros':macros,'command':command,'inputs':{
        str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths},
        'limitations':'Synthetic saturated CPU/GPU traffic and bounded memory timing; no game FPS claim.'}
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(out/'results.json')


if __name__=='__main__':
    main()
