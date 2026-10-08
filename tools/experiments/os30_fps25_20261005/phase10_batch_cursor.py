#!/usr/bin/env python3
"""F11 backend-only local staging cursor; producer and packet order unchanged."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP,INPUTS,sha,write
BASE=CAMP/'phase9/checks-state-f11';OUT=CAMP/'phase10/batch-cursor-v1'
def rep(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def main():
 OUT.mkdir(exist_ok=False)
 for n in INPUTS:(OUT/n).write_bytes((BASE/n).read_bytes())
 p=OUT/'gfx_gpu.c';old=p.read_text();a=old.index('void gfx_gpu_vtx_cache_batch(');z=old.index('\n/* Raw geometry path.',a);part=old[a:z]
 part=rep(part,'    /* No state mutation occurs inside this bounded CPU-only run. */','''#ifndef OF_PC
    /* A private cursor owns an already-free staging/ring region. No SDK call
     * may see uncommitted words: settle before capacity reserve, kick or exit.
     * The GPU only consumes ring space, so the initial known-free bound remains
     * conservative while the CPU fills this region. */
    uint32_t *packet=NULL,*first=NULL,*limit=NULL;
#endif
    /* No state mutation occurs inside this bounded CPU-only run. */''')
 first=part.index('    /* One reservation preserves');end=part.index('#endif',first)
 part=part[:first]+'''    /* Every triangle emits at most three9-word loads plus a2-word draw.
     * Reserving this fixed upperbound avoids per-triangle popcount and global
     * commit bookkeeping. Near a boundary, at most28 words go unused. */
    if(!packet || limit-packet<29) {
        if(packet)_gpu_ring_commit((uint32_t)(packet-first));
        _gpu_stream_reserve_words(29);
        first=packet=_gpu_ring_claim();
        unsigned available=OF_GPU_COMMAND_STREAM_BATCH_WORDS-_gpu_cmd_words;
        const unsigned ring_available=_gpu_ring_free_known()/4u;
        if(available>ring_available)available=ring_available;
        limit=packet+available;
    }
''' +part[end:]
 part=rep(part,'    _gpu_ring_commit(packet_words);','    /* The local cursor is committed at an SDK boundary below. */')
 part=rep(part,'        g_vc_kick = 0;\n        of_gpu_kick();','''        g_vc_kick = 0;
#ifndef OF_PC
        _gpu_ring_commit((uint32_t)(packet-first));packet=NULL;
#endif
        of_gpu_kick();''')
 assert part.endswith('    }\n}\n')
 part=part[:-2]+'''#ifndef OF_PC
    if(packet)_gpu_ring_commit((uint32_t)(packet-first));
#endif
}
'''
 t=old[:a]+part+old[z:];p.write_text(t)
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),t.splitlines(True),fromfile='F11/gfx_gpu.c',tofile='batch-cursor-v1/gfx_gpu.c')))
 write(OUT/'source.json',dict(parent_elf_sha256=sha(BASE/'app.elf'),source_sha256={n:sha(OUT/n)for n in INPUTS},helper_sha256=sha(Path(__file__)),producer_unchanged=True,scope='Backend localcursor bounded by staging AND ringfree; complete commit before SDK reserve/kick/return; exact packet order, original32triangle kick cadence; conservative capacity publication maymove earlier by<=28words.',qualified_for_deployment=False));print(OUT)
if __name__=='__main__':main()
