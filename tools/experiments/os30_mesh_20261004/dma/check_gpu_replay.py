#!/usr/bin/env python3
"""Replay projected-triangle jobs through actual DMA, engine, GPU and SDRAM RTL.

Inputs already contain projected vertices. This measures neither transformation
nor clipping nor CPU/game work; it includes real fetch, setup, queue and raster
costs and compares exact complete memory, color/depth and fence output.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PRIOR = ROOT / 'docs/measurements/os30-parallel-20261004'
SOURCE = PRIOR / 'edges-backend'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def replace(s, old, new):
    assert s.count(old) == 1, (old[:100], s.count(old))
    return s.replace(old, new, 1)

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def build(dest, compact=0, enable_dot=1, extended_isa=1, fifo_bits=8, code_pages=3):
    dest.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    for rel, digest in manifest['sources'].items():
        assert sha(SOURCE / rel) == digest, rel
    shutil.copytree(SOURCE / 'frozen', dest / 'frozen')
    stage = load_module('mesh_prepare', HERE.parent / 'prepare.py').stage(
        dest / 'rtl', compact=compact, enable_dot=enable_dot, extended_isa=extended_isa,
        queue_bits=fifo_bits, code_pages=code_pages)
    for source in stage.glob('*.v'):
        if source.name != 'core_top.v':
            shutil.copy2(source, dest / 'frozen/common' / source.name)
    p = dest / 'frozen/tb_gpu.v'
    s = replace(p.read_text(), '    output wire        busy,', '''    output wire mesh_engine_active, mesh_dma_active, mesh_pixel_active,
    output wire mesh_output_blocked, mesh_rd_grant, mesh_rd_beat,
    output wire [2:0] mesh_fault,
    output wire        busy,''')
    s = replace(s, ') gpu (\n    .clk(clk),', ''') gpu (
    .mesh_bus_reset_n(reset_n),
    .mesh_arvalid(mesh_arvalid), .mesh_arready(mesh_arready),
    .mesh_araddr(mesh_araddr), .mesh_arlen(mesh_arlen),
    .mesh_rvalid(mesh_rvalid), .mesh_rready(mesh_rready),
    .mesh_rdata(mesh_rdata), .mesh_rresp(2'b00), .mesh_rlast(mesh_rlast),
    .clk(clk),''')
    start = s.index("    .m2_arvalid(1'b0),")
    end = s.index("    .m2_awvalid(1'b0),", start)
    s = s[:start] + '''    .m2_arvalid(mesh_arvalid), .m2_arready(mesh_arready),
    .m2_araddr(mesh_araddr), .m2_arlen(mesh_arlen),
    .m2_rvalid(mesh_rvalid), .m2_rdata(mesh_rdata),
    .m2_rlast(mesh_rlast), .m2_rready(mesh_rready),
''' + s[end:]
    tail = '''
wire mesh_arvalid, mesh_arready, mesh_rvalid, mesh_rready, mesh_rlast;
wire [31:0] mesh_araddr, mesh_rdata;
wire [7:0] mesh_arlen;
assign mesh_engine_active = gpu.g_geometry_worker.worker.dbg_engine_busy;
assign mesh_dma_active = gpu.g_geometry_worker.worker.dbg_dma_busy;
assign mesh_pixel_active = gpu.state == gpu.S_FRAG_PIPE;
assign mesh_output_blocked = gpu.g_geometry_worker.worker.engine_out_valid &&
                            !gpu.g_geometry_worker.worker.engine_out_ready;
assign mesh_fault = gpu.mesh_fault;
assign mesh_rd_grant = mesh_arvalid && mesh_arready;
assign mesh_rd_beat = mesh_rvalid && mesh_rready;
'''
    index = s.rindex('endmodule')
    p.write_text(s[:index] + tail + s[index:])
    p = dest / 'frozen/replay.cpp'
    s = replace(p.read_text(), 'static uint64_t cycles, busy_cycles;', '''static uint64_t cycles, busy_cycles;
static bool mesh_measure;
static uint64_t mesh_engine,mesh_dma,mesh_pixel,mesh_pair,mesh_all,mesh_blocked,mesh_ar,mesh_beats;
''')
    s = replace(s, '        tb->clk = 1; tb->eval();', '''        if (mesh_measure) {
            mesh_engine += tb->mesh_engine_active;
            mesh_dma += tb->mesh_dma_active;
            mesh_pixel += tb->mesh_pixel_active;
            mesh_pair += tb->mesh_engine_active && tb->mesh_pixel_active;
            mesh_all += tb->mesh_engine_active && tb->mesh_dma_active && tb->mesh_pixel_active;
            mesh_blocked += tb->mesh_output_blocked;
            mesh_ar += tb->mesh_rd_grant; mesh_beats += tb->mesh_rd_beat;
            if (tb->mesh_fault) throw std::runtime_error("Mesh frontend fault");
        }
        tb->clk = 1; tb->eval();''')
    s = replace(s, '    const uint64_t start_cycles = cycles, start_busy = busy_cycles;', '''    const uint64_t start_cycles = cycles, start_busy = busy_cycles;
    mesh_engine=mesh_dma=mesh_pixel=mesh_pair=mesh_all=mesh_blocked=mesh_ar=mesh_beats=0;
    mesh_measure=true;''')
    s = replace(s, '    tb->final();', '''    mesh_measure=false;
    printf("MESH engine=%llu dma=%llu pixel=%llu engine_pixel=%llu all_three=%llu blocked=%llu bursts=%llu beats=%llu\\n",
        (unsigned long long)mesh_engine,(unsigned long long)mesh_dma,(unsigned long long)mesh_pixel,
        (unsigned long long)mesh_pair,(unsigned long long)mesh_all,(unsigned long long)mesh_blocked,
        (unsigned long long)mesh_ar,(unsigned long long)mesh_beats);
    tb->final();''')
    p.write_text(s)
    command = [x.replace(str(SOURCE), str(dest)) for x in manifest['command']]
    for source in sorted((dest / 'frozen/common').glob('gpu_mesh*.v')):
        command.append(str(source))
    manifest.update(command=command, scope=__doc__, fifo_bits=fifo_bits, code_pages=code_pages, sources={str(p.relative_to(dest)): sha(p)
        for p in (dest / 'frozen').rglob('*') if p.is_file()})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (dest / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)

def vector_records(raw):
    offset = 0
    while offset < len(raw):
        start = offset
        tag = raw[offset:offset+4]
        offset += 4
        if tag == b'MEM1':
            _, n = struct.unpack_from('<II', raw, offset)
            offset += 8 + ((n + 3) & ~3)
        elif tag == b'CMDS':
            n, = struct.unpack_from('<I', raw, offset)
            offset += 4 + n * 4
        elif tag == b'RAND': offset += 12
        elif tag == b'DUMP': offset += 8
        elif tag == b'PALB': offset += 4
        elif tag == b'TLUT': offset += 32768
        else: assert tag in (b'FENC', b'FLSH', b'HASH', b'END.'), (tag, start)
        assert offset <= len(raw)
        yield tag, raw[start:offset]
    assert offset == len(raw)

def kernel_records(path):
    raw = path.read_bytes()
    words = struct.unpack('<' + 'I' * (len(raw)//4), raw)
    i = 0
    while i < len(words):
        n, present = words[i:i+2]
        inputs = list(words[i+2:i+26])
        i += 26
        if present:
            packet = [0x61000000 | (9+n), *words[i:i+9+n]]
            i += 9 + n
            yield inputs, packet
        else:
            i += n
    assert i == len(words)

def command_record(words):
    assert len(words) <= 4095
    return b'CMDS' + struct.pack('<' + 'I' * (len(words)+1), len(words), *words)

def make_vectors(name, dest, program):
    source = PRIOR / 'edges-checks' / name / 'planes.vec'
    kernel = HERE.parent / 'kernel/results/reference' / name / 'kernel.bin'
    records = list(vector_records(source.read_bytes()))
    inputs = list(kernel_records(kernel))
    base = 0x03e00000
    end = base + 128 * len(inputs)
    assert end <= 0x03f00000
    output_offset = 0
    hash_ranges = []
    for tag, record in records:
        if tag in (b'MEM1', b'RAND', b'DUMP'):
            address, n = struct.unpack_from('<II', record, 4)
            address &= 0x03ffffff  # GPU vectors include CPU virtual aliases.
            assert address + n <= 0x04000000
            assert end <= address or base >= address+n, (name, tag, hex(address), n)
        if tag == b'DUMP': output_offset += struct.unpack_from('<I', record, 8)[0]
        if tag == b'FENC': output_offset += 4
        if tag == b'HASH':
            hash_ranges.append((output_offset, output_offset+8))
            output_offset += 8
    backing = bytearray(128 * len(inputs))
    for i, (words, _) in enumerate(inputs): struct.pack_into('<24I', backing, i*128, *words)
    memory = b'MEM1' + struct.pack('<II', base, len(backing)) + backing
    baseline = bytearray(memory)
    mesh = bytearray(memory)
    mesh += command_record([0x63000000 | (len(program)+1), 0, *program])
    index = 0
    for tag, record in records:
        baseline += record
        if tag != b'CMDS':
            mesh += record
            continue
        n, = struct.unpack_from('<I', record, 4)
        words = struct.unpack_from('<'+'I'*n, record, 8)
        j = 0
        converted = []
        while j < len(words):
            length = 1 + (words[j] & 8191)
            packet = list(words[j:j+length])
            assert len(packet) == length
            if packet[0] >> 24 == 0x61:
                assert index < len(inputs)
                assert packet == inputs[index][1], (name, index, 'kernel packet does not match scene')
                address = base + index*128
                converted += [0x62000007, address, 24, address+96, 1, address+100, 1, 0]
                index += 1
            else:
                converted += packet
            j += length
        mesh += command_record(converted)
    assert index == len(inputs), (name, index, len(inputs))
    (dest / 'baseline.vec').write_bytes(baseline)
    (dest / 'mesh.vec').write_bytes(mesh)
    return dict(triangles=index, input_address=base, input_bytes=len(backing), hash_ranges=hash_ranges,
                source_sha256=sha(source), kernel_sha256=sha(kernel),
                baseline_sha256=sha(dest/'baseline.vec'), mesh_sha256=sha(dest/'mesh.vec'))

def without_hashes(data, ranges):
    previous = 0
    out = bytearray()
    for start, end in ranges:
        out += data[previous:start]
        previous = end
    return out + data[previous:]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--reuse-build', action='store_true')
    ap.add_argument('--build-dir', type=Path)
    ap.add_argument('--scene', action='append')
    ap.add_argument('--reuse-result', action='append', type=Path, default=[],
                    help='Reuse an already validated scene with this same executable/program.')
    args = ap.parse_args()
    dest = args.out.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE.parent / 'engine'))
    sys.path.insert(0, str(HERE.parent / 'kernel'))
    program = load_module('mesh_microcode', HERE.parent/'kernel/microcode.py').build(fused=True).finish()
    build_dir = args.build_dir.resolve() if args.build_dir else dest / 'build'
    if not args.reuse_build: build(build_dir)
    program_data = struct.pack('<'+'I'*len(program), *program)
    (dest / 'program.bin').write_bytes(program_data)
    reused = []
    for result_path in args.reuse_result:
        result_path = result_path.resolve()
        parent_summary = json.loads((result_path.parent.parent/'result.json').read_text())
        assert parent_summary['program_sha256'] == sha(dest/'program.bin')
        assert parent_summary['executable_sha256']['mesh'] == sha(build_dir/'obj/Vtb_gpu')
        row = json.loads(result_path.read_text())
        assert row['output_sha256'] == sha(result_path.parent/'mesh.bin')
        assert row['exact_memory_and_fence'] and row['original_outputs_unchanged']
        reused.append(dict(row, retained_result=str(result_path)))
    scenes = args.scene or [p.name for p in sorted((PRIOR/'edges-checks').iterdir()) if (p/'planes.vec').exists()]
    scenes = [name for name in scenes if name not in {r['name'] for r in reused}]
    def run(name):
        result_dir = dest / name
        result_dir.mkdir(exist_ok=False)
        info = make_vectors(name, result_dir, program)
        traffic = int(re.search(r'-m(\d+)-', name)[1])
        exes = dict(baseline=SOURCE/'obj/Vtb_gpu', mesh=build_dir/'obj/Vtb_gpu')
        cycles = {}
        for label in ['baseline', 'mesh']:
            with (result_dir/(label+'.log')).open('w') as log:
                subprocess.run([str(exes[label]), str(result_dir/(label+'.vec')),
                                str(result_dir/(label+'.bin')), f'+memory_traffic={traffic}'],
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1200)
            text = (result_dir/(label+'.log')).read_text()
            cycles[label] = int(re.search(r'RESULT cycles=(\d+)', text)[1])
        before = (result_dir/'baseline.bin').read_bytes()
        after = (result_dir/'mesh.bin').read_bytes()
        assert before == after, (name, 'complete memory/fence mismatch', len(before), len(after),
            sum(a != b for a, b in zip(before, after)))
        original = (PRIOR/'edges-checks'/name/'backend.bin').read_bytes()
        assert without_hashes(before, info['hash_ranges']) == without_hashes(original, info['hash_ranges']), (name, 'input memory clobbered original outputs')
        counters = re.search(r'MESH (.+)', (result_dir/'mesh.log').read_text())[1]
        counters = {k: int(v) for k,v in re.findall(r'(\w+)=(\d+)', counters)}
        result = dict(name=name, traffic=traffic, exact_memory_and_fence=True,
                      original_outputs_unchanged=True, cycles=cycles, counters=counters,
                      projected_job_gain_percent=100*(cycles['baseline']/cycles['mesh']-1),
                      output_sha256=sha(result_dir/'mesh.bin'), **info)
        (result_dir/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result), flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool: results=reused+list(pool.map(run,scenes))
    (dest/'result.json').write_text(json.dumps(dict(passed=True, scope=__doc__, program_words=len(program),
        program_sha256=sha(dest/'program.bin'), build_dir=str(build_dir),
        executable_sha256=dict(baseline=sha(SOURCE/'obj/Vtb_gpu'), mesh=sha(build_dir/'obj/Vtb_gpu')),
        results=results), indent=2)+'\n')

if __name__ == '__main__': main()
