#!/usr/bin/env python3
"""Replace only guarded 32-slot byte-offset divisions; never change membership."""
import argparse,difflib,hashlib,json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];C=ROOT/'docs/measurements/os30-simple-20261005'
BASE=C/'baseline/gfx_pc.c';EXPECTED='4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84'
sha=lambda data:hashlib.sha256(data).hexdigest()
HELPER='''/* Only call after the unchanged byte-range check for the 32 cache slots.
 * For the 36-byte layout, 0 <= offset < 1152 and q=offset>>2 is at most
 * 287. floor(q*57/512) is exactly floor(q/9), hence floor(offset/36).
 * Keep the ordinary quotient if another build changes LoadedVertex. */
static inline __attribute__((always_inline)) size_t gfx_vc_slot_index(uintptr_t offset) {
    if (sizeof(struct LoadedVertex) == 36)
        return ((offset >> 2) * 57u) >> 9;
    return offset / sizeof(struct LoadedVertex);
}

'''
def transform(s):
 assert sha(s.encode())==EXPECTED
 a='static int gfx_vc_try_push('
 assert s.count(a)==1;s=s.replace(a,HELPER+a)
 old='''        if (offset >= 32 * sizeof(struct LoadedVertex))
            goto per_tri_fallback;          /* no stable slot */
        const size_t idx = offset / sizeof(struct LoadedVertex);'''
 assert s.count(old)==1;s=s.replace(old,old.replace('offset / sizeof(struct LoadedVertex)','gfx_vc_slot_index(offset)'))
 old='''            if(offset<32*sizeof(struct LoadedVertex)) {
                unsigned slot=offset/sizeof(struct LoadedVertex);'''
 assert s.count(old)==1;s=s.replace(old,old.replace('offset/sizeof(struct LoadedVertex)','gfx_vc_slot_index(offset)'))
 return s

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=C/'slot-index-v1');a=ap.parse_args()
 old=BASE.read_text();new=transform(old);out=a.output;out.mkdir(parents=True,exist_ok=True)
 assert not(out/'gfx_pc.c').exists() or (out/'gfx_pc.c').read_text()==new
 (out/'gfx_pc.c').write_text(new)
 (out/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/gfx_pc.c',tofile='b/gfx_pc.c')))
 (out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
 for offset in range(1152):assert offset//36==((offset>>2)*57>>9)
 # Exercise the literal emitted helper in C, and its generic-layout path.
 pre='#include <stddef.h>\n#include <stdint.h>\n#include <stdio.h>\n#include <assert.h>\n'
 proof=pre+'struct LoadedVertex {unsigned char raw[LAYOUT];};\n'+HELPER+'''int main(void) {
 for(uintptr_t p=0;p<32*sizeof(struct LoadedVertex);p++)assert(gfx_vc_slot_index(p)==p/sizeof(struct LoadedVertex));
 printf("PASS layout=%zu all%zu byte offsets; range/membership unchanged\\n",sizeof(struct LoadedVertex),32*sizeof(struct LoadedVertex));return 0;}
'''
 (out/'quotient.c').write_text(proof);runs=[]
 for layout in [28,32,36,40,64]:
  exe=out/f'quotient-{layout}';cmd=['gcc','-std=c11','-O3',f'-DLAYOUT={layout}',str(out/'quotient.c'),'-o',str(exe)]
  subprocess.run(cmd,check=True);r=subprocess.run([str(exe)],capture_output=True,text=True,check=True)
  runs.append({'layout':layout,'output':r.stdout.strip(),'binary_sha256':sha(exe.read_bytes())})
 (out/'proof.log').write_text('\n'.join(r['output'] for r in runs)+'\n')
 record={'passed':True,'parent_sha256':EXPECTED,'candidate_sha256':sha(new.encode()),'recipe_sha256':sha(Path(__file__).read_bytes()),'scope':'Two range-guarded byte-offset quotient expressions plus inline helper; unchanged cache range/membership, control, arithmetic, layout and state','exact_domain':'All1152 byte offsets, including non-multiples of36; maximum reciprocal product16359 cannot overflow uintptr_t','generic_layout_fallback':'Exact original offset/sizeof(LoadedVertex) for every layout except36','focused_tests':runs,'candidate_note':'Target DIVU elimination, application timing and GPU replay not yet qualified'}
 (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
if __name__=='__main__':main()
