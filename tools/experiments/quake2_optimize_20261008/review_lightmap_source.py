#!/usr/bin/env python3
"""Read existing evidence only; no compilation, simulation or product writes."""
import ast
import csv
import hashlib
import json
import shlex
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-optimize-20261008'
O = P / 'lightmap-source-review-v1'
S = P / 'demo-screen-v2'
L = P / 'components/lightmap-v1'
E = {}

def bind(p):
    p = Path(p)
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    E[str(p.relative_to(R))] = h
    return h

def txt(p):
    bind(p)
    return p.read_text()

def js(p):
    return json.loads(txt(p))

def manifest(p):
    data = js(p)
    for rel, expected in data['evidence_sha256'].items():
        assert bind(R / rel) == expected, rel
    return data

assert not O.exists()
manifest(L / 'manifest.json')
manifest(P / 'core-compat-v1/manifest.json')
manifest(P / 'comparison-lightmap-v1/manifest.json')
manifest(P / 'comparison-combined-v1/manifest.json')

baseline = txt(P / 'work/baseline/src/quake2/openfpga/of_gl1.c')
candidate = txt(L / 'of_gl1.c')
old, new = txt(L / 'old-function.c'), txt(L / 'new-function.c')
assert baseline.count(old) == 1 and candidate.count(new) == 1
assert baseline.replace(old, new) == candidate
assert candidate.replace(new, old) == baseline
assert 'ofgl_lm_store(' in old and 'ofgl_lm_store(' in new
assert bind(L / 'of_gl1.c') == 'a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'
literal = txt(L / 'checks/literal.c')
assert old.replace('ofgl_lm_store', 'original_store') in literal
assert new.replace('ofgl_lm_store', 'candidate_store') in literal
assert 'PASS rectangle_cases=28930 black_and_serial_wrap=1' in txt(L / 'checks/literal-run.log')
assert txt(L / 'checks/baseline-renderer.log') == txt(L / 'checks/candidate-renderer.log')
assert 'Quake2 renderer regressions passed' in txt(L / 'checks/candidate-renderer.log')
# The actual GL1 callers allocate bounded lightmap atlas rectangles; arbitrary
# overflowing signed coordinates are outside this component's stated domain.
txt(P / 'work/baseline/src/quake2/src/client/refresh/gl1/gl1_surf.c')

build = js(P / 'build-lightmap-v1/result.json')
assert build['returncode'] == 0 and build['of_gl1_sha256'] == bind(L / 'of_gl1.c')
assert build['elf_sha256'] == bind(P / 'build-lightmap-v1/app.elf') == bind(S / 'lightmap.elf')
js(P / 'build-lightmap-v1/command.json')
def compile_arguments(p):
    lines = [line for line in txt(p).splitlines()
             if line.startswith('riscv64-unknown-elf-gcc ') and line.endswith(' openfpga/of_gl1.c')]
    assert len(lines) == 1
    args = shlex.split(lines[0])
    n = args.index('-o')
    return args[:n] + ['-o', '<private-output>'] + args[n+2:]
args = compile_arguments(P / 'baseline-build.log')
assert args == compile_arguments(P / 'build-lightmap-v1/build.log')
for flag in ('-march=rv32imafc', '-mabi=ilp32f', '-O3', '-flto=auto', '-ffast-math', '-fwrapv'):
    assert flag in args

adapt = js(S / 'harness-adaptation.json')
ext = Path(adapt['reviewed_extension'])
assert bind(ext) == adapt['reviewed_extension_sha256']
fn = next(n for n in ast.parse(txt(ext)).body if isinstance(n, ast.FunctionDef) and n.name == 'prepare')
expected = []
for st in fn.body:
    if (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)
        and isinstance(st.value.func, ast.Name) and st.value.func.id == 'replace'):
        name, before, after = map(ast.literal_eval, st.value.args)
        expected.append(dict(file=name, before=before, after=after))
expected += [dict(file=n, before=a, after=b) for n,a,b in (
    ('env.c','0x2913775bu','0x2b136753u'),
    ('env.c','ENV_GPU_BASE, 0, 1,','ENV_GPU_BASE, 0x01000000u, 1,'),
    ('cache.c','p->ic_sets = 256;','p->ic_sets = 512;'),
    ('cache.c','p->dc_sets = 1024;','p->dc_sets = 512;'))]
assert adapt['substitutions'] == expected
sources = sorted(p for p in (P/'demo-screen-v1/qsim').iterdir()
                 if p.suffix in ('.c','.h') or p.name == 'Makefile')
for p in sources:
    text = txt(p)
    for change in expected:
        if change['file'] == p.name:
            assert text.count(change['before']) == 1
            text = text.replace(change['before'], change['after'])
    assert text == txt(S / 'qsim' / p.name), p.name
