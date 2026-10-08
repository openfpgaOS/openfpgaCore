#!/usr/bin/env python3
"""Private exact rebase of proved queue finality onto the selected e75 GPU."""
import concurrent.futures
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import phase10_gpu_queue_finality as literal_helper

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'gpu-control-owner-v1/gpu_core.v'
OLD_PROOF = P / 'gpu-control-owner-review-v1'
OUT = P / 'gpu-queue-finality-v18'
REVIEW = P / 'gpu-queue-finality-review-v18'
BASE_SHA = 'e75ac93db5483ee8525c44ef1aeed4e610f8deecf8fa42765997367873dd4f15'
OLD = """wire fbwq_head_chain_final = (fbwq_start_burst_words == 4'd8)
                          || (fbwq_count > {1'b0, fbwq_start_burst_words});"""
NEW = """// A chain is final at eight queued words, or at any broken link
// between existing entries. Do not encode its length just to test finality.
// The actual AWLEN/remaining encoder above is unchanged.
wire fbwq_head_chain_final = (fbwq_count >= 5'd8)
    || ((fbwq_count >= 5'd2) && !fbwq_link_next[fbwq_rd_ptr])
    || ((fbwq_count >= 5'd3) && !fbwq_link_next[fbwq_rd_ptr_1])
    || ((fbwq_count >= 5'd4) && !fbwq_link_next[fbwq_rd_ptr_2])
    || ((fbwq_count >= 5'd5) && !fbwq_link_next[fbwq_rd_ptr_3])
    || ((fbwq_count >= 5'd6) && !fbwq_link_next[fbwq_rd_ptr_4])
    || ((fbwq_count >= 5'd7) && !fbwq_link_next[fbwq_rd_ptr_5]);"""
PROBE = r'''
reg[63:0] finality_cycles=0,finality_true=0,finality_ahead=0,finality_gather=0;
wire finality_reference=(gpu.fbwq_start_burst_words==4'd8)||
 (gpu.fbwq_count>{1'b0,gpu.fbwq_start_burst_words});
always @(posedge clk)if(reset_n&&!gpu.soft_reset)begin
 if(gpu.fbwq_head_chain_final!==finality_reference)$fatal(1,"live queue finality differs");
 if(gpu.fbwq_count>16)$fatal(1,"queue count outside capacity");
 finality_cycles<=finality_cycles+1;
 if(finality_reference)finality_true<=finality_true+1;
 if(gpu.fbwq_aw_ahead_issue)finality_ahead<=finality_ahead+1;
 if(gpu.fbwq_gather_prefix)finality_gather<=finality_gather+1;
end
final $display("FINALITY_AUDIT cycles=%0d final=%0d ahead=%0d gather=%0d",
 finality_cycles,finality_true,finality_ahead,finality_gather);
'''


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p): return json.loads(p.read_text())
def save(p, v):
    with p.open('x') as f:
        json.dump(v, f, indent=2, sort_keys=True); f.write('\n')


def checked_run(command, log):
    with log.open('x') as f:
        subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)


def prepare():
    assert sha(BASE) == BASE_SHA
    old = BASE.read_text(); assert old.count(OLD) == 1
    previous = (P / 'gpu-queue-finality-v15/gpu_core.v').read_text()
    assert previous.count(NEW) == 1
    selected = old.replace(OLD, NEW)
    assert selected.replace(NEW, OLD) == old
    OUT.mkdir(); REVIEW.mkdir()
    (OUT / 'gpu_core.v').write_text(selected)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True), selected.splitlines(True), fromfile='e75-parent/gpu_core.v', tofile='queue-finality-v18/gpu_core.v')))
    shutil.copy2(__file__, OUT / 'recipe.py')
    save(OUT / 'prepared.json', dict(parent_sha256=sha(BASE), candidate_sha256=sha(OUT / 'gpu_core.v'), added_FF=0, added_states=0, added_cycles=0, single_predicate_only=True, previous_literal_manifest_sha256=sha(P/'gpu-queue-finality-review-v15/manifest.json'), deployment_ready=False))
    frozen = REVIEW / 'model/frozen'
    shutil.copytree(OLD_PROOF / 'model/frozen', frozen)
    gp = frozen / 'common/gpu_core.v'; source = gp.read_text()
    assert source.count(OLD) == 1
    gp.write_text(source.replace(OLD, NEW))
    tb = frozen / 'tb_gpu.v'; source = tb.read_text()
    assert source.count('endmodule') == 1
    tb.write_text(source.replace('endmodule', PROBE + '\nendmodule'))
    commands = [s.replace(str(OLD_PROOF / 'model'), str(REVIEW / 'model')) for s in read(OLD_PROOF / 'model/command.json')]
    save(REVIEW / 'model/command.json', commands)
    original_files = {str(f.relative_to(OLD_PROOF/'model/frozen')):sha(f) for f in (OLD_PROOF/'model/frozen').rglob('*') if f.is_file()}
    selected_files = {str(f.relative_to(frozen)):sha(f) for f in frozen.rglob('*') if f.is_file()}
    assert original_files.keys() == selected_files.keys()
    assert [k for k in original_files if original_files[k] != selected_files[k]] == ['tb_gpu.v', 'common/gpu_core.v']
    save(REVIEW / 'prepared.json', dict(candidate_sha256=sha(OUT/'gpu_core.v'), immediate_parent_sha256=BASE_SHA, baseline_manifest_sha256=sha(OLD_PROOF/'manifest.json'), baseline_frozen_sha256=original_files, candidate_frozen_sha256=selected_files, scope='Exact previously instrumented current-parent model, one functional predicate substitution and one passive literal finality assertion; inherited queue/operand/ownership monitors retained.'))


