#!/usr/bin/env python3
"""Keep immutable-batch plans without direct-map collision rebuilds."""
import difflib
import hashlib
import json
from pathlib import Path

import prepare_batch_position_plan as v1

OUT = v1.MEASUREMENTS / 'batch-position-plan-v2'
OLD_LOOKUP = '''    const unsigned hash = ((address >> 3) ^ (count * 40503u)) & 255u;
    struct BatchPositionPlan *plan = &batch_position_plans[hash];
    if (plan->source == address && plan->count == count)
        return plan;

    uint32_t keys[32][3];'''
NEW_LOOKUP = '''    const uint32_t key = (uint32_t)(address >> 3) ^
        (uint32_t)(address >> 17) ^ ((uint32_t)count * 40503u);
    const unsigned hash = (key * 2654435761u) >> 23;
    unsigned index = hash, tries = 0;
    struct BatchPositionPlan *plan;
    for (; tries < 512; ++tries, index = (index + 1) & 511u) {
        plan = &batch_position_plans[index];
        if (!plan->source)
            break;
        if (plan->source == address && plan->count == count)
            return plan->reuse_mask ? plan : NULL;
    }
    if (tries == 512) {
        /* No tombstones: clear occupancy only when every slot is occupied. */
        for (unsigned i = 0; i < 512; ++i)
            batch_position_plans[i].source = 0;
        plan = &batch_position_plans[hash];
    }

    plan->reuse_mask = 0;
    uint32_t keys[32][3];'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(source):
    first = v1.transform(source)
    helper = v1.HELPER
    changes = [
        ('    size_t count;\n', '    size_t count;\n    uint32_t reuse_mask;\n'),
        ('batch_position_plans[256]', 'batch_position_plans[512]'),
        (OLD_LOOKUP, NEW_LOOKUP),
        ('                plan->previous[i] = (uint8_t)(j + 1);\n',
         '                plan->previous[i] = (uint8_t)(j + 1);\n'
         '                plan->reuse_mask |= 1u << i;\n'),
        ('    plan->count = count;\n    return plan;\n',
         '    plan->count = count;\n    return plan->reuse_mask ? plan : NULL;\n'),
    ]
    for old, new in changes:
        assert helper.count(old) == 1
        helper = helper.replace(old, new)
    assert first.count(v1.HELPER) == 1
    candidate = first.replace(v1.HELPER, helper)
    restored_helper = helper
    for old, new in reversed(changes):
        assert restored_helper.count(new) == 1
        restored_helper = restored_helper.replace(new, old)
    assert restored_helper == v1.HELPER
    assert candidate.replace(helper, v1.HELPER) == first
    return candidate


def main():
    original = v1.BASE.read_text()
    candidate = transform(original)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'gfx_pc.c'
    assert not path.exists() or path.read_text() == candidate
    path.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    (OUT / 'prepare_batch_position_plan.py').write_bytes(Path(v1.__file__).read_bytes())
    manifest = {
        'parent_sha256': sha(original.encode()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'v1_recipe_sha256': sha(Path(v1.__file__).read_bytes()),
        'source_review_passed': True,
        'qualified': False,
        'entries': 512,
        'target_plan_bytes': 44,
        'target_total_cache_bytes': 22528,
        'changes_from_v1': [
            'Open addressing with complete source/count comparison and '
            'a mixed address/count hash.',
            'Occupancy is recycled only when all 512 slots are full.',
            'Plans with no repeated positions are retained, but return NULL '
            'to avoid per-vertex plan-byte reads.',
        ],
        'proof': [
            'Lookup checks complete keys before returning a plan; a hash '
            'collision can cause probing but cannot cause a false hit.',
            'There are no removals or tombstones. Stopping at an empty slot '
            'is valid, and all occupancy is cleared together on saturation.',
            'Only helper implementation differs from v1. Exact reverse '
            'substitution restores v1 and all its baseline-source checks.',
            'reuse_mask is cleared before construction and sets bit i only '
            'when previous[i] holds a proven earlier equal position. count<=32 '
            'keeps every unsigned shift within range.',
            'A NULL result for a no-duplicate plan uses original transforms; '
            'plan metadata still records the source so future batches avoid '
            'rebuilding the same no-duplicate descriptor.',
        ],
        'limits': [
            'All v1 immutable-range, same-call geometry reuse and target '
            'floating-point qualification requirements remain.',
            'Probes and cache construction still cost CPU work; runtime '
            'evidence, not static duplicate rates, determines selection.',
            'No builds, shared-stage changes, sibling edits or deployment '
            'are performed by this preparation.',
        ],
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
