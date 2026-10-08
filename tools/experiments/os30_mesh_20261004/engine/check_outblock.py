#!/usr/bin/env python3
"""Actual RTL OUTBLOCK count/wrap/stall/cancel and state-preservation checks."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess

from isa import Program
from emulator import run

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--label', default='outblock-engine-v1')
a = p.parse_args()
dest = ROOT / 'docs/measurements/os30-mesh-20261004' / a.label
dest.mkdir(exist_ok=False)
rng = random.Random(0x55b10c)
cases = 0
with (dest / 'vectors.bin').open('wb') as f:
    for count in range(1, 257):
        for start in [0, 1, 31, 63, 127, 191, 223, 239, 248, 254, 255, *[rng.randrange(256) for _ in range(6)]]:
            scratch = [rng.getrandbits(32) for _ in range(256)]
            scratch[200] = start
            # Include equal, signed/unsigned unequal comparisons to exercise
            # condition preservation around every block length and wrap point.
            scratch[3] = scratch[4] if cases % 3 == 0 else scratch[3]
            program = Program().emit('ACCSET', a=1, b=2).emit('CMP', a=3, b=4)
            program.emit('OUTBLOCK', count - 1, 200)
            program.jump('equal', 'EQ').emit('OUT', a=5).jump('checked')
            program.label('equal').emit('OUT', a=6).label('checked')
            for i in range(256):
                program.emit('OUT', a=i)
            program.emit('ACCLO', 240).emit('ACCHI', 241)
            program.emit('OUT', a=240).emit('OUT', a=241).emit('HALT')
            code = program.finish()
            result = run(code, scratch=scratch)
            expected = [scratch[(start+i)&255] for i in range(count)]
            expected += [scratch[6 if scratch[3] == scratch[4] else 5]] + scratch + [scratch[2], scratch[1]]
            assert result['output'] == expected
            f.write(struct.pack('<4I', len(code), len(expected), result['cycles'], 256))
            f.write(struct.pack('<%dI' % len(code), *code))
            f.write(struct.pack('<%dI' % len(expected), *expected))
            for i, word in enumerate(scratch):
                f.write(struct.pack('<2I', i, word))
            cases += 1
cpp = (HERE / 'engine_test.cpp').read_text()
old = '            reset();bank.fill(0);upload(code);'
assert cpp.count(old) == 1
cpp = cpp.replace(old, '''            reset();bank.fill(0);
            for(unsigned i=0;i<h[3];i++) {
                uint32_t pair[2];require(fread(pair,4,2,f)==2,"scratch pair read");
                dut.scratch_we=1;dut.scratch_addr=pair[0];dut.scratch_data=pair[1];tick();
            }
            dut.scratch_we=0;upload(code);''')
start = cpp.index('        // Cancellation at every phase')
end = cpp.index('        reset();upload({0xff000000});', start)
cpp = cpp[:start] + '''        unsigned aborts=0;
        for(unsigned count: {1u,17u,256u})for(unsigned delay=0;delay<count+15;delay++)
        for(unsigned ready=0;ready<3;ready++) {
            const std::vector<uint32_t> cancellation={0x01c800f8u,0x3700c800u|((count-1)<<16),0};
            reset();upload(cancellation);dut.start=1;tick();dut.start=0;
            for(unsigned i=0;i<delay;i++) {dut.out_ready=ready==1 || (ready==2 && rnd()%3);tick();}
            dut.cancel=1;dut.clk=0;dut.eval();require(!dut.out_valid&&!dut.bank_we,"abort leaks output/write");
            tick();dut.cancel=0;tick();require(!dut.busy&&!dut.done,"abort did not cancel");
            upload({0x01001234,0x18000000,0});execute({0x1234},30,true);++aborts;
        }
''' + cpp[end:]
(dest / 'outblock_test.cpp').write_text(cpp)
sources = sorted(p for folder in ('engine', 'dma', 'kernel')
                 for p in (HERE.parent / folder).glob('gpu_mesh*.v'))
for source in sources:
    (dest / source.name).write_bytes(source.read_bytes())
for name in ('isa.py', 'emulator.py'):
    (dest / name).write_bytes((HERE / name).read_bytes())
command = ['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal', '--top-module', 'gpu_mesh_engine',
           '--Mdir', str(dest / 'obj'), '-CFLAGS', '-std=c++17 -O2', '-GCOMPACT_LAYOUT=1',
           '-GENABLE_DOT=0', '-GEXTENDED_ISA=0', '-GCODE_PAGES=2',
           *[str(dest / source.name) for source in sources], str(dest / 'outblock_test.cpp')]
with (dest / 'build.log').open('w') as log:
    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
done = subprocess.run([str(dest / 'obj/Vgpu_mesh_engine'), str(dest / 'vectors.bin')], capture_output=True, text=True)
(dest / 'run.log').write_text(done.stdout + done.stderr)
if done.returncode:
    raise RuntimeError(done.stdout + done.stderr)
report = dict(passed=True, cases=cases, result=done.stdout, command=command,
              sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file()})
(dest / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
print(done.stdout, end='')
