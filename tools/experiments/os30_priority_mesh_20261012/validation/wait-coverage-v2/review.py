#!/usr/bin/env python3
"""Independent, source-bound AJ02 wait-path audit; no timing claim."""
import hashlib,json,re
from pathlib import Path
O=Path(__file__).resolve().parent;B=O.parent.parent;W=B/'work-app-v2'
G=W/'src/sm64/sm64/src/pc/gfx';gpu=G/'gfx_gpu.c';pc=G/'gfx_pc.c';sdk=W/'src/sdk/include/of_gpu.h'
adapter=B/'work-core-v2/frozen/src/fpga/common/psram32_axi_adapter.sv'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
S={p:p.read_text() for p in (gpu,pc,sdk,adapter)}
def fn(p,name):
 s=S[p];m=re.search(r'^[^\n;]*\b'+name+r'\([^;]*?\)\s*\{',s,re.M);assert m,name
 i=s.index('{',m.start())+1;depth=1
 while depth:depth+=(s[i]=='{')-(s[i]=='}');i+=1
 return s[m.start():i]+'\n'
def loc(p,text):return {'path':str(p),'line':S[p][:S[p].index(text)].count('\n')+1}
receipt=W/'evidence/priority-tests-v1/result.json';test=json.loads(receipt.read_text());assert test['status']=='PASS'
for p,h in test['inputs_sha256'].items():assert sha(p)==h,p
for name,h in test['extracted_functions_sha256'].items():
 p=pc if name.startswith('load_alias_') or name=='gfx_async_materialize_raw' else gpu
 assert hashlib.sha256(fn(p,name).encode()).hexdigest()==h,name
paths=[]
for name in ('_gpu_wait_dma_idle_debug','_gpu_wait_dma_desc_slot_debug','_gpu_wait_transluc_idle','_gpu_ring_ensure','of_gpu_try_reserve_bytes','of_gpu_wait'):
 body=fn(sdk,name);assert 'OF_GPU_WAIT_HOOK();'in body,name
 paths.append({'function':name,**loc(sdk,body),'progress_hook':'OF_GPU_WAIT_HOOK -> gfx_gpu_wait_service'})
assert '#define OF_GPU_WAIT_HOOK() gfx_gpu_wait_service()'in S[gpu]
for name in ('gfx_gpu_wait_service','gpu_wait_flip_fence'):
 body=fn(gpu,name)
 assert ('gfx_gpu_async_before_wait();' if name=='gfx_gpu_wait_service' else 'gfx_gpu_wait_service();')in body
 paths.append({'function':name,**loc(gpu,body),'progress_hook':'yield canonical rows/release if unfinished queued jobs exist, then service repair'})
drain=fn(gpu,'gfx_gpu_async_drain');assert drain.index('gfx_gpu_async_before_wait();')<drain.index('for(;;)')
paths.append({'function':'gfx_gpu_async_drain',**loc(gpu,drain),'progress_hook':'yield before true completed-sequence source lifetime wait'})
body=fn(pc,'load_alias_vertex_legacy')
assert body.index('!gfx_gpu_cram_vertex_source(')<body.index('gfx_cpu_submit_pending();')<body.index('gfx_gpu_async_raw_acquire()')<body.index('load_alias_transaction=1;')
assert body.index('load_lazy_slots = loaded;')<body.index('load_alias_transaction=0;')
assert 'gfx_gpu_wait_service'not in body
assert 'if(!priority)'in body and 'if(!priority&&(LOAD_ALIAS_REGS->status&1u))'in body
callback=fn(pc,'gfx_async_materialize_raw')
assert 'if(load_alias_transaction)__builtin_trap();'in callback
assert 'gfx_gpu_async_service();'in callback and 'load_alias_materialize_all();'in callback
assert not any(x in callback for x in ('_gpu_ring','of_gpu_','gfx_cpu_submit','malloc('))
for needle in ('requests={upload_pending,tex_arvalid,s_axi_awvalid && !cpu_reset,s_axi_arvalid && !cpu_reset}',
 'assign upload_busy=upload_collecting || upload_pending || (state!=IDLE && owner==UPLOAD)',
 'assign w_valid=state==TRANSFER && cmd_write && (owner==UPLOAD || abort_cpu || s_axi_wvalid)',
 'assign w_data=owner==UPLOAD ? upload_buffer[transferred[4:0]] : s_axi_wdata'):
 assert needle in S[adapter],needle
