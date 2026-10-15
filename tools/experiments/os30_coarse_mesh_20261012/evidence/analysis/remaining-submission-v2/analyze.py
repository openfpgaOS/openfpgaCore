#!/usr/bin/env python3
"""Read-only F28 warm submission accounting; no counterfactual timing subtraction."""
from pathlib import Path
import collections
import csv
import hashlib
import json
import re
import statistics

HERE = Path(__file__).resolve().parent
C = HERE.parent.parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
load = lambda p: json.loads(Path(p).read_text())
assert not (HERE/'result.json').exists(), 'Keep completed evidence immutable.'
R = C/'validation/candidate-v4/run'
CONTROL = C/'validation/control-v4/run'
APP = C/'work-app-v4/evidence/app-build-v4'
inputs = [Path(__file__), C/'analysis/phases-v4.json', C/'analysis/native-v4.json',
          APP/'build-result.json', APP/'app.dis', C/'native-v2/prepared.json',
          C/'validation/candidate-v4/check-independent-v4.json',
          C/'validation/control-v4/check-independent-v4.json',
          C/'work-app-v4/src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
          C/'work-app-v4/src/sm64/sm64/src/pc/gfx/gfx_pc.c', C/'validation/group_hooks_v2.py']
app_receipt=load(APP/'build-result.json')
elf=Path(app_receipt['elf'])
assert sha(elf)==app_receipt['elf_sha256']
inputs.append(elf)
phase_report = load(C/'analysis/phases-v4.json')
for name, digest in phase_report['inputs_sha256'].items(): assert sha(name) == digest
warm = phase_report['phases'][1]
def boundaries(run):
    inputs.append(run/'boundaries.tsv')
    rows = [list(map(int, s.split())) for s in (run/'boundaries.tsv').read_text().splitlines()]
    assert len(rows) == 3
    return rows[1][1], rows[2][1]
lo, hi = boundaries(R)
assert hi-lo == warm['candidate']['cycles']
def mmio(run):
    first, last = boundaries(run)
    inputs.append(run/'mmio.tsv')
    counts = collections.Counter(); commands = collections.Counter()
    for line in (run/'mmio.tsv').open():
        cycle, block, offset, data = line.split()
        if first <= int(cycle) < last and block == 'GEO':
            word = int(offset)//4
            counts[word] += 1; commands[(word,int(data))] += 1
    return dict(total=sum(counts.values()), matrix=sum(counts[i] for i in range(64,80)),
                scalar=sum(counts[i] for i in range(5,13)), load_setup=sum(counts[i] for i in (2,3,4)),
                lease_protocol=sum(counts[i] for i in (43,44,45)),
                acquire_commands=commands[(45,1)], release_commands=commands[(45,2)],
                per_word={hex(k):v for k,v in sorted(counts.items())})
candidate_mmio, control_mmio = mmio(R), mmio(CONTROL)

inputs.append(R/'pc-phase-2.csv')
pcs = {int(r['pc']):[int(r['retired']),int(r['cycles_since_previous_retirement'])]
       for r in csv.DictReader((R/'pc-phase-2.csv').open())}
sites = {'acquire_calls':0x104deec0, 'new_lease':0x104df0ec, 'failed_poll':0x104df07a,
 'acquire_initial_epoch':0x104def5c, 'held_epoch':0x104deef0, 'sequence_precheck':0x104def88,
 'retry_epoch_without_flush':0x104df0ba, 'retry_epoch_after_flush':0x104def9c,
 'target_write':0x104defdc, 'ticket_write':0x104defec, 'acquire_command_write':0x104df006,
 'loop_epoch':0x104df084, 'loop_status':0x104df05a, 'loop_ticket':0x104df06a,
 'release_if_pending_calls':0x104d9200, 'pending_epoch':0x104d9224, 'pending_sequence':0x104d92de,
 'pending_release_epoch':0x104d92ee, 'pending_release_write':0x104d92c8,
 'before_wait_calls':0x104d9340, 'before_wait_epoch':0x104d9380, 'before_wait_sequence':0x104d93c0,
 'before_wait_release_epoch':0x104d93e8, 'before_wait_release_write':0x104d942e,
 'service_calls':0x104d94c0, 'service_status':0x104d94e4,
 'direct_release_calls':0x104df180, 'direct_release_epoch':0x104df19e, 'direct_release_write':0x104df1e4}
instruction = {}
for line in (APP/'app.dis').open():
    m=re.match(r'^([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)',line)
    if m and int(m[1],16) in sites.values(): instruction[int(m[1],16)] = (m[3]+' '+m[4]).strip()
