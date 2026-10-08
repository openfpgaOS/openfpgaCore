#!/usr/bin/env python3
"""Prepare a private Bob-omb Battlefield capture from the unmodified SM64 ELF.

One-shot debugger hooks select act 1 and its normal entrance warp. Gameplay
thereafter executes the original binary. CPU cycles are approximate qsim
estimates; rendering correctness and GPU timings come from separate RTL replays.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-bob-20261002'
PREVIOUS = ROOT / 'docs/measurements/os30-depth-prefetch-20261002'
OUT.mkdir(parents=True, exist_ok=True)
q = OUT / 'qsim'
q.mkdir(exist_ok=True)
for f in (PREVIOUS / 'qsim').iterdir():
    if f.suffix in ('.c', '.h') or f.name == 'Makefile':
        shutil.copy2(f, q / f.name)

def once(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new, 1)

s = (q / 'env.c').read_text()
s = once(s, 'static int on_hook(void *ctx, qsim_cpu *cpu, uint32_t pc)', r'''
/* One-shot debugger hook: remove itself and resume the original instruction. */
static void bob_unhook(qsim_cpu *cpu, uint32_t pc)
{
    for (size_t i = 0; i < cpu->nhooks; i++) if (cpu->hooks[i] == pc) {
        memmove(cpu->hooks + i, cpu->hooks + i + 1,
                (cpu->nhooks - i - 1) * sizeof *cpu->hooks);
        cpu->nhooks--;
        qsim_invalidate(cpu, pc, 4);
        cpu->sd_dc[(pc - cpu->sdram_base) >> 1].h = 0;
        return;
    }
    abort();
}
static void bob_set(qenv *E, const char *name, const void *value, unsigned size)
{
    const qsim_sym *sym = qsim_syms_lookup(&E->syms, name);
    if (!sym || !gp(E, sym->addr, size)) abort();
    memcpy(gp(E, sym->addr, size), value, size);
    env_wrote(E, sym->addr, size);
}
static int on_hook(void *ctx, qsim_cpu *cpu, uint32_t pc)''')
s = once(s, '    if (i < 0 || i >= ENV_NSVC) { fatal(E, "hook at %08x", pc); return QSIM_TRAP_STOP; }', r'''
    if (i < 0 || i >= ENV_NSVC) {
        const qsim_sym *start = qsim_syms_lookup(&E->syms, "lvl_init_from_save_file");
        const qsim_sym *init = qsim_syms_lookup(&E->syms, "init_level");
        if (start && pc == start->addr) {
            uint8_t debug = 1;
            int16_t act = 1;
            cpu->x[11] = 9; /* LEVEL_BOB; normal level initialization follows. */
            bob_set(E, "gDebugLevelSelect", &debug, sizeof debug);
            bob_set(E, "gCurrActNum", &act, sizeof act);
            fprintf(E->log, "bob: select level 9 act 1 at flip %llu\n", (unsigned long long)E->flips);
            bob_unhook(cpu, pc);
            return QSIM_TRAP_RESUME;
        }
        if (init && pc == init->addr) {
            uint8_t debug = 0;
            uint8_t warp[8] = {1, 9, 1, 0x0a, 0, 0, 0, 0};
            const qsim_sym *mario = qsim_syms_lookup(&E->syms, "gMarioState");
            uint32_t mp = 0;
            memcpy(&mp, gp(E, mario->addr, 4), 4);
            /* A normal painting entry follows an already initialized Mario.
             * Supply its nonzero action so the normal warp loads Mario's area. */
            wr32(E, mp + 0x0c, 0x0c400201u); /* ACT_IDLE */
            bob_set(E, "gDebugLevelSelect", &debug, sizeof debug);
            bob_set(E, "sWarpDest", warp, sizeof warp);
            fprintf(E->log, "bob: normal entrance warp at flip %llu\n", (unsigned long long)E->flips);
            bob_unhook(cpu, pc);
            return QSIM_TRAP_RESUME;
        }
        fatal(E, "unexpected hook at %08x", pc);
        return QSIM_TRAP_STOP;
    }''')
s = once(s, '    /* Data slots (ENV_SPEC 6.1)', r'''
    const char *bob_hooks[] = {"lvl_init_from_save_file", "init_level"};
    for (unsigned bi = 0; bi < 2; bi++) {
        const qsim_sym *sym = qsim_syms_lookup(&E->syms, bob_hooks[bi]);
        if (!sym || qsim_add_hook(cpu, sym->addr)) abort();
    }
    /* Data slots (ENV_SPEC 6.1)''')
# Start, select file, dismiss the entrance dialog; then a reproducible walk.
s = once(s, '''        if (getenv("SM64_INTRO") && a0 == 0)
            wr32(E, a1, (E->flips >= 120 && E->flips < 122) ? (1u << 15)
                : (E->flips >= 200 && E->flips < 202) ? (1u << 6) : 0);''', r'''
        if (a0 == 0) {
            uint32_t buttons = 0;
            if (E->flips >= 120 && E->flips < 122) buttons = 1u << 15;
            if (E->flips >= 200 && E->flips < 202) buttons = 1u << 6;
            if (E->flips >= 270 && E->flips < 350 && E->flips % 20 < 2) buttons = 1u << 6;
            if (E->flips >= 420 && E->flips < 600) buttons = (1u << 0) | (1u << 4);
            if ((E->flips >= 620 && E->flips < 622) || (E->flips >= 640 && E->flips < 642)) buttons = 1u << 9;
            wr32(E, a1, buttons);
        }''')
(q / 'env.c').write_text(s)

s = (q / 'cache.c').read_text()
s = once(s, 'p->ic_nl_prefetch = 1;', 'p->ic_nl_prefetch = 0;')
s = once(s, 'p->btb_sets = 256;', 'p->btb_sets = 1024;')
s = once(s, 'p->gshare_counters = 4096;', 'p->gshare_counters = 16384;')
s = s.replace('timing model (os25 VexiiRiscv)', 'approximate timing model (os30 config; qsim os25-derived latencies)')
(q / 'cache.c').write_text(s)

s = (q / 'main.c').read_text()
s = once(s, '    size_t cap_rows = 4096, nrows = 0;', r'''
    snprintf(path, sizeof path, "%s/scene.csv", out);
    FILE *scene_csv = fopen(path, "w");
    if (!scene_csv) abort();
    fprintf(scene_csv, "frame,level,area,act,action,x,y,z,camera_x,camera_y,camera_z\n");
    size_t cap_rows = 4096, nrows = 0;''')
s = once(s, '                nrows++;', r'''
                nrows++;
                const qsim_sym *lev=qsim_syms_lookup(&E.syms,"gCurrLevelNum"),
                    *area=qsim_syms_lookup(&E.syms,"gCurrAreaIndex"),
                    *act=qsim_syms_lookup(&E.syms,"gCurrActNum"),
                    *mario=qsim_syms_lookup(&E.syms,"gMarioState"),
                    *lakitu=qsim_syms_lookup(&E.syms,"gLakituState");
                uint32_t mp=0, action=0;
                float pos[3]={0}, cam[3]={0};
                int16_t lv=0, ar=0, ac=0;
                memcpy(&mp,qsim_mem_ptr(E.cpu,mario->addr,4),4);
                if (mp) {
                    memcpy(pos,qsim_mem_ptr(E.cpu,mp+0x3c,12),12);
                    memcpy(&action,qsim_mem_ptr(E.cpu,mp+0x0c,4),4);
                }
                memcpy(&lv,qsim_mem_ptr(E.cpu,lev->addr,2),2);
                memcpy(&ar,qsim_mem_ptr(E.cpu,area->addr,2),2);
                memcpy(&ac,qsim_mem_ptr(E.cpu,act->addr,2),2);
                memcpy(cam,qsim_mem_ptr(E.cpu,lakitu->addr+0x0c,12),12);
                fprintf(scene_csv, "%llu,%d,%d,%d,%08x,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f\n",
                    (unsigned long long)fr->seq,lv,ar,ac,action,
                    pos[0],pos[1],pos[2],cam[0],cam[1],cam[2]);
                if (profile && (nrows==360 || nrows==420 || nrows==480 || nrows==540 || nrows==600 || nrows==660 || nrows==720)) {
                    profile_t window_profile;
                    build_profile(&E, &window_profile);
                    char window_tsv[4096];
                    snprintf(path, sizeof path, "%s/profile-%04zu.txt", out,nrows);
                    snprintf(window_tsv, sizeof window_tsv, "%s/profile-%04zu.tsv", out,nrows);
                    write_profile(&E,&window_profile,path,window_tsv,80,sha);
                    free(window_profile.rows);
                    free(window_profile.edges);
                }''')
s = once(s, '    fclose(csv);', '    fclose(csv);\n    fclose(scene_csv);')
(q / 'main.c').write_text(s)

shutil.copy2(PREVIOUS / 'sm64.app', OUT / 'sm64.app')
(OUT / 'inputs').mkdir(exist_ok=True)
shutil.copy2(ROOT.parent / 'SM64/.obj/sm64/app.elf', OUT / 'inputs/sm64.elf')
with (OUT / 'qsim-build.log').open('w') as log:
    subprocess.run(['make', '-j3'], cwd=q, stdout=log, stderr=subprocess.STDOUT, check=True)
files=[*q.glob('*.c'),*q.glob('*.h'),OUT/'inputs/sm64.elf',ROOT/'src/fpga/vendor/vexriscv/configs/os30.cfg']
(OUT/'inputs.json').write_text(json.dumps({'scope':__doc__,'sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}},indent=2)+'\n')
print(q/'qsim')
