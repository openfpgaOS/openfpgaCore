#!/usr/bin/env python3
"""Read-only independent texture owned-address review; no duplicate simulation."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'texture-read-address-v1'
R=P/'texture-read-review-v1'
O=P/'texture-read-address-source-review-v1'
BASE=P/'compact-lod5-core-v11/frozen/src/fpga/common/gpu_tex_cache.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def main():
    for p in (C/'manifest.json',R/'manifest.json'):
        for n,h in read(p)['evidence_sha256'].items():
            assert sha(ROOT/n)==h,n
    prep=read(C/'prepared.json'); result=read(C/'result.json')
    old=BASE.read_text(); new=(C/'gpu_tex_cache.v').read_text()
    a='wire [SET_BITS-1:0] read_set_a  = prime_a ? lat_set  : addr_set_a;\nwire [1:0]          read_word_a = prime_a ? lat_word : addr_word_a;'
    b='// On an enabled read, accept means req_valid=1, while prime_a means\n// FILL_OUT/A/no flush and therefore req_ready=1, req_valid=0. Select the\n// owned address directly; differing idle addresses are never consumed.\nwire [SET_BITS-1:0] read_set_a  = req_valid ? addr_set_a  : lat_set;\nwire [1:0]          read_word_a = req_valid ? addr_word_a : lat_word;'
    assert old.count(a)==1 and old.replace(a,b)==new
    assert sha(BASE)==prep['parent_sha256']
    assert sha(C/'gpu_tex_cache.v')==prep['candidate_sha256']==result['candidate_sha256']
    assert result['passed'] and result['exact_cycle_pixel_pairs']==16
    # These equal state decodes are independently kept for fanout. Their
    # literal equality, rather than a state-reachability assumption, is used.
    for text in ('wire state_fillout_ready  = (state == S_FILL_OUT);',
                 'wire state_fillout_prime  = (state == S_FILL_OUT);',
                 '|| (state_fillout_ready && (lat_port == 1\'b0) && !flush_block);',
                 'wire prime_a = state_fillout_prime && (lat_port == 1\'b0)\n            && !flush_block\n            && !(req_valid && req_ready);',
                 'wire                read_en_a   = (req_valid && req_ready) || prime_a;',
                 'if (read_en_a) begin',
                 '? ((state == S_INIT) ? init_counter : lat_set) : read_set_a;',
                 '? {lat_set, fill_beat} : {read_set_a, read_word_a};'):
        assert text in new,text
    literal=read(C/'literal/result.json')
    assert literal['passed'] and literal['counts']['checks']==131072 and literal['counts']['negative']>0
    protocols=[]
    for bits in (4,10,11):
        q=read(C/f'protocol-{bits}/result.json')
        assert q['passed'] and q['set_bits']==bits and q['counts']['accept_fill']==8
        assert q['outputs_including_invalid_payloads_exact']
        assert q['candidate_sha256']==sha(C/'gpu_tex_cache.v') and q['reference_sha256']==sha(BASE)
        protocols.append(q)
    gpu=read(R/'manifest.json')
    assert gpu['passed'] and gpu['exact_cycle_pixel_pairs']==16 and gpu['added_registers']==gpu['added_cycles']==0
    assert gpu['candidate_sha256']==sha(C/'gpu_tex_cache.v')
    O.mkdir(exist_ok=False)
    review=dict(passed=True,source_review_only=True,no_duplicate_simulation=True,
        candidate_sha256=sha(C/'gpu_tex_cache.v'),exact_two_mux_assignments_only=True,
        all_enabled_ram_addresses_unchanged=True,all_enables_writes_states_and_port_b_unchanged=True,
        reasoning=[
          'When accept is true, req_valid is true and prime_a is false. Both expressions select the incoming request address.',
          'When prime_a is true, the identical FILL_OUT state decodes, port A and !flush imply req_ready. The negated accept term therefore proves req_valid is false; both expressions select the latched fill address.',
          'If neither case holds, read_en_a is false and the read-result registers hold. Different unowned addresses are not consumed. Unchanged write-priority muxes select initialization/fill addresses for all writes.',
          'The Boolean ownership proof is independent of legal SET_BITS address width. Complete cache tests cover 4,10,11; the full selected GPU tests cover its actual cache configuration. No four-state/X equivalence or unsupported parameter claim.',
          'Synthesis can remove the hit/ready/prime dependency from the port-A address select without changing memory enable or topology. Mapping and routed timing remain separate gates.'
        ],literal_checks=literal['counts'],protocol_results=protocols,
        exact_gpu_cycle_pixel_pairs=16,registers_added=0,cycles_added=0,
        map_or_fit_performed=False,deployment_ready=False)
    (O/'review.json').write_text(json.dumps(review,indent=2)+'\n')
    (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
    refs=[BASE,C/'gpu_tex_cache.v',C/'prepared.json',C/'manifest.json',C/'result.json',
          C/'literal/result.json',R/'manifest.json',R/'REPORT.txt',Path(__file__)]
    refs += [C/f'protocol-{n}/result.json' for n in (4,10,11)]
    manifest=dict(files={p.name:sha(p) for p in O.iterdir() if p.is_file()},
                  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in refs})
    (O/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(sha(O/'manifest.json'))

if __name__=='__main__':main()
