#!/usr/bin/env python3
"""Independent source/ownership review; reuse sealed author tests without rerunning."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'geometry-clip-write-v1'
O=P/'geometry-clip-write-review-v1'
BASE=P/'compact-lod5-core-v9/frozen/src/fpga/common/geometry_raw_load.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def require(ok, why):
    if not ok:raise RuntimeError(why)

def main():
    m=read(C/'manifest.json')
    for n,h in m['files'].items():require(sha(C/n)==h,'Component drift: '+n)
    for n,h in m['evidence_sha256'].items():require(sha(ROOT/n)==h,'Evidence drift: '+n)
    prep=read(C/'prepared.json');result=read(C/'result.json')
    old=BASE.read_text();new=(C/'geometry_raw_load.v').read_text()
    a='attribute_write_address=0;attribute_write_data=0;attribute_write_bytes=0;'
    b="attribute_write_address=0;attribute_write_data={26'b0,classification};attribute_write_bytes=0;"
    x="attribute_write_data={26'b0,classification};attribute_write_bytes=4'b1111;"
    y="attribute_write_bytes=4'b1111;"
    require(old.count(a)==old.count(x)==1,'Unexpected parent topology')
    require(old.replace(a,b).replace(x,y)==new,'Candidate contains an additional change')
    require(sha(BASE)==prep['parent_source_sha256'],'Wrong parent source')
    require(sha(C/'geometry_raw_load.v')==prep['candidate_sha256']==result['candidate_sha256'],'Candidate binding differs')
    require(new==(C/'frozen/geometry_raw_load.v').read_text(),'Tested candidate differs')
    require(result['passed'] and result['clip_arithmetic_unchanged'] and result['all_owned_RAM_writes_exact'],'Component gate failed')
    proof=read(C/'proof/result.json');native=read(C/'wrapper/result.json')
    require(proof['passed'] and proof['all_control_and_classification_cases']==524288 and proof['checks']==1524288,'Combinational coverage differs')
    require(proof['unowned_data_differences']>0 and proof['negative_missing_lighting_override']>0,'Missing sensitivity control')
    require(native['passed'] and native['exact_parent_cycles_and_outputs'] and native['live_old_new_enabled_write_assertions'],'Wrapper gate failed')
    command=read(C/'wrapper/build-command.json')
    require('--assert' in command and '+define+GEOMETRY_SCAN_CHECKS' in command,'Live assertions disabled')
    instrumented=(C/'wrapper/geometry_raw_load.v').read_text()
    for byte in range(4):
        text=f'if(attribute_write_bytes[{byte}])assert(attribute_write_data[{byte*8+7}:{byte*8}]==reference_attribute_write_data[{byte*8+7}:{byte*8}]);'
        require(text in instrumented,'Missing live owned-byte check')
    require('assert(attribute_write_address==reference_attribute_write_address);' in instrumented and
            'assert(attribute_write_bytes==reference_attribute_write_bytes);' in instrumented,'Missing live RAM ownership check')
    # Exact two-substitution reconstruction establishes that every producer
    # condition, branch priority, complete payload override, address/byte-enable
    # equation, comparator and sequential statement remains unchanged.
    for statement in (
        'attribute_write_data={v_product[31:16],u_product[31:16]};',
        'attribute_write_data={word_data[15:8],word_data[7:0],normal_xy};',
        'attribute_write_data={4{attribute_rgb_data}};'):
        require(new.count(statement)==1,'Lost full-width earlier producer override')
    require('.wren_a(|attribute_write_bytes),.byteena_a(attribute_write_bytes)' in new,'Physical RAM write contract differs')
    require('if(attribute_write_bytes[byte_index])' in new,'Simulation byte-write contract differs')
    O.mkdir(exist_ok=False)
    review=dict(passed=True,source_review_only=True,no_duplicate_simulation=True,
        candidate_sha256=sha(C/'geometry_raw_load.v'),component_manifest_sha256=sha(C/'manifest.json'),
        exact_two_substitutions_only=True,comparators_and_sequential_logic_unchanged=True,
        all_addresses_byte_enables_priorities_unchanged=True,all_enabled_write_payloads_exact=True,
        reasoning=[
            'UV/source-RGBA/lighting higher-priority branches each completely override the changed default with their original 32-bit payload.',
            'The SCAN branch has the identical classification payload through the new default and retains its original address/full byte enables.',
            'All remaining cases retain byte-enable zero; changed idle data cannot alter the physical or simulation RAM.',
            'No register, phase, arithmetic, alias/read ownership, reset, or final write/done edge is changed.'],
        author_combination_checks=proof['checks'],author_native_wrapper=native,
        map_or_fit_performed=False,timing_gain_not_established=True,deployment_ready=False)
    (O/'review.json').write_text(json.dumps(review,indent=2)+'\n')
    (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
    evidence=[BASE,C/'geometry_raw_load.v',C/'manifest.json',C/'prepared.json',C/'result.json',
              C/'proof/result.json',C/'proof/probe.sv',C/'proof/main.cpp',C/'wrapper/result.json',
              C/'wrapper/build-command.json',C/'wrapper/geometry_raw_load.v',Path(__file__)]
    manifest=dict(files={p.name:sha(p) for p in O.iterdir() if p.is_file()},
                  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence})
    (O/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(sha(O/'manifest.json'))

if __name__=='__main__':main()
