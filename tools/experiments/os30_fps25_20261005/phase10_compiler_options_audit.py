#!/usr/bin/env python3
"""Read existing compiler/capture evidence; never compile or change an app."""
import hashlib,json,shlex
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
C=ROOT/'docs/measurements/os30-fps25-20261005'
P=C/'phase10'
O=P/'compiler-options-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    log=P/'load-submit-rebuild-v1/build-normal.log'
    commands=[shlex.split(line) for line in log.read_text().splitlines() if line.startswith('riscv') and 'gcc ' in line]
    comp=[c for c in commands if '-c' in c]
    assert len(commands)==299 and len(comp)==298
    assert all([x for x in c if x.startswith('-O')][-1]=='-O3' for c in comp)
    assert not any(x.startswith(('-flto','-fprofile-','-fauto-profile')) for c in commands for x in c)
    strict=[c for c in comp if '-fno-fast-math' in c]
    assert len(strict)==1 and any('gd_trig_openfpga.c' in x for x in strict[0]) and '-ffp-contract=off' in strict[0]
    result=C/'validation/results-phase2-renderer-lto-v1-fps.json'
    old=json.loads(result.read_text())
    assert old['exact_inputs_except_app'] and old['scene_identical'] and not old['gpu_comparisons']
    decisions=json.loads((C/'phase2/preliminary-decisions.json').read_text())['renderer-lto-v1']
    assert decisions['exact_gpu_output'] is None
    build=json.loads((C/'phase2/renderer-lto-v1/build-diagnostic.json').read_text())
    O.mkdir(exist_ok=False)
    summary=dict(read_only=True,app_changed=False,new_builds_or_tests=False,
      selected_app_sha256=sha(P/'load-submit-rebuild-v1/app.elf'),compile_commands=len(comp),
      effective_optimization='-O3',strict_trig_translation_units=1,lto_enabled=False,pgo_enabled=False,
      renderer_lto_prior=dict(parent=old['baseline'],scope='gfx_pc.c, gfx_gpu.c, gfx_cc.c only',
        gameplay=old['gameplay'],gpu_rendering_qualified=False,decision=decisions['decision']),
      search_scope='Experiment helpers and text/JSON/Makefile evidence in phase9/phase10; no whole-app LTO/PGO experiment found. This is a bounded history search, not proof that no attempt ever occurred.',
      no_new_performance_or_native_claim=True)
    (O/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
    (O/'REPORT.txt').write_text('''The selected bbb1c31a app already uses -O3, not merely the SDK's earlier -O2.
The exact clean ordinary build records 298 compiled translation units and one
link command. Every compile command's final optimization level is -O3; the
renderer and engine use fast-math, single-precision constants, no math errno,
no trapping math, fno-strict-aliasing and fwrapv. The later loop alignment is8,
with function alignment64 and SiFive5 scheduling on unchanged rv32imafc/ilp32f.
One Goddard trig translation unit explicitly restores strict FP and disables
contraction. Selected PROFILE=0/PERF_DIAG=0 and the selected app are unchanged.

No selected compile or link command uses LTO or profile-guided optimization.
The Makefile explicitly leaves whole-game LTO off because upstream display-list
definitions have cross-translation-unit type mismatches. Prebuilt musl/libgcc
also remain ordinary machine code; enabling app LTO alone would not expose them.

LTO has been tried in a narrower, real prior experiment: phase2 renderer-lto-v1
compiled gfx_pc/gfx_gpu/gfx_cc with -flto and linked with -Werror=lto-type-mismatch;
all other app/asset/SDK objects stayed native. Its build and721-frame capture
completed. Against render-v6-fps, gameplay frames360..720 reduced instructions
1.186495% but estimated single-issue CPU cycles only0.201365%. Command word
counts and scene records matched. No actual GPU buffer replay qualified this
LTO candidate, and it was not selected because the modeled gain was too small.
This is historical evidence on a different renderer, not a current-client bound.

The scoped phase9/phase10/helper search found no whole-app LTO or PGO trial.
Thus switching to -O3 is already exhausted; whole-app LTO/PGO remain unmeasured
compiler-wide avenues, not ruled out by small per-function opportunities or by
the earlier three-file LTO result. They would require their own type/FP/ABI and
exact rendering qualification, especially the retained FMA behavior. This audit
proposes no flag change and makes no frame-rate, native CPU or timing claim.
''')
    (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
    refs=[log,P/'load-submit-rebuild-v1/Makefile.sm64',P/'load-submit-rebuild-v1/build-normal.json',
      P/'load-submit-rebuild-v1/app.elf',C/'phase2/preliminary-decisions.json',result,
      C/'phase2/renderer-lto-v1/build-diagnostic.json',C/'phase2/renderer-lto-v1/manifest.json',
      ROOT/'tools/experiments/os30_fps25_20261005/prepare_renderer_lto.py',Path(__file__)]
    manifest=dict(files={p.name:sha(p) for p in O.iterdir() if p.is_file()},
      evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in refs})
    (O/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(sha(O/'manifest.json'))
if __name__=='__main__':main()