source_receipt=W/'evidence/source-cache-tests-v1/result.json';source_tests=json.loads(source_receipt.read_text());assert source_tests['status']=='PASS'
for p,h in source_tests['inputs_sha256'].items():assert sha(p)==h,p
assert 'async_legacy_held||async_legacy_quiescent' in fn(gpu,'gpu_async_raw_blocks_jobs')
assert 'async_legacy_held=1;async_legacy_quiescent=0;' in fn(gpu,'gfx_gpu_async_raw_acquire')
assert 'async_legacy_held=0;async_legacy_quiescent=0;' in fn(gpu,'gfx_gpu_async_raw_release')
assert 'gfx_async_forget_sources();' in S[gpu]
result={
 'status':'PASS_SOURCE_BOUND_INDEPENDENT_WAIT_REVIEW',
 'scope':'AJ02 priority lease software liveness; requires the separately tested RTL forward-progress and shared-context contract. No elapsed-cycle or hardware qualification claim.',
 'covered_progress_waits':paths,
 'independent_progress_exceptions':[
  {'site':loc(sdk,'while (_GPU_FAST_TEX_UP_DATA & 1u)'), 'reason':'PSRAM fast-upload status waits do not service the mesh hook. A complete upload batch is already buffered; adapter TRANSFER supplies data without CPU or mesh progress. Round-robin arbitration serves pending uploads. Renderer texture reuse first uses the hooked GPU finish. No queued-job or legacy-lease dependency occurs in this bus-only completion wait.', 'proof_source':str(adapter)},
  {'site':loc(pc,'unsigned remaining=4096,status;'),'reason':'Accepted legacy raw LOAD polling remains repair-only; its reserved engine/PSRAM transaction progresses without an async job. Generic yield inside context-write/START/result-registration is forbidden by load_alias_transaction. Source upload and prior CPU submission happen before acquire.'},
  {'site':loc(gpu,'void gfx_gpu_mesh_wait_source(void)'),'reason':'Inherited MTF2, QGF1 and BIM2 blocking helpers are capability-inactive on AJ02; the priority legacy branch explicitly skips prior QGF/BIM waits.'}
 ],
 'host_overhead_review':[
  'A retained lease caches quiescence only after checking real completed==submitted. Every new async submit releases first; fresh grants/releases clear the memo. Consumer reset preserves matching legacy raw ownership and cancels queued jobs, so it cannot introduce a blocked dependency.',
  'Parallel task source offsets bind the exact immutable topology slot, vertex pointer, count/command shape and actual pool generation. Every real recycle clears the table, including generation wrap, and slot initialization invalidates the corresponding key.',
  'Hint/dead-suffix cache rejection changes routing only. Exact transform/material acceptance and repair remain authoritative. Full context writes initialize every field and retain one42-word reservation/commit.'
 ],
 'reviewed_source_cache_tests':{'path':str(source_receipt),'sha256':sha(source_receipt),'checks':source_tests['checks'],'inputs_independently_rehashed':True},
 'ordering_findings':[
  'Epoch-checked completed_sequence==submitted_sequence permits retaining an idle legacy lease: ordinary commands still forward and no unfinished async job can be blocked.',
  'Otherwise a progress wait materializes canonical rows without emitting GPU commands, then releases the lease before repair/drain polling. The recursion guard prevents duplicate copy/release during service re-entry.',
  'First ordinary canonical materialization releases immediately when all lazy slots are copied and unfinished jobs exist; survivor materialization preserves unrelated canonical/fog rows.',
  'Source pool recycle waits the real completed sequence; an early priority grant is never treated as source completion.',
  'One 42-word reservation/commit contains full CONTEXT28 then JOB11. No wait can split the context ownership bundle; legacy context is refreshed after fresh acquisition.',
  'Reset retains only a matching granted ticket/raw bank, invalidates async sequence epoch, and traps on a contradictory legacy ownership record.'
 ],
 'reviewed_actual_C_tests':{'path':str(receipt),'sha256':sha(receipt),'checks':test['checks'],'inputs_and_extracted_bodies_independently_rehashed':True,'note':'Existing frozen actual-C ASan/UBSan suite covers masks, partial overwrites, delayed completion, recursion, ownership/reset, priority grant before completion and atomic context packets; not rerun or represented as RTL timing.'},
 'inputs_sha256':{str(p):sha(p)for p in (gpu,pc,sdk,adapter,receipt,source_receipt,Path(__file__).resolve())}}
report=O/'result.json';assert not report.exists();report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':result['status'],'covered_wait_functions':len(paths),'reviewed_actual_C_checks':test['checks']},indent=2))
