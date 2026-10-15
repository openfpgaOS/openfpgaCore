#!/usr/bin/env python3
"""Supplemental independent checks; preserves previously bound replay/checkers."""
import argparse,hashlib,importlib.util,json
from pathlib import Path
Q=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('qcheck',Q/'check_replays.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
def semantics(frames):
    slots={};material=None;obj=None;events=[];draws=0
    for frame in frames:
        out=[]
        for op,data in frame['commands']:
            if op==0x56:
                assert len(data)==8 and 0<=data[0]<32 and obj is not None
                slots[data[0]]=[data[1:],obj.copy()];continue
            if op==0x4a:assert len(data)==17;material=data;continue
            if op==0x50:assert len(data)==26;obj=data;continue
            if op==0x54:
                assert len(data)==1 and data[0]<32768 and material is not None and obj is not None
                out.append([op,[slots[data[0]>>(5*k)&31]for k in range(3)],material,obj]);draws+=1
            elif op==0x4e:
                assert material is not None;out.append([op,data,material])
            else:
                assert op in (0x11,0x42,0x02);out.append([op,data])
        events.append(out)
    return events,draws

def main():
    p=argparse.ArgumentParser();p.add_argument('candidate');p.add_argument('baseline');p.add_argument('fixture');p.add_argument('capacity',type=int);p.add_argument('report');a=p.parse_args()
    run=Q/a.candidate/'run';base=Q/a.baseline/'run';fixture=Q/a.fixture
    bound={}
    def verify(root,inventory):
        for path,h in inventory.items():
            f=Path(path);f=f if f.is_absolute() else root/f
            assert sha(f)==h,f;bound[str(f)]=h
    prepared=read(fixture/'prepared.json');assert prepared['passed'];verify(fixture,prepared['files'])
    for part in ('capture','guest'):
        manifest=read(fixture/part/'manifest.json');assert manifest['passed'];verify(fixture/part,manifest['files'])
    c.ref.N=fixture;c.ref.CFG=dict(line.split('=',1)for line in(fixture/'input.cfg').read_text().splitlines()if '='in line);c.ref.prior.health=c.health
    builds=[read(x.parent/'build.json')for x in(base,run)]
    assert all(Path(x['config']).resolve()==(fixture/'input.cfg').resolve()for x in builds)
    assert all(sha(Path(x['elf']))==prepared['elf_sha256']for x in builds)
    assert Path(builds[0]['model']).resolve()==(Q.parent/'cached-cpu-v1/native-sdram-v2').resolve()
    baseline,commands,golden=c.ref.validate(base);c.ref.front_preparation(base)
    assert [f['commands']for f in commands]==[f['commands']for f in golden]
    candidate,ingress,_=c.ref.validate(run);c.ref.front_preparation(run)
    output,_=c.ref.commands(run/'worker-commands.bin')
    original,draws=semantics(commands);actual,draws2=semantics(output)
    assert original==actual and draws==draws2
    queue=c.queue_checks(run,ingress);states=c.rows(run/'queue-events.tsv')
    assert not any(s['error']or s['import_error']or s['math_abort']for s in states)
    # active includes an accepted START still waiting for shared-FMA ownership.
    assert not any(s['math_owner']and s['raw_active']and not s['raw_pending']for s in states)
    assert not any(s['math_outstanding']and not s['math_owner']for s in states)
    assert all(((s['output_write']-s['output_read'])&4095)<=a.capacity-1 for s in states)
    final=states[-1]
    assert final['state']==1 and final['input_read']==final['input_write'] and final['output_read']==final['output_write']
    assert not any(final[k]for k in('repair','math_owner','math_outstanding','raw_active','raw_pending'))
    written=readwords=0
    for x,y in zip(states,states[1:]):
        w=(y['output_write']-x['output_write'])&4095;r=(y['output_read']-x['output_read'])&4095
        assert w<=1 and r<=1;written+=w;readwords+=r
    assert written==readwords==(run/'worker-commands.bin').stat().st_size//4
    prep=read(run/'prepared-queue.json');s0=states[0]
    assert prep['host_clock_edges_added']==0 and prep['ingress_after']==int(c.ref.CFG['ring_word_pointer'],0)
    assert s0['input_read']==s0['input_write']==prep['ingress_after']
    assert s0['output_write']==s0['output_read']==prep['output_empty_pointer_preserved'] and s0['state']==0
    def geometry(path):
        return [x.split()[2:]for x in(path/'mmio.tsv').read_text().splitlines()if x.split()[1]=='GEO'and int(x.split()[2])!=60 and not 0x80<=int(x.split()[2])<0xc0]
    assert geometry(run)==geometry(base)
    for r in (base,run):
        for f in r.iterdir():
            if f.is_file():bound[str(f)]=sha(f)
        for name in ('build.json','native_profiled','native_profiled.cpp'):bound[str(r.parent/name)]=sha(r.parent/name)
    for f in (Path(__file__),Q/'check_replays.py',c.T/'tests/check_replays.py',c.ref.OLD,fixture/'prepared.json'):
        bound[str(f)]=sha(f)
    report=dict(status='PASS_STRICT_QUEUE_REPLAY_AUDIT',candidate=a.candidate,baseline=a.baseline,fixture=a.fixture,
        cycles=candidate['health']['cycles'],indexed_draws=draws,vertex_load_time_full_object_state_exact=True,
        draw_time_state_and_side_effect_order_exact=True,baseline_commands_match_bound_functional_capture=True,
        queue=queue,maximum_output_occupancy=max((s['output_write']-s['output_read'])&4095 for s in states),
        output_capacity_words=a.capacity,worker_logged_words=written,all_published_words_consumed=True,
        no_import_protocol_error_or_math_overlap=True,initial_empty_queue_preparation=prep,
        all_color_depth_buffers_pool_registers_FCSR_and_deadlines_exact=True,
        accepted_normalizations=['Typed SET_TRI_STATE framebuffer base and FLIP buffer index only.',
          'Functional capture only: exact known third-buffer zero clear, justified by zero preimage and prepared cleared flag.',
          'Integer s5 frame timestamp at later boundaries only; initial registers and all FP/FCSR exact.',
          'Unused cache slots and redundant sticky state writes may differ; every draw compares payload plus full load-time projection/object state and current material/object state.'],
        limitations=['Two prepared intervals; no sustained gameplay/FPS, fit, timing or hardware claim.'],evidence_sha256=bound)
    dst=Q/'review'/a.report;assert not dst.exists();dst.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items()if k!='evidence_sha256'},indent=2))
if __name__=='__main__':main()
