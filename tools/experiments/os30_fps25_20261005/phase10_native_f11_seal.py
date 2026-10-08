#!/usr/bin/env python3
"""Seal the bounded F11 reference and guest preparation, not native timing."""
import csv,json,shutil
from phase10_native_renderer_check import ROOT,P,WINDOW,sha,read,save

G=WINDOW/'guest-f11-warm-v1'
C=P/'native-f11-warm-capture-v1'
S=P/'native-f11-services-v1'
OLD=P/'native-warm-capture-v4'

def main():
    assert not (G/'manifest.json').exists()
    evidence={}
    def bind(path):evidence[str(path.relative_to(ROOT))]=sha(path)
    for path in [C/'manifest.json',S/'component-manifest.json',G/'literal-check/manifest.json']:
        data=read(path);assert data['passed']
        for n,h in data.get('files',{}).items():assert sha(path.parent/n)==h,n
        for n,h in data.get('evidence_sha256',{}).items():assert sha(ROOT/n)==h,n
        bind(path)
    a,b=OLD/'run-reference',C/'run-reference'
    for name in ['scene.csv','geometry.csv','culling.csv']:
        x=list(csv.DictReader((a/name).open()));y=list(csv.DictReader((b/name).open()))
        assert x==y and len(x)==363,name
        bind(a/name);bind(b/name)
    x=list(csv.DictReader((a/'frames.csv').open()));y=list(csv.DictReader((b/'frames.csv').open()))
    assert len(x)==len(y)==363
    assert all(s['gpu_cmd_words']==t['gpu_cmd_words'] for s,t in zip(x,y))
    bind(a/'frames.csv');bind(b/'frames.csv')
    for n in [360,361,362]:assert (a/f'frame_{n:05}.bin').read_bytes()==(b/f'frame_{n:05}.bin').read_bytes()
    assert (a/'snapshot/commands.bin').read_bytes()==(b/'snapshot/commands.bin').read_bytes()
    src=(P/'native-renderer-services-v3/native_services.c').read_text()
    assert src.replace('0x10fe1604','0x10fda714')==(S/'native_services.c').read_text()
    assert (P/'native-renderer-services-v3/native_services.S').read_bytes()==(S/'native_services.S').read_bytes()
    state=(P/'native-renderer-services-v3/native_state.h').read_text()
    assert state.replace('288714752u','288681984u')==(S/'native_state.h').read_text()
    for name in ['native_services.c','native_services.S','native_state.h']:
        assert (S/name).read_bytes()==(G/name).read_bytes();bind(S/name)
    for name in ['restore.S','link.ld']:
        assert (WINDOW/'guest-warm-v3'/name).read_bytes()==(G/name).read_bytes();bind(WINDOW/'guest-warm-v3'/name)
    service=read(S/'component-result.json');assert service['passed'] and service['host_actual_c_trace_records']==229
    assert read(G/'literal-check/restore-result.json')['passed']
    assert read(G/'literal-check/ecall-result.json')['passed']
    app=P.parent/'phase9/checks-state-f11/app.elf'
    assert sha(app)=='fb4ebb1c291cab41226a940b34d534ffcb723a8fe820addf06e78eed342f02cd';bind(app)
    for path in [P.parent/'phase9/REPORT.json',P.parent/'phase9/published.json',P.parent/'phase9/QUALIFIED.json',
                 P/'native-renderer-services-v3/component-manifest.json',P/'native-warm-guest-source-review-v1/manifest.json',
                 ROOT/'tools/experiments/os30_fps25_20261005/phase10_native_f11_warm_capture.py',
                 ROOT/'tools/experiments/os30_fps25_20261005/phase10_native_f11_warm_guest.py',
                 ROOT/'tools/experiments/os30_fps25_20261005/phase10_native_f11_services.py',
                 ROOT/'tools/experiments/os30_fps25_20261005/phase10_native_f11_warm_guest_check.py']:
        bind(path)
    result=dict(passed=True,scope='F11 normal on selected-v7-core; not the historically deployed hardware',
        elf_sha256=sha(app),reference_frames=[360,361,362],semantic_records_exact_to_PGO=363,
        command_counts_exact_to_PGO=363,reference_pixels_exact_to_PGO=True,
        reference_interval_raw_commands_exact_to_PGO=True,reference_interval_words=(b/'snapshot/commands.bin').stat().st_size//4,
        service_records_checked=229,guest_formula_changes=['token symbol address10fe1604 ->10fda714'],
        guest_state_changes=['captured cur_brk288714752 ->288681984; other initial service state identical'],
        caller_and_renderer_unmodified=True,restore_and_trap_assembly_unchanged=True,
        sparse_pre_release_overlay_only=True,native_execution_pending=True,full_frame_FPS_claimed=False,
        limitations=['Every ELF uses its own architecture/memory goldens. No cross-ELF register equality is claimed.',
                    'Guest functional mixer/time/input/video/MMAP services retain the prior bounded contract; this is not native OS/PCM/IRQ qualification.',
                    'F11 normal was locally published; F10 diagnostic was the last historically verified SD executable. No SD access or deployment here.',
                    'Capture/render references use the existing real-GPU/native-FMA harness; actual Vexii interval timing remains pending.'])
    save(G/'result.json',result)
    (G/'REPORT.txt').write_text('F11-normal native baseline preparation: PASS\n\n'
        'Exact preserved ELF fb4ebb1c; fresh full snapshot at gfx_run360, original caller/game/audio path through entries361/362. '
        'All363 scene/geometry/culling records and command counts, the three rendered buffers, and46,040 captured interval command words match the qualified PGO reference.\n\n'
        'The bounded service formulas differ only in the ELF token address; initial service state differs only in cur_brk. '
        'All229 captured returns, output bytes, voices/allocator and literal restore/ECALL checks pass. '
        'The original application is never overlaid or patched; guest helper segments lie below the application load region.\n\n'
        'Use native-f11-warm-capture-v1/run-reference/snapshot and this guest directory/run-config.txt with the existing selected-v7 native model. '
        'This defines F11-on-selected-v7-core, not a historically deployed hardware baseline. No native timing or FPS result is asserted yet.\n')
    shutil.copyfile(__file__,G/'seal-recipe.py')
    save(G/'manifest.json',dict(passed=True,files={str(p.relative_to(G)):sha(p)for p in G.rglob('*')if p.is_file()},evidence_sha256=evidence))
    print(sha(G/'manifest.json'))

if __name__=='__main__':main()
