#!/usr/bin/env python3
"""Stage the single saved-seed geometry/prefetch fit and its verified runtime."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
FIT = OUT / 'fit-prefetch-v1'
STAGE = OUT / 'SM64'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
load = lambda p: json.loads(p.read_text())

status = load(FIT / 'results/pocket-os30-s2/status.json')
assert status['valid'] and status['state'] == 'complete'
assert status['worst']['setup'] > -1.303 and status['worst']['hold'] > 0.052
assert load(FIT / 'config.json')['candidates'] == 1
inventory = load(FIT / 'inventory.json')
assert len(inventory) == 1 and inventory[0]['seed'] == 2
accept = load(OUT / 'accept-prefetch-v1/os30/manifest.json')
assert inventory[0]['defs'] == accept['variant']['defs']
frozen = load(FIT / 'frozen-sources.json')
for file, digest in accept['sources'].items():
    rel = str(Path(file).relative_to(ROOT))
    if rel in frozen:
        assert frozen[rel] == digest, rel
boot = 'src/fpga/targets/pocket/firmware.mif'
assert sha(FIT / 'frozen' / boot) == sha(ROOT / 'docs/measurements/sm64-geometry-20261003/fit-v1/frozen' / boot)
osbin = STAGE / 'runtime/pocket/os.bin'
assert sha(osbin) == '054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
assert sha(osbin) == sha(ROOT.parent / 'SM64/runtime/pocket/os.bin')
output = Path(status['directory']) / 'output_files'
rbf, sof = output / 'ap_core.rbf', output / 'ap_core.sof'
assert sha(rbf) == status['hashes']['rbf'] and sha(sof) == status['hashes']['sof']
reverse = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
(STAGE / 'runtime/pocket/os30.rbf_r').write_bytes(rbf.read_bytes().translate(reverse))
shutil.copyfile(sof, STAGE / 'runtime/pocket/ap_core.sof')
assert (STAGE / 'runtime/pocket/os30.rbf_r').read_bytes().translate(reverse) == rbf.read_bytes()
manifest = STAGE / 'runtime/MANIFEST'
rows = []
for line in manifest.read_text().splitlines():
    if line.startswith('# os30 geometry trial'):
        line = '# os30 geometry + depth prefetch; seed 2; 100 MHz configured, 91.04 MHz Fmax; timing open.'
    if line and not line.startswith('#'):
        _, path = line.split()
        if path in ('./pocket/ap_core.sof', './pocket/os30.rbf_r'):
            line = hashlib.md5((STAGE / 'runtime' / path).read_bytes()).hexdigest() + '  ' + path
    rows.append(line)
manifest.write_text('\n'.join(rows) + '\n')
for line in rows:
    if line and not line.startswith('#'):
        digest, path = line.split()
        assert hashlib.md5((STAGE / 'runtime' / path).read_bytes()).hexdigest() == digest, path
record = dict(fit=status, configuration=inventory[0],
              boot_unchanged_sha256=sha(FIT / 'frozen' / boot),
              os_bin_unchanged_sha256=sha(osbin),
              files_sha256={p: sha(STAGE / p) for p in (
                  'runtime/MANIFEST', 'runtime/pocket/ap_core.sof', 'runtime/pocket/os30.rbf_r')})
(OUT / 'runtime-staged.json').write_text(json.dumps(record, indent=2) + '\n')
print('Staged geometry + depth prefetch, seed 2; boot ROM/os.bin pair unchanged; manifest verified.')
