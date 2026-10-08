#!/usr/bin/env python3
"""Private, exact lightmap rectangle clipping component. Never writes Quake2."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-optimize-20261008'
B = P / 'work/baseline'
O = P / 'components/lightmap-v1'
REL = 'src/quake2/openfpga/of_gl1.c'
PARENT = 'fd93411dd58ec75dc931928c5bf1303cf6e85ede607ca9a3cb85468d69e8de54'
START = 'static void\nofgl_lm_store('
END = '\nstatic inline uint16_t\nofgl_lm_sample'
NEW = '''static void
ofgl_lm_store(int block, int x0, int y0, int w, int h, const uint32_t *src)
{
	uint16_t *page = ofgl_lm_page(block);

	if (!page || !src)
	{
		return;
	}

	ofgl_lm_version[block] = ++ofgl_lm_serial;
	/* Clip once, retaining the original source row stride. Test disjoint
	 * rectangles before negating an origin, including INT_MIN origins. */
	if (w <= 0 || h <= 0 || x0 >= OFGL_LM_DIM || y0 >= OFGL_LM_DIM ||
		x0 <= -w || y0 <= -h)
	{
		return;
	}
	int first_col = x0 < 0 ? -x0 : 0;
	int first_row = y0 < 0 ? -y0 : 0;
	int dx = x0 + first_col, dy = y0 + first_row;
	int columns = w - first_col, rows = h - first_row;
	if (columns > OFGL_LM_DIM - dx) columns = OFGL_LM_DIM - dx;
	if (rows > OFGL_LM_DIM - dy) rows = OFGL_LM_DIM - dy;
	for (int row = 0; row < rows; row++)
	{
		const uint32_t *in = src + (size_t)(first_row + row) * w + first_col;
		uint16_t *out = page + (dy + row) * OFGL_LM_DIM + dx;
		for (int col = 0; col < columns; col++)
		{
			/* Black is a valid modulation colour, not a transparent key. */
			out[col] = ofgl_rgba_to_565(in[col]);
		}
	}
}
'''


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p, v): p.write_text(json.dumps(v, indent=2, sort_keys=True) + '\n')
def block(text): return text[text.index(START):text.index(END)]


def prepare():
    assert not O.exists(), 'Preserve existing stage'
    assert sha(B / REL) == PARENT
    before = (B / REL).read_text()
    old = block(before)
    assert before.count(old) == 1
    after = before.replace(old, NEW)
    O.mkdir(parents=True)
    shutil.copy2(__file__, O / 'recipe.py')
    (O / 'of_gl1.c').write_text(after)
    (O / 'old-function.c').write_text(old)
    (O / 'new-function.c').write_text(NEW)
    (O / 'candidate.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile='a/' + REL, tofile='b/' + REL)))
    put(O / 'prepared.json', dict(parent_sha256=PARENT, candidate_sha256=sha(O / 'of_gl1.c'),
        changed_files=[REL], scope='One function; no conversion, version, null or allocation semantic change',
        source_precondition='Original source rectangle storage is valid and original signed coordinate additions do not overflow. Actual GL uploads satisfy these conditions.',
        outside_workspace_writes=False))
    print(json.dumps(json.loads((O / 'prepared.json').read_text())))


def proof_source():
    before = (B / REL).read_text()
    conv = before[before.index('static inline uint16_t\nofgl_rgba_to_565'):before.index('/* src over dst')]
    old = (O / 'old-function.c').read_text().replace('ofgl_lm_store(', 'original_store(')
    new = (O / 'new-function.c').read_text().replace('ofgl_lm_store(', 'candidate_store(')
    return r'''#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#define OFGL_LM_DIM 128
static uint16_t storage[128*128];
static uint32_t ofgl_lm_version[4], ofgl_lm_serial;
static int page_calls, page_enabled;
static uint16_t *ofgl_lm_page(int block) { page_calls++; return block>=0 && block<4 && page_enabled ? storage : NULL; }
''' + conv + old + new + r'''
static uint32_t rng=0x73529e1u;
static uint32_t random32(void) { rng^=rng<<13; rng^=rng>>17; rng^=rng<<5; return rng; }
static uint16_t expected[128*128];
static uint32_t source[256*256];
static unsigned cases;
static void check(int x, int y, int w, int h, int block, int enabled, int null_source) {
    uint32_t version[4], serial=random32();
    if ((cases&15)==0) for(unsigned i=0;i<256*256;i++)source[i]=random32();
    for (unsigned i=0;i<4;i++)version[i]=random32();
    memset(storage, 0xa6, sizeof storage);
    memcpy(ofgl_lm_version,version,sizeof version); ofgl_lm_serial=serial; page_calls=0;page_enabled=enabled;
    original_store(block,x,y,w,h,null_source ? NULL : source);
    memcpy(expected,storage,sizeof storage);
    uint32_t endversion[4];memcpy(endversion,ofgl_lm_version,sizeof endversion);
    uint32_t endserial=ofgl_lm_serial; int calls=page_calls;
    memset(storage, 0xa6, sizeof storage);
    memcpy(ofgl_lm_version,version,sizeof version); ofgl_lm_serial=serial;page_calls=0;
    candidate_store(block,x,y,w,h,null_source ? NULL : source);
    assert(!memcmp(expected,storage,sizeof storage));assert(!memcmp(endversion,ofgl_lm_version,sizeof endversion));
    assert(endserial==ofgl_lm_serial && calls==page_calls);cases++;
}
int main(void) {
    const int origins[]={-256,-129,-128,-127,-1,0,1,63,126,127,128,129,255};
    const int lengths[]={-2,0,1,2,7,127,128,129,255,256};
    for(unsigned x=0;x<sizeof origins/sizeof *origins;x++)for(unsigned y=0;y<sizeof origins/sizeof *origins;y++)
    for(unsigned w=0;w<sizeof lengths/sizeof *lengths;w++)for(unsigned h=0;h<sizeof lengths/sizeof *lengths;h++)
        check(origins[x],origins[y],lengths[w],lengths[h],1,1,0);
    for(unsigned i=0;i<12000;i++)check((int)(random32()%513)-256,(int)(random32()%513)-256,
        random32()%257,random32()%257,random32()%6-1,random32()%4!=0,random32()%8==0);
    for(int block=-1;block<=4;block++)for(int enabled=0;enabled<=1;enabled++)for(int nul=0;nul<=1;nul++)
        check(-2,-3,9,11,block,enabled,nul);
    check(INT_MIN,0,256,1,1,1,0);check(INT_MAX,0,1,1,1,1,0);
    check(0,INT_MIN,1,256,1,1,0);check(0,INT_MAX,1,1,1,1,0);
    check(0,0,INT_MIN,1,1,1,0);check(0,0,1,INT_MIN,1,1,0);
    source[0]=0;ofgl_lm_serial=UINT32_MAX;candidate_store(0,0,0,1,1,source);
    assert(storage[0]==0 && ofgl_lm_serial==0 && ofgl_lm_version[0]==0);
    printf("PASS rectangle_cases=%u black_and_serial_wrap=1\n",cases);
    return 0;
}
'''


def check():
    prep = json.loads((O / 'prepared.json').read_text())
    assert sha(B / REL) == PARENT and sha(O / 'of_gl1.c') == prep['candidate_sha256']
    assert not (O / 'checks').exists(), 'Preserve prior checks'
    C = O / 'checks'; C.mkdir()
    (C / 'literal.c').write_text(proof_source())
    commands = []
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
    def run(cmd, label):
        commands.append(dict(label=label, argv=cmd))
        with (C / (label + '.log')).open('w') as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
    flags = ['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer']
    run(flags + [str(C/'literal.c'),'-o',str(C/'literal')], 'literal-build')
    run([str(C/'literal')], 'literal-run')
    G = B/'src/quake2'
    includes = [G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',B/'src/sdk/include',G/'openfpga']
    common = flags + ['-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections',
        '-DYQ2OSTYPE="test"','-DYQ2ARCH="host"'] + ['-I'+str(p) for p in includes]
    for name,source in [('baseline',B/REL),('candidate',O/'of_gl1.c')]:
        run(common + ['-DOFGL_SOURCE="'+str(source)+'"',str(B/'tools/tests/quake2_renderer_test.c'),
            '-Wl,--gc-sections','-lm','-o',str(C/name)],name+'-build')
        run([str(C/name)],name+'-renderer')
    put(C/'commands.json',commands)
    cases=int(re.search(r'rectangle_cases=(\d+)',(C/'literal-run.log').read_text())[1])
    put(O/'result.json',dict(passed=True,literal_original_source=True,rectangle_cases=cases,
        sanitizers=['ASan','UBSan'],actual_renderer_baseline_and_candidate_pass=True,
        black_and_uint32_serial_wrap_pass=True,performance_measured=False,sibling_modified=False))
    files=[p for p in O.rglob('*') if p.is_file() and p.name!='manifest.json']+[Path(__file__),B/REL,B/'tools/tests/quake2_renderer_test.c']
    put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(files)}))
    print('MANIFEST',sha(O/'manifest.json'))


def apply(target):
    target = target.resolve()
    assert target.is_relative_to(P/'work') and target != B and not target.is_symlink()
    prep=json.loads((O/'prepared.json').read_text());p=target/REL
    assert not p.is_symlink() and sha(p)==PARENT and sha(O/'of_gl1.c')==prep['candidate_sha256']
    for parent in p.parents: assert not parent.is_symlink()
    mode=p.stat().st_mode & 0o777
    temporary=p.with_name(p.name+'.lightmap-v1.tmp');assert not temporary.exists()
    temporary.write_bytes((O/'of_gl1.c').read_bytes());temporary.chmod(mode);os.replace(temporary,p)
    print('APPLIED',str(p),sha(p))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','check','apply'])
    parser.add_argument('--target',type=Path)
    args=parser.parse_args()
    if args.action=='apply': assert args.target;apply(args.target)
    else: assert args.target is None;globals()[args.action]()
