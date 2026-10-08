#!/usr/bin/env python3
"""Capture the accepted AW's candidate hot slot before the hit decision."""
import difflib
import hashlib
import json
import shutil
from pathlib import Path
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'render-cache-speculative-read-v1/gpu_color_depth_cache.sv'
O=P/'render-cache-owned-write-slot-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def transform(old):
    start=old.index('            IDLE: begin\n')
    stop=old.index('                end else if(s_arvalid)',start)
    body=old[start:stop]
    a='                else if(s_awvalid) begin\n'
    b='''                else if(s_awvalid) begin
                    // The accepted AW owns this candidate slot. On a miss,
                    // LOOK replaces it before any data/metadata consumer;
                    // bypass states never consume it. Flush still wins above.
                    // Use the owned AW address, avoiding the shared live/
                    // registered lookup mux and hit qualification on slot CE.
                    slot<=(hot0_valid && hot0_line==s_awaddr[ADDR_W-1:LINE_BITS])
                        ? hot0_slot : hot1_slot;
'''
    assert body.count(a)==1
    body=body.replace(a,b)
    a='                            slot<=hot_slot; hits<=hits+1;\n'
    assert body.count(a)==1
    body=body.replace(a,'                            hits<=hits+1;\n')
    return old[:start]+body+old[stop:]


def main():
    assert sha(B)=='a88bf7835c4f8f7fc78328a3d34c5994b1fe881ea724a96b0f5fb214c30c432d'
    old=B.read_text();new=transform(old)
    O.mkdir(exist_ok=False)
    (O/'baseline.sv').write_text(old);(O/'gpu_color_depth_cache.sv').write_text(new)
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='cachea88/gpu_color_depth_cache.sv',tofile='owned-write-slot/gpu_color_depth_cache.sv')))
    shutil.copy2(__file__,O/'recipe.py')
    (O/'prepared.json').write_text(json.dumps(dict(parent_sha256=sha(B),source_sha256=sha(O/'gpu_color_depth_cache.sv'),recipe_sha256=sha(Path(__file__)),added_registers=0,added_cycles=0,production_modified=False,quartus_invoked=False,deployment_ready=False,scope='Move only the accepted IDLE AW slot capture ahead of hit/bypass decisions, selecting with its owned AW address. Flush priority and all other slot writers remain exact. Unowned slot/RAM reads may differ.'),indent=2,sort_keys=True)+'\n')
    print(sha(O/'gpu_color_depth_cache.sv'))


if __name__=='__main__':main()