site_counts = {name:dict(pc=hex(pc),instruction=instruction[pc],retired=pcs.get(pc,[0,0])[0],
                          preceding_retirement_gap_cycles=pcs.get(pc,[0,0])[1]) for name,pc in sites.items()}
count = lambda name: site_counts[name]['retired']
assert count('new_lease') == candidate_mmio['acquire_commands'] == count('target_write') == count('ticket_write') == count('acquire_command_write')
assert count('loop_status') == count('new_lease') + count('failed_poll')
assert count('pending_release_write') + count('direct_release_write') == candidate_mmio['release_commands']
assert count('held_epoch') == count('before_wait_calls') == 0

inputs.extend([R/'async-events.tsv',R/'async-overlap.tsv'])
keys = ['cycle','legacy','raw_state','lease_granted','lease_pending','granted_ticket','old_start',
        'output_pending','snapshot_detached','group_active','repair_pending','gpu_state','job_take','front_job_take','group_take']
dur = collections.Counter(); grant_events=[]; pending_durations=[]; starts=[]
prev=None; pending_start=None; submission_events=collections.Counter()
for row in csv.DictReader((R/'async-events.tsv').open(),delimiter='\t'):
    z={k:int(row[k]) for k in keys}; t=z['cycle']
    if prev is not None:
        dt=max(0,min(t,hi)-max(prev['cycle'],lo))
        if dt:
            for k in ['lease_granted','lease_pending','raw_state','output_pending','group_active','repair_pending']:
                dur[k] += dt*bool(prev[k])
            dur['legacy_raw_active'] += dt*bool(prev['raw_state'] and prev['legacy'])
            dur['lease_without_raw_active'] += dt*bool(prev['lease_granted'] and not prev['raw_state'])
            dur['lease_and_old_async_output'] += dt*bool(prev['lease_granted'] and prev['output_pending'])
            dur['lease_pending_and_raster'] += dt*bool(prev['lease_pending'] and prev['gpu_state']==6)
        if z['lease_pending'] and not prev['lease_pending']: pending_start=t
        if prev['lease_pending'] and not z['lease_pending'] and pending_start is not None:
            if lo<=pending_start<hi: pending_durations.append(t-pending_start)
            pending_start=None
        if z['lease_granted'] and not prev['lease_granted'] and lo<=t<hi: grant_events.append((t,z['granted_ticket']))
        if z['old_start'] and not prev['old_start'] and lo<=t<hi: starts.append(t)
        if lo<=t<hi:
            for key in ['job_take','front_job_take','group_take']:
                if z[key] and not prev[key]: submission_events[key] += 1
    prev=z
    if t>=hi: break
assert len(grant_events) == len(pending_durations) == count('new_lease')
assert len(starts) == count('acquire_calls') == 223
assert sum(pending_durations) == dur['lease_pending']
overlap=collections.Counter()
for row in csv.DictReader((R/'async-overlap.tsv').open(),delimiter='\t'):
    if int(row['phase'])!=2: continue
    mask=int(row['mask']); n=int(row['cycles'])
    for name,bit in [('all',0),('output_full',256),('lease_granted',512),('lease_pending',1024)]:
        if bit==0 or mask&bit: overlap[name]+=n
    if mask&256 and mask&128: overlap['output_full_and_raster']+=n
assert overlap['all']==hi-lo and overlap['lease_granted']==dur['lease_granted'] and overlap['lease_pending']==dur['lease_pending']
function_names=['gfx_gpu_async_raw_acquire','gfx_gpu_async_raw_release_if_pending','gfx_gpu_async_raw_release',
 'gfx_gpu_async_service','gfx_async_try_load','load_alias_vertex_legacy','gfx_run_dl','load_alias_materialize_all',
 'gfx_mesh_lookup_impl','gfx_async_group_build','gfx_async_sources','gfx_async_route_task','gfx_async_dead_caller']
functions={name:{'candidate':warm['candidate']['function_retire_and_preceding_gap_cycles'].get(name,[0,0]),
                 'control':warm['control']['function_retire_and_preceding_gap_cycles'].get(name,[0,0]),
                 'delta':warm['function_delta'].get(name,[0,0])} for name in function_names}
acquire_read_names=['acquire_initial_epoch','held_epoch','sequence_precheck','retry_epoch_without_flush',
                    'retry_epoch_after_flush','loop_epoch','loop_status','loop_ticket']
