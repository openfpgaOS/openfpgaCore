#!/usr/bin/env python3
"""Read-only canonical handback feasibility; no RTL/performance prediction."""
from pathlib import Path
import csv,json,hashlib,statistics
H=Path(__file__).resolve().parent;C=H.parents[1];run=C/'validation/candidate-v1/run';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
fields=['cycle','phase','group_take','group_request_seq','group_request_vtx_w0','group_active','group_index','group_count','group_state','job_take','job_seq','descriptor_serial','completed_serial','lease_granted','lease_pending','legacy','raw_start','active','pending','snapshot_detached','output_pending','packet_raw_read_active','raw_state','cull_owner','gpu_state','repair_pending']
groups=[];g=None;prev=None
for source in csv.DictReader((run/'async-events.tsv').open(),delimiter='\t'):
 r={k:int(source[k])for k in fields}
 if prev is not None and g is not None:
  delta=r['cycle']-prev['cycle'];assert delta>=0
  for key,yes in [('legacy_lease',prev['lease_granted']),('pending_lease',prev['lease_pending']),('raw_active',prev['active']),('raster_fragment',prev['gpu_state']==6),('repair_pending',prev['repair_pending']),('output_pending',prev['output_pending']),('snapshot_detached',prev['output_pending']and prev['snapshot_detached']),('no_legacy_and_no_raw',not prev['lease_granted']and not prev['active'])]:
   if yes:g['residence_cycles'][key]=g['residence_cycles'].get(key,0)+delta
 if r['group_take']:
  assert g is None
  g={'phase':r['phase'],'firstseq':r['group_request_seq'],'members':r['group_request_vtx_w0'],'begin':r['cycle'],'legacy_raw_starts':0,'legacy_raw_starts_after_first_member':0,'residence_cycles':{}}
  g['lastseq']=g['firstseq']+g['members']-1
 if g is not None:
  if r['job_take']and r['job_seq']==g['firstseq']:g.setdefault('first_member',r['cycle'])
  if r['raw_start']and r['legacy']:
   g['legacy_raw_starts']+=1
   if 'first_member'in g:g['legacy_raw_starts_after_first_member']+=1
  if r['descriptor_serial']==g['lastseq']and r['snapshot_detached']and r['output_pending']:g.setdefault('last_snapshot_detached',r['cycle'])
  if r['completed_serial']==g['lastseq']and not r['group_active']:
   g['end']=r['cycle'];g['duration']=g['end']-g['begin'];
   if 'last_snapshot_detached'in g:g['after_last_snapshot']=g['end']-g['last_snapshot_detached']
   groups.append(g);g=None
 prev=r
assert g is None and len(groups)==26
phase={}
for ph in (1,2):
 gs=[x for x in groups if x['phase']==ph];stat=lambda a:{'sum':sum(a),'median':statistics.median(a),'max':max(a)}
 phase[str(ph)]={'groups':len(gs),'members':sum(x['members']for x in gs),'group_residence':stat([x['duration']for x in gs]),'legacy_raw_starts_during_groups':sum(x['legacy_raw_starts']for x in gs),'legacy_raw_starts_after_first_member':sum(x['legacy_raw_starts_after_first_member']for x in gs),'groups_with_legacy_raw_interposition':sum(x['legacy_raw_starts_after_first_member']>0 for x in gs),'residence_cycles':{k:sum(x['residence_cycles'].get(k,0)for x in gs)for k in sorted(set().union(*(x['residence_cycles']for x in gs)))},'after_last_snapshot':stat([x['after_last_snapshot']for x in gs if 'after_last_snapshot'in x])}
ledger=json.loads((C/'analysis/span-coverage-v1/ledger.json').read_text());shape={}
for ph in ('1','2'):
 calls={x['call']:x for x in ledger[ph]['calls']};chains=ledger[ph]['all_chains'];survivors=[];maxlast=[]
 for chain in chains:
  counts=[calls[i]['count']for i in chain['calls']];last=counts[-1];maximum=max(counts)
  survivors.append({'loads':len(counts),'max_count':maximum,'last_count':last,'high_slots_from_earlier_LOAD':maximum-last,'last_covers_union':last==maximum})
 shape[ph]={'chains':len(chains),'loads':sum(x['loads']for x in survivors),'chains_requiring_earlier_high_slots':sum(not x['last_covers_union']for x in survivors),'sum_terminal_high_slots_from_earlier_LOAD':sum(x['high_slots_from_earlier_LOAD']for x in survivors),'terminal_max_chains':sum(x['last_covers_union']for x in survivors),'terminal_max_multiload_chains':sum(x['last_covers_union']and x['loads']>1 for x in survivors),'terminal_max_multiload_LOADs':sum(x['loads']for x in survivors if x['last_covers_union']and x['loads']>1)}
