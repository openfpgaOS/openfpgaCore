#!/usr/bin/env python3
"""Independent emitted scope, live-phase and operand review of DDS reuse."""
from pathlib import Path
from collections import deque
import hashlib
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'texture-offer-v1/gpu_core.v'
SOURCE=ART/'gpu-coordinate-stage-v1/gpu_core.v'
OUT=ART/'gpu-coordinate-stage-emitted-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def clean(s):
    s=re.sub(r'/\*.*?\*/',lambda m:'\n'*m[0].count('\n'),s,flags=re.S)
    return re.sub(r'//[^\n]*','',s)
def blocks(s):
    start=s.index('            case (dstate)')
    end=s.index('            end // INCLUDE_VERT_TRI',start)
    text=s[start:end]
    matches=list(re.finditer(r'^                (DRV_\w+|default):',text,re.M))
    return {m[1]:text[m.start():matches[i+1].start() if i+1<len(matches) else text.rindex('            endcase')] for i,m in enumerate(matches)},(start,end)

def main():
    assert sha(BASE)=='2d4870c5b1c687cb51a3e60aef0fa0d31372bcea4c4dd521a0c825dd8ba24ec7'
    assert sha(SOURCE)=='8e47122870ad71aea38f761691b252173fdc351b22255f7a591207f4ac245185'
    old,new=BASE.read_text(),SOURCE.read_text()
    oldb,span=blocks(old);newb,_=blocks(new)
    added='''                    // Reuse the not-yet-live delta/anchor bank as coordinate staging.
                    // Sorting is complete; raw vertices/order hold until DRV_DELTA.
                    dd_x0 <= dvx[dv_ord[0]];
                    dd_y0 <= dvy[dv_ord[0]];
                    dd1x <= $signed(dvx[dv_ord[1]]);
                    dd2x <= $signed(dvx[dv_ord[2]]);
                    dd1y <= $signed(dvy[dv_ord[1]]);
                    dd2y <= $signed(dvy[dv_ord[2]]);
'''
    oldreads='''                    sx0 = dvx[dv_ord[0]]; sx1 = dvx[dv_ord[1]]; sx2 = dvx[dv_ord[2]];
                    sy0 = dvy[dv_ord[0]]; sy1 = dvy[dv_ord[1]]; sy2 = dvy[dv_ord[2]];'''
    newreads='''                    sx0 = dd_x0; sx1 = dd1x[15:0]; sx2 = dd2x[15:0];
                    sy0 = dd_y0; sy1 = dd1y[15:0]; sy2 = dd2y[15:0];'''
    anchor='                DRV_PROD_L0: begin\n'
    assert old.count(anchor)==old.count(oldreads)==1
    assert new==old.replace(anchor,anchor+added).replace(oldreads,newreads)
    assert newb['DRV_PROD_L0'].replace(added,'')==oldb['DRV_PROD_L0']
    assert newb['DRV_DELTA'].replace(newreads,oldreads)==oldb['DRV_DELTA']
    names=['dd_x0','dd_y0','dd1x','dd2x','dd1y','dd2y']
    captures=dict(re.findall(r'\b(dd\w+)\s*<=\s*([^;]+);',added))
    oldmap=dict(re.findall(r'\b(s[xy][012])\s*=\s*([^;]+);',oldreads))
    newmap=dict(re.findall(r'\b(s[xy][012])\s*=\s*([^;]+);',newreads))
    for k,rhs in newmap.items():
        if rhs.endswith('[15:0]'):
            rhs=rhs[:-6]
            assert captures[rhs].startswith('$signed(')
            resolved=captures[rhs][8:-1]
        else: resolved=captures[rhs]
        assert resolved==oldmap[k]
    cleaned=clean(old)
    assert 'reg signed [16:0] dd1x, dd2x, dd1y, dd2y;' in cleaned
    assert 'reg signed [15:0] dd_x0, dd_y0;' in cleaned
    assert 'reg signed [15:0] sx0, sx1, sx2, sy0, sy1, sy2;' in cleaned
    assert 'reg signed [16:0] e1x, e2x, e1y, e2y;' in cleaned
    # Close every old DDS token: declarations or the three actual FSM arms.
    allowed=''.join(oldb[k] for k in ['DRV_DELTA','DRV_PLANE_NUM','DRV_PLANE_ORG'])
    remaining=cleaned
    for k in ['DRV_DELTA','DRV_PLANE_NUM','DRV_PLANE_ORG']:remaining=remaining.replace(clean(oldb[k]),'')
    lines=[s.strip() for s in remaining.splitlines() if re.search(r'\b(?:'+'|'.join(names)+r')\b',s)]
    assert lines==['reg signed [16:0] dd1x, dd2x, dd1y, dd2y;','reg signed [15:0] dd_x0, dd_y0;']
    # Main-state writes cannot alter coordinates during the derivation arm.
    state_labels=list(re.finditer(r'^        (S_\w+):',cleaned,re.M))
    writes=[]
    for m in re.finditer(r'\btri_v[012]_[xy]\s*<=',cleaned):
        label=next(x[1] for x in reversed(state_labels) if x.start()<m.start())
        assert label in ('S_PAY_DATA','S_XFORM','S_VCREAD'),label
        writes.append(label)
    assert writes
    for k,b in oldb.items():
        assert not re.search(r'\btri_v[012]_[xy]\s*<=',clean(b))
        if k not in ('DRV_SORT_A','DRV_SORT_B','DRV_SORT_C'):
            assert not re.search(r'\bdv_ord\[[^\]]+\]\s*<=',clean(b))
    for axis in 'xy':
        for i in range(3):assert f'assign dv{axis}[{i}] = tri_v{i}_{axis};' in cleaned
    entries=re.findall(r'dstate\s*<=\s*DRV_SORT_A\s*;\s*state\s*<=\s*S_TRI_DERIVE\s*;',cleaned)
    assert len(entries)==len(re.findall(r'state\s*<=\s*S_TRI_DERIVE\s*;',cleaned))==3
    body=clean(old[span[0]:span[1]])
    assert re.findall(r'dstate\s*<=\s*(\w+)',cleaned.replace(body,''))==['DRV_SORT_A']*3
    # Independently walk a conservative graph. All conditional edges and holds
    # are admitted; L0/DELTA must unconditionally advance with their data write.
    graph={k:set(re.findall(r'dstate\s*<=\s*(DRV_\w+)',clean(b)))|{k} for k,b in oldb.items()}
    for k,dest in [('DRV_PROD_L0','DRV_PROD_L1'),('DRV_DELTA','DRV_DET_W')]:
        assert graph[k]=={k,dest}
        tail=clean(oldb[k]).strip()
        assert tail.endswith('dstate <= '+dest+';\n                end')
        assert not re.search(r'\b(if|case)\b',tail)
        graph[k].remove(k)
    seen=set();todo=deque([('DRV_SORT_A',0)])
    while todo:
        node,phase=todo.popleft()
        if (node,phase) in seen:continue
        seen.add((node,phase))
        if node=='DRV_DELTA':assert phase==1
        if node in ('DRV_PLANE_NUM','DRV_PLANE_ORG'):assert phase==2
        following=1 if node=='DRV_PROD_L0' else 2 if node=='DRV_DELTA' else phase
        for dst in graph[node]:
            if dst=='DRV_DONE':continue
            todo.append((dst,following))
    assert ('DRV_DELTA',1) in seen and ('DRV_PLANE_ORG',2) in seen
    # Source review confirms reset and soft_reset dominate case(state), set
    # S_IDLE, and leave DDS unreset; all reentries above restart SORT_A.
    assert 'if (!reset_n) begin\n        state <= S_IDLE;' in old
    assert 'if (soft_reset) begin\n            state        <= S_IDLE;' in old
    proof=(SOURCE.parent/'proof.sv').read_text()
    for label,bb in [('original',oldb),('candidate',newb)]:
        module=re.search(r'module '+label+r'\(.*?endmodule',proof,re.S)[0]
        for key in ['DRV_PROD_L0','DRV_DELTA','DRV_PLANE_NUM','DRV_PLANE_ORG']:
            actual=bb[key].rstrip()
            if key=='DRV_PLANE_NUM':actual=actual.replace('DRV_PLANE_NUM:',"6'd63, DRV_PLANE_NUM:")
            assert actual in module,key
    OUT.mkdir(exist_ok=False)
    with (OUT/'proof-rerun.log').open('w') as log:
        subprocess.run([str(SOURCE.parent/'obj/Vcoordinate_proof')],stdout=log,stderr=subprocess.STDOUT,check=True)
    report=dict(passed=True,reference_sha256=sha(BASE),candidate_sha256=sha(SOURCE),
        exact_complete_source_reconstruction=True,all_six_capture_to_low16_substitutions_exact=True,
        all_arithmetic_widths_signedness_and_post_read_delta_source_exact=True,
        all_original_dds_consumers_closed=True,coordinate_write_main_states=sorted(set(writes)),
        order_and_coordinates_stable_between_capture_and_delta=True,
        conservative_reachable_phase_states=len(seen),all_three_entries_restart_sort=True,
        reset_and_abort_invalidate_scratch_before_later_observation=True,
        actual_extracted_proof_blocks_verified=True,focused_binary_rerun_pass=True,
        universal_scope='Copied six arbitrary16-bit coordinates, low16 of sign-extension is identity; identical subsequent17-bit arithmetic. Supporting tests are not exhaustive96-bit data.',
        pre_delta_scratch_is_intentionally_different_and_unobserved=True,new_registers=0,new_cycles=0,
        whole_gpu_runtime_and_physical_result='Separate; not implied by this emitted/phase audit.')
    (OUT/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
