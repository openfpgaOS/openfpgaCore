#!/usr/bin/env python3
"""Extract the unchanged optional triangle report from the normal hot path."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-optimize-pass2-20261008'
B = P / 'work/base'
W = P / 'work/cold'
C = P / 'components/cold-v1'
REL = 'src/quake2/openfpga/of_gl1.c'
PARENT = 'a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p, value): p.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def extract(before):
    begin = before.index('\t/* DIAGNOSTIC: report triangles')
    end = before.index('\n\tzscale = ', begin)
    report = before[begin:end]
    guard = '\tif (ofgl_bisect_cv && ofgl_bisect_cv->value && ofgl_dbg_big < 20)\n\t{\n'
    assert report.count(guard) == 1 and report.endswith('\n')
    body = report[report.index(guard) + len(guard):]
    assert body.endswith('\t}\n')
    body = body[:-3]
    body = ''.join(line[1:] if line.startswith('\t') else line for line in body.splitlines(True))
    helper = '''/* Keep optional reporting and its screen-coordinate temporaries off the
 * normal triangle path. The caller retains the original diagnostic guard. */
static void __attribute__((noinline, cold))
ofgl_report_big_tri(const ofgl_cvert_t *v0, const ofgl_cvert_t *v1,
\tconst ofgl_cvert_t *v2, float tw, float th)
{
\tconst ofgl_cvert_t *v[3] = {v0, v1, v2};
\tfloat fx[3], fy[3];
\tint k;
\tfor (k = 0; k < 3; k++)
\t{
\t\tfx[k] = v[k]->sx;
\t\tfy[k] = v[k]->sy;
\t}
''' + body + '}\n\n'
    after = before.replace(report, guard + '\t\tofgl_report_big_tri(v0, v1, v2, tw, th);\n\t}\n', 1)
    start = '/* Emit a finite, visible triangle. Returns 1 if it reached the GPU. */'
    after = after.replace(start, helper + start, 1)
    after = after.replace('\tfloat iw[3], fx[3], fy[3];', '\tfloat iw[3];', 1)
    copies = '\t\tfx[k] = v[k]->sx;\n\t\tfy[k] = v[k]->sy;\n'
    assert after.count(copies) == 2
    at = after.index(start)
    after = after[:at] + after[at:].replace(copies, '', 1)
    return after, helper, report


def main():
    assert sha(B / REL) == PARENT
    assert not C.exists() and not W.exists(), 'Preserve prior experiment'
    before = (B / REL).read_text()
    after, helper, report = extract(before)
    C.mkdir(parents=True)
    shutil.copytree(B, W)
    (W / REL).write_text(after)
    (C / 'of_gl1.c').write_text(after)
    (C / 'report-helper.c').write_text(helper)
    (C / 'old-report.c').write_text(report)
    (C / 'candidate.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='a/' + REL, tofile='b/' + REL)))
    shutil.copy2(__file__, C / 'recipe.py')
    put(C / 'prepared.json', dict(parent_sha256=PARENT, source_sha256=sha(W / REL), changes=[REL], private_worktree=str(W), scope='Move optional big-triangle reporting and its six screen-coordinate loads to a noinline cold helper. Preserve preguard, finite guard placement, reporting limit, diagnostic text, and GPU math/commands.'))
    checks = C / 'checks'; checks.mkdir()
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
    G = W / 'src/quake2'
    includes = [G, G/'src/common/header', G/'src/client/refresh/gl1/glad-gles1/include', W/'src/sdk/include', G/'openfpga']
    flags = ['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie', '-DOF_PC', '-DYQ2_OPENFPGA=1', '-DYQ2_GL1_GLES=1', '-ffunction-sections', '-fdata-sections', '-DYQ2OSTYPE="test"', '-DYQ2ARCH="host"'] + ['-I'+str(p) for p in includes]
    commands=[]
    def run(cmd, name):
        commands.append(dict(argv=cmd, name=name))
        with (checks/(name+'.log')).open('w') as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
    run(flags + [str(W/'tools/tests/quake2_renderer_test.c'), '-Wl,--gc-sections', '-lm', '-o', str(checks/'renderer')], 'renderer-build')
    run([str(checks/'renderer')], 'renderer-run')
    put(checks/'commands.json', commands)
    put(C/'result.json', dict(passed=True, sanitizers=['ASan','UBSan'], actual_renderer=True, performance_measured=False, sibling_modified=False))
    put(C/'manifest.json', dict(passed=True, evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(C.rglob('*')) if p.is_file()}))
    print(json.dumps(json.loads((C/'prepared.json').read_text()), indent=2))
    print('manifest', sha(C/'manifest.json'))


if __name__ == '__main__': main()
