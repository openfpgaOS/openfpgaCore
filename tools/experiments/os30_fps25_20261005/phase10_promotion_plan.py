#!/usr/bin/env python3
"""Read-only production guards; write a private promotion plan, never publish."""
from pathlib import Path
import hashlib
import json
import stat
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'promotion-plan-v1'
NORMAL = P / 'normal-build-integration-v2'
APP = P / 'lod5-recycle-delivery-v1'
SM64 = ROOT.parent / 'SM64'
QUAKE = ROOT.parent / 'Quake2'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def regular(p, base):
    assert p.is_relative_to(base)
    for q in [p, *p.parents]:
        assert not q.is_symlink(), q
        if q == base:
            break
    assert stat.S_ISREG(p.stat().st_mode), p
    return sha(p)


def main():
    before = read(NORMAL / 'production-before.json')
    prep = read(NORMAL / 'prepared.json')
    for n, h in before.items():
        assert regular(ROOT / n, ROOT) == h, n
    hardware_files = []
    for n, h in prep['proposed_sha256'].items():
        assert regular(NORMAL / 'proposed' / n, NORMAL) == h, n
        p = ROOT / n
        if n in before:
            assert regular(p, ROOT) == before[n]
        else:
            assert not p.exists() and not p.is_symlink(), n
        hardware_files.append(dict(destination=str(p), source=str(NORMAL / 'proposed' / n),
                                   before_sha256=before.get(n), after_sha256=h))
    f11 = ROOT / 'docs/measurements/os30-fps25-20261005/phase9/QUALIFIED.json'
    app_before = read(f11)['source_tree_sha256']
    assert len(app_before) == 6747
    names = {'Makefile', 'Mario64.z64'}
    for top in ('src', 'tools'):
        assert not (SM64 / top).is_symlink()
        for p in (SM64 / top).rglob('*'):
            if any(v in ('.git', '__pycache__') for v in p.relative_to(SM64).parts):
                continue
            assert not p.is_symlink(), p
            if p.is_file():
                names.add(str(p.relative_to(SM64)))
    assert names == app_before.keys()
    for n, h in app_before.items():
        assert regular(SM64 / n, SM64) == h, n
    sources = read(APP / 'sources.json')
    software_files = []
    for n, info in sources['inputs'].items():
        assert regular(SM64 / n, SM64) == info['before_sha256']
        assert regular(APP / 'sources' / n, APP) == info['after_sha256']
        if n in sources['changed_files']:
            software_files.append(dict(destination=str(SM64 / n), source=str(APP / 'sources' / n),
                                       before_sha256=info['before_sha256'], after_sha256=info['after_sha256']))
    assert len(hardware_files) == 29 and len(software_files) == 3

    retained = {}
    for base, application, core_name in [(SM64, 'sm64', 'ThinkElastic.SM64'),
                                        (QUAKE, 'quake2', 'thinkelastic.quake2')]:
        paths = ['runtime/pocket/os30.rbf_r', 'runtime/pocket/os.bin', 'runtime/pocket/loader.bin',
                 f'build/pocket/{application}/Cores/{core_name}/os30.rbf_r',
                 f'build/pocket/{application}/Cores/{core_name}/loader.bin',
                 f'build/pocket/{application}/Assets/{application}/common/{application}.elf',
                 f'build/pocket/{application}/Assets/{application}/common/os.bin']
        if base == SM64:
            paths += ['.obj/sm64/app.elf', '.obj/sm64-perfdiag/app.elf']
        for n in paths:
            retained[str(base / n)] = regular(base / n, base)
    artifact = APP / 'payload/Assets/sm64/common/sm64.elf'
    assert sha(artifact) == 'bb41f2088130935dc637d55a4a1dbd0afddb68f94794440ddb5a67e504d4a2c5'
    current = read(f11)
    assert retained[str(SM64 / '.obj/sm64/app.elf')] == current['normal_elf_sha256']
    assert retained[str(SM64 / '.obj/sm64-perfdiag/app.elf')] == current['diagnostic_elf_sha256']
    assert retained[str(SM64 / 'build/pocket/sm64/Assets/sm64/common/sm64.elf')] == current['diagnostic_elf_sha256']

    evidence = [NORMAL / 'manifest.json', NORMAL / 'production-before.json', NORMAL / 'prepared.json',
                NORMAL / 'result.json', NORMAL / 'candidate.patch', APP / 'manifest.json',
                APP / 'sources.json', APP / 'integration.patch', APP / 'rebuild-proof.json',
                APP / 'sm64-lod5-recycler-app.zip', artifact, f11,
                P / 'compact-lod5-core-v5/prepared.json', P / 'compact-lod5-core-v5/independent-review.json',
                P / 'cpu-os30-regenerate-v2/manifest.json', P / 'cache-write-lod5-coupled-v2/manifest.json',
                Path(__file__)]
    quake_sources = ['src/quake2/openfpga/of_emit_q2.c', 'src/quake2/openfpga/of_gl1.c',
                    'src/quake2/openfpga/hunk_of.c', 'src/quake2/openfpga/snd_of.c',
                    'src/sdk/include/of_gpu.h', 'src/sdk/include/of_texture.h',
                    'src/sdk/include/of_caps.h', 'src/sdk/app.ld']
    qs = {str(QUAKE / n): regular(QUAKE / n, QUAKE) for n in quake_sources}
    for n in ['src/fpga/common/phase10_load_alias.v', 'src/fpga/common/phase10_bram_alias.v',
              'src/fpga/common/axi_periph_slave.v', 'src/fpga/common/gpu_core.v',
              'src/fpga/targets/pocket/core_top.v']:
        evidence.append(P / 'compact-lod5-core-v5/frozen' / n)
    evidence += [ROOT / 'src/firmware/os/targets/pocket/target_platform.h', ROOT / 'src/firmware/os/os.ld']

    OUT.mkdir(exist_ok=False)
    shutil.copyfile(__file__, OUT / 'recipe.py')
    snapshot = dict(passed=True, read_only=True, core_input_count=len(before), sm64_input_count=len(app_before),
                    core_production_sha256=before, sm64_source_sha256=app_before, retained_artifacts=retained,
                    quake_source_sha256=qs, sd_mounted=Path('/run/media/alberto/POCKETDEV').is_mount(),
                    sd_app_or_core_hash_assumed=False)
    (OUT / 'retained-before.json').write_text(json.dumps(snapshot, indent=2) + '\n')
    plan = dict(private_only=True, promotion_performed=False, deployment_performed=False,
                hardware_source_files=hardware_files, sm64_source_files=software_files,
                tested_app_elf=str(artifact), tested_app_sha256=sha(artifact),
                app_archive=str(APP / 'sm64-lod5-recycler-app.zip'),
                bitstream_status='Pending the root-owned compact-v5 fit89301 result and explicit selection; no bitstream hash or clock qualification is inferred.',
                artifact_destinations_after_selection={
                    'normal_app': [str(SM64 / '.obj/sm64/app.elf'), str(SM64 / 'build/pocket/sm64/Assets/sm64/common/sm64.elf')],
                    'core': [str(SM64 / 'runtime/pocket/os30.rbf_r'), str(SM64 / 'build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r')],
                    'optional_sd': ['Assets/sm64/common/sm64.elf', 'Cores/ThinkElastic.SM64/os30.rbf_r']},
                preserve=['Existing F11 diagnostic ELF stays a separately labelled old artifact, never replaced with a normal ELF under its diagnostic path.',
                          'OS, loader, assets, saves, manifests and every Quake2 file remain unchanged by the SM64 plan.'],
                steps=[
                    'Assess and seal the exact one-seed fit, assembler output, both slow corners, setup/hold and constraints. State actual configured100MHz separately from comparative >90MHz target. Negative slack at the configured clock remains experimental, not timing closure.',
                    'Bind selected fit source hashes to normal-build-integration-v2 prepared closure, including generated CPU b5397043. Derive device rbf_r only from the selected assembler rbf with the existing verified byte bit-reversal convention; retain input/output hashes and verify reversing twice restores the raw rbf.',
                    'Immediately before changes, rerun all132 openfpga production input hashes, require all new proposed destinations absent, and recheck the exact6747-file F11 SM64 source inventory plus old ELF/core/OS/loader hashes. Abort on drift; never force-apply over user changes.',
                    'Back up every existing destination with modes and hashes into a new transaction directory; record previously absent paths. Check both frozen patches with git apply --check or equivalent against the guarded trees. Copy only29 hardware/build files and3 SM64 renderer files from the immutable proposed/source trees, using same-directory temporary files, fsync and atomic replace. Log each committed destination for rollback.',
                    'Post-copy verify all destination hashes and the expected whole SM64 source tree (three substitutions only). Reuse the sealed clean-QSF and actual regeneration proofs; verify the normal build resolves to the same closure. Do not rebuild Quartus with new flags or a new seed as part of copying.',
                    'Install the exact tested normal ELF at the two named local normal/package paths and the selected core at the two named local core paths. Explicit PROFILE=0 PERF_DIAG=0 is required for any later rebuild; current Makefile defaults PERF_DIAG=1. A private patch-apply rebuild already reproduced the exact tested normal ELF.',
                    'An optional later SD copy must first verify mount identity and fresh on-card old file hashes, backup both exact targets and perform atomic file replacements with readback hashes and sync. The card is presently absent. Install app before core if operations cannot be atomic as a pair: the new app safely uses CPU fallback on the old core. Do not hot-reload while an application is running.',
                    'Rollback uses the transaction log: restore only files whose current hashes still equal this promotion output, and remove only newly created files still equal to the recorded output. Restore app/core as a pair; stop on intervening edits. Keep all historical evidence and backups.'],
                quake_compatibility=dict(
                    legacy_interface_source_compatible=True, matched_quake_replay_passed=False,
                    core_copy_in_this_plan=False,
                    findings=[
                        'LOD5 adds MMIO0x4b000000 ID0x4c4f4405 and read-only1KiB raw-result alias0x13f80000 (uncached0x53f80000). Quake2 does not call this interface. GPU opcode0x4B is unrelated to MMIO region0x4B; existing mixer is MMIO0x48.',
                        'GPU command opcodes, status/capability bits, CPU ring4096 words,32 projected slots and CRAM texture upload/routing registers remain the legacy interface. CPU ISA/cache geometry is retained; queue sizing and exact combinational factoring require no app ABI change.',
                        'The alias is inside the existing OS speculation guard, above the runtime stack exclusive top and outside app static/heap allocation. It is not general writable SDRAM; software that intentionally uses the guard is not covered.',
                        'The new source pool0xfc0000..0x1000000 is reserved by the SM64 LOD5 client only, not stolen globally by hardware. Quake2 can keep its current16MiB texture allocator when geometry is idle. Its texture reset/upload route behavior remains valid; no SM64 source-cache code should be copied into Quake2.',
                        'The new app requires exact ID5 and full16MiB texture memory before enabling the source pool. An old core, LOD4/6 ID or absent device follows original CPU rendering. Existing apps on the new core leave the accelerator inactive; no paired SDK update is required for those legacy apps.',
                        'Quake2 currently carries a different core hash b4d55466..., not the SM64 retained d3d90e20... baseline. Its OS and loader match. Source compatibility is not a replay or performance qualification of that replacement; a Quake2 core update must have a separate before-hash, backup and focused boot/render/audio check.']),
                evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence})
    (OUT / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text('''Private promotion plan; no publication or device write

The exact source payload is29 openfpga files from normal-build-integration-v2/proposed and3 SM64 renderer files from lod5-recycle-delivery-v1/sources. plan.json lists every source, destination and before/after digest. The read-only guard passes132 production hardware/build inputs and the complete6747-file retained F11 SM64 source tree. Local source/package/core/OS/loader digests are recorded. The SD card is not mounted; no on-card bytes are assumed.

The normal app is the already tested/rebuilt bb41f208... ELF. The packaged F11 diagnostic will be replaced only at the normal delivery path; its separate diagnostic object remains labelled F11. PROFILE=0 PERF_DIAG=0 must be explicit because the current Makefile defaults diagnostics on. Source defaults, SDK and assets are unchanged.

The selected bitstream remains pending root fit89301 assessment. Both corners and actual configured100MHz must be reported: an Fmax above90MHz does not establish timing closure at100MHz. No output RBF or deployment readiness is invented here. Source promotion, local delivery and a later SD trial are separate recorded operations, each guarded and backed up.

Quake2 uses the preserved legacy GPU interface and never starts LOD5; no matched software change is required simply to remain a legacy client. Its source uses the standard allocator below the new guard-region alias, and its CRAM allocator can retain all16MiB because only SM64 software reserves the vertex pool. However, its current core hash differs from the retained SM64 core and there is no matched Quake2 replay for this candidate. Quake2 is excluded from this automatic plan; any later core copy is a separate guarded compatibility check, not an assumed performance result.

No production file, sibling file or SD file was modified by this helper. Only the private plan and current read-only guard snapshot were created.
''')
    (OUT / 'manifest.json').write_text(json.dumps({p.name: sha(p) for p in OUT.iterdir() if p.is_file()}, indent=2) + '\n')
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
