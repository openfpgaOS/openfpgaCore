#!/usr/bin/env python3
"""Prepare private rollback bytes for the reviewed local promotion; no writes to destinations."""
from pathlib import Path
import json
import shutil
import stat
import phase10_promotion_plan_v8 as m

ROOT, P, sha, regular = m.ROOT, m.P, m.sha, m.regular
PLAN = P / 'promotion-plan-v8'
OUT = P / 'promotion-rollback-v8'


def main():
    # Snapshot only; no restore/remove routine and no destination writes.
    plan = m.read(PLAN / 'plan.json')
    assert plan['hardware_selection_gate']['configured_clock_mhz'] == 100
    assert plan['promotion_performed'] is False and plan['deployment_performed'] is False
    before = m.read(PLAN / 'retained-before.json')
    for n, digest in before['core_production_sha256'].items():
        assert regular(ROOT / n, ROOT) == digest, n
    for n, digest in before['sm64_source_sha256'].items():
        assert regular(m.SM64 / n, m.SM64) == digest, n
    rows = [dict(row) for row in plan['hardware_source_files'] + plan['sm64_source_files']]
    for kind, destinations in plan['artifact_destinations_after_selection'].items():
        if kind == 'optional_sd':
            continue
        for name in destinations:
            rows.append(dict(destination=name,
                             before_sha256=before['retained_artifacts'][name],
                             after_sha256=plan['tested_app_sha256'] if kind == 'normal_app' else None,
                             selected_bitstream_hash_pending=(kind == 'core')))
    assert len(rows) == 38
    OUT.mkdir(exist_ok=False)
    for row in rows:
        dst = Path(row['destination'])
        anchor = ROOT if dst.is_relative_to(ROOT) else m.SM64
        digest = row['before_sha256']
        if digest is None:
            assert not dst.exists() and not dst.is_symlink(), dst
            row.update(prior_absent=True, rollback_file=None, prior_mode=None)
            continue
        assert regular(dst, anchor) == digest
        relative = ('openfpgaCore' if anchor == ROOT else 'SM64') + '/' + str(dst.relative_to(anchor))
        backup = OUT / 'payload' / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, backup)
        assert sha(backup) == digest == regular(dst, anchor)
        assert stat.S_IMODE(backup.stat().st_mode) == stat.S_IMODE(dst.stat().st_mode)
        row.update(prior_absent=False, rollback_file=str(backup.relative_to(OUT)),
                   prior_mode=stat.S_IMODE(dst.stat().st_mode))
    shutil.copyfile(__file__, OUT / 'recipe.py')
    data = dict(private_only=True, destination_writes=0, sd_writes=0, entries=rows,
                current_guard_snapshot_sha256=sha(PLAN / 'retained-before.json'),
                plan_manifest_sha256=sha(PLAN / 'manifest.json'),
                promotion_requires_fresh_guard_recheck=True,
                rollback_requires_current_hash_equal_promotion_output=True,
                core_output_hashes_pending=True,
                deployment_or_promotion_performed=False)
    (OUT / 'rollback.json').write_text(json.dumps(data, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text(
        'Private local-promotion rollback payload\n\n'
        f'{len(rows)} destinations are inventoried:31 hardware/build files,3 SM64 sources,2 normal/package app paths and2 SM64 core paths. '
        f'{sum(not row["prior_absent"] for row in rows)} existing files have exact byte/mode backups; '
        f'{sum(row["prior_absent"] for row in rows)} new destinations are recorded absent. '
        'Production sources and artifacts were only read. OS, loader, Quake2 and SD are excluded.\n\n'
        'Before any promotion, recheck all original guards and bind the chosen core output hashes. '
        'Rollback may restore a saved file or remove a newly created file only when its current bytes still equal that transaction output; '
        'intervening user edits require stopping, not force restoration. No mutation routine is supplied here.\n')
    (OUT / 'manifest.json').write_text(json.dumps({str(p.relative_to(OUT)): sha(p)
        for p in OUT.rglob('*') if p.is_file()}, indent=2) + '\n')
    print(sha(OUT / 'manifest.json'))
    print('destinations', len(rows), 'backups', sum(not row['prior_absent'] for row in rows))


if __name__ == '__main__':
    main()
