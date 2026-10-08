#!/usr/bin/env python3
"""Private renderer-side material-lifetime VC memo, with no stage writes."""
import argparse,difflib,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
C=ROOT/'docs/measurements/os30-simple-20261005'
BASE=C/'baseline/gfx_pc.c'
EXPECTED='4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84'
sha=lambda x:hashlib.sha256(x).hexdigest()

def transform(s):
 assert sha(s.encode())==EXPECTED
 old=s
 def replace(a,b):
  nonlocal s
  assert s.count(a)==1,a
  s=s.replace(a,b)
 replace('static inline __attribute__((always_inline)) bool gfx_vc_texture_state(',
 '''/* These inputs have the same lifetime as triangle_material_ready. A cold
 * material refreshes eligibility after all backend setters and defers the
 * texture signature check until the first eligible cached triangle/run. */
static int triangle_vc_elig, triangle_vc_rgb_input, triangle_vc_alpha_input;
static bool triangle_vc_texture_ready;

static inline __attribute__((always_inline)) bool gfx_vc_texture_state(''')
 replace('static inline __attribute__((always_inline)) bool gfx_vc_texture_state(const struct ColorCombiner *comb, int textured, float bias) {\n',
 '''static inline __attribute__((always_inline)) bool gfx_vc_texture_state(const struct ColorCombiner *comb, int textured, float bias) {
        if (triangle_vc_texture_ready) return false;
        triangle_vc_texture_ready = true;
''')
 replace('    int rgb_in, alpha_in;\n    const int elig = gfx_gpu_vtx_cache_begin(&rgb_in, &alpha_in);',
 '''    const int rgb_in = triangle_vc_rgb_input, alpha_in = triangle_vc_alpha_input;
    const int elig = triangle_vc_elig;''')
 replace('        triangle_material_comb=comb;triangle_material_fog=use_fog;',
 '''        triangle_vc_rgb_input = triangle_vc_alpha_input = -1;
        triangle_vc_elig = gfx_gpu_vtx_cache_begin(&triangle_vc_rgb_input,
                                                &triangle_vc_alpha_input);
        triangle_vc_texture_ready = false;
        triangle_material_comb=comb;triangle_material_fog=use_fog;''')
 assert s.count('triangle_material_ready=false;')==old.count('triangle_material_ready=false;')==3
 return s

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=C/'vc-material-memo-v1');a=ap.parse_args()
 old=BASE.read_text();new=transform(old);a.output.mkdir(parents=True,exist_ok=True)
 output=a.output/'gfx_pc.c';assert not output.exists() or output.read_text()==new
 output.write_text(new)
 (a.output/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/gfx_pc.c',tofile='b/gfx_pc.c')))
 (a.output/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
 manifest={'parent_sha256':sha(old.encode()),'candidate_sha256':sha(new.encode()),'recipe_sha256':sha(Path(__file__).read_bytes()),'scope':['Cache backend eligibility/color selectors after existing cold material setup','Skip repeated ten-field texture signature comparisons within the same material lifetime'],'state_bytes':16,'preserved':['All three existing material invalidators','Lazy first eligible texture signature check','All per-triangle W/alpha/LOD/slot/clip guards and vertex arithmetic','Vertex reload stale mask updates, primitive/environment color invalidation','Full GPU command emission and flush order'],'qualification':'source frozen; invalidation/source review and focused/runtime tests pending'}
 (a.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
