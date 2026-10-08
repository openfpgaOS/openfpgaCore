#!/usr/bin/env python3
"""Real MMIO soft reset during live lookahead, then invalid/valid recovery."""
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
O = P / 'gpu-control-owner-review-v1/abort'
BASE = P / 'gpu-recip-write-review-v2/model'
NEW = P / 'gpu-control-owner-review-v1/model'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


O.mkdir(exist_ok=False)
source = (NEW / 'frozen/replay.cpp').read_text()
assert source == (BASE / 'frozen/replay.cpp').read_text()
anchor = "        if(tag==GV_TAG('T','E','X','1')){"
assert source.count(anchor) == 1
source = source.replace(anchor, '''        if(tag==GV_TAG('A','B','R','T')){
            unsigned phase=rd32();
            uint64_t start=cycles;
            while(tb->rootp->tb_gpu__DOT__gpu__DOT__span_la!=phase){
                if(cycles-start>2000000)throw std::runtime_error("active lookahead abort not reached");
                tick();
            }
            if(!tb->rootp->tb_gpu__DOT__gpu__DOT__cmd_is_tri_walker)
                throw std::runtime_error("abort does not have tri owner");
            printf("OWNER_ABORT phase=%u live=1\\n",phase);
            mmio_write(0,2); // public GPU_CTRL soft-reset bit; no DUT backdoor
            tick(64);
            if(tb->rootp->tb_gpu__DOT__gpu__DOT__span_la!=0 ||
               tb->rootp->tb_gpu__DOT__gpu__DOT__cmd_is_tri_walker)
                throw std::runtime_error("soft reset retained lookahead owner");
        }else if(tag==GV_TAG('T','E','X','1')){''')
(O / 'replay-abort.cpp').write_text(source)
save(O / 'prepared.json', dict(baseline_gpu_sha256=sha(BASE / 'frozen/common/gpu_core.v'),
    candidate_gpu_sha256=sha(NEW / 'frozen/common/gpu_core.v'),
    replay_parent_sha256=sha(NEW / 'frozen/replay.cpp'),
    host_sha256=sha(O / 'replay-abort.cpp'),
    scope='Only host ABRT token added. Passive read waits for selected live lookahead phase, then public MMIO CTRL bit1 resets GPU. RTL/models unchanged; complete output and cycle comparison after invalid/valid command recovery.'))
bindings = {}
for name, model in [('baseline', BASE), ('candidate', NEW)]:
    obj = model / 'obj'
    objects = [obj / n for n in ('verilated.o', 'verilated_dpi.o', 'verilated_threads.o', 'Vtb_gpu__ALL.a')]
    cmd = ['g++', '-std=c++17', '-O2', '-I' + str(obj), '-I' + str(model / 'frozen'),
           '-I/usr/share/verilator/include', '-I/usr/share/verilator/include/vltstd',
           '-DVERILATOR=1', '-DVM_TRACE=0', '-DVM_TRACE_FST=0', '-DVM_TRACE_VCD=0',
           '-DVM_TRACE_SAIF=0', str(O / 'replay-abort.cpp')]
    cmd += [str(p) for p in objects] + ['-pthread', '-lpthread', '-latomic', '-o', str(O / name)]
    save(O / (name + '-command.json'), cmd)
    with (O / (name + '-build.log')).open('x') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    for p in objects + [obj / 'Vtb_gpu.h', obj / 'Vtb_gpu___024root.h']:
        bindings[str(p.relative_to(R))] = sha(p)

pack = lambda *words: struct.pack('<' + 'I' * len(words), *[x & 0xffffffff for x in words])
cmd = lambda op, words: [op << 24 | len(words)] + words
fb, z, tex = 0x100000, 0x180000, 0x10000
texture = pack(0xffffffff) + bytes(60)
prefix = b'TEX1' + pack(tex, len(texture)) + texture
prefix += b'MEM1' + pack(tex, len(texture)) + texture
prefix += b'RAND' + pack(fb, 128 * 128 * 2, 0x92134)
prefix += b'MEM1' + pack(z, 128 * 128 * 2) + bytes(128 * 128 * 2)
state = [fb, 256, 2, tex, 1, 0, 0xa0 | 1 << 12 | 1 << 31, 0, 0, 0, 0,
         z, 256, 2, 128 << 16, 128 << 16]
xy = [(4 * 16, 4 * 16), (100 * 16 + 3, 4 * 16 + 1), (4 * 16 + 1, 100 * 16 + 2)]
tri = [(y & 65535) << 16 | (x & 65535) for x, y in xy]
tri += [0, 0, 0] + [0, 0, 0] + [65536, 32768, 49152]
tri += [0x07e0f800, 0x001f] + [0x20000000, 0x60000000, 0x40000000]
draw = cmd(0x4a, state) + cmd(0x4e, tri)
invalid = cmd(0xff, [0x12345678]) + cmd(0x4e, []) + cmd(0x54, [0])
recovery = invalid + draw + cmd(2, [0x76c019])
rows = []
for phase in (1, 3):  # LA_SELECT and LA_WAIT, while first span owns lookahead
    vector = prefix + b'CMDS' + pack(len(draw)) + pack(*draw) + b'ABRT' + pack(phase)
    vector += b'CMDS' + pack(len(recovery)) + pack(*recovery)
    vector += b'DUMP' + pack(fb, 128 * 128 * 2) + b'DUMP' + pack(z, 128 * 128 * 2) + b'FENCEND.'
    vp = O / f'phase{phase}.vec'
    vp.write_bytes(vector)
    for traffic in (0, 2):
        stats = {}
        for name in ('baseline', 'candidate'):
            output = O / f'{name}-phase{phase}-t{traffic}.bin'
            log = O / f'{name}-phase{phase}-t{traffic}.log'
            command = [str(O / name), '+fast_tex=1', '+tagged_tex=1',
                       f'+memory_traffic={traffic}', str(vp), str(output)]
            with log.open('x') as f:
                subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)
            text = log.read_text()
            assert 'FATAL' not in text and f'OWNER_ABORT phase={phase} live=1' in text
            stats[name] = re.search(r'RESULT cycles=(\d+) busy_cycles=(\d+) batches=(\d+)', text).groups()
            if name == 'candidate':
                counts = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', re.search(r'CONTROL_OWNER_AUDIT (.*)', text)[1])}
                assert counts['soft_resets'] > 0 and counts['live_la'] > 0 and counts['handoffs'] > 0
        assert stats['baseline'] == stats['candidate']
        assert (O / f'baseline-phase{phase}-t{traffic}.bin').read_bytes() == (O / f'candidate-phase{phase}-t{traffic}.bin').read_bytes()
        rows.append(dict(phase=phase, traffic=traffic, cycles=int(stats['candidate'][0]),
                         counts=counts, bytes_equal=True, cycles_equal=True))
save(O / 'result.json', dict(passed=True, pairs=4, rows=rows,
    supported_recovery_and_unsupported_wrong_size_noop=True,
    real_mmio_soft_reset_while_lookahead_active=True,
    limits=['Active abort targets LA_SELECT/LA_WAIT before the first span emits pixels; arbitrary in-flight AXI abort semantics are outside this change. Hard reset is independently exercised by every run and literal next-state tests.']))
save(O / 'manifest.json', dict(passed=True,
    files={str(p.relative_to(O)): sha(p) for p in O.iterdir() if p.is_file()},
    evidence_sha256=bindings))
print('ABORT', sha(O / 'manifest.json'))
