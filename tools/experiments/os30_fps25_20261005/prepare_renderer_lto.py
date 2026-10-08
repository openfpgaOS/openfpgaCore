#!/usr/bin/env python3
"""Freeze the renderer-only LTO experiment; never build or modify its stage."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
PHASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase2'
DEST = PHASE / 'renderer-lto-v1'
FILES = ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h',
         'perf_pocket.h', 'perf_pocket.c', 'Makefile.sm64')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    DEST.mkdir(exist_ok=False)
    baseline = PHASE / 'baseline'
    for name in FILES:
        shutil.copy2(baseline / name, DEST / name)
    source = (baseline / 'Makefile.sm64').read_text()
    old_note = ('# NOTE: -flto is intentionally OFF — the upstream SM64 decomp has benign\n'
                '# cross-TU display-list type mismatches (-Wlto-type-mismatch) and the\n'
                '# original Makefile disabled LTO; enable it only after a hardware soak.\n')
    new_note = ('# Whole-game LTO remains disabled: upstream display-list asset definitions\n'
                '# have cross-TU type mismatches. Only the three renderer translation\n'
                '# units below participate in LTO; all assets and SDK objects stay native.\n')
    anchor = 'SDK_CFLAGS += -mtune=sifive-5-series\n'
    addition = ('# Keep LTO inside the renderer boundary. private prevents these flags\n'
                '# from reaching prerequisites through target-specific inheritance.\n'
                'SM64_RENDERER_LTO_SRCS := $(SRC)/pc/gfx/gfx_pc.c \\\n'
                '                          $(SRC)/pc/gfx/gfx_gpu.c \\\n'
                '                          $(SRC)/pc/gfx/gfx_cc.c\n'
                'SM64_RENDERER_LTO_OBJS := $(foreach src,$(SM64_RENDERER_LTO_SRCS),$(call sdk_app_object,$(src)))\n'
                '$(SM64_RENDERER_LTO_OBJS): private CFLAGS += -flto\n'
                'LDFLAGS += -flto -Werror=lto-type-mismatch\n')
    assert source.count(old_note) == source.count(anchor) == 1
    candidate = source.replace(old_note, new_note).replace(anchor, anchor + addition)
    (DEST / 'Makefile.sm64').write_text(candidate)
    (DEST / 'change.patch').write_text(''.join(difflib.unified_diff(
        source.splitlines(True), candidate.splitlines(True),
        fromfile='baseline/Makefile.sm64', tofile='renderer-lto-v1/Makefile.sm64')))
    original_inputs = json.loads((PHASE / 'source-baseline.json').read_text())['files']
    inputs = DEST / 'review-inputs'
    inputs.mkdir()
    reviewed = {}
    for rel in ('src/sdk/sdk.mk', 'src/sdk/include/of_gpu.h',
                'src/sdk/include/of_syscall.h', 'src/sdk/include/of_cache.h',
                'src/sm64/sm64/src/pc/gfx/gfx_cc.c',
                'src/sm64/sm64/src/pc/gfx/gfx_cc.h',
                'src/sm64/sm64/src/pc/gfx/gfx_pc.h',
                'src/sm64/sm64/src/pc/gfx/gfx_rendering_api.h'):
        path = PHASE / 'SM64' / rel
        assert sha(path) == original_inputs[rel], rel
        shutil.copy2(path, inputs / path.name)
        reviewed[rel] = sha(path)
    manifest = {
        'candidate': 'renderer-lto-v1', 'parent': 'render-v6',
        'prepared_only': True, 'qualified': False,
        'changed_files': ['Makefile.sm64'],
        'files': {name: {'sha256': sha(DEST / name),
                         'parent_sha256': sha(baseline / name),
                         'unchanged': (DEST / name).read_bytes() == (baseline / name).read_bytes()}
                  for name in FILES},
        'review_input_sha256': reviewed,
        'generator_sha256': sha(Path(__file__)),
        'compile_lto_sources': ['sm64/src/pc/gfx/gfx_pc.c',
                                'sm64/src/pc/gfx/gfx_gpu.c',
                                'sm64/src/pc/gfx/gfx_cc.c'],
        'link_added_flags': ['-flto', '-Werror=lto-type-mismatch'],
        'non_lto_scope': 'Other app TUs, extracted asset definitions, SDK init/SDL objects, CRT, musl and libgcc remain ordinary machine-code objects.',
        'unchanged_policy': 'Keep existing O3/fast-math, no-strict-aliasing, wrapv, single-precision constants, loop alignment and SiFive5 tuning. No whole-program, ABI, GPU/core or rendering-source change.',
        'build_note': 'Existing Makefile dependency invalidates every app object after a new Makefile timestamp. An incremental experiment may deliberately preserve unchanged ordinary objects, but must force all three renderer objects to rebuild and force relink. A clean build also remains correct.',
        'required_gates': [
            'Compile log/object-section audit: exactly three renderer TUs contain LTO IR; ordinary objects have none and retain prior hashes if reused.',
            'Fail on LTO type mismatch; confirm GCC plugin actually runs at link and preserves target/optimization options.',
            'Inspect exported interfaces and renderer code size/stack/register/FP contraction changes.',
            'Paired gameplay trace/model and exact GPU color/depth replay; confirm command/capability invariants and memory barriers.',
            'Use actual A CPU or hardware evidence before treating a modeled gain as a real frame-rate improvement.'
        ]
    }
    (DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(DEST)


if __name__ == '__main__':
    main()
