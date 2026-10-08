#!/usr/bin/env python3
"""Private lean dual-issue RTL validation; component cycles are not game FPS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT / 'docs/measurements/os30-dual-20261004/validation'
sys.path.insert(0, str(ROOT / 'tools'))
from check_pocket_memory_contention import once, pocket_sdram_twin

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(cmd, log, **kwargs):
    with log.open('w') as f:
        subprocess.run(list(map(str, cmd)), check=True, stdout=f, stderr=subprocess.STDOUT, **kwargs)

def prepare():
    out = OUT / 'firmware'
    out.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'src/fpga/test/cpu_stress'
    run(['python3', source / 'generate.py', out], out / 'generate.log')
    run(['python3', ROOT / 'src/fpga/test/cpu_i2f/generate.py', out], out / 'generate-i2f.log')
    start = once((source / 'start.S').read_text(), '  bne t0, t1, 3b', '''  beq t0, t1, sync_trap
  li t1, 0x80000007
  bne t0, t1, 3b
  la t0, irq_count
  lw t1, 0(t0)
  addi t1, t1, 1
  sw t1, 0(t0)
  li t0, 0x400000b8
  li t1, 3
  sw t1, 0(t0)
  j trap_return
sync_trap:''')
    start = once(start, '  lw t0, 0(sp)', 'trap_return:\n  lw t0, 0(sp)')
    (out / 'start.S').write_text(start)
    shutil.copy2(source / 'link.ld', out)
    stress = once((source / 'main.c').read_text(), 'volatile unsigned traps;',
                  'volatile unsigned traps;\nvolatile unsigned irq_count;')
    stress = once(stress, '    f2i_ranges();', '''    *(volatile unsigned *)0x400000b4u = 4001;
    *(volatile unsigned *)0x400000b8u = 3;
    __asm__ volatile("li t0,128; csrs mie,t0; csrsi mstatus,8" ::: "t0", "memory");
    f2i_ranges();''')
    stress = once(stress, '    puts_uart("CPU stress PASS:', '''    __asm__ volatile("csrci mstatus,8" ::: "memory");
    *(volatile unsigned *)0x400000b8u = 2;
    if (irq_count < 200) { puts_uart("IRQ COUNT FAIL\\n"); hex(irq_count); fail(); }
    puts_uart("IRQ count: "); hex(irq_count); puts_uart("\\n");
    puts_uart("CPU stress PASS:''')
    (out / 'stress.c').write_text(stress)
    i2f = (ROOT / 'src/fpga/test/cpu_i2f/main.c').read_text().replace(
        'volatile unsigned traps;', 'volatile unsigned traps;\nvolatile unsigned irq_count;')
    (out / 'i2f.c').write_text(i2f)
    shutil.copy2(HERE / 'kernels.c', out)
    fixtures(out)
    container = ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
        '-v', f'{ROOT}:{ROOT}', '-w', str(out),
        '-e', 'CPATH=/usr/lib/picolibc/riscv64-unknown-elf/include', 'openfpgaos-firmware']
    compiler = ['riscv64-unknown-elf-gcc', '-march=rv32imafc_zicsr_zifencei',
        '-mabi=ilp32f', '-O2', '-ffreestanding', '-fno-builtin', '-nostdlib',
        '-nostartfiles', '-Wl,-T,link.ld', '-Wl,--no-relax', '-ffp-contract=off']
    records = {}
    for name in ['stress', 'i2f', 'kernels']:
        cmd = [*container, *compiler, 'start.S', name + '.c', '-lgcc', '-o', name + '.elf']
        run(cmd, out / (name + '-build.log'))
        run([*container, 'riscv64-unknown-elf-objcopy', '-O', 'binary', name + '.elf', name + '.bin'],
            out / (name + '-objcopy.log'))
        records[name] = {'command': cmd, 'elf_sha256': sha(out / (name + '.elf')),
                         'bin_sha256': sha(out / (name + '.bin'))}
    (out / 'manifest.json').write_text(json.dumps({'programs': records,
        'sources': {str(p): sha(p) for p in out.iterdir() if p.suffix in ('.c', '.h', '.S', '.ld', '.mif')}}, indent=2) + '\n')

def fixtures(out):
    def u(x): return x & 0xffffffff
    def fold(h, x): return u((h ^ u(x)) * 16777619)
    expected = {}
    a,b,c,d = [u(x+4096*y) for x,y in [(1,3),(2,7),(3,11),(4,15)]]
    expected['ALU'] = fold(fold(fold(a,b),c),d)
    x=0x12345678;h=2166136261
    for _ in range(4096):
        x=u(x^u(x<<13));x^=x>>17;x=u(x^u(x<<5))
        h=u(h+x) if x&1 else h^x
        h=u((h<<3)|(h>>29))
    expected['SHIFT']=h
    h=2166136261
    for i in range(4096):
        a=u(i*0x10204081);b=(a&0xffff00ff)|((i&255)<<8)
        c=(b&0xffff)|(((i^0xabcd)&0xffff)<<16)
        h=fold(fold(fold(h,a),b),c)
    expected['RAW']=h
    h=2166136261
    for _ in range(4):
        for i in range(1024):
            x,y,z=[(i*k)%1024-512 for k in [13,17,29]]
            for v in [x-.25*y+.5*z+4,.25*x+y-.125*z-8,-.5*x+.125*y+z+16]:
                h=fold(h,int(v))
    expected['VECTORS']=h
    for name,n in [('CACHE16',4096),('CACHE96',24576)]:
        expected[name]=u(4*sum(u(i*0x10204081) for i in range(n)))
    archive=ROOT/'docs/measurements/os30-mesh-20261004/companion-triangle-host-v1'
    inputs=list(struct.unpack('<%dI'%((archive/'scene-input.bin').stat().st_size//4),
        (archive/'scene-input.bin').read_bytes()))
    words=list(struct.unpack('<%dI'%((archive/'scene-expected.bin').stat().st_size//4),
        (archive/'scene-expected.bin').read_bytes()))
    h=2166136261
    for v in words:h=fold(h,v)
    expected['TRIANGLES']=h
    text='/* Exact independent Python/integer and captured-RTL references. */\n'
    text+='\n'.join(f'#define EXPECT_{k} 0x{v:08x}u' for k,v in expected.items())+'\n'
    text+=f'#define TRIANGLE_JOBS {inputs[0]}u\n'
    for name,values in [('triangle_input',inputs[1:]),('triangle_expected',words)]:
        text+=f'static const uint32_t {name}[]={{\n'
        text+='\n'.join(','.join(f'0x{x:08x}u' for x in values[i:i+12])+',' for i in range(0,len(values),12))+'\n};\n'
    (out/'fixtures.h').write_text(text)
    mathfile=ROOT.parent/'SM64/src/sm64/sm64/src/engine/math_util.c'
    math=mathfile.read_text();start=math.index('void mtxf_mul_vec3s(');end=math.index('\n}',start)+2
    (out/'sm64_vector.h').write_text('typedef float f32;typedef float Mat4[4][4];typedef int16_t Vec3s[3];\n'+math[start:end]+'\n')
    triangle=ROOT/'tools/experiments/os30_mesh_20261004/companion/triangle_firmware.c'
    text=triangle.read_text();text=text[text.index('struct Vertex'):text.index('\nint main(void)')]
    (out/'triangle_packet.h').write_text(text+'\n')
    (out/'workload-origins.json').write_text(json.dumps(dict(
        scope='Exact current SM64 vector function with synthetic inputs; private C triangle setup with captured projected scene inputs; neither is full-game rendering.',
        triangle_jobs=inputs[0],triangle_expected_words=len(words),expected=expected,
        origins={str(p):sha(p) for p in [mathfile,triangle,archive/'scene-input.bin',archive/'scene-expected.bin']}),indent=2)+'\n')

def build(label, netlist, skid_monitor=None):
    out = OUT / label
    out.mkdir(parents=True, exist_ok=False)
    test = ROOT / 'src/fpga/test'
    twin = pocket_sdram_twin(ROOT, out)
    tb = once((test / 'tb_system_sdram.v').read_text(), 'cpu_system cpu_sys (',
              'wire test_timer_irq;\ncpu_system cpu_sys (')
    tb = once(tb, ".int_m_timer   (1'b0)", '.int_m_timer   (test_timer_irq)')
    tb = once(tb, '.timer_irq  ()', '.timer_irq  (test_timer_irq)')
    tb, count = re.subn(r'io_sdram\s+(\w+)\s*\(',
        r"assign phy_ncs = 1'b0;\nio_sdram #(.BANK_ROW_TRACK(1)) \1 (", tb)
    assert count == 1
    tb = once(tb, '    .phy_ncs(phy_ncs),', '')
    cpp = (test / 'tb_system_sdram_main.cpp').read_text()
    cpp = once(cpp, '    uint32_t final_pc =',
        '    std::printf("\\n=== UART BEGIN ===\\n%s\\n=== UART END ===\\n", uart_buf.c_str());\n'
        '    uint32_t final_pc =')
    cpp = once(cpp, '        tick_cycle();\n        if (tb->inj_burst_rd)', '''        tick_half(false);
        const bool aw_take = tb->dbg_aw_valid && tb->dbg_aw_ready;
        const uint32_t aw_addr = tb->dbg_aw_addr, aw_len = tb->dbg_aw_len;
        const bool w_take = tb->dbg_w_valid && tb->dbg_w_ready;
        const uint32_t w_data = tb->dbg_w_data, w_strb = tb->dbg_w_strb;
        tb->vsync = (cpu_cycles % VSYNC_PERIOD == 0);
        tick_half(true); cpu_cycles++;
        if (tb->inj_burst_rd)''')
    cpp = once(cpp, 'if (tb->dbg_aw_valid && tb->dbg_aw_ready)\n'
        '            aw_fifo.push_back({tb->dbg_aw_addr >> 2, tb->dbg_aw_len});',
        'if (aw_take) aw_fifo.push_back({aw_addr >> 2, aw_len});')
    cpp = once(cpp, 'if (tb->dbg_w_valid && tb->dbg_w_ready)\n'
        '            push_w_beat(tb->dbg_w_data, tb->dbg_w_strb, c);',
        'if (w_take) push_w_beat(w_data, w_strb, c);')
    if skid_monitor:
        from skid_monitor import instrument
        shutil.copy2(skid_monitor,out/'skid-observations.json')
        tb,cpp=instrument(tb,cpp,out/'skid-observations.json')
    (out / 'tb_irq.v').write_text(tb)
    (out / 'main.cpp').write_text(cpp)
    (out / 'test.mk').write_text(f'include {test}/Makefile\n'
        f'SYSTEM_SDRAM_SRCS := $(subst tb_system_sdram.v,{out}/tb_irq.v,$(SYSTEM_SDRAM_SRCS))\n'
        f'SYSTEM_SDRAM_SRCS := $(subst io_sdram_mister_test.v,{twin},$(SYSTEM_SDRAM_SRCS))\n')
    cmd = ['make', '-f', out / 'test.mk', out / 'obj/Vtb_system',
           f'SYSTEM_SDRAM_DIR={out}/obj', f'SYSTEM_SDRAM_CPP={out}/main.cpp',
           f'VEXII_MISTER={netlist}', 'VERILATOR=verilator -j 3']
    inputs = [netlist, *out.glob('*.v'), out / 'main.cpp', test / 'Makefile',
        *test.glob('*.v'), *list((ROOT / 'src/fpga/common').glob('*.v')),
        ROOT / 'src/fpga/targets/pocket/io_sdram.v',
        ROOT / 'src/fpga/targets/pocket/cram0_cdc.v',
        ROOT / 'src/fpga/targets/pocket/snac_shifter.v',
        ROOT / 'src/fpga/targets/pocket/apf/common.v']
    if skid_monitor:inputs += [out/'skid-observations.json',HERE/'skid_monitor.py']
    manifest = dict(command=list(map(str, cmd)), netlist=str(netlist),
                    sources={str(p): sha(p) for p in inputs})
    (out / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    run(cmd, out / 'build.log', cwd=test)
    manifest['simulator_sha256'] = sha(out / 'obj/Vtb_system')
    (out / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

def execute(label, programs, modes):
    out = OUT / label
    bench = out / 'obj/Vtb_system'
    manifest = json.loads((out / 'build-manifest.json').read_text())
    assert sha(bench) == manifest['simulator_sha256']
    results = []
    for program in ['stress', 'i2f', 'kernels']:
        for mode in ['quiet', 'scanout']:
            path = out / (program + '-' + mode + '.log')
            env = dict(os.environ, SCAN_ENABLE='1' if mode == 'scanout' else '0',
                       SCAN_PERIOD='700', SCAN_LEN='128', SCAN_PHASE='317',
                       STRIKE_SCAN_PERIOD='1000000000')
            if program in programs and mode in modes:
                run([bench, program + '.bin', '40000000'], path,
                    cwd=OUT / 'firmware', env=env, timeout=600)
            log = path.read_text()
            uart = log.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
            assert 'PASS' in uart and 'FAIL' not in uart, path
            assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in log, path
            result = dict(program=program, mode=mode, log_sha256=sha(path), uart=uart,
                          program_sha256=sha(OUT / 'firmware' / (program + '.bin')),
                          simulator_sha256=sha(bench))
            if program == 'stress':
                result['interrupts'] = int(re.search(r'IRQ count: ([0-9a-f]{8})', uart)[1], 16)
                assert result['interrupts'] >= 200
            if program == 'i2f':
                result['hash'] = re.search(r'I2F hash ([0-9a-f]{8})', uart)[1]
            if program == 'kernels':
                result['kernels'] = [dict(name=m[0], cycles=int(m[1], 16), hash=m[2]) for m in
                    re.findall(r'KERNEL (\w+) ([0-9a-f]{8}) ([0-9a-f]{8})', uart)]
                assert len(result['kernels']) == 14
            result['cycles_to_pass'] = int(re.search(r"'HAL init' at cycle (\d+)", log)[1])
            results.append(result)
            (out / 'results.json').write_text(json.dumps({'scope': __doc__, 'results': results}, indent=2) + '\n')
            print(label, program, mode, result['cycles_to_pass'], flush=True)
    (out / 'results.json').write_text(json.dumps({'scope': __doc__, 'results': results}, indent=2) + '\n')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['prepare', 'build', 'run', 'collect'])
    ap.add_argument('--label')
    ap.add_argument('--netlist', type=Path)
    ap.add_argument('--skid-monitor',type=Path,help='Testbench-only hierarchical skid observation map.')
    ap.add_argument('--program', action='append', choices=['stress','i2f','kernels'],
                    help='Run only these unchanged programs; collect previous logs for the others.')
    ap.add_argument('--mode', action='append', choices=['quiet','scanout'],
                    help='Run only these modes; collect previous logs for the others.')
    a = ap.parse_args()
    if a.action == 'prepare': prepare()
    elif a.action == 'build': build(a.label, a.netlist.resolve(),a.skid_monitor)
    elif a.action == 'collect': execute(a.label, [], [])
    else: execute(a.label, a.program or ['stress','i2f','kernels'], a.mode or ['quiet','scanout'])

if __name__ == '__main__': main()