prepared = js(S / 'prepared.json')
for rel,h in prepared['inputs'].items():
    assert bind(S / rel) == h, rel
assert bind(S / 'qsim/qsim') == prepared['qsim_sha256']
recipe = txt(S / 'recipe.py')
assert "k.startswith(('SM64_', 'QSIM_'))" in recipe and 'del env[k]' in recipe
assert '--no-normalize-build-stamp' in recipe and "'--mem','flat'" in recipe
assert 'GEOMETRY=0' in recipe
assert bind(S / 'recipe.py') == prepared['recipe_sha256']
assert prepared['capability_hw_flags'] == '0x2b136753'
assert prepared['geometry_status_bit10'] is False
caps = js(P / 'core-compat-v1/result.json')
assert caps['old']['hw_features'] == caps['new']['hw_features'] == '0x2b136753'
assert caps['new']['gpu_status_geometry_bit10'] == 0

rows = {}
for label in ('baseline', 'lightmap', 'combined'):
    result = js(S / (label + '-result.json'))
    assert result['passed'] and result['prepared_sha256'] == bind(S / 'prepared.json')
    assert result['elf_sha256'] == bind(S / (label + '.elf'))
    summary = js(S / label / 'summary.json')
    assert summary['frames'] == 362 and summary['stop_reason'] == 'frame limit'
    assert summary['env']['unmapped_mmio'] == summary['gpu_sync']['ring_overflows'] == 0
    assert 'GEOMETRY=0' in txt(S / label / 'console.log')
    txt(S / label / 'frames.csv')
    rows[label] = list(csv.DictReader((S / label / 'frames.csv').open()))
    assert len(rows[label]) == 362
for key in ('frame','host_framecount','buffer','gpu_cmd_words','hash_cmd','hash_px','hash_z','hash_pal'):
    assert [x[key] for x in rows['baseline']] == [x[key] for x in rows['lightmap']], key
cycles = {k: sum(int(x['est_cycles']) for x in v[20:360])/340 for k,v in rows.items()}
assert cycles['lightmap'] < cycles['combined'] < cycles['baseline']
selection = js(P / 'rotation-selection.json')
comparison = js(P / 'comparison-lightmap-v1/result.json')
assert comparison['modeled_cpu_percent_saved'] == 100*(cycles['baseline']-cycles['lightmap'])/cycles['baseline']
for pair in comparison['sampled_rtl_pairs']:
    assert pair['commands_textures_mmio_exact'] and pair['rendered_depth_nonzero_pixels'] > 0

result = dict(passed=True, passed_for_lightmap_source_and_paired_model=True,
    selected_source_sha256=bind(L/'of_gl1.c'), selected_normal_elf_sha256=build['elf_sha256'],
    source_delta='Exactly ofgl_lm_store; conversion, allocation/null handling, source stride and serial/version updates retained.',
    semantic_review=[
        'Version increments before empty/disjoint return, just as original.',
        'Positive dimensions short-circuit before -w/-h; disjoint check prevents negating INT_MIN origins.',
        'Clipped loops visit the same in-bounds coordinates in the same row/column order with original source stride.',
        'Black stays ordinary RGB565 zero, and unsigned serial wrap is preserved.',
        'Scope is valid source rectangles and nonoverflowing original signed coordinate sums; no arbitrary malformed GL-input equivalence claim.'],
    existing_proof=dict(rectangle_cases=28930, sanitizers=['ASan','UBSan'], actual_renderer_both_pass=True,
        actual_target_compile_and_link_pass=True, compiler_flags_unchanged=True),
    harness=dict(exact_old_source_plus_reviewed_changes=True, source_files=len(sources),
        hw_features='0x2b136753', geometry_status_bit10=False, texture_bytes=16777216,
        cache_sets=dict(instruction=512,data=512), environment_overrides_removed=True,
        elf_instructions_unpatched=True, fast_upload_busy_instantaneous=True),
    paired_model=dict(steady_frames=340, indices=[20,359], cycles_per_frame=cycles,
        lightmap_percent_saved=comparison['modeled_cpu_percent_saved'], software_image_hash_frames=362,
        sampled_rtl_pairs=4),
    rotation_selection='Rejected for production: adding memo increases modeled cycles versus lightmap-only; component correctness evidence remains archived.',
    limitations=comparison['limits'],
    reviewer_scope='Independent read-only review of lightmap and harness; reviewer authored excluded rotation component.',
    new_builds_simulations_or_cad=0, sibling_writes=False, production_changed=False)
O.mkdir()
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n')
bind(O/'result.json');bind(Path(__file__))
(O/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256=E),indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(output=str(O),manifest_sha256=hashlib.sha256((O/'manifest.json').read_bytes()).hexdigest(),bindings=len(E))))
