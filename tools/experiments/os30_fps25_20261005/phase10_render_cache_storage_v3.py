#!/usr/bin/env python3
"""Freeze only the legacy-compatible genvar declaration spelling."""
from pathlib import Path
import hashlib,json,difflib,shutil
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'render-cache-storage-v2'; O=P/'render-cache-storage-v3'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    assert sha(B/'gpu_color_depth_cache.sv')=='13fa29b4d44202be88ca22fd89ca2b1c901c86049e9e75c828de77a2e651bdb8'
    old=(B/'gpu_color_depth_cache.sv').read_text()
    assert old.count('generate if(DATA_BITS<=11)')==1
    assert old.count('for(genvar b=0;b<BANKS;b=b+1)')==1
    s=old.replace('generate if(DATA_BITS<=11)','genvar b;\ngenerate if(DATA_BITS<=11)',1)
    s=s.replace('for(genvar b=0;b<BANKS;b=b+1)','for(b=0;b<BANKS;b=b+1)',1)
    O.mkdir(exist_ok=False)
    (O/'gpu_color_depth_cache.sv').write_text(s)
    shutil.copy2(B/'baseline.sv',O/'baseline.sv')
    shutil.copy2(__file__,O/'recipe.py')
    (O/'v2-to-v3.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile='storage-v2/cache',tofile='storage-v3/cache')))
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff((B/'baseline.sv').read_text().splitlines(True),s.splitlines(True),fromfile='parent/cache',tofile='storage-v3/cache')))
    (O/'prepared.json').write_text(json.dumps(dict(source_sha256=sha(O/'gpu_color_depth_cache.sv'),baseline_sha256=sha(O/'baseline.sv'),v2_source_sha256=sha(B/'gpu_color_depth_cache.sv'),v2_manifest_sha256=sha(B/'manifest.json'),recipe_sha256=sha(Path(__file__)),change='Only hoist genvar b declaration to module scope; identical elaborated bank loop',extra_transaction_cycles=0,production_modified=False,quartus_invoked=False,deployment_ready=False),indent=2)+'\n')
    print(sha(O/'gpu_color_depth_cache.sv'))
if __name__=='__main__':main()
