#!/usr/bin/env python3
"""Seal scoped write-speculation proof and routed-path diagnosis."""
from pathlib import Path
import hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'cache-write-speculate-v1';OUT=P/'cache-write-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 old=(C/'baseline.sv').read_text();new=(C/'gpu_color_depth_cache.sv').read_text()
 # Independently undo the three local changes: every other byte must match.
 restored=new.replace('''parameter REGISTER_FILL_HAZARD = 0,
    // Speculate on hot0's data slot before AW is registered. A hot1-only
    // hit re-reads through existing W_WAIT; no live AW tag selects data RAM.
    // With unstaged reads, AR retains its original immediate hot-slot path.
    parameter SPECULATE_WRITE_HOT0 = 0''','parameter REGISTER_FILL_HAZARD = 0')
 restored=restored.replace('''IDLE: data_ra={(SPECULATE_WRITE_HOT0 && (REGISTER_READ_LOOKUP || s_awvalid))
                       ? hot0_slot : hot_slot,lookup_addr[2+:WORD_BITS]};''',
                          'IDLE: data_ra={hot_slot,lookup_addr[2+:WORD_BITS]};')
 restored=restored.replace('''slot<=hot_slot; hits<=hits+1;
                            state<=(SPECULATE_WRITE_HOT0 && !hot0_hit) ? W_WAIT : W_GET;''',
                          'slot<=hot_slot; hits<=hits+1; state<=W_GET;')
 assert restored==old
 result=json.loads((C/'protocol/result.json').read_text());assert result['passed']and result['default_cycle_identical']
 assert result['runs']['baseline']==result['runs']['default']
 totals={k:sum(x['coverage'][k]for mode in('enabled','ranged')for x in result['runs'][mode])
         for k in('hot0','hot1','wait','wait_w','reset_wait')}
 assert all(totals.values())
 coupled=P/'cache-write-lod5-coupled-v1'
 d=json.loads((coupled/'manifest.json').read_text());assert d['passed']
 for n,h in d['files'].items():assert sha(coupled/n)==h,n
 for n,h in d['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 timing=json.loads((coupled/'result.json').read_text());assert timing['passed']
 paths=json.loads((P/'compact-timing-audit-v3/paths.json').read_text())
 path=next(p for p in paths['slow_85_render_cache']if p['from_node'].endswith('m_wr_awaddr[8]'))
 OUT.mkdir(exist_ok=False);shutil.copyfile(__file__,OUT/'recipe.py')
 evidence=[C/'prepared.json',C/'gpu_color_depth_cache.sv',C/'baseline.sv',C/'candidate.patch',
           C/'protocol/result.json',coupled/'manifest.json',P/'compact-timing-audit-v3/manifest.json',
           ROOT/'tools/experiments/os30_fps25_20261005/phase10_cache_write_speculate.py']
 data=dict(passed=True,deployment_ready=False,source_sha256=sha(C/'gpu_color_depth_cache.sv'),
           target_routed_path=path,new_state_bits=0,new_data_register_bits=0,
           source_proof={
            'default_parameter':'All old state transitions and data addresses are identical when disabled.',
            'hot0_write':'IDLE speculative address equals original hot_slot when hot0_hit, so W_GET sees the same RAM word on the same edge.',
            'hot1_write':'IDLE captures accepted addr/slot and selects W_WAIT. W_WAIT reads registered slot/word_index, refuses all new input ownership, and the next W_GET consumes the correct synchronous data.',
            'cold_bypass':'Original transitions retained; IDLE speculative data is not consumed by LOOK or bypass. Metadata lookup still uses accepted AW.',
            'reads':'Selected REGISTER_READ_LOOKUP=1 already ignores IDLE data and reads at R_ACCEPT; the guarded unstaged-read branch retains original hot_slot.',
            'reset':'Existing reset clears FSM ownership; no new pending state or register exists. W_WAIT cancellation and restart are exercised.',
            'timing':'With both selected parameters1, IDLE data slot depends only on hot0_slot; AW line/tag comparison no longer selects the M10K address. Other state/slot paths and physical timing remain unqualified.'},
           protocol_totals=totals,coupled_results=timing['results'],
           limits=['No FPGA map/fit was run by this proof. About0.200ms extra modeled frame time buys a shorter proposed input path, not an established higher Fmax.',
                   'Selected CACHE_ALL1/2way/8KiB configuration is tested, plus ranged-cache compatibility. CPU timing in coupled runs is modeled.'],
           evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in evidence})
 (OUT/'review.json').write_text(json.dumps(data,indent=2)+'\n')
 (C/'REPORT.txt').write_text('Hot0 write speculation: selected AW critical-path remedy.\n\n'
  'No new registers or states. Hot0 hits keep original timing; hot1-only writes take one existing W_WAIT. '
  'All other traffic retains the original schedule. Default0 exactly reproduces baseline component cycles. '
  'Twelve hostile-memory protocol runs pass, with poisoned mixed-port RAM collisions, stale AW/AR addresses, '
  'first W concurrent with AW, flush/AR contention, posted-write hazards, and W_WAIT reset/recovery.\n\n'
  'Matched recycler actual-RTL windows preserve all16 color/depth buffers and complete histories. '
  'Warm frame cost +0.19994125ms; cold +0.20088625ms. CPU timing remains modeled. No fitted timing claim.\n')
 files={str(p.relative_to(C)):sha(p)for p in C.rglob('*')if p.is_file()and'obj'not in p.parts}
 for p in(C/'protocol').glob('*/obj/Vgpu_color_depth_cache'):files[str(p.relative_to(C))]=sha(p)
 (C/'manifest.json').write_text(json.dumps(dict(passed=True,deployment_ready=False,files=files),indent=2)+'\n')
 (OUT/'manifest.json').write_text(json.dumps(dict(files={p.name:sha(p)for p in OUT.iterdir()if p.is_file()},
  component_manifest_sha256=sha(C/'manifest.json')),indent=2)+'\n')
 print('component',sha(C/'manifest.json'));print('review',sha(OUT/'manifest.json'))
if __name__=='__main__':main()
