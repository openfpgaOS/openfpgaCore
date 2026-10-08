#!/usr/bin/env python3
"""Overlap an adjacent next LOAD with submission of the prepared prior batch."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP, INPUTS, sha, write

BASE = CAMP / 'phase10/lod5-recycle-driver-v1'
OUT = CAMP / 'phase10/load-submit-overlap-v1'


def rep(t, a, b):
    assert t.count(a) == 1, (a, t.count(a))
    return t.replace(a, b)


def main():
    OUT.mkdir(exist_ok=False)
    for n in INPUTS:
        (OUT / n).write_bytes((BASE / n).read_bytes())
    p = OUT / 'gfx_pc.c'
    original = t = p.read_text()
    marker = '/* Private LOD5. The fixed view occupies the existing reserved SDRAM guard.'
    helpers = '''/* One owned prepared batch may cross exactly one adjacent G_VTX.
 * It contains values, not pointers into the overwritten CPU vertex array.
 * No material, viewport, texture or draw command can intervene. The next
 * LOAD submits this batch after START, hiding submission behind transform;
 * every no-START path submits it before the CPU fallback loop. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define GFX_LOAD_SUBMIT_OVERLAP 1
static struct gfx_vc_prepared load_submit_payload[32];
static uint32_t load_submit_jobs[64];
static unsigned load_submit_count;
static uint8_t load_submit_alpha;
/* queued batches, submitted while LOAD runs, fallback submissions. */
uint32_t load_submit_stats[3];
static __attribute__((noinline)) void gfx_cpu_submit_pending(void) {
    unsigned count = load_submit_count;
    if (!count) return;
    load_submit_count = 0;
    gfx_gpu_vtx_cache_batch(load_submit_payload, load_submit_jobs,
                            count, load_submit_alpha);
}
#else
#define GFX_LOAD_SUBMIT_OVERLAP 0
#endif

'''
    t = rep(t, marker, helpers + marker)
    t = rep(t, '    load_driver_stats[1]++;load_driver_stats[11]+=writes;', '''    load_driver_stats[1]++;load_driver_stats[11]+=writes;
#if GFX_LOAD_SUBMIT_OVERLAP
    if (load_submit_count) {
        load_submit_stats[1]++;
        gfx_cpu_submit_pending();
    }
#endif''')
    t = rep(t, '''    if (n_vertices == 0)
        return;''', '''    if (n_vertices == 0) {
#if GFX_LOAD_SUBMIT_OVERLAP
        if (load_submit_count) {
            load_submit_stats[2]++;
            gfx_cpu_submit_pending();
        }
#endif
        return;
    }''')
    marker = '''#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP
    const int geometry_result='''
    t = rep(t, marker, '''#if GFX_LOAD_SUBMIT_OVERLAP
    if (load_submit_count) {
        load_submit_stats[2]++;
        gfx_cpu_submit_pending();
    }
#endif
''' + marker)
    t = rep(t, '''    struct gfx_vc_prepared payload[32];
    uint32_t jobs[64];''', '''#if GFX_LOAD_SUBMIT_OVERLAP
    struct gfx_vc_prepared *payload = load_submit_payload;
    uint32_t *jobs = load_submit_jobs;
#else
    struct gfx_vc_prepared payload[32];
    uint32_t jobs[64];
#endif''')
    t = rep(t, '''        gfx_gpu_vtx_cache_batch(payload, jobs, count, alpha);
        vc_slot_stale = stale;''', '''#if GFX_LOAD_SUBMIT_OVERLAP
        if (!fallback && (cmd->words.w0 >> 24) == (uint8_t)G_VTX) {
            load_submit_alpha = alpha;
            load_submit_count = count;
            load_submit_stats[0]++;
        } else
#endif
        {
            gfx_gpu_vtx_cache_batch(payload, jobs, count, alpha);
        }
        vc_slot_stale = stale;''')
    p.write_text(t)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), t.splitlines(True),
        fromfile='LOD5-recycler/gfx_pc.c', tofile='load-submit-overlap-v1/gfx_pc.c')))
    write(OUT / 'source.json', dict(
        parent_elf_sha256=sha(BASE / 'app.elf'),
        sources={n: sha(OUT / n) for n in INPUTS},
        helper_sha256=sha(Path(__file__)),
        abi='Unchanged LOD5; no hardware or source-pool changes.',
        ownership='Single static prepared batch; defer only across immediate G_VTX, '
                  'submit after START or before fallback. No payload copying.',
        qualified_for_deployment=False))
    print(OUT)


if __name__ == '__main__':
    main()
