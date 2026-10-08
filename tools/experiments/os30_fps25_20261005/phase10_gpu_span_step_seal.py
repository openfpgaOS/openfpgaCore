#!/usr/bin/env python3
"""Seal the exact running-step factor and approved full-writer shadow."""
import hashlib,json,re,shutil
from pathlib import Path
import phase10_gpu_span_step_enable as c
import phase10_gpu_span_step_shadow_v3 as shadow
R,P,O=c.R,c.P,c.O;T=R/'tools/experiments/os30_fps25_20261005'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
old=c.B.read_text();new=(O/'gpu_core.v').read_text()
assert c.transform(old)==new
assert sha(c.B)==c.BASE_SHA
lit=json.loads((O/'literal/result.json').read_text());assert lit['passed'] and lit['cases']==2097152
lint=json.loads((O/'shadow-v3/result.json').read_text());assert lint['passed_lint']
assert shadow.instrument_shadow(new)==(O/'shadow-v3/instrumented.v').read_text()
for r in ('sp_sstep','sp_tstep'):
 assert len(re.findall(r'^[ \t]*'+r+r'\s*<=',old,re.M))==len(re.findall(r'^[ \t]*'+r+r'\s*<=',new,re.M))==9
proof='''Exact running-step enable factor

Original enable = load_p0a && persp_active && sp_seg_left!=0. Under those
last two terms, the scalar availability's (!active || seg!=0 || Bready ||
last) term is true, and !persp_issue_stall reduces exactly to Aready.
p0a_free_after = !p0a_valid || (stream ? stream_launch : p0a_valid&&!p0_valid)
reduces to !p0a_valid || (stream ? stream_launch : !p0_valid).
The pending-Z consume has one unconditional blocking assignment before
the FSM: pending && !flush. Thus !flush && (!pending || consume) = !flush,
and source_z_ready = !write_only || !flush. No protocol/reachability
assumption or command-feature restriction is needed for these identities.
The unchanged count/warm/swap gates remain; src_done is the final gate.

Only the two sp_sstep/tstep += curvature writes leave the nested load_p0a
arm. The new block remains inside S_FRAG_PIPE and before the existing
persp_swap_pending block. No other writer occurs between their old/new
positions. All nine real writers per register retain relative priority,
including tasks, decoder loads, swap and later PSS commits. Reset/soft
reset ownership and arithmetic widths/signedness remain unchanged.
One kept combinational early qualifier is introduced, with no state or
cycle. Routing/mapping benefit remains unproven.

The literal probe extracts the original assignments, compares every
combination of13 Boolean inputs, all16segment values/all4warm values,
and count0,1,2,65535 (original count usage is onlyzero/one/other).
2,097,152cases pass, including15,120active updates.

Only phase10_gpu_span_step_shadow_v3.py is approved for replay. Its
instrument_shadow(source) mirrors all nine original writers per register
using line-anchored noncomment assignments, restores the two original
increment sites using independent shadow values, and preserves every
other writer/priority. PROBE compares full register values each cycle
and records advances/swaps/src_done. The selected test configuration
with inherited passive monitors passes lint; full replay is parent-owned
and still pending. The original broad regex incorrectly matched a source
comment; shadow-v2's extra local-scope enable monitor also failed lint.
Both drafts are retained as failed fixture evidence and are excluded
from qualification. The unchanged DUT and literal proof are unaffected.
'''
(O/'ownership.txt').write_text(proof)
c.put(O/'result.json',dict(passed_for_native_composition=True,source_sha256=sha(O/'gpu_core.v'),parent_sha256=c.BASE_SHA,literal_cases=lit['cases'],literal_original_assignment_extraction=True,original_real_writers_per_register=9,added_cycles=0,added_registers=0,kept_combinational_qualifier=True,approved_shadow_utility=str(Path(shadow.__file__).relative_to(R)),approved_shadow_utility_sha256=sha(Path(shadow.__file__)),approved_probe_sha256=hashlib.sha256(shadow.PROBE.encode()).hexdigest(),full_writer_shadow_lint_pass=True,full_writer_shadow_replay_pending=True,failed_fixture_drafts_preserved=True,quartus_invoked=False,production_modified=False,physical_timing_qualification=False))
shutil.copy2(__file__,O/'seal.py')
files=[p for p in O.rglob('*')if p.is_file()]+[Path(__file__),Path(c.__file__),Path(shadow.__file__),T/'phase10_gpu_span_step_shadow.py',c.B,P/'gpu-parallel-control-v1/manifest.json',P/'gpu-parallel-control-v1/lint/command.json',P/'gpu-parallel-control-v1/lint/gpu_core.v']
cmd=json.loads((O/'shadow-v3/command.json').read_text());files +=[Path(n)for n in cmd if Path(n).is_file()]
c.put(O/'manifest.json',dict(passed_for_native_composition=True,full_replay_pending=True,quartus_invoked=False,evidence_sha256={str(p.relative_to(R)):sha(p)for p in sorted(set(files))}))
print('MANIFEST',sha(O/'manifest.json'))
