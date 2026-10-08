#!/usr/bin/env python3
"""Freeze an isolated 16-byte lighting memo layout; do not build or stage it."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase2'
BASE = OUT / 'baseline'
DEST = OUT / 'lighting-layout-v1'
HASHES = {
    'gfx_pc.c': 'a5c16f135e4b89006a08891794562329f7b0a56daf0b22bb1cd35d791f7f90b5',
    'gfx_gpu.c': 'c98384c86f8106553f09f66014313f20affd8c2628704d8bcfa1674ca70036fb',
    'gfx_gpu.h': '0d67e4fdc8025d7cc113f4e1add3c8ac5327fc834918cfd35abc1326d51c3e5d',
    'gfx_geometry.h': '02f95cda2cdc7bf7b3c1aef9cc9bf387072440a0c84b4fa24bcec6e0a216a1a6',
    'perf_pocket.h': '77ddb6cea8d240a7ac0aac27effc36f6ff3470f73e871a8a51c662ebf1771036',
    'perf_pocket.c': '6447ea178011abc2852d8f489f668f03974c23d280b4dd2455f1b41f05a0b2fb',
    'Makefile.sm64': 'ec6d12e81e085fce4026cb098b202b9a03ae9cab25e02c4b1666af4fee4bafff',
}
ELF = '6c5ce963d9ff817d69b3f76a6f2074125ee9bbd960ce28089994319a611e5e76'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
OLD = '''struct LightingMemo {uint32_t epoch,normal;struct RGBA color;};
static struct LightingMemo lighting_memo[256];'''
NEW = '''/* A power-of-two stride makes the hot lookup address a single shift.
 * Keep the original key/epoch/RGB fields and 256-entry replacement policy.
 * Align the table so each entry's active twelve bytes stay within its slot. */
struct LightingMemo {uint32_t epoch,normal;struct RGBA color;uint32_t unused_padding;};
_Static_assert(sizeof(struct LightingMemo)==16,"lighting memo stride");
static struct LightingMemo lighting_memo[256] __attribute__((aligned(16)));'''


def main():
    assert not DEST.exists(), DEST
    for name, expected in HASHES.items():
        assert sha(BASE / name) == expected, name
    assert sha(BASE / 'normal.elf') == ELF
    source = (BASE / 'gfx_pc.c').read_text()
    assert source.count(OLD) == 1
    candidate = source.replace(OLD, NEW)
    assert candidate.replace(NEW, OLD) == source
    # Inspect existing emitted code only; this command does not compile.
    asm = subprocess.check_output(['/usr/bin/llvm-objdump', '-d', '--mattr=+m,+a,+f,+c',
        '--no-show-raw-insn', '--disassemble-symbols=gfx_sp_vertex', str(BASE / 'normal.elf')], text=True)
    lookup = [line for line in asm.splitlines()
              if line.startswith(('104cf2d8:', '104cf2f4:', '104cf2f8:', '104cf2fc:',
                  '104cf300:', '104cf304:', '104cf308:', '104cf30c:', '104cf30e:',
                  '104cf312:', '104cf316:', '104cf31a:', '104cf31e:', '104cf320:',
                  '104cf324:', '104cf328:', '104cf32c:', '104cf330:',
                  '104cf64c:', '104cf64e:', '104cf650:'))]
    assert len(lookup) == 21
    DEST.mkdir()
    for name in HASHES:
        shutil.copy2(BASE / name, DEST / name)
    (DEST / 'gfx_pc.c').write_text(candidate)
    (DEST / 'prepare.py').write_bytes(Path(__file__).read_bytes())
    (DEST / 'change.patch').write_text(''.join(difflib.unified_diff(
        source.splitlines(True), candidate.splitlines(True),
        fromfile='baseline/gfx_pc.c', tofile='lighting-layout-v1/gfx_pc.c')))
    (DEST / 'baseline-lookup.txt').write_text('\n'.join(lookup) + '\n')
    after = {name: sha(DEST / name) for name in HASHES}
    assert [name for name in HASHES if HASHES[name] != after[name]] == ['gfx_pc.c']
    record = {
        'parent': 'render-v6', 'candidate': 'lighting-layout-v1', 'qualified': False,
        'runtime_qualified': False, 'source_sha256': after['gfx_pc.c'],
        'parent_sha256': HASHES['gfx_pc.c'], 'source_files': after, 'parent_files': HASHES,
        'generator_sha256': sha(Path(__file__)), 'baseline_elf_sha256': ELF,
        'baseline_lookup_sha256': sha(DEST / 'baseline-lookup.txt'),
        'change': 'Only pad each existing memo from12 to16bytes and align the table to16. Normal key construction is unchanged.',
        'source_proof': {
            'single_declaration_replacement_reversible_exact': True,
            'all_executable_expressions_unchanged': True,
            'other_six_inputs_byte_identical': True,
            'entry_count': 256, 'old_table_bytes': 3072, 'expected_table_bytes': 4096,
            'additional_table_bytes': 1024,
            'unchanged_semantics': 'Epochs, normal keys, hash/index, collisions, RGB lookup/update, alpha, lighting arithmetic, clipping and GPU emission.',
        },
        'codegen_hypothesis': 'Existing lookup uses slli(index,1),add(index),slli(2);16-byte entries permit one slli(4). Existing miss code also reconstructs index*12.',
        'halfword_key_assessment': {
            'included': False,
            'alignment': 'Vtx_t.cn is offset18 in GBI_FLOATS (offset12 otherwise); Vtx aligns at least2. GeometryInputV.cn is offset12 with2-byte-aligned fields. The only two helper callers use these members.',
            'tradeoff': 'An aligned16-bit read plus the last byte can reduce key packing, but the current byte loads are reused when converting signed normals on misses. Measure separately.',
            'portable_approach_if_tested': 'Little-endian aligned __builtin_memcpy of two bytes into uint16_t, with explicit alignment contract; preserve the existing generic/native-endian fallback. No unaligned32-bit loads or strict-aliasing casts.',
        },
        'risks': ['The table occupies4KiB instead of3KiB; cache mapping and neighboring BSS placement can offset fewer address instructions.',
                  'Compiler register allocation and scheduling can change despite identical expressions. No target build or speedup is claimed.'],
        'required': ['Target disassembly verifies reduced address work and4096-byte table.',
                     'Paired gameplay capture and exact scene/GPU validation before selection.'],
    }
    (DEST / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'candidate': str(DEST), 'gfx_pc_sha256': after['gfx_pc.c'],
                      'qualified': False, 'table_byte_increase': 1024}, indent=2))


if __name__ == '__main__':
    main()
