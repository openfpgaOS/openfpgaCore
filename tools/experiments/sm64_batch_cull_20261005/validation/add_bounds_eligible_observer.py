#!/usr/bin/env python3
"""Observe v3 bounds call arguments/cache words without guest/model mutation."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
q=OUT/'qsim-bounds-eligible-observed';q.mkdir(exist_ok=False)
parent=OUT/'qsim-observed'
for p in parent.iterdir():
 if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
def change(name,a,b):
 p=q/name;s=p.read_text();assert s.count(a)==1,(name,a);p.write_text(s.replace(a,b,1))
change('cpu.h','    /* architectural state */','''    /* Host-only observation; never read by guest execution/timing. */
    uint32_t observe_pc;
    void (*observe)(struct qsim_cpu *);
    void *observe_context;
    /* architectural state */''')
change('cpu_exec.h','#define INSN() do { \\\n        size_t _i', '#define INSN() do { \\\n        if (unlikely(e->pc == cpu->observe_pc && cpu->observe != NULL)) cpu->observe(cpu); \\\n        size_t _i')
change('main.c','#include "lint.h"','''#include "lint.h"

typedef struct {
    FILE *out;
    const size_t *frame;
    uint32_t start,end,cache;
} bounds_observer;
static void observe_bounds(qsim_cpu *cpu) {
    bounds_observer *o=cpu->observe_context;
    uint32_t address=cpu->x[9],count=cpu->x[11],first=cpu->x[8];
    unsigned immutable=address>=o->start && address<o->end && count<=(o->end-address)/24u;
    unsigned hash=(((address>>3)^(count*40503u))*2654435761u)>>22;
    uint32_t cached_source;
    uint16_t cached_count,retry;
    const unsigned char *p=qsim_mem_ptr(cpu,o->cache+32u*hash,32);
    if(!p)abort();
    memcpy(&cached_source,p,4);memcpy(&cached_count,p+4,2);memcpy(&retry,p+6,2);
    unsigned hit=immutable && cached_source==address && cached_count==count;
    fprintf(o->out,"%zu,%08x,%u,%u,%u,%u,%u,%u,%u,%08x,%u\\n",
        *o->frame,address,count,first,immutable,hash,hit,retry,hit&&retry!=0,cached_source,cached_count);
}
''')
change('main.c','    size_t cap_rows = 4096, nrows = 0;','''    size_t cap_rows = 4096, nrows = 0;
    const qsim_sym *bounds_fn=qsim_syms_lookup(&E.syms,"geometry_prepare_software");
    const qsim_sym *bounds_cache=qsim_syms_lookup(&E.syms,"geometry_software_bounds_cache");
    const qsim_sym *bounds_start=qsim_syms_lookup(&E.syms,"_start");
    const qsim_sym *bounds_end=qsim_syms_lookup(&E.syms,"__EH_FRAME_BEGIN__");
    if(!bounds_fn || !bounds_cache || !bounds_start || !bounds_end)abort();
    snprintf(path,sizeof path,"%s/bounds-calls.csv",out);
    bounds_observer bounds={fopen(path,"w"),&nrows,bounds_start->addr,bounds_end->addr,bounds_cache->addr};
    if(!bounds.out)abort();
    fprintf(bounds.out,"frame,address,count,first,immutable,cache_index,expected_hit,retry,expected_skip,cached_source,cached_count\\n");
    if(bounds_fn->addr!=0x104cf140u || bounds_fn->size!=776u)abort();
    E.cpu->observe_pc=bounds_fn->addr+0x10au;E.cpu->observe=observe_bounds;E.cpu->observe_context=&bounds;
    snprintf(path,sizeof path,"%s/bounds-symbols.json",out);
    FILE *bounds_symbols=fopen(path,"w");if(!bounds_symbols)abort();
    fprintf(bounds_symbols,"{\\"entry\\":%u,\\"cache\\":%u,\\"cache_size\\":%u,\\"immutable_start\\":%u,\\"immutable_end\\":%u}\\n",
        bounds_fn->addr,bounds_cache->addr,bounds_cache->size,bounds_start->addr,bounds_end->addr);
    fclose(bounds_symbols);
    if(bounds_cache->size!=32768)abort();''')
change('main.c','    if(culling_csv)fclose(culling_csv);','    if(culling_csv)fclose(culling_csv);\n    fclose(bounds.out);')
changed=[]
for p in q.iterdir():
 if p.is_file() and sha(p)!=sha(parent/p.name):changed.append(p.name)
assert sorted(changed)==['cpu.h','cpu_exec.h','main.c']
with (OUT/'qsim-bounds-eligible-observed-build.log').open('w') as f:subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
(OUT/'qsim-bounds-eligible-observed-manifest.json').write_text(json.dumps(dict(exe_sha256=sha(q/'qsim'),parent_manifest_sha256=sha(OUT/'qsim-observed-manifest.json'),script_sha256=sha(Path(__file__)),changed=changed,sources={p.name:sha(p) for p in q.iterdir() if p.is_file()},scope='At exact v3 post-eligibility PC104cf24a, read s1=source,a1=count,s0=first after actual level/count/mode/perspective gates. Exact24-byte Vtx range and32-byte cache header. Host-only observer, no guest/model state changes.'),indent=2)+'\n')
print(q/'qsim')