def literal():
    literal_helper.BASE = BASE; literal_helper.OUT = OUT
    literal_helper.literal()


def build():
    checked_run(read(REVIEW/'model/command.json'), REVIEW/'model/build.log')


def counters(text, tag):
    m = re.search(tag+r' (.*)', text); assert m, tag
    return {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)', m[1])}


def compare(log, output, old_log, old_output):
    a = log.read_text(); b = old_log.read_text()
    assert 'FATAL' not in a and 'Error' not in a
    pattern = r'RESULT cycles=(\d+) busy_cycles=(\d+) batches=(\d+)'
    actual = re.search(pattern,a).groups(); expected = re.search(pattern,b).groups()
    assert actual == expected and output.read_bytes() == old_output.read_bytes()
    return dict(cycles=int(actual[0]), busy_cycles=int(actual[1]), batches=int(actual[2]), output_sha256=sha(output), bytes_equal=True, cycles_equal=True, finality=counters(a,'FINALITY_AUDIT'), queue=counters(a,'QUEUE_AUDIT'), owner=counters(a,'CONTROL_OWNER_AUDIT'), baseline_log_sha256=sha(old_log), baseline_output_sha256=sha(old_output))


def run():
    work = REVIEW/'runs'; work.mkdir()
    jobs = []
    for traffic,frames in [(0,[360,367]),(1,list(range(360,368))),(2,[360,367])]:
        for frame in frames:
            label = f'candidate-t{traffic}-f{frame}'
            jobs.append((label, [f'+memory_traffic={traffic}','+fast_tex=1','+tagged_tex=1'], P/f'gpu-critical-bound-v2/replay/frame_{frame:05}.vec', OLD_PROOF/'runs'/label/'run.log', OLD_PROOF/'runs'/label/'output.bin'))
    for fast in (0,1):
        for traffic in (0,2):
            label = f'candidate-fast{fast}-t{traffic}'
            jobs.append((label,[f'+fast_tex={fast}','+tagged_tex=1',f'+memory_traffic={traffic}'],OLD_PROOF/'boundaries-v3/triangles.vec',OLD_PROOF/f'boundaries-v3/{label}.log',OLD_PROOF/f'boundaries-v3/{label}.bin'))
    def one(job):
        label,opts,vector,old_log,old_output = job
        d=work/label; d.mkdir()
        command=[str(REVIEW/'model/obj/Vtb_gpu'),*opts,str(vector),str(d/'output.bin')]
        save(d/'command.json',command); checked_run(command,d/'run.log')
        result=compare(d/'run.log',d/'output.bin',old_log,old_output)
        result.update(label=label,vector_sha256=sha(vector)); save(d/'result.json',result)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: rows=list(pool.map(one,jobs))
    totals={tag:{key:sum(row[tag][key] for row in rows) for key in rows[0][tag]} for tag in ['finality','queue','owner']}
    assert totals['queue']['pushes']==totals['queue']['reads']
    assert all(totals['queue'][k]>0 for k in ['ahead','partial','w_stall','aw_stall','resets'])
    assert totals['finality']['ahead']>0 and totals['finality']['final']>0
    save(REVIEW/'result.json',dict(passed=True,pairs=16,totals=totals,rows=rows))