profile=json.loads((C/'analysis/native-v1.json').read_text());join=json.loads((C/'analysis/span-coverage-v1/admission-join.json').read_text());admission=json.loads((C/'analysis/admission-v1/result.json').read_text())
inputs=[Path(__file__),run/'async-events.tsv',C/'analysis/native-v1.json',C/'analysis/span-coverage-v1/ledger.json',C/'analysis/span-coverage-v1/result.json',C/'analysis/span-coverage-v1/admission-join.json',C/'analysis/admission-v1/result.json',C/'validation/candidate-v1/check-independent-v1.json',C/'native-v2/prepared.json']
for n in ['geometry_async_mesh.v','geometry_raw_load.v','geometry_mesh_stream.v','geometry_mesh_packet.v','geometry_mesh_task_ctrl.v']:inputs.append(C/'native-v2/frozen/src/fpga/common'/n)
result={'status':'READ_ONLY_DESIGN_NO_IMPLEMENTATION','decision':'NO_GO_AS_MINIMAL_CPU_UNBLOCKING_CHANGE; technically feasible as a separately measured canonical-transfer architecture','measured_existing_groups':phase,'structural_terminal_view':shape,'current_whole_phase_overlapping_counters':profile['overlap'],'groups':groups,'requirements':[
 'Only immutable contiguous VTX/TRI chains with fixed transform/light/UV/fog/material context; stop before state/list boundaries. The111structural chains are not111material-qualified jobs.',
 'Track group touched mask and actual raw valid_slots. For dest0-only members, touched mask is prefix[0,maxcount). Preserve incoming canonical slots outside that prefix before enqueue, and invalidate touched GPU cache slots.',
 'A hold-raw GROUP flag must prohibit every legacy raw overwrite across all members and terminal handback. Priority context restoration alone cannot reconstruct aggregate high-slot raw rows.',
 'After final numeric snapshot commit and accepted DMA drain, transfer raw alias to a unique terminal CPU lease while final packet may keep draining from snapshot RAM. Terminal alias ownership and true final output completion are separate fences.',
 'CPU may defer canonical copying until first actual consumer or overlapping producer, but must copy retained rows or prove them dead and release before every wait requiring later queued work to progress.',
 'Expose touched_mask and final_valid_mask, not just maxcount. A failed LOAD partially overwrites/invalidates its incoming rows; clip/cull/packet exceptions after a successful LOAD do not.',
 'On a raw-failure repair, store exact canonical rows in a group-owned CPU side record indexed byslot. Later failed LOADs overwrite those saved rows. At terminal handback select raw rows for final-valid bits and saved latest-failure canonical rows for invalid touched bits. This requires extending the pure repair helper; it currently emits draw packets only.',
 'Source descriptors, CPU/PSRAM vertex inputs, transform/projection/fog values and any failure records must survive until BOTH terminal canonical obligation and final ordered output obligation are discharged.',
 'The smallest design allows only one pending terminal canonical owner. Queueing another group must first materialize retained rows or prove them dead and arrange terminal release; otherwise the next group can wait behind a raw lease that the CPU forgot to service.',
 'Fog coefficients and incoming canonical rows outside the touched mask belong to the CPU task record. The existing28 hardware context words alone do not capture all deferred canonical/fog bookkeeping.',
 'Consumer reset while a canonical obligation is pending cannot merely revoke it. Preserve an already-granted terminal raw lease, or mark the canonical obligation invalid and reconstruct from retained descriptors/context before CPUuse. Partial-group reset requires final last-writer provenance for submittedmembers; no stale raw alias may be trusted.',
 'Group preflight/repair/material setup must not wait on a GPU fence that includes its own unavailable terminal handback. Head repair continues streaming independently of ingress fullness; CPU never repairs via ordinary ringtail.'
 ],'ownership_tradeoff':[
 'The present one-snapshot design cannot start memberN+1 until all outputwords frommemberN enter the511usable-word output FIFO. Holding raw across a longchain therefore retains it during raster-dependent output backpressure, not just raw transform time.',
 'Terminal handback removes per-member CPU submissions/readback but makes the first legacy raw request or canonicalconsumer wait for the wholechain. It can overlap independentCPUwork before that boundary; it cannot guarantee an additional CPU unblock.',
 'A terminal raw lease can overlap the final snapshot output drain, but cannot remove waiting for preceding members. Retaining it lazily can block every later GROUP, ordinaryfence and source-pool recycle unless software releases before those progresswaits.',
 'The observed residence/overlap counts describe the unchanged scheduling only. They cannot be added or subtracted to predict the proposed locked-group runtime.'
 ],'smaller_alternative':[
 'If the finalLOAD count covers the entire group touchedprefix, earlymember rawrows need not survive legacypreemption. Keep ordinary priority betweenmembers, then reserve only the terminal snapshot rawview for CPUhandoff.',
 'This structural shortcut covers only7multiLOAD chains/19LOADs among111chains in this scene (beforematerialqualification);55otherterminal-maxchains are singletons. It does not recovermost171dead-suffix legacyloads.',
 'Another path is a stronger cached canonical liveness proof through known displaylistreturns, but this needs real caller-sensitive control-flow evidence. It is not implied by ENDDL and is not implemented.'
 ],'limits':[
 'Current measured groups intentionally contain only individually dead-slot-proven tasks. No broadhandback runtime or FPS was simulated here.',
 '111chains/320LOADs is a structural ceiling; preparedmaterial eligibility and clipping/rawfailure handling remain necessary.',
 'Event residence spans are exclusive between groups, but the state categories overlap eachother and CPU/GPUexecution. Counts have at mostoneedge convention difference; do not treat them as recoverable criticalpathtime.',
 'No extraM10K/FPU is required for the proposed masks/terminal lease if CPU provenance lives in normalSDRAM; added control/software/ALMcost remainsunqualified.',
 'Candidate-v1 is the fixedF25 initialGROUP trace. F26/F27 cache/publication variants were being measured separately and are not included.'
 ],'inputs_sha256':{str(p):sha(p)for p in inputs}}
