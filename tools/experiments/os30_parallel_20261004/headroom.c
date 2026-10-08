/* Counterfactual scheduling experiment, NOT a geometry-processor emulator.
 * Execute every original CPU instruction and the unchanged GPU/SDRAM RTL;
 * discount only selected functions' exclusive CPU time on the wall clock.
 * This measures headroom before paying any new DMA, firmware or worker cost.
 * No values, instructions, rendering work or ordering checks are skipped.
 */
#include "headroom.h"
#include "env.h"
#include "cache.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { OTHER, PREPARE, VERTEX, FACES, UPLOAD, FALLBACK, DISPATCH, GROUPS };
static const char *names[GROUPS] = {
    "other", "bounds_matrix", "vertex_load_and_fallback", "face_selection",
    "packing_upload", "triangle_fallback", "display_list_and_material"
};
static unsigned discounts[GROUPS];
static uint64_t self_cycles[GROUPS], previous, credit;
static unsigned last_group;
static uint32_t base, size;
static unsigned char *groups;
static char directory[4096];
static const char *scenario;

static int classify(const char *name) {
    if (!strcmp(name, "geometry_prepare")) return PREPARE;
    if (!strcmp(name, "gfx_sp_vertex")) return VERTEX;
    if (!strcmp(name, "gfx_geometry_cached_run") ||
        !strcmp(name, "gfx_geometry_run") ||
        !strcmp(name, "geometry_reject")) return FACES;
    if (!strcmp(name, "gfx_geometry_submit") ||
        !strcmp(name, "gfx_gpu_geometry_batch")) return UPLOAD;
    if (!strcmp(name, "gpu_draw_triangles") ||
        !strcmp(name, "gfx_push_triangle") ||
        !strcmp(name, "gfx_sp_tri1") ||
        !strncmp(name, "gfx_clip_triangle", 17) ||
        !strncmp(name, "gfx_gpu_vtx_cache", 17)) return FALLBACK;
    if (!strncmp(name, "gfx_", 4) || !strcmp(name, "emit_tri_state") ||
        !strncmp(name, "geometry_attributes_", 20)) return DISPATCH;
    return OTHER;
}

static void account(uint64_t now) {
    if (!groups) return;
    assert(now >= previous);
    uint64_t old = self_cycles[last_group];
    self_cycles[last_group] += now - previous;
    credit += self_cycles[last_group] * discounts[last_group] / 100
              - old * discounts[last_group] / 100;
    previous = now;
}

void headroom_begin(void *environment, const char *folder) {
    qenv *e = environment;
    scenario = getenv("PARALLEL_CASE");
    if (!scenario) scenario = "control";
    if (!strcmp(scenario, "mesh75")) {
        discounts[PREPARE] = discounts[FACES] = discounts[UPLOAD] = 75;
    } else if (!strcmp(scenario, "geometry75")) {
        for (unsigned i = PREPARE; i <= FALLBACK; ++i) discounts[i] = 75;
    } else if (!strcmp(scenario, "renderer90")) {
        for (unsigned i = PREPARE; i < GROUPS; ++i) discounts[i] = 90;
    } else assert(!strcmp(scenario, "control"));
    base = e->cpu->sdram_base;
    size = e->cpu->sdram_size;
    groups = calloc(size / 2, 1);
    assert(groups && strlen(folder) < sizeof(directory));
    strcpy(directory, folder);
    for (size_t i = 0; i < e->syms.n; ++i) {
        const qsim_sym *s = &e->syms.s[i];
        if (s->type != 2 || s->addr < base || s->addr - base >= size ||
            s->size > size - (s->addr - base)) continue;
        memset(groups + (s->addr-base)/2, classify(s->name), s->size/2);
    }
    previous = e->cpu->model->cyc;
}

void headroom_instruction(uint64_t now, uint32_t pc) {
    if (!groups) return;
    account(now);
    last_group = pc >= base && pc - base < size ? groups[(pc-base)/2] : OTHER;
}

uint64_t headroom_credit(uint64_t now) { account(now); return credit; }

void headroom_finish(uint64_t now) {
    account(now);
    char path[4200];
    snprintf(path, sizeof(path), "%s/headroom.json", directory);
    FILE *f = fopen(path, "w"); assert(f);
    fprintf(f, "{\"counterfactual\":true,\"scenario\":\"%s\",\"discounted_cycles\":%llu,\"groups\":[",
            scenario, (unsigned long long)credit);
    for (unsigned i = 0; i < GROUPS; ++i)
        fprintf(f, "%s{\"name\":\"%s\",\"raw_cycles\":%llu,\"discount_percent\":%u}",
                i ? "," : "", names[i], (unsigned long long)self_cycles[i], discounts[i]);
    fprintf(f, "]}\n"); fclose(f);
}
