#!/usr/bin/env python3
"""Bind isolated F28 source delta, proof contract and successful focused builds."""
from pathlib import Path
import difflib,hashlib,json
E=Path(__file__).resolve().parent;W=E.parent;C=W.parent;PRE=C/'work-app-v3'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
assert not (E/'scope-v1.json').exists()
build=E/'app-build-v4/build-result.json';oldbuild=PRE/'evidence/app-build-v3/build-result.json'
j=load(build);old=load(oldbuild)
for root,receipt in [(W,j),(PRE,old)]:
 for name,digest in receipt['source_sha256'].items():assert sha(root/name)==digest,(root,name)
 assert sha(Path(receipt['elf']))==receipt['elf_sha256']
changes=[n for n in j['source_sha256'] if j['source_sha256'][n]!=old['source_sha256'][n]]
assert set(changes)=={'src/sm64/sm64/src/pc/gfx/gfx_pc.c','src/sm64/pocket/perf_pocket.c'}
patch=''
for name in changes:
 patch+=''.join(difflib.unified_diff((PRE/name).read_text().splitlines(True),(W/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
(E/'f27-to-f28.patch').write_text(patch)
receipts=[E/'priority-tests-v1/result.json',E/'group-cache-tests-v1/result.json']
checks={}
for p in receipts:
 t=load(p);assert t['status']=='PASS';checks[p.parent.name]=t['checks']
 for name,digest in t['inputs_sha256'].items():assert sha(Path(name))==digest,name
inputs=[build,oldbuild,Path(j['elf']),Path(old['elf']),Path(__file__).resolve(),E/'f27-to-f28.patch',
 C/'analysis/state-liveness-v1/source-audit-v1.json',C/'analysis/state-liveness-v1/native-traversal-v2/final.json',
 C/'design/group-abi.md']+receipts
inputs += [root/name for root,receipt in [(W,j),(PRE,old)] for name in receipt['source_sha256']]
result=dict(status='PASS_SOURCE_TEST_BUILD_NOT_FULL_FRAME_QUALIFIED',predecessor=str(PRE),selected=str(W),changes=changes,
 proof=dict(scan_limit=256,local_return_stack=8,external_return_limit=1,
            neutral_opcodes=['0x'+format(v,'02x') for v in [0xd7,0xfd,0xd9,0xfb,0xf5,0xf2,0xf3,0xf4,0xf0,0xfa,0xfc,0xf8,0xe6,0xe7,0xe8,0xe9]],
            explicit_other_mode_barriers=['0xe2','0xe3'],
            callback='gfx_run_dl(command, immediate_return_command); root NULL, pushed child command+1, branch retains argument',
            address_checks='Every consumed command including branch/call/continuation targets is eight-byte aligned and inside immutable ELF [_start,__EH_FRAME_BEGIN__). Caller may be absent or mutable only when the proof succeeds before consuming it.',
            caches='Original source-only MeshTask.dead_after remains untouched. Separate proof cache retains caller-independent positive, exact-caller positive and exact-caller negative. Caller-sensitive group prefixes bind the same immediate continuation, including a rejected lookahead member.',
            semantics='Only liveness lookahead skips state/call/return opcodes; actual dispatcher still executes them. GROUP stops at every state/list boundary. The sixteen neutral handlers do not read canonical transformed vertices in this compiled backend; E2/E3 remain barriers.'),
 unchanged=['AJ03 RTL and protocol','GPU backend and atomic force-published GROUP','AJ02 single fallback','repair arithmetic helper','source pool generation fence and complete descriptor retry','exact repair-hint invalidation','raw lease and progress-wait paths','canonical survivor/fog/drop machinery'],
 tests=checks,scope=['Source-bound focused C tests and successful RV32 app build; no integrated correctness or performance claim.',
 'Potential +56 cold structural spans comes from earlier independent traversal and is not a measured F28 admission count or speed gain.',
 'Fresh same-ELF capability-off/candidate replay must independently witness actual display-list continuations and verify every logical job and raster output.'],
 symbols={k:j['symbols'][k] for k in ['gfx_run','gfx_end_frame','gfx_run_dl','async_return_command','sm64_async_front_capability','sm64_async_group_capability','gfx_async_stats','gfx_async_group_stats']},
 elf_sha256=j['elf_sha256'],input_sha256={str(p):sha(p) for p in inputs},physical_build=False,deployed=False,no_timing_claim=True)
(E/'scope-v1.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'scope_sha256':sha(E/'scope-v1.json'),'tests':checks,'changes':changes}))
