#!/usr/bin/env python3
"""Fix conflicting _start declaration; preserve the failed v1 build."""
import argparse
import difflib
import json
from pathlib import Path
import phase10_position_id as v1

OUT=v1.CAMP/'phase10/position-id-v2'

def prepare():
    source=v1.OUT
    record=json.loads((source/'source.json').read_text())
    OUT.mkdir(exist_ok=False)
    for n,digest in record['source_sha256'].items():
        assert v1.sha(source/n)==digest
        text=(source/n).read_text()
        if n=='gfx_pc.c':
            old='extern const unsigned char _start[], __EH_FRAME_BEGIN__[];'
            assert text.count(old)==1
            text=text.replace(old,'extern const unsigned char __EH_FRAME_BEGIN__[];')
        (OUT/n).write_text(text)
    original=(v1.BASE/'gfx_pc.c').read_text();current=(OUT/'gfx_pc.c').read_text()
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),current.splitlines(True),fromfile='F11/gfx_pc.c',tofile='position-id-v2/gfx_pc.c')))
    record.update(source_sha256={n:v1.sha(OUT/n) for n in record['source_sha256']},recipe_sha256=v1.sha(Path(__file__)),parent_recipe_sha256=v1.sha(Path(v1.__file__)),failed_parent='position-id-v1: _start already declared function by SDK include')
    (OUT/'source.json').write_text(json.dumps(record,indent=2)+'\n')
    print(OUT)

def pack():
    v1.OUT=OUT
    v1.pack()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','pack']);a=p.parse_args();globals()[a.action]()
