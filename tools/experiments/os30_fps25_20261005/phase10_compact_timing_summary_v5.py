#!/usr/bin/env python3
"""Summarize frozen v5 STA paths and source-bound before/after attribution."""
import collections,csv,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'compact-timing-audit-v5'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def family(row):
    end=row['to_node']
    if '|VexiiRiscv:cpu|' in end:
        if 'execute_ctrl2_up_float_RS2' in end:return 'CPU_FP_RS2_forwarding'
        if 'execute_ctrl2_up_early0_SrcPlugin_SRC' in end:return 'CPU_integer_load_forwarding'
        return 'CPU_dispatch_hazard_fetch_backpressure'
    if 'deriv_a0_q' in end:return 'GPU_attribute_selection'
    if 'dsp2_a' in end:return 'GPU_shared_DSP_operand_control'
    if 'gpu_edge_walker' in end:return 'GPU_walker_scan_endpoint'
    if 'pss_zinv_clamp' in end:return 'GPU_reciprocal_advance_clamp'
    raise AssertionError(end)
def main():
    d=json.loads((O/'paths.json').read_text());old=json.loads((P/'compact-timing-audit-v3/paths.json').read_text())
    census=list(csv.DictReader((O/'reports/census.tsv').open(),delimiter='\t'))
    assert all(x['complete']=='1' and float(x['last_slack'])>=10-1000/90 for x in census)
    families={};comparisons={}
    for corner in ('slow_85','slow_0'):
        rows=d[corner+'_below90'];groups=collections.defaultdict(list)
        for row in rows:
            assert row['relationship_ns']==10 and row['period_bound_mhz']<90
            groups[family(row)].append(row)
        families[corner]={name:dict(endpoint_count=len(items),worst=min(items,key=lambda x:x['slack_ns']),endpoints=items) for name,items in groups.items()}
        assert len(rows)==int(next(x for x in census if corner=='slow_'+x['temperature'])['below_90'])
        comparisons[corner]={}
        for name in ('cpu','geometry','gpu_internal','render_cache','sdram'):
            before=min((x for x in old[corner+'_'+name] if x['relationship_ns']==10),key=lambda x:x['slack_ns'])
            after=min((x for x in d[corner+'_'+name] if x['relationship_ns']==10),key=lambda x:x['slack_ns'])
            comparisons[corner][name]=dict(before_exported_worst=before,after_exported_worst=after,scope='Module worst observed in each routed implementation; endpoints need not be identical.')
        comparisons[corner]['rvc_bits']={}
        for bit in (15,16,26,28):
            current=next(x for x in d[corner+'_rvc'] if x['to_node'].endswith(f'Decode_INSTRUCTION_0[{bit}]'))
            previous=[x for k,v in old.items() if k.startswith(corner) for x in v if x['to_node']==current['to_node']]
            comparisons[corner]['rvc_bits'][str(bit)]=dict(before=min(previous,key=lambda x:x['slack_ns']) if previous else None,after=current)
        comparisons[corner]['aligner_to_PC']=d[corner+'_aligner_pc'][0]
    (O/'families.json').write_text(json.dumps(families,indent=2)+'\n')
    (O/'comparisons.json').write_text(json.dumps(comparisons,indent=2)+'\n')
    result=dict(passed_readonly_export=True,timing_passed_90mhz=False,worst_mhz={k:d[k+'_global'][0]['period_bound_mhz'] for k in ('slow_85','slow_0')},census=census,family_counts={k:{f:v['endpoint_count'] for f,v in g.items()} for k,g in families.items()},fitter_invoked=False,frozen_inputs_modified=False)
    (O/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    (O/'REPORT.txt').write_text('''Compact-v5 read-only routed STA audit

Worst internal100MHz-domain period bound is81.900MHz at slow85 and84.239MHz at slow0 (slack−2.210/−1.871ns). The complete register-endpoint census finds73/18 endpoints below90MHz;1000 worst endpoints per corner were enough, because the final returned slack is−0.354/−0.212ns, above the−1.111111ns cutoff. Full path details for all below-threshold endpoints, module reports and targeted queries are retained. External SDRAM input paths have14ns relationships; they are reported separately and not converted into a100MHz-domain bound.

The corrected cones improved. All32 decoded-instruction destination bits now exceed90.975MHz slow85/92.584slow0; previously bit26 was84.947/85.551. The changed bits15/16/26/28 reach94.233/93.015/92.584/93.145MHz slow85. Aligner-buffer→PC worst now103.799/107.377MHz versus the previous PC-increment86.281MHz slow0 path. Render cache now94.029/93.371MHz; geometry94.162/93.528; internal SDRAM98.020/98.338. This is a before/after routed observation, not an isolated causal guarantee for each rewrite.

Remaining dominant family is CPU dispatch dependency checks feeding fetch backpressure. Worst path: execute-stage8 RD_PHYS[3]→RS3 match and stage eligibility→hazard reduction→candidate sending→dispatch373→fetch3_ready→PLRU read-enable. It has8logic levels,11.797ns data delay,7.943ns routing(67%), and the fetch3_ready node drives270 loads. Routing is dominant, but the path also retains real multi-stage combinational work; it is not solely one high-fanout wire. Required hazard checks were not removed or retimed.

Separate sub90 families are FP RS2 forwarding(priority/mux), integer load-result formatting/forwarding, GPU attribute selection, GPU shared-DSP operand control, GPU edge-walker endpoint arithmetic and reciprocal-advance clamp. Families.json lists each exact endpoint and worst path. GPU attribute selection is5levels/71%routing, shared-DSP control4levels/81%routing; reciprocal clamp remains8levels/52%routing with add/absolute-value/compare work. These are distinct from the repaired RVC/cache/SDRAM cones.

No fitter, source change, constraint change, seed change, timing exception or promotion. STA ran on a copied completed netlist; original QSF/SDC/db inputs were verified unchanged afterward. Functional/native/regeneration proofs remain valid, but this candidate does not meet the physical90MHz target.
''')
    inputs=[P/'compact-timing-audit-v3/manifest.json',P/'compact-lod5-core-v5/final-manifest.json']
    files={str(p.relative_to(O)):sha(p) for p in O.rglob('*') if p.is_file() and 'job' not in p.relative_to(O).parts and p.name!='manifest.json'}
    files['summary-recipe.py']=sha(Path(__file__))
    (O/'summary-recipe.py').write_bytes(Path(__file__).read_bytes())
    (O/'manifest.json').write_text(json.dumps(dict(files=files,parent_inputs={str(p.relative_to(ROOT)):sha(p) for p in inputs}),indent=2)+'\n')
    print(json.dumps(result,indent=2));print('manifest',sha(O/'manifest.json'))
if __name__=='__main__':main()
