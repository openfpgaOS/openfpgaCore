#!/usr/bin/env python3
"""Read-only F27 warm submission accounting; no counterfactual timing subtraction."""
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
R = C/'validation/candidate-v3/run'
CONTROL = C/'validation/control-v3/run'
APP = C/'work-app-v3/evidence/app-build-v3'
inputs = [Path(__file__), C/'analysis/phases-v3.json', C/'analysis/native-v3.json',
          APP/'build-result.json', APP/'app.dis', C/'native-v2/prepared.json',
          C/'validation/candidate-v3/check-independent-v2.json',
          C/'validation/control-v3/check-independent-v2.json',
          C/'work-app-v3/src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
          C/'work-app-v3/src/sm64/sm64/src/pc/gfx/gfx_pc.c', C/'validation/group_hooks.py']
phase_report = load(C/'analysis/phases-v3.json')
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
sites = {'acquire_calls':0x104debc0, 'new_lease':0x104dedec, 'failed_poll':0x104ded7a,
 'acquire_initial_epoch':0x104dec5c, 'held_epoch':0x104debf0, 'sequence_precheck':0x104dec88,
 'retry_epoch_without_flush':0x104dedba, 'retry_epoch_after_flush':0x104dec9c,
 'target_write':0x104decdc, 'ticket_write':0x104decec, 'acquire_command_write':0x104ded06,
 'loop_epoch':0x104ded84, 'loop_status':0x104ded5a, 'loop_ticket':0x104ded6a,
 'release_if_pending_calls':0x104d8f00, 'pending_epoch':0x104d8f24, 'pending_sequence':0x104d8fde,
 'pending_release_epoch':0x104d8fee, 'pending_release_write':0x104d8fc8,
 'before_wait_calls':0x104d9040, 'before_wait_epoch':0x104d9080, 'before_wait_sequence':0x104d90c0,
 'before_wait_release_epoch':0x104d90e8, 'before_wait_release_write':0x104d912e,
 'service_calls':0x104d91c0, 'service_status':0x104d91e4,
 'direct_release_calls':0x104dee80, 'direct_release_epoch':0x104dee9e, 'direct_release_write':0x104deee4}
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
        'output_pending','snapshot_detached','group_active','repair_pending','gpu_state']
dur = collections.Counter(); grant_events=[]; pending_durations=[]; starts=[]
prev=None; pending_start=None
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
    prev=z
    if t>=hi: break
assert len(grant_events) == len(pending_durations) == count('new_lease')
assert len(starts) == count('acquire_calls') == 250
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
 'gfx_mesh_lookup_impl','gfx_async_group_build','gfx_async_sources']
functions={name:{'candidate':warm['candidate']['function_retire_and_preceding_gap_cycles'].get(name,[0,0]),
                 'control':warm['control']['function_retire_and_preceding_gap_cycles'].get(name,[0,0]),
                 'delta':warm['function_delta'].get(name,[0,0])} for name in function_names}
acquire_read_names=['acquire_initial_epoch','held_epoch','sequence_precheck','retry_epoch_without_flush',
                    'retry_epoch_after_flush','loop_epoch','loop_status','loop_ticket']