def abort():
    d=REVIEW/'abort';d.mkdir();model=REVIEW/'model'; obj=model/'obj'
    source=OLD_PROOF/'abort/replay-abort.cpp';shutil.copy2(source,d/'replay-abort.cpp')
    command=read(OLD_PROOF/'abort/candidate-command.json')
    command=[x.replace(str(OLD_PROOF/'model'),str(model)).replace(str(OLD_PROOF/'abort'),str(d)) for x in command]
    save(d/'command.json',command);checked_run(command,d/'build.log');rows=[]
    for phase in (1,3):
        vector=OLD_PROOF/f'abort/phase{phase}.vec'
        for traffic in (0,2):
            name=f'candidate-phase{phase}-t{traffic}'; log=d/(name+'.log');output=d/(name+'.bin')
            cmd=[str(d/'candidate'),'+fast_tex=1','+tagged_tex=1',f'+memory_traffic={traffic}',str(vector),str(output)]
            save(d/(name+'-command.json'),cmd);checked_run(cmd,log)
            result=compare(log,output,OLD_PROOF/f'abort/{name}.log',OLD_PROOF/f'abort/{name}.bin')
            assert result['owner']['soft_resets']>0
            result.update(phase=phase,traffic=traffic,vector_sha256=sha(vector));rows.append(result)
    save(d/'result.json',dict(passed=True,pairs=4,rows=rows,scope='Existing actual MMIO reset during LA_SELECT/LA_WAIT before initial pixels, unsupported/wrong-size commands and valid recovery. Not arbitrary active-AXI abort.'))


def seal():
    lit=read(OUT/'literal/result.json'); native=read(REVIEW/'result.json'); ab=read(REVIEW/'abort/result.json')
    assert lit['passed'] and native['passed'] and ab['passed']
    assert sha(BASE)==BASE_SHA and (OUT/'gpu_core.v').read_text()==BASE.read_text().replace(OLD,NEW)
    evidence={str(f.relative_to(R)):sha(f) for f in [BASE,Path(__file__),P/'gpu-queue-finality-review-v15/manifest.json',OLD_PROOF/'manifest.json',OLD_PROOF/'abort/manifest.json']}
    for root in [OLD_PROOF/'model/frozen',OLD_PROOF/'runs',OLD_PROOF/'boundaries-v3',OLD_PROOF/'abort']:
        for f in root.rglob('*'):
            if f.is_file() and ('obj' not in f.parts):evidence[str(f.relative_to(R))]=sha(f)
    shutil.copy2(__file__, REVIEW/'recipe.py')
    save(REVIEW/'manifest.json',dict(passed=True,files={str(f.relative_to(REVIEW)):sha(f) for f in REVIEW.rglob('*') if f.is_file() and 'obj' not in f.parts},evidence_sha256=evidence))
    save(OUT/'result.json',dict(passed=True,candidate_sha256=sha(OUT/'gpu_core.v'),parent_sha256=BASE_SHA,literal=lit,native_pairs=16,abort_pairs=4,live_finality_checks=native['totals']['finality']['cycles'],added_FF=0,added_cycles=0,added_states=0,AWLEN_data_and_queue_storage_unchanged=True,tests=native['totals'],deployment_ready=False))
    (OUT/'REPORT.txt').write_text('PASS: only direct head-chain-final predicate rebased onto e75ac93. AWLEN encoder, queue depth16, storage, data/strobe ordering, pointer/count updates, reset and throughput are unchanged.\nFresh literal RTL exhausts all33,554,432 count/pointer/link combinations. Current-parent model replay/boundary16 pairs and existing MMIO reset/recovery4 pairs preserve exact bytes and cycles, with live finality and inherited queue ownership/stall assertions.\nSelected live GPU_WRITE_GATHER=0; all-count/all-link literal equivalence covers its unchanged Boolean input, not separate enabled-gather rendering. Abort phases precede first pixels; no new active-AXI cancellation claim. No synthesis/fitter/production action or timing improvement claim.\n')
    save(OUT/'manifest.json',dict(passed=True,passed_for_resource_screen=True,deployment_ready=False,files={str(f.relative_to(OUT)):sha(f) for f in OUT.rglob('*') if f.is_file() and 'obj' not in f.parts},evidence_sha256={str((REVIEW/'manifest.json').relative_to(R)):sha(REVIEW/'manifest.json')}))
    print('SEALED',sha(OUT/'manifest.json'))


if __name__=='__main__':
    for action in sys.argv[1:]:globals()[action]()
