#!/usr/bin/env python3
"""Prepare the reviewed opt-in Quake2 diagnostics in an isolated source copy."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
canonical = Path(__file__).resolve().parents[3] / 'src/firmware/api/of_gpu.h'

def replace(path, old, new):
    p = root / path
    text = p.read_text()
    assert text.count(old) == 1, (path, old[:100], text.count(old))
    p.write_text(text.replace(old, new))

# Quake2's header differs from this version only by the verified F7 wait hooks.
(root / 'src/sdk/include/of_gpu.h').write_bytes(canonical.read_bytes())
replace('src/sdk/include/of_gpu.h', '#ifndef OF_PC\n#include "of_caps.h"', '''/* Optional per-batch transport timing. CPU-ring timing includes the staged
 * loads, MMIO stores, status check and publish. DMA timing covers source
 * visibility preparation only; descriptor/idle waits are separate wait hooks.
 * Neither hook runs per word; defaults generate no code. */
#ifndef OF_GPU_SUBMIT_PROFILE_BEGIN
#define OF_GPU_SUBMIT_PROFILE_BEGIN(cpu_ring) ((void)0)
#endif
#ifndef OF_GPU_SUBMIT_PROFILE_END
#define OF_GPU_SUBMIT_PROFILE_END(cpu_ring, words) ((void)0)
#endif

#ifndef OF_PC
#include "of_caps.h"''')
replace('src/sdk/include/of_gpu.h', '    if (_gpu_cpu_ring) {\n        /* Space', '    if (_gpu_cpu_ring) {\n        OF_GPU_SUBMIT_PROFILE_BEGIN(1);\n        /* Space')
replace('src/sdk/include/of_gpu.h', '        GPU_CTRL = GPU_CTRL_CPU_RING_PUBLISH;\n        _gpu_cmd_words = 0;', '        GPU_CTRL = GPU_CTRL_CPU_RING_PUBLISH;\n        OF_GPU_SUBMIT_PROFILE_END(1, submit_words);\n        _gpu_cmd_words = 0;')
replace('src/sdk/include/of_gpu.h', '    if (_gpu_publish_uncached < 0)\n', '    OF_GPU_SUBMIT_PROFILE_BEGIN(0);\n    if (_gpu_publish_uncached < 0)\n')
replace('src/sdk/include/of_gpu.h', '    _gpu_wait_dma_desc_slot_debug();\n\n    /* The publish', '    OF_GPU_SUBMIT_PROFILE_END(0, submit_words);\n    _gpu_wait_dma_desc_slot_debug();\n\n    /* The publish')

replace('src/quake2/Makefile', 'APP       = quake2\n', 'APP       = quake2\nGPU_PROFILE ?= 0\nifneq ($(GPU_PROFILE),0)\nifneq ($(GPU_PROFILE),1)\n$(error GPU_PROFILE must be 0 or 1)\nendif\nendif\n')
replace('src/quake2/Makefile', 'OBJ_DIR   = $(ROOT)/.obj/$(APP)\n', 'OBJ_DIR   = $(ROOT)/.obj/$(APP)$(if $(filter 1,$(GPU_PROFILE)),-gpuprofile)\n')
replace('src/quake2/Makefile', 'CFLAGS  = -I. -I$(YQ2)\n', 'CFLAGS  = -I. -I$(YQ2)\nCFLAGS += -DOFQ2_GPU_PROFILE=$(GPU_PROFILE)\n')
replace('src/quake2/Makefile', 'ifeq ($(TARGET),mister)\nrelease: game-dist', '''ifeq ($(GPU_PROFILE),1)
# Diagnostic builds never replace the normal Pocket package.
release: $(OBJ_DIR)/app.elf
\t@mkdir -p "$(ROOT)/build/diagnostics"
\tcp "$<" "$(ROOT)/build/diagnostics/quake2-gpuprofile.elf"
else ifeq ($(TARGET),mister)
release: game-dist''')
replace('src/quake2/openfpga/of_emit_q2.c', '#include "of_gpu.h"\n', '#include "of_gpu_profile_q2.h"\n#include "of_gpu.h"\n')
replace('src/quake2/openfpga/of_emit_q2.c', '/* ring-backpressure snapshot: SDK statics accumulate per TU; read +\n * reset by the perf heartbeat to expose CPU time spent waiting for\n * ring space (each spin iteration ~ a few cycles) */', '''/* Ring-backpressure counts, not elapsed time. MMIO polling latency varies;
 * use the optional episode timers to measure time spent waiting. */''')

(root / 'src/quake2/openfpga/of_gpu_profile_q2.h').write_text(r'''/* Optional GPU transport diagnostics. Included only by the GPU-owner TU.
 * The normal build compiles out all state, hooks, timers and UI calls. */
#ifndef OF_GPU_PROFILE_Q2_H
#define OF_GPU_PROFILE_Q2_H
#ifndef OFQ2_GPU_PROFILE
#define OFQ2_GPU_PROFILE 0
#endif
#if OFQ2_GPU_PROFILE
#include <stdint.h>
#include <string.h>
#include "of_timer.h"

static int ofq2_profile_active, ofq2_profile_visible, ofq2_profile_valid;
static uint32_t ofq2_profile_started[3], ofq2_profile_wait[3];
static uint32_t ofq2_profile_copy_start, ofq2_profile_copy_us;
static uint32_t ofq2_profile_words, ofq2_profile_batches, ofq2_profile_modes;
static uint32_t ofq2_profile_window, ofq2_profile_frames;
static unsigned ofq2_profile_warmup;
static char ofq2_profile_lines[6][40];

static void ofq2_profile_counters_clear(void)
{
    memset(ofq2_profile_wait, 0, sizeof(ofq2_profile_wait));
    ofq2_profile_copy_us = ofq2_profile_words = ofq2_profile_batches = 0;
    ofq2_profile_modes = 0;
    ofq2_profile_frames = 0;
}
static void ofq2_profile_reset(void)
{
    ofq2_profile_counters_clear();
    ofq2_profile_active = 0;
    ofq2_profile_visible = 0;
    ofq2_profile_valid = 0;
    ofq2_profile_warmup = 2;
}
static inline void ofq2_profile_wait_begin(unsigned kind, int first)
{
    if (first && ofq2_profile_active)
        ofq2_profile_started[kind] = of_time_us();
}
static inline void ofq2_profile_wait_end(unsigned kind, int waited)
{
    if (waited && ofq2_profile_active)
        ofq2_profile_wait[kind] += of_time_us() - ofq2_profile_started[kind];
}
static inline void ofq2_profile_submit_begin(void)
{
    if (ofq2_profile_active) ofq2_profile_copy_start = of_time_us();
}
static inline void ofq2_profile_submit_end(unsigned cpu_ring, uint32_t words)
{
    if (!ofq2_profile_active) return;
    ofq2_profile_copy_us += of_time_us() - ofq2_profile_copy_start;
    ofq2_profile_words += words;
    ofq2_profile_batches++;
    ofq2_profile_modes |= 1u << cpu_ring;
}
#define OF_GPU_WAIT_PROFILE_BEGIN(kind, first) ofq2_profile_wait_begin(kind, first)
#define OF_GPU_WAIT_PROFILE_END(kind, waited) ofq2_profile_wait_end(kind, waited)
#define OF_GPU_WAIT_PROFILE_RESET() ofq2_profile_reset()
#define OF_GPU_SUBMIT_PROFILE_BEGIN(cpu_ring) ofq2_profile_submit_begin()
#define OF_GPU_SUBMIT_PROFILE_END(cpu_ring, words) ofq2_profile_submit_end(cpu_ring, words)
#endif
#endif
''')

profiler = r'''
#if OFQ2_GPU_PROFILE
/* Windows end after presentation. No additional GPU barrier is inserted.
 * The frozen overlay is drawn only after collection has stopped. */
void OFQ2_GPUProfileToggle(void)
{
    if (ofq2_profile_visible)
    {
        ofq2_profile_visible = 0;
        ofq2_profile_active = 0;
        ofq2_profile_warmup = 2;
    }
    else if (ofq2_profile_valid)
    {
        ofq2_profile_visible = 1;
        ofq2_profile_active = 0;
    }
}

const char *OFQ2_GPUProfileLine(unsigned line)
{
    return ofq2_profile_visible && line < 6 ? ofq2_profile_lines[line] : NULL;
}

static uint32_t ofq2_profile_ms10(uint32_t us)
{
    return (uint32_t)((uint64_t)us / ((uint64_t)ofq2_profile_frames * 100u));
}

void OFQ2_GPUProfileFrame(int gameplay)
{
    if (!gameplay)
    {
        ofq2_profile_reset();
        return;
    }
    if (ofq2_profile_visible) return;
    if (ofq2_profile_warmup) { ofq2_profile_warmup--; return; }
    uint32_t now = of_time_us();
    if (!ofq2_profile_active)
    {
        ofq2_profile_counters_clear();
        ofq2_profile_window = now;
        ofq2_profile_active = 1;
        return;
    }
    ofq2_profile_frames++;
    uint32_t elapsed = now - ofq2_profile_window;
    if (elapsed < 2000000u) return;
    if (elapsed <= 4000000u)
    {
        uint32_t fps10 = (uint32_t)((uint64_t)ofq2_profile_frames * 10000000u / elapsed);
        uint32_t frame = ofq2_profile_ms10(elapsed);
        uint32_t ring = ofq2_profile_ms10(ofq2_profile_wait[0]);
        uint32_t dma = ofq2_profile_ms10(ofq2_profile_wait[1]);
        uint32_t fence = ofq2_profile_ms10(ofq2_profile_wait[2]);
        uint32_t copy = ofq2_profile_ms10(ofq2_profile_copy_us);
        snprintf(ofq2_profile_lines[0], 40, "Q2 P1 GPU SNAPSHOT");
        snprintf(ofq2_profile_lines[1], 40, "FPS %u.%u FRAME %u.%u MS", fps10/10, fps10%10, frame/10, frame%10);
        snprintf(ofq2_profile_lines[2], 40, "RING %u.%u DMA %u.%u MS", ring/10, ring%10, dma/10, dma%10);
        snprintf(ofq2_profile_lines[3], 40, "FENC %u.%u COPY %u.%u MS", fence/10, fence%10, copy/10, copy%10);
        snprintf(ofq2_profile_lines[4], 40, "WORDS %u BATCH %u / FRAME", ofq2_profile_words/ofq2_profile_frames, ofq2_profile_batches/ofq2_profile_frames);
        snprintf(ofq2_profile_lines[5], 40, "MODE %s TEX FREE %uK", ofq2_profile_modes == 2 ? "CPU" : ofq2_profile_modes == 1 ? "DMA" : "MIX", of_texture_budget_free()/1024u);
        ofq2_profile_valid = 1;
    }
    /* Formatting is outside the next measurement window. */
    ofq2_profile_counters_clear();
    ofq2_profile_window = of_time_us();
}
#endif
'''
p = root / 'src/quake2/openfpga/of_emit_q2.c'
p.write_text(p.read_text() + profiler)
replace('src/quake2/openfpga/of_emit_q2.h', '\n#endif\n', '''
#if OFQ2_GPU_PROFILE
void OFQ2_GPUProfileToggle(void);
void OFQ2_GPUProfileFrame(int gameplay);
const char *OFQ2_GPUProfileLine(unsigned line);
#endif

#endif
''')
replace('src/quake2/src/client/cl_screen.c', '#include "header/client.h"\n', '#include "header/client.h"\n#if OFQ2_GPU_PROFILE\n#include "openfpga/of_emit_q2.h"\n#endif\n')
replace('src/quake2/src/client/cl_screen.c', '\tSCR_Framecounter();\n\tR_EndFrame();', '''\tSCR_Framecounter();
#if OFQ2_GPU_PROFILE
\t/* Collection freezes before the panel is drawn, so its CPU blits do not
\t * contaminate the captured window. A build marker distinguishes it. */
\tfor (unsigned row = 0; row < 6; row++)
\t{
\t\tconst char *line = OFQ2_GPUProfileLine(row);
\t\tif (line) DrawStringScaled(0, 16 + row * 8, line, 1.0f);
\t}
#endif
\tR_EndFrame();
#if OFQ2_GPU_PROFILE
\tOFQ2_GPUProfileFrame(cls.state == ca_active && cls.key_dest == key_game && !cl.cinematictime);
#endif''')
replace('src/quake2/openfpga/input_of.c', '#include "of_input.h"\n', '#include "of_input.h"\n#if OFQ2_GPU_PROFILE\n#include "of_emit_q2.h"\nstatic int ofq2_profile_chord;\nstatic uint32_t ofq2_profile_suppressed;\n#endif\n')
replace('src/quake2/openfpga/input_of.c', '\tbuttons = pad.buttons;\n\tchanged = buttons ^ prev_buttons;', '''\tbuttons = pad.buttons;
#if OFQ2_GPU_PROFILE
\t/* Reuse this input sample; no additional hardware poll. Release any game
\t * keys already held when the chord is completed, then suppress its keys. */
\tuint32_t chord_mask = OF_BTN_SELECT | OF_BTN_L1 | OF_BTN_R1;
\tint chord = (buttons & chord_mask) == chord_mask;
\tif (chord && !ofq2_profile_chord) OFQ2_GPUProfileToggle();
\tofq2_profile_chord = chord;
\tofq2_profile_suppressed &= buttons;\n\tif (chord) ofq2_profile_suppressed = chord_mask;\n\tbuttons &= ~ofq2_profile_suppressed;
#endif
\tchanged = buttons ^ prev_buttons;''')
replace('src/quake2/Makefile', 'clean: sdk-clean\n\trm -rf $(OUT)', 'clean: sdk-clean\nifeq ($(GPU_PROFILE),1)\n\trm -f "$(ROOT)/build/diagnostics/quake2-gpuprofile.elf"\nelse\n\trm -rf $(OUT)\nendif')
replace('src/quake2/src/client/cl_screen.c', '\tfloat scale = SCR_GetMenuScale();\n\n\t/* if the screen is disabled', '\tfloat scale = SCR_GetMenuScale();\n#if OFQ2_GPU_PROFILE\n\t/* Capture before SCR_DrawLoading clears its flag. Reset even when a\n\t * disabled/uninitialized screen returns without reaching EndFrame. */\n\tint profile_gameplay = cls.state == ca_active && cls.key_dest == key_game &&\n\t\t!cl.cinematictime && !(cl_paused && cl_paused->value) &&\n\t\t!scr_draw_loading && !cls.disable_screen &&\n\t\tscr_initialized && con.initialized;\n\tif (!profile_gameplay) OFQ2_GPUProfileFrame(0);\n#endif\n\n\t/* if the screen is disabled')
replace('src/quake2/src/client/cl_screen.c', 'OFQ2_GPUProfileFrame(cls.state == ca_active && cls.key_dest == key_game && !cl.cinematictime);', 'OFQ2_GPUProfileFrame(profile_gameplay);')
print('Prepared opt-in diagnostics:', root)
