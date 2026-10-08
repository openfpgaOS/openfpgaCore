#!/usr/bin/env python3
"""Keep the complete indexed predicate lazy: unrelated draws never compute it."""
import difflib
import hashlib
import json
from pathlib import Path
import phase7_eligibility_prepare as v1

OUT = v1.OUT.with_name('eligibility-v2')
sha = lambda b: hashlib.sha256(b).hexdigest()


def main():
    original = v1.BASE.read_text()
    candidate, _ = v1.transform(original)
    candidate = candidate.replace('static bool triangle_cpu_indexed_elig;',
                                  'static int triangle_cpu_indexed_elig;')
    candidate = candidate.replace('''        triangle_cpu_indexed_elig = gfx_cpu_indexed_material_eligible(comb, use_fog);
''', '''        /* Negative means the backend accepts this material, but the
         * additional CPU-run constraints have not been needed yet. */
        triangle_cpu_indexed_elig = triangle_vc_elig ? -1 : 0;
''')
    candidate = candidate.replace('''            if (!triangle_cpu_indexed_elig) goto submit;
''', '''            if (triangle_cpu_indexed_elig < 0)
                triangle_cpu_indexed_elig = gfx_cpu_indexed_material_eligible(comb, use_fog);
            if (!triangle_cpu_indexed_elig) goto submit;
''')
    OUT.mkdir(exist_ok=True)
    inputs = json.loads((OUT.parent / 'baseline.json').read_text())['inputs']
    for name in inputs:
        data = (v1.OUT / name).read_bytes() if name != 'gfx_pc.c' else candidate.encode()
        dest = OUT / name
        assert not dest.exists() or dest.read_bytes() == data, dest
        dest.write_bytes(data)
    (OUT / 'eligibility.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='F8/gfx_pc.c', tofile='eligibility-v2/gfx_pc.c')))
    record = dict(parent_sha256=sha(original.encode()), candidate_sha256=sha(candidate.encode()),
        recipe_sha256=sha(Path(__file__).read_bytes()),
        changes='Same complete predicate as v1, evaluated lazily only on the first surviving CPU indexed-run attempt per material.',
        qualified=False)
    (OUT / 'eligibility-source.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
