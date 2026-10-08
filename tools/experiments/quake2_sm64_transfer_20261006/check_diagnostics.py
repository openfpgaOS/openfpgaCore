#!/usr/bin/env python3
"""Test actual Q2 profiling helpers and SDK wait/copy ordering with fake MMIO."""
from pathlib import Path
import importlib.util
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('waittests', ROOT / 'tools/experiments/os30_fps25_20261005/phase5_wait_tests.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
base, candidate, out = map(Path, sys.argv[1:])
out.mkdir(parents=True, exist_ok=True)
baseline = (base / 'src/sdk/include/of_gpu.h').read_text()
header = (candidate / 'src/sdk/include/of_gpu.h').read_text()
app = (candidate / 'src/quake2/openfpga/of_emit_q2.c').read_text()
hooks = (candidate / 'src/quake2/openfpga/of_gpu_profile_q2.h').read_text().replace('#include "of_timer.h"', '')
hooks = hooks.replace('of_time_us()', 'fake_clock()')
sdkhooks = header[header.index('/* Optional episode timing'):header.index('\n#ifndef OF_PC', header.index('/* Optional episode timing'))]

def run_c(name, text, profile=1):
    path = out / (name + '.c')
    path.write_text(text)
    cmd = ['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Wno-unused-function', '-DTEST_PROFILE='+str(profile), '-DOFQ2_GPU_PROFILE='+str(profile), str(path), '-o', str(out/name)]
    subprocess.run(cmd, check=True)
    run = subprocess.run([str(out/name)], capture_output=True, text=True, check=True)
    (out/(name+'.log')).write_text(run.stdout)
    return run.stdout

# Existing exhaustive episode tests, with Quake2's real profiling helpers.
# Copy hooks are checked separately below; disable them for these wait-only cases.
test_hooks = hooks + '''
#undef OF_GPU_SUBMIT_PROFILE_BEGIN
#undef OF_GPU_SUBMIT_PROFILE_END
#define OF_GPU_SUBMIT_PROFILE_BEGIN(cpu_ring) ((void)0)
#define OF_GPU_SUBMIT_PROFILE_END(cpu_ring, words) ((void)0)
#if TEST_PROFILE
#define gpu_wait_prof_ticks ofq2_profile_wait
static void gpu_wait_prof_read(uint32_t *r,uint32_t *d,uint32_t *f) {
    *r=ofq2_profile_wait[0]; *d=ofq2_profile_wait[1]; *f=ofq2_profile_wait[2];
}
#endif
'''
tests = old.TESTS.replace('    OF_GPU_WAIT_PROFILE_RESET();\n}', '    OF_GPU_WAIT_PROFILE_RESET();\n#if TEST_PROFILE\n    ofq2_profile_active=1;\n#endif\n}')
expected = None
for name, source, enabled in [('wait-baseline', baseline, 0), ('wait-off', header, 0), ('wait-on', header, 1)]:
    funcs = '\n'.join(old.function(source, n) for n in old.FUNCS).replace('__asm__ volatile("fence" ::: "memory");', '__asm__ volatile("" ::: "memory");')
    output = run_c(name, old.PREFIX + test_hooks + sdkhooks + funcs + tests, enabled)
    if expected is None: expected = output
    assert output == expected, name

prefix = old.PREFIX + hooks + sdkhooks
funcs = '\n'.join(old.function(header,n) for n in old.FUNCS).replace('__asm__ volatile("fence" ::: "memory");', '__asm__ volatile("" ::: "memory");')
copy_tests = r'''
int main(void) {
    ofq2_profile_reset();
    _gpu_cpu_ring=1;_gpu_batch_buf=storage;_gpu_cmd_words=19;
    status_values[0]=GPU_STATUS_CPU_RING_MODE;ns=1;
    for(unsigned i=0;i<19;i++)storage[i]=0x100+i;
    ofq2_profile_active=1;
    _gpu_flush_cmd_stream();
    assert(timer_reads==2 && ofq2_profile_batches==1 && ofq2_profile_words==19);
    assert(ofq2_profile_modes==2 && ofq2_profile_copy_us==10);
    assert(reg_port==0x112 && reg_ctrl==GPU_CTRL_CPU_RING_PUBLISH && _gpu_cmd_words==0);
    assert(ofq2_profile_wait[0]==0 && ofq2_profile_wait[1]==0 && ofq2_profile_wait[2]==0);
    _gpu_flush_cmd_stream();assert(timer_reads==2); /* empty flush */
    ofq2_profile_active=0;
    _gpu_cmd_words=1;status_values[1]=GPU_STATUS_CPU_RING_MODE;ns=2;
    _gpu_flush_cmd_stream();assert(timer_reads==2); /* frozen overlay */
    ofq2_profile_reset();ofq2_profile_active=1;
    _gpu_cpu_ring=0;_gpu_publish_uncached=0;_gpu_cmd_words=3;_gpu_batch_inflight_mask=0;
    _gpu_flush_cmd_stream();
    assert(timer_reads==4 && ofq2_profile_words==3 && ofq2_profile_batches==1 && ofq2_profile_modes==1);
    assert(reg_len==3 && reg_kick==1); /* DMA descriptor unaffected */
    ofq2_profile_reset();ofq2_profile_active=1;
    now=0xfffffff0;ofq2_profile_submit_begin();now=8;ofq2_profile_submit_end(1,4);
    assert(ofq2_profile_copy_us==24 && ofq2_profile_words==4);
    puts("PASS: CPU-ring batch, empty/frozen paths, DMA publication, timer wrap");
}
'''
run_c('copy', prefix + funcs + copy_tests)

public = app[app.index('#if OFQ2_GPU_PROFILE\n/* Windows end'):]
public = public.replace('of_time_us()', 'fake_clock()')
frame_prefix = '''#include <stdint.h>\n#include <stdio.h>\n#include <assert.h>\nstatic uint32_t now,reads;\nstatic uint32_t fake_clock(void){reads++;return now;}\nstatic uint32_t of_texture_budget_free(void){return 102400;}\n'''
frame_tests = r'''
int main(void) {
    ofq2_profile_reset();OFQ2_GPUProfileFrame(1);OFQ2_GPUProfileFrame(1);
    assert(reads==0);OFQ2_GPUProfileFrame(1);assert(ofq2_profile_active && reads==1);
    for(unsigned i=0;i<40;i++) {
        now+=50000;
        ofq2_profile_wait[0]+=1000;ofq2_profile_wait[1]+=2000;ofq2_profile_wait[2]+=3000;
        ofq2_profile_copy_us+=4000;ofq2_profile_words+=100;ofq2_profile_batches+=2;ofq2_profile_modes=2;
        OFQ2_GPUProfileFrame(1);
    }
    assert(ofq2_profile_valid);
    OFQ2_GPUProfileToggle();assert(ofq2_profile_visible && !ofq2_profile_active);
    assert(!strcmp(OFQ2_GPUProfileLine(0),"Q2 P1 GPU SNAPSHOT"));
    assert(!strcmp(OFQ2_GPUProfileLine(1),"FPS 20.0 FRAME 50.0 MS"));
    assert(!strcmp(OFQ2_GPUProfileLine(2),"RING 1.0 DMA 2.0 MS"));
    assert(!strcmp(OFQ2_GPUProfileLine(3),"FENC 3.0 COPY 4.0 MS"));
    assert(!strcmp(OFQ2_GPUProfileLine(4),"WORDS 100 BATCH 2 / FRAME"));
    assert(!strcmp(OFQ2_GPUProfileLine(5),"MODE CPU TEX FREE 100K"));
    unsigned before=reads;now+=100000;OFQ2_GPUProfileFrame(1);assert(reads==before);
    OFQ2_GPUProfileToggle();OFQ2_GPUProfileFrame(1);OFQ2_GPUProfileFrame(1);OFQ2_GPUProfileFrame(1);
    assert(ofq2_profile_active && reads==before+1);
    now+=5000000;OFQ2_GPUProfileFrame(1); /* reject long pause, keep last valid */
    assert(!strcmp(ofq2_profile_lines[1],"FPS 20.0 FRAME 50.0 MS"));
    OFQ2_GPUProfileFrame(0);assert(!ofq2_profile_valid && !ofq2_profile_active);
    /* A two-second window spanning the 32-bit microsecond timer wrap. */
    OFQ2_GPUProfileFrame(1);OFQ2_GPUProfileFrame(1);now=0xfffffff0;OFQ2_GPUProfileFrame(1);
    now+=2000000;OFQ2_GPUProfileFrame(1);assert(ofq2_profile_valid);
    puts("PASS: per-frame units, game/menu reset, frozen overlay, warmup, pause and timer wrap");
}
'''
run_c('window', frame_prefix + hooks + public + frame_tests)
input_source = (candidate/'src/quake2/openfpga/input_of.c').read_text()
chord = input_source[input_source.index('\tbuttons = pad.buttons;'):input_source.index('\tchanged = buttons ^ prev_buttons;')]
run_c('input', r'''
#include <stdint.h>
#include <assert.h>
#include <stdio.h>
#define OF_BTN_SELECT 1u
#define OF_BTN_L1 2u
#define OF_BTN_R1 4u
static int ofq2_profile_chord, toggles;
static uint32_t ofq2_profile_suppressed;
static struct { uint32_t buttons; } pad;
static void OFQ2_GPUProfileToggle(void) { toggles++; }
static uint32_t filtered(uint32_t incoming) { uint32_t buttons;pad.buttons=incoming;
''' + chord + r'''
return buttons; }
int main(void) {
    assert(filtered(6)==6 && toggles==0);
    assert(filtered(7)==0 && toggles==1);
    assert(filtered(7)==0 && toggles==1);
    assert(filtered(6)==0 && toggles==1); /* suppress until each key released */
    assert(filtered(4)==0 && toggles==1);
    assert(filtered(0)==0 && toggles==1);
    assert(filtered(6)==6 && toggles==1);
    assert(filtered(7)==0 && toggles==2);
    assert(filtered(8)==8 && toggles==2); /* unrelated game key survives */
    puts("PASS: chord edge, hold, staggered release, repeat, other buttons");
}
''')
clean = subprocess.run(['make', '-n', '-C', str(candidate/'src/quake2'), 'GPU_PROFILE=1', 'clean'], capture_output=True, text=True, check=True)
assert 'build/pocket/quake2' not in clean.stdout
assert 'build/diagnostics/quake2-gpuprofile.elf' in clean.stdout
invalid = subprocess.run(['make', '-n', '-C', str(candidate/'src/quake2'), 'GPU_PROFILE=2', 'clean'], capture_output=True, text=True)
assert invalid.returncode and 'GPU_PROFILE must be 0 or 1' in invalid.stderr
result = {'passed': True, 'wait_poll_and_state_order_unchanged': True,
          'diagnostic_clean_preserves_normal_package': True,
          'invalid_profile_mode_rejected': True,
          'ready_path_wait_timer_reads': 0, 'timer_reads_per_blocked_episode': 2,
          'batch_timer_reads': 2, 'copy_timer_not_per_word': True,
          'frozen_overlay_no_collection': True, 'counter_and_clock_wrap_tested': True,
          'limits': ['Fake MMIO/clock tests establish accounting and ordering, not physical timing or FPS.',
                     'CPU COPY includes loads, MMIO stores, status check and publish; DMA COPY covers visibility preparation.',
                     'Wait counters omit texture/LUT uploads, translucency table waits, nonblocking probes and kernel presentation waits.'],
          'hashes': {str(p.relative_to(candidate)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [candidate/'src/sdk/include/of_gpu.h',candidate/'src/quake2/openfpga/of_gpu_profile_q2.h',candidate/'src/quake2/openfpga/of_emit_q2.c']}}
(out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