fused_removed = count('target_write') + count('ticket_write')
report=dict(status='READ_ONLY_MEASURED_SUBMISSION_ACCOUNTING_NOT_IMPLEMENTED',phase='F28 warm',
 interval=dict(begin=lo,end=hi,candidate_cycles=hi-lo,control_cycles=warm['control']['cycles'],
               delta_cycles=hi-lo-warm['control']['cycles'],delta_percent=warm['cycle_change_percent'],
               candidate_retired=warm['candidate']['retired'],control_retired=warm['control']['retired']),
 calls=dict(async_members=submission_events['job_take'], frontend_requests=submission_events['front_job_take'], group_requests=submission_events['group_take'], single_requests=submission_events['front_job_take']-submission_events['group_take'], legacy_loads=len(starts),acquire_calls=count('acquire_calls'),actual_leases=count('new_lease'),
            retained_lease_reuses=count('acquire_calls')-count('new_lease'),
            release_if_pending_calls=count('release_if_pending_calls'),actual_pending_sequence_reads=count('pending_sequence'),
            pending_releases=count('pending_release_write'),new_quiescent_leases=count('pending_sequence')-count('pending_release_write'),
            acquire_status_reads=count('loop_status'),unsuccessful_status_iterations=count('failed_poll'),
            acquire_epoch_sequence_status_ticket_reads=sum(count(n) for n in acquire_read_names),
            service_calls=count('service_calls'),service_status_reads=count('service_status'),warm_repairs=0),
 hardware_residence_cycles=dict(dur),output_pressure=dict(overlap),
 lease_wait_distribution=dict(count=len(pending_durations),min=min(pending_durations),median=statistics.median(pending_durations),
                              max=max(pending_durations),sum=sum(pending_durations),one_cycle=sum(x==1 for x in pending_durations),
                              greater_than_two_cycles=sum(x>2 for x in pending_durations)),
 geo_mmio_writes=dict(candidate=candidate_mmio,control=control_mmio),
 hypothetical_fused_acquire_transaction_count_only=dict(
     condition='If target/ticket/control writes were replaced by one ticket-bearing ACQUIRE write with equivalent ownership/reset rules.',
     acquire_writes_before=3*count('new_lease'),acquire_writes_after=count('new_lease'),removed_writes=fused_removed,
     fraction_of_candidate_geo_writes_percent=100*fused_removed/candidate_mmio['total'],
     removed_store_pc_charged_gap_cycles=sum(site_counts[n]['preceding_retirement_gap_cycles'] for n in ['target_write','ticket_write']),
     wall_time_saving='UNMEASURED: posted-store retirement gaps are not end-to-end write latency; no cycle or FPS saving credited.'),
 selected_function_buckets=functions,top_added_charged_buckets=list(warm['function_delta'].items())[:15],
 selected_instruction_sites=site_counts,
 interpretation=[
  'Priority/snapshot keeps most grants prompt:75 of97 pending intervals last one cycle; total pending20328cycles.',
  'Lease-granted residence includes useful CPU rendering and canonical lifetime;1987610 granted cycles are not blocked CPU cycles.',
  'The three-write acquire protocol is291 writes. Context matrix/scalar writes are3518, up2037 versus control because new leases invalidate legacy dirty/control caches.',
  'Candidate executes223 legacy LOADs plus103 async members;19GROUPs and14single jobs reduce async frontend requests to33. Remaining legacy setup/admission/materialization persists.',
  'The acquire charged bucket96579 includes software setup/polling/cache gaps; it is not all hardware ownership wait.',
  'Fused acquisition alone would not change raw transformation, canonical readback, context reprogramming, material/admission lookup or raster/output pressure.'
 ],
 limits=['Two bounded native intervals; this report diagnoses warm only, not sustained Pocket FPS.',
         'Function/instruction buckets charge the gap preceding retirement, not exclusive execution latency.',
         'Hardware residence counters overlap; never sum them or subtract hypothetical eliminated work from frame cycles.',
         'No new ABI, implementation, fit, or replay was performed.'],
 inputs_sha256={str(p.resolve()):sha(p) for p in dict.fromkeys(inputs)})
prior = load(C/'analysis/remaining-submission-v1/result.json')
single = load(C/'analysis/phases-v4-vs-single.json')
for name,digest in single['inputs_sha256'].items(): assert sha(name)==digest
report['strict_validation'] = {name:load(C/f'validation/{name}/check-independent-v4.json')['status']
                               for name in ['candidate-v4','control-v4','single-v4']}
