#!/usr/bin/env python3
"""Compose the two qualified, disjoint framebuffer queue edits privately."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
INPUTS={
 'base':('validation/gpu-count-nonzero/gpu_core.v','16059ac4b79bc6436885646e0cf6d88878bae56549dc22db1836f9a28b19dae9'),
 'single_read':('fbwq-single-read-v1/gpu_core.v','4ab7ecda7d6ab8c751a89c629d978e406ba0fbdda380e23dd90004f6d8c27544'),
 'reset_removed':('fbwq-payload-reset-v1/gpu_core.v','2df16811df7bf380434977a45fa44a433e68f11c111a28ca072c31488ffce4b6')}

def sha(text):return hashlib.sha256(text.encode()).hexdigest()

def drop_reset(source):
    result=source
    for name,value in [('addr',"{GPU_ADDR_W{1'b0}}"),('data',"32'd0"),('strb',"4'd0")]:
        pattern=r'^ +fbwq_req_'+name+r' <= '+re.escape(value)+r';\n'
        matches=re.findall(pattern,source,re.M)
        assert len(matches)==2,(name,len(matches))
        for statement in matches:result=result.replace(statement,'',1)
        assert re.findall(r'\bfbwq_req_'+name+r'\s*<=\s*([^;]+);',result)==['fbwq_push_'+name]
    return result

def combine_read(source):
    stmt="                m_wr_awaddr  <= {{(32-GPU_ADDR_W){1'b0}}, fbwq_addr[fbwq_rd_ptr]};\n"
    assert source.count(stmt)==2
    assert len(re.findall(r'\bm_wr_awaddr\s*<=',source))==2
    first="            if (fbwq_start_now) begin\n                m_wr_awvalid <= 1'b1;\n"+stmt
    assert source.count(first)==1
    second=re.search(r'            if \(fbwq_aw_ahead_issue\) begin\n.*?\n            end',source,re.S)[0]
    assert second.count(stmt)==1 and 'if (' not in second.split('begin\n',1)[1]
    replacement=('            // Both AW launches read the same queue head at this edge.\n'
                 '            // One capture expression avoids duplicating the MLAB read port.\n'
                 '            if (fbwq_start_now || fbwq_aw_ahead_issue)\n'+stmt)
    anchor='            if (fbwq_start_now) begin\n'
    assert source.count(anchor)==1
    result=source.replace(stmt,'').replace(anchor,replacement+'\n'+anchor,1)
    restored=result.replace(replacement+'\n','',1).replace(first.replace(stmt,''),first,1).replace(second.replace(stmt,''),second,1)
    assert restored==source
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ART/'validation/gpu-fbwq-combined-v1')
    out=parser.parse_args().output.resolve()
    source={}
    for label,(path,digest) in INPUTS.items():
        source[label]=(ART/path).read_text();assert sha(source[label])==digest,label
    assert drop_reset(source['base'])==source['reset_removed']
    assert combine_read(source['base'])==source['single_read']
    a=drop_reset(source['single_read']);b=combine_read(source['reset_removed'])
    assert a==b,'The guarded edits must commute exactly.'
    # Neither edit changes the other edit's signals, validity guards or writes.
    for name in ['fbwq_req_valid','zw_snoop_pending','cbw_snoop_pending']:
        extract=lambda s:re.findall(r'\b'+name+r'\s*<=\s*[^;]+;',s)
        assert extract(source['base'])==extract(a),name
    out.mkdir(parents=True,exist_ok=False)
    (out/'gpu_core.v').write_text(a)
    shutil.copy2(Path(__file__),out/'combine-snapshot.py')
    for label,text in source.items():(out/(label+'-gpu_core.v')).write_text(text)
    for label in ['base','single_read','reset_removed']:
        (out/(label+'-to-combined.patch')).write_text(''.join(difflib.unified_diff(
            source[label].splitlines(True),a.splitlines(True),fromfile=label+'.v',tofile='combined.v')))
    manifest=dict(candidate_sha256=sha(a),inputs={label:dict(path=path,sha256=digest) for label,(path,digest) in INPUTS.items()},
        script_sha256=sha(Path(__file__).read_text()),qualified_edits_reproduced_exactly=True,
        both_composition_orders_byte_identical=True,valid_and_delayed_snoop_assignments_unchanged=True,
        payload_reset_statements_removed=6,payload_bits=62,aw_address_read_expressions=1,
        added_registers=0,added_pipeline_cycles=0,whole_gpu_validation='pending')
    (out/'composition.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
