#!/usr/bin/env python3
"""Keep proven storage schedule; spell bank mux as an unpacked word array."""
from pathlib import Path
import hashlib,json,difflib,shutil
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';B=P/'render-cache-storage-v1';O=P/'render-cache-storage-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert sha(B/'gpu_color_depth_cache.sv')=='e72520a045032fef7b4127bed9e02111c4592f958b1fe15fddb42dfdd2ec11a7'
 old=(B/'gpu_color_depth_cache.sv').read_text();s=old
 for a,b in [('wire [BANKS*40-1:0] bank_q;','wire [39:0] bank_q [0:BANKS-1];'),('assign bank_q[b*40+:40]=q;','assign bank_q[b]=q;'),('assign data_q=bank_q[read_bank*40+:40];','assign data_q=bank_q[read_bank];')]:
  assert s.count(a)==1;a0=s;s=s.replace(a,b,1)
 O.mkdir(exist_ok=False);(O/'gpu_color_depth_cache.sv').write_text(s);shutil.copy2(B/'baseline.sv',O/'baseline.sv');shutil.copy2(__file__,O/'recipe.py')
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff((B/'baseline.sv').read_text().splitlines(True),s.splitlines(True),fromfile='parent/cache',tofile='storage-v2/cache')))
 (O/'v1-to-v2.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile='storage-v1/cache',tofile='storage-v2/cache')))
 (O/'prepared.json').write_text(json.dumps(dict(source_sha256=sha(O/'gpu_color_depth_cache.sv'),baseline_sha256=sha(O/'baseline.sv'),v1_source_sha256=sha(B/'gpu_color_depth_cache.sv'),v1_manifest_sha256=sha(B/'manifest.json'),recipe_sha256=sha(Path(__file__)),change='Only packed flat mux spelling becomes unpacked word array; no explicit index multiply',extra_transaction_cycles=0,production_modified=False,quartus_invoked=False,deployment_ready=False),indent=2)+'\n')
 print(sha(O/'gpu_color_depth_cache.sv'))
if __name__=='__main__':main()
