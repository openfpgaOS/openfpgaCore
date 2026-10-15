#!/usr/bin/env python3
"""Build the bounded producer against owned actual frame task bytes."""
from pathlib import Path
import hashlib,json,os,subprocess
H=Path(__file__).resolve().parent;vectors=H.parent/'task-vectors-frame360.json'
v=json.loads(vectors.read_text());assert v['format']=='mesh-input-v2' and v['vertex_stride_bytes']==24
entries=v['vectors'];offsets={};top=0xfc0000
body='#include <vector>\n#include <cstdint>\nstruct ActualVector {uint32_t address,geometry,psram;std::vector<uint32_t> commands;std::vector<uint8_t> vertices;};\nstatic std::vector<ActualVector> actual_vectors(){return {\n'
for e in entries:
    key=(e['source_address'],e['count'])
    if key not in offsets:offsets[key]=top;top+=(e['count']*24+63)&~63
    assert top<=0x1000000
    raw=bytes.fromhex(e['source_vertex_bytes_hex']);assert len(raw)==e['count']*24
    body+='{'+f"{e['span_start']}u,{e['geometry_mode']}u,{offsets[key]}u,"+'{'+','.join(str(w)+'u' for w in e['commands_u32'])+'},{'+','.join(str(b) for b in raw)+'}},\n'
body+='};}\n';header=H/'vectors.hpp';header.write_text(body)
exe=H/'test_producer';cmd=['g++','-std=c++17','-O2','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(H),str(H/'test_producer.cpp'),'-o',str(exe)]
r=subprocess.run(cmd,capture_output=True,text=True);(H/'build.log').write_text(r.stdout+r.stderr);r.check_returncode()
run_env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0');r=subprocess.run([str(exe)],capture_output=True,text=True,env=run_env);(H/'run.log').write_text(r.stdout+r.stderr);r.check_returncode();result=json.loads(r.stdout)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
result.update(compile_command=cmd,sanitizer_environment={"ASAN_OPTIONS":"detect_leaks=0","reason":"LeakSanitizer cannot run under this tool environment ptrace; address and undefined behavior checks remain enabled."},physical_source_bindings={'kind':'Deterministic mechanism-test allocation in the existing256KiB arena; actual hardware offsets join from passive trace separately.','unique_sources':len(offsets),'allocated_bytes':top-0xfc0000},inputs_sha256={str(p):sha(p) for p in [Path(__file__).resolve(),vectors,H/'task_producer.hpp',H/'test_producer.cpp',header,exe]},limitations=['Not linked into SM64; no runtime or performance claim. Host std::map/shared_ptr costs are not RV32 cycle estimates.',
 'Physical source addresses in this producer are PSRAM offsets0..0x01000000. The scene RTL harness source window may be CPU-address based; its adapter/parameter must explicitly translate the source override.',
 'Source-range overlap checks model an arena lease. Detached std::vector bytes do not prove hardware DMA ownership: the real uploader must honor the lease before writing and must drain all worker/source/repair reads before recycle.','Three repetitions exercise immutable cache hits, not actual later frames or an assertion that dynamic instance state repeats.','Task source bytes are actual. Context words and physical offset allocation are mechanism-test inputs until joined to passive trace.','Real admission still needs exact material/fallback handling, source-pool worker fence, cache maintenance and an ordered CPU/GPU publication adapter.'])
(H/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
