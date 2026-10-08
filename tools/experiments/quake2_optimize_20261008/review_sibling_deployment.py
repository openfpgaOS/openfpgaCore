#!/usr/bin/env python3
"""Verify the exact seven-file plan without invoking its mutator."""
from pathlib import Path
import difflib,hashlib,json
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-optimize-20261008'
D=P/'delivery-v1';O=P/'sibling-deployment-review-v1';E={}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()if p.exists()else None
def bind(p):E[str(p)]=sha(p);return E[str(p)]
planfile=D/'sibling/plan.json';expected='fb9b0878ffa9a2403c6b3df2b1893e0ed5c17c0324e45e7ba446201401e99efd'
assert bind(planfile)==expected
plan=json.loads(planfile.read_text());Q=Path(plan['destination']);assert Q==R.parent/'Quake2'
expectedpaths={'src/quake2/openfpga/of_gl1.c','.obj/quake2/app.elf',
 'build/pocket/quake2/Assets/quake2/common/quake2.elf','runtime/pocket/os30.rbf_r',
 'build/pocket/quake2/Cores/thinkelastic.quake2/os30.rbf_r','runtime/MANIFEST',
 'docs/quake2-os30-optimization-20261008.txt'}
assert {x['path']for x in plan['files']}==expectedpaths and len(plan['files'])==7
assert len(plan['guards'])==882 and not(expectedpaths&plan['guards'].keys())
for rel,h in plan['guards'].items():assert bind(Q/rel)==h,rel
patch=[]
for entry in plan['files']:
 target=Q/entry['path'];source=Path(entry['source'])
 assert target.resolve().is_relative_to(Q.resolve())and not target.is_symlink()
 assert bind(target)==entry['before'];assert bind(source)==entry['after']
 if entry['path'].endswith(('.c','.txt'))or entry['path']=='runtime/MANIFEST':
  before=target.read_text().splitlines(keepends=True)if target.exists()else[]
  patch.extend(difflib.unified_diff(before,source.read_text().splitlines(keepends=True),
    fromfile='a/'+entry['path'],tofile='b/'+entry['path']))
assert ''.join(patch)==(D/'source.patch').read_text();bind(D/'source.patch')
for path,h in plan['evidence'].items():assert bind(Path(path))==h,path
doc=(D/'payload/quake2-os30-optimization-20261008.txt').read_text()
assert 'bash tools/sdk-container.sh -C src/quake2 make GPU_PROFILE=0 "$PWD/.obj/quake2/app.elf"'in doc
bind(Q/'tools/sdk-container.sh');bind(Q/'tools/docker/Dockerfile.firmware')
assert 'ENTRYPOINT'not in(Q/'tools/docker/Dockerfile.firmware').read_text()
assert '90.33 MHz hot / 91.75 MHz cold'in doc and 'not 100 MHz setup closure'in doc
assert '0.789%'in doc and 'not Pocket FPS'in doc
assert sha(D/'payload/quake2.elf')=='6f4ec790371d6a610976fa72d220df6636cfb5fbccf36284f028dc4893bc9959'
assert sha(D/'payload/os30.rbf_r')=='b1083c26b99c063684b411af1299e3b820c69f025ce6ee0003f6d7d4ab93a8e4'
assert sha(D/'payload/of_gl1.c')=='a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'
assert not(D/'sibling/complete.json').exists()
result=dict(passed=True,go_for_exact_sibling_plan=True,plan_sha256=expected,
 mutator_sha256=sha(R/'tools/experiments/quake2_optimize_20261008/apply_deployment.py'),
 changed_paths=sorted(expectedpaths),preservation_guards=882,all_preimages_payloads_evidence_rehashed=True,
 source_patch_reconstructed_exact=True,documented_container_command_corrected=True,
 review=['Plan hash pinned at invocation and mutator hash pinned within evidence.',
 'All existing target hashes checked before writes; exact seven destinations restricted to sibling root and not symlinks.',
 'Backups verified before modification, file and directory fsync, atomic replacement, complete post-write hash checks.',
 'Unchanged guards verified before and after; rollback restores completed paths on any exception.',
 'Only os30 manifest row and scoped comment change; no unrelated runtime/core/asset/SDK change.',
 'Normal executable and source selected; no rotation/PGO changes; existing diagnostic executable remains guarded.'],
 limitations=['This authorizes the reviewed sibling plan only; SD UUID and payload plan need their own exact guard check.',
 'Read-only preflight, not a claim that deployment already occurred.'],
 mutator_invoked=False,builds_tests_or_sibling_writes=False)
O.mkdir(exist_ok=False);(O/'result.json').write_text(json.dumps(result,indent=2)+'\n');bind(O/'result.json');bind(Path(__file__))
(O/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256=E),indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(manifest_sha256=sha(O/'manifest.json'),result=str(O/'result.json'))))
