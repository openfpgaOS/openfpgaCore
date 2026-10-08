#!/usr/bin/env python3
"""Prepare one guarded rotation-only source component; no sibling writes."""
import difflib,hashlib,json,re
from pathlib import Path
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-20261008'
B=P/'work/baseline/src/quake2/openfpga/of_gl1.c'
O=P/'components/rotation-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def function(s,name):
 m=re.search(r'(?:static (?:inline )?\w+\n)'+re.escape(name)+r'\([^)]*\)\n\{',s)
 assert m,name
 start=m.start();p=m.end();depth=1
 while depth:
  if s[p]=='{':depth+=1
  elif s[p]=='}':depth-=1
  p+=1
 return s[start:p]
BASE=B.read_text();OLD=function(BASE,'ofgl_Rotatef')
LOOKUP='''	/* Repeated world/entity rotations share only their local rotation
	 * matrix. Keep every argument bit (including signed-zero axes) in the
	 * key, and still postmultiply the current matrix on every valid call. */
	static struct {
		uint32_t key[4];
		ofgl_mat4 matrix;
		int valid;
	} cache[4];
	static unsigned next;
	uint32_t key[4];
	memcpy(&key[0], &angle, sizeof(angle));
	memcpy(&key[1], &x, sizeof(x));
	memcpy(&key[2], &y, sizeof(y));
	memcpy(&key[3], &z, sizeof(z));
	for (unsigned i = 0; i < 4; i++)
		if (cache[i].valid && !memcmp(key, cache[i].key, sizeof(key)))
		{
			ofgl_mat_postmul(cache[i].matrix);
			return;
		}
'''
STORE='''	/* The integer-exponent helper remains valid under -ffast-math.
	 * Nonfinite/overflowing inputs always take the original arithmetic path. */
	if (ofgl_finite(rad) && ofgl_finite(len))
	{
		memcpy(cache[next].key, key, sizeof(key));
		memcpy(cache[next].matrix, m, sizeof(m));
		cache[next].valid = 1;
		next = (next + 1) & 3u;
	}
'''
assert OLD.count('\tfloat rad =')==OLD.count('\tofgl_mat_postmul(m);')==1
NEW=OLD.replace('\tfloat rad =',LOOKUP+'\tfloat rad =').replace('\tofgl_mat_postmul(m);',STORE+'\tofgl_mat_postmul(m);')
def apply_to_text(s):
 assert s.count(OLD)==1 and NEW not in s,'Expected exact original rotation function once'
 return s.replace(OLD,NEW)
def prepare():
 O.mkdir(parents=True,exist_ok=False)
 candidate=apply_to_text(BASE)
 assert candidate.replace(NEW,OLD)==BASE
 # Removing only the memo insertions recovers every original arithmetic,
 # early-return and postmultiply statement literally.
 assert NEW.replace(LOOKUP,'').replace(STORE,'')==OLD
 (O/'of_gl1.c').write_text(candidate)
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(BASE.splitlines(True),candidate.splitlines(True),fromfile='a/src/quake2/openfpga/of_gl1.c',tofile='b/src/quake2/openfpga/of_gl1.c')))
 (O/'old-function.c').write_text(OLD+'\n');(O/'new-function.c').write_text(NEW+'\n')
 (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
 d=dict(baseline_sha256=sha(B),candidate_sha256=sha(O/'of_gl1.c'),recipe_sha256=sha(Path(__file__)),
   original_arithmetic_byte_exact=True,lookup_entries=4,replacement_policy='round-robin on successful finite miss',
   zero_angle_early_return_unchanged=True,cache_key='all128 raw input bits, no normalized-key approximation',
   nonfinite_policy='Never insert unless rad and len exponent fields are finite; original miss arithmetic/return remains',
   no_sibling_or_shared_candidate_writes=True,tests_pending=True)
 (O/'prepared.json').write_text(json.dumps(d,indent=2,sort_keys=True)+'\n')
 print(json.dumps(d))
if __name__=='__main__':prepare()
