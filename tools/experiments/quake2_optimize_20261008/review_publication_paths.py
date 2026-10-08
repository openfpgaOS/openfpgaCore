#!/usr/bin/env python3
"""Inspect existing Quake2 publication conventions; never publish or mount."""
import hashlib,json,re
from pathlib import Path
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-20261008'
Q=R.parent/'Quake2';O=P/'publication-path-review-v1';E={}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def bind(p):
 h=sha(p);E[str(p)]=h;return h
def text(p):bind(p);return p.read_text()
def js(p):return json.loads(text(p))
assert not O.exists()
before=js(P/'before.json')
for rel,h in before['source_sha256'].items():assert bind(Q/rel)==h,rel
for rel,h in before['artifacts_sha256'].items():assert bind(Q/rel)==h,rel
raw=text(Q/'runtime/MANIFEST');rows={}
for line in raw.splitlines():
 if re.match(r'[0-9a-f]{32}  ',line):
  h,rel=line.split('  ');f=Q/'runtime'/rel;assert hashlib.md5(f.read_bytes()).hexdigest()==h;bind(f);rows[rel]=h
assert rows['./pocket/os30.rbf_r']=='65ffa4b31eb3c6b5341a8b6da0ebe26a'
selected=R/'docs/measurements/os30-fps25-20261005/phase10/final-candidate-delivery-v3/payload/Cores/ThinkElastic.SM64/os30.rbf_r'
assert bind(selected)=='b1083c26b99c063684b411af1299e3b820c69f025ce6ee0003f6d7d4ab93a8e4'
newmd5=hashlib.md5(selected.read_bytes()).hexdigest()
assert newmd5=='65ae8880209ee0d6708ff21befb792da'
meta=js(Q/'dist/quake2/Cores/thinkelastic.quake2/core.json')['core']
assert meta['cores']==[dict(name='default',id=0,filename='os30.rbf_r',chip32_vm='loader.bin')]
assert meta['metadata']['version']=='1.0.1'
image=text(Q/'src/sdk/platforms/pocket/image.sh')
assert 'rm -rf "$OUT"'in image and 'OUT="$ROOT/build/pocket/$APP"'in image
assert 'MANIFEST'not in image
package=text(Q/'src/sdk/platforms/pocket/package.sh')
assert 'OUTPUT="$REL/${LABEL}-v${GAME_VERSION}.zip"'in package
text(Q/'src/sdk/platforms/pocket/copy.sh');text(Q/'src/quake2/Makefile')
assert not (Q/'releases/pocket').exists()
for f in sorted((Q/'releases/mister').rglob('*')):
 if f.is_file():bind(f)
result=dict(passed_read_only_path_review=True,source_guard_count=len(before['source_sha256']),
 artifact_guard_count=len(before['artifacts_sha256']),all_captured_preimages_unchanged=True,
 current_runtime_manifest_all_rows_match=True,os30_manifest_old_md5=rows['./pocket/os30.rbf_r'],
 os30_manifest_new_md5=newmd5,manifest_edit='Change only exact os30 row and add a scoped provenance comment; leave every unrelated row/header intact.',
 sibling_updates=['src/quake2/openfpga/of_gl1.c','runtime/pocket/os30.rbf_r','runtime/MANIFEST',
  '.obj/quake2/app.elf','build/pocket/quake2/Assets/quake2/common/quake2.elf',
  'build/pocket/quake2/Cores/thinkelastic.quake2/os30.rbf_r'],
 selected_source_sha256='a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb',
 selected_elf_sha256='6f4ec790371d6a610976fa72d220df6636cfb5fbccf36284f028dc4893bc9959',
 selected_core_sha256=sha(selected),
 sd_updates=['Cores/thinkelastic.quake2/os30.rbf_r','Assets/quake2/common/quake2.elf'],
 sd_guards=['Verify UUID995B-1B19 and expected Quake2 identity before writing.',
  'Capture both destination preimages; atomic replacement plus full hash readback; sync before unmount.',
  'Preserve all config, paks, saves, OS, loader, metadata and SM64 paths.'],
 release=dict(existing_pocket_package=False,optional_standard_path='releases/pocket/quake2-v1.0.1.zip',
  unchanged_mister_releases=True,github_publication_requested=False),
 build_caution='image.sh deletes the app build directory before reconstruction; targeted prepared copies avoid touching unrelated staged assets.',
 verification_scope='Prepublication source/artifact guards and path conventions only; parent owns payload review and actual writes.',
 mounts_writes_builds_or_simulations_performed=False)
O.mkdir();(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');bind(O/'result.json');bind(Path(__file__))
(O/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256=E),indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(output=str(O),manifest_sha256=sha(O/'manifest.json'),bindings=len(E))))
