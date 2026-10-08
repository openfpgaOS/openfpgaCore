#!/usr/bin/env python3
"""Keep adjacent LOAD/submission overlap inside the original indexed stack frame."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP,INPUTS,sha,write
P=CAMP/'phase10';BASE=P/'load-submit-overlap-v1';OUT=P/'load-submit-stack-v2'
def rep(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def main():
 OUT.mkdir(exist_ok=False)
 for n in INPUTS:(OUT/n).write_bytes((BASE/n).read_bytes())
 p=OUT/'gfx_pc.c';old=t=p.read_text()
 t=rep(t,'''/* One owned prepared batch may cross exactly one adjacent G_VTX.
 * It contains values, not pointers into the overwritten CPU vertex array.
 * No material, viewport, texture or draw command can intervene. The next
 * LOAD submits this batch after START, hiding submission behind transform;
 * every no-START path submits it before the CPU fallback loop. */''','''/* One prepared batch stays in its indexed caller's stack frame while that
 * caller executes exactly one adjacent G_VTX synchronously. The next LOAD
 * submits after START, or every no-START path submits before CPU fallback.
 * Backend submission copies words into its own SDK staging; no stack pointer
 * survives this synchronous call or escapes through renderer state. */''')
 t=rep(t,'static struct gfx_vc_prepared load_submit_payload[32];\nstatic uint32_t load_submit_jobs[64];','static const struct gfx_vc_prepared *load_submit_payload;\nstatic const uint32_t *load_submit_jobs;')
 t=rep(t,'''    gfx_gpu_vtx_cache_batch(load_submit_payload, load_submit_jobs,
                            count, load_submit_alpha);
}''','''    gfx_gpu_vtx_cache_batch(load_submit_payload, load_submit_jobs,
                            count, load_submit_alpha);
    load_submit_payload = NULL;
    load_submit_jobs = NULL;
}''')
 t=rep(t,'''#if GFX_LOAD_SUBMIT_OVERLAP
    struct gfx_vc_prepared *payload = load_submit_payload;
    uint32_t *jobs = load_submit_jobs;
#else
    struct gfx_vc_prepared payload[32];
    uint32_t jobs[64];
#endif''','''    struct gfx_vc_prepared payload[32];
    uint32_t jobs[64];''')
 t=rep(t,'''        if (!fallback && (cmd->words.w0 >> 24) == (uint8_t)G_VTX) {
            load_submit_alpha = alpha;
            load_submit_count = count;
            load_submit_stats[0]++;
        } else
#endif
        {
            gfx_gpu_vtx_cache_batch(payload, jobs, count, alpha);
        }
        vc_slot_stale = stale;''','''        if (!fallback && (cmd->words.w0 >> 24) == (uint8_t)G_VTX) {
            load_submit_payload = payload;
            load_submit_jobs = jobs;
            load_submit_alpha = alpha;
            load_submit_count = count;
            load_submit_stats[0]++;
            /* Record this draw first: G_VTX then marks its overwritten slots
             * stale. Do not restore this old mask after the nested load. */
            vc_slot_stale = stale;
            const uint32_t word = cmd->words.w0;
#ifdef F3DEX_GBI_2
            const unsigned vertices = (word >> 12) & 0xffu;
            gfx_sp_vertex(vertices, ((word >> 1) & 0x7fu) - vertices,
                          seg_addr(cmd->words.w1));
#elif defined(F3DEX_GBI) || defined(F3DLP_GBI)
            gfx_sp_vertex((word >> 10) & 0x3fu, ((word >> 16) & 0xffu) / 2,
                          seg_addr(cmd->words.w1));
#else
            gfx_sp_vertex((word & 0xffffu) / sizeof(Vtx), (word >> 16) & 0xfu,
                          seg_addr(cmd->words.w1));
#endif
            assert(load_submit_count == 0);
            ++cmd;
        } else
#endif
        {
            gfx_gpu_vtx_cache_batch(payload, jobs, count, alpha);
            vc_slot_stale = stale;
        }''')
 p.write_text(t)
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),t.splitlines(True),fromfile='load-submit-overlap-v1/gfx_pc.c',tofile='load-submit-stack-v2/gfx_pc.c')))
 write(OUT/'source.json',dict(parent_elf_sha256=sha(BASE/'app.elf'),sources={n:sha(OUT/n)for n in INPUTS},helper_sha256=sha(Path(__file__)),scope='Synchronous adjacent G_VTX inside the indexed caller; original local arrays, no payload copy or escaping stack pointers. LOD5 ABI unchanged.',profile_limit='PROFILE=0 selected; a PROFILE=1 indexed scope now encloses the nested vertex call and is not directly comparable to old per-function buckets.',deployment_ready=False))
 print(OUT)
if __name__=='__main__':main()