assert all(status.startswith('PASS') for status in report['strict_validation'].values())
report['full_intervals'] = {name:[p[name]['cycles'] for p in phase_report['phases']] for name in ['candidate','control']}
report['full_intervals']['combined_delta_cycles'] = sum(report['full_intervals']['candidate'])-sum(report['full_intervals']['control'])
report['comparison_to_f27_candidate'] = dict(cycle_delta=report['interval']['candidate_cycles']-prior['interval']['candidate_cycles'],
    note='Different app ELF; descriptive measured candidate comparison, not a same-ELF architectural comparison.',
    legacy_load_delta=report['calls']['legacy_loads']-prior['calls']['legacy_loads'],
    real_lease_delta=report['calls']['actual_leases']-prior['calls']['actual_leases'],
    geo_write_delta=candidate_mmio['total']-prior['geo_mmio_writes']['candidate']['total'],
    lease_wait_delta=dur['lease_pending']-prior['hardware_residence_cycles']['lease_pending'])
report['same_elf_aj02_warm'] = dict(cycles=single['phases'][1]['control']['cycles'],
    candidate_delta_cycles=report['interval']['candidate_cycles']-single['phases'][1]['control']['cycles'],
    candidate_delta_percent=single['phases'][1]['cycle_change_percent'])
for q in [C/'analysis/remaining-submission-v1/result.json',C/'analysis/phases-v4-vs-single.json',
          C/'validation/single-v4/check-independent-v4.json',C/'validation/f28-dl-binding-v1.json']:
    report['inputs_sha256'][str(q.resolve())] = sha(q)
(HERE/'result.json').write_text(json.dumps(report,indent=2)+'\n')
(HERE/'REPORT.txt').write_text('F28 remaining CPU submission work (read-only)\n\n'
 'The complete warm interval is4,630,859 cycles versus4,631,100 for the same-ELF control:241cycles fewer (0.0052%), effectively tied. Retired instructions fall from2,202,217 to2,023,020. Cold remains4,806,008 versus4,256,732; combined9,436,867 versus8,887,832 (+549,035cycles). These measured boundaries determine performance. All three control/AJ02/AJ03 strict v4 checks passed.\n\n'
 '103 async members use33 requests (19GROUPs+14single). The223 remaining legacy LOADs invoke acquisition, but126 reuse a held lease. There are97 actual grants, each with three acquisition writes, followed by97 releases.507 release-if-pending calls generate97 completed-sequence MMIO reads:83 release and14 mark the lease quiescent. Acquisition performs486 status reads, including389 unsuccessful iterations; the zero-repair service path is called777 times.\n\n'
 'Hardware lease-pending time totals20,328cycles (median1, max1,667);75 of97 waits take one cycle. Granted residence1,987,610cycles includes131,497 legacy raw-transform cycles and106,537 cycles concurrent with old async output. This is useful ownership, not CPU blocking. Output full totals1,028,967cycles,931,824 overlapping fragment rasterization. These residence counters overlap.\n\n'
 'GEO writes are4,575 versus2,459 control: matrix2,496 versus1,136; scalar1,022 versus345; LOAD setup669 versus978; lease388 versus0. Thus there are2,037 extra context writes, despite103 fewer legacy LOADs. The source invalidates legacy matrix/scalar dirty state after every newly acquired lease because the shared worker may have changed it.\n\n'
 'A hypothetical fused ticket-bearing ACQUIRE could replace291 acquisition writes with97:194 fewer transactions (4.24% of candidate GEO writes). The two removed store PCs carry776 charged preceding-retirement-gap cycles, which are neither full posted-write latency nor predicted frame savings. No new ABI or implementation was made.\n\n'
 'Top added charged function buckets are acquire+96,579, mesh lookup+50,428, interpreter+48,215, admission+37,277, route-task+28,914, group build+20,437 and service+19,552. Removed buckets include CPU indexed work−171,677, prepared vertex upload−66,617 and canonical copy−51,365. Software setup/classification and context traffic still offset the reduced geometry work; these diagnostic buckets are not exclusive intervals and cannot be subtracted as future savings.\n\n'
 'Compared with F27 candidate, F28 executes27 fewer legacy LOADs, six fewer leases and337 fewer GEO writes; its own warm interval is93,403cycles shorter, with different ELF/layout. Against same-F28 AJ02 single-job mode, grouping saves18,659 warm cycles (0.4013%); the comparison with unchanged same-F28 control remains a tie. No physical fit, deployment or FPS prediction is supported.\n')
print(json.dumps({'status':report['status'],'result_sha256':sha(HERE/'result.json'),'report_sha256':sha(HERE/'REPORT.txt')},indent=2))