fused_removed = count('target_write') + count('ticket_write')
report=dict(status='READ_ONLY_MEASURED_SUBMISSION_ACCOUNTING_NOT_IMPLEMENTED',phase='F27 warm',
 interval=dict(begin=lo,end=hi,candidate_cycles=hi-lo,control_cycles=warm['control']['cycles'],
               delta_cycles=hi-lo-warm['control']['cycles'],delta_percent=warm['cycle_change_percent'],
               candidate_retired=warm['candidate']['retired'],control_retired=warm['control']['retired']),
 calls=dict(legacy_loads=len(starts),acquire_calls=count('acquire_calls'),actual_leases=count('new_lease'),
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
  'Priority/snapshot already makes most acquisitions prompt: 87 of 103 pending intervals last one cycle; total pending is 15267 cycles.',
  'Lease-granted residence includes useful CPU rendering and canonical lifetime, so 2277534 granted cycles are not CPU blocked cycles.',
  'The three-write acquire protocol is only 309 writes. Context matrix/scalar writes are 3750 and increase by2269 versus control, because new leases invalidate legacy dirty/control caches.',
  'Candidate still executes250 legacy LOADs plus76 async members; GROUP batches the latter into27 submissions without removing legacy setup/admission/materialization.',
  'The acquire function charged bucket108293 includes software setup/polling/cache gaps; it is not all hardware ownership wait and cannot be replaced with a summed savings estimate.',
  'Fused acquisition alone does not change raw transformation, canonical readback, context reprogramming, material/admission lookup or raster/output pressure.'
 ],
 limits=['Two bounded native intervals; this report diagnoses warm only, not sustained Pocket FPS.',
         'Function/instruction buckets charge the gap preceding retirement, not exclusive execution latency.',
         'Hardware residence counters overlap; never sum them or subtract hypothetical eliminated work from frame cycles.',
         'No new ABI, implementation, fit, or replay was performed.'],
 inputs_sha256={str(p.resolve()):sha(p) for p in dict.fromkeys(inputs)})
(HERE/'result.json').write_text(json.dumps(report,indent=2)+'\n')
(HERE/'REPORT.txt').write_text('F27 remaining CPU submission work (read-only)\n\n'
 'The complete warm interval is4,724,262 cycles versus4,649,707 for the same-ELF control: +74,555 (+1.603%). Retired instructions fall from2,198,460 to2,080,136. These measured boundaries, not charged buckets, decide performance.\n\n'
 '250 legacy LOADs invoke acquisition, but147 reuse an existing lease. There are103 actual grants, each with three acquisition writes, followed by103 releases. The555 release-if-pending calls produce only103 completed-sequence MMIO reads:87 release a blocking lease and16 mark it quiescent. Acquisition reads status414 times, including311 unsuccessful loop iterations. The no-repair service fast path is still called832 times.\n\n'
 'Actual hardware lease-pending time totals15,267 cycles (median1, maximum2,001);87 of103 waits are one cycle. Lease-granted residence is2,277,534 cycles, of which146,838 overlap legacy raw transformation. This is ownership while the CPU uses canonical state, not a blocked-CPU counter. Output FIFO full lasts1,093,279 cycles,988,952 overlapping fragment rendering; these overlap other counters.\n\n'
 'GEO writes rise from2,459 to4,912: matrix1,136→2,656; scalar345→1,094; LOAD setup978→750; lease protocol0→412. New lease acquisition deliberately invalidates the legacy matrix/scalar dirty cache. Context reprogramming therefore accounts for2,269 extra writes even though hardware LOAD starts fall326→250.\n\n'
 'A hypothetical one-write ticket-bearing ACQUIRE could replace309 acquisition writes with103, eliminating206 transactions (4.19% of candidate GEO writes). The two corresponding store PCs carry824 preceding-retirement-gap cycles in this run; that is not their full posted-write cost or a prediction of saved frame time. Epoch/status/grant checks, lazy lifetime, material lookup, context restoration and output backpressure remain. No fused ABI is implemented.\n\n'
 'Largest added charged function buckets are acquire+108,293, mesh lookup+51,101, display-list interpreter+40,218, admission+30,404, service+21,725 and source lookup+17,338. Large removed buckets include CPU indexed work−135,447, prepared vertex upload−42,823 and canonical copy−33,505. These diagnostic buckets are not exclusive work intervals or recoverable savings. Exact sites, disassembly, hashes and the complete comparison are in result.json.\n')
print(json.dumps({'status':report['status'],'result_sha256':sha(HERE/'result.json'),'report_sha256':sha(HERE/'REPORT.txt')},indent=2))