(H/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'phase':phase,'structural':shape},indent=2))

text = f"""Broad terminal canonical handback: read-only assessment

Recommendation: do not implement whole-chain raw locking as a small CPU
unblocking fix. It is technically feasible without another M10K or FPU, but
it trades per-job submissions for a potentially long terminal dependency.
It requires a separate full-frame experiment, not a claim based on saved calls.

The immutable scene contains 111 state-free chains covering 320 LOADs, with a
maximum 23 LOADs / 212 triangles per chain. These are structural counts, not
material-qualified jobs. The current warm path still has 171 legacy LOADs
rejected by the conservative dead-suffix proof, mostly at ENDDL.

A correct aggregate handback must protect every group's retained raw row from
legacy overwrites. For dest0 LOADs, the touched set is [0,maxcount), but 49 of 111
chains finish with a smaller LOAD and retain 359 high slots from earlier LOADs.
Context restoration cannot restore those rows. The worker must lock raw across
the whole group, then lend the terminal alias to a token-matched CPU owner after
final snapshot commit and DMA drain. The CPU may copy lazily, but must copy or
prove rows dead and release before any later queued-progress wait. Final output
completion remains a separate source-lifetime fence.

A valid mask is essential: failed LOADs leave partial/invalid rows. Pure repair
must also save canonical results into a group-owned CPU side record, indexed by
slot; final valid rows use raw, invalid touched rows use the latest failed-LOAD
record. The current repair helper emits packets only, so this is additional
software work. Reset must preserve a granted terminal alias or reconstruct
canonical values from retained immutable descriptors/context. Fog coefficients,
incoming untouched slots and terminal ownership must be captured explicitly.

The current warm GROUP trace illustrates the ownership cost: 14 groups / 63
members occupy 374,262 cycles (median 12,925.5, maximum 121,132). Raster fragment
processing overlaps 298,058 of those cycles. During 8 groups, the CPU starts 24
legacy LOADs after the group's first member. Whole-group raw locking removes
those demonstrated preemption opportunities. Terminal snapshot-to-final-output
residence totals only 8,912 cycles; releasing at that point helps the final tail
while preceding members still wait for the 511-word output FIFO. Across the
whole warm interval, output-full overlaps raster for 1,003,944 of 1,106,209 cycles.
These are overlapping observations, not additive costs or a speed prediction.

A narrower terminal-last-LOAD shortcut can preserve priority if the final LOAD
overwrites the entire touched set. It covers only 7 multi-LOAD chains / 19 LOADs
before material qualification; the other 55 terminal-max chains are singletons.
That does not recover most of the rejected suffix scope.

A useful prerequisite is to measure which terminal canonical slots are actually
read before replacement. ENDDL does not prove death or liveness. A conservative
cached caller-aware liveness proof could avoid handback where rows are unused;
otherwise deferred reconstruction needs explicit provenance. Neither approach
has been implemented or assigned a predicted gain here.

Evidence: result.json binds the frozen F25 native trace, strict receipt, source
and coverage ledgers. F26/F27 measurements are separate. No RTL, production
files, physical build or SD contents were changed by this assessment.
"""
(H/'REPORT.txt').write_text(text)
