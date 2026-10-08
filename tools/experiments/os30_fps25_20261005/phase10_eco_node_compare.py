#!/usr/bin/env python3
"""Read native before/after ATM dumps; canonicalize only named node/port IDs."""
import collections
import hashlib
import json
from pathlib import Path
import re
import zlib

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'eco-v7-local-placement-v1'
OUT = P / 'eco-v7-equivalence-review-v1'
NODE = re.compile(r'^node (\w+) (n\d+) "([^\n]*)"$', re.M)
PORT = re.compile(r'^\t\t([io]\d+) ([^ \n]+)(?: [^\n]*)?$', re.M)
TOKEN = re.compile(r'(?<![A-Za-z0-9_])([nio]\d+)(?![A-Za-z0-9_])')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def load(label):
    locations = {}
    for line in (BASE / (label + '-nodes.tsv')).read_text().splitlines():
        node_id, name, loc = line.split('\t')
        assert node_id not in locations
        locations[node_id] = (name, loc)
    text = zlib.decompress((BASE / (label + '.atm')).read_bytes()[8:]).decode()
    nodes, symbols, portkeys = {}, {}, {}
    for m in NODE.finditer(text):
        typ, nid, short = m.groups()
        name, loc = locations[nid[1:]]
        end = text.index('\n}\n', m.end()) + 3
        block = text[m.start():end]
        assert name not in nodes
        nodes[name] = dict(type=typ, node_id=nid, location=loc, block=block)
        symbols[nid] = ('node', name)
        for port in PORT.finditer(block):
            pid, formal = port.groups()
            key = (pid[0], name, formal)
            assert pid not in symbols and key not in portkeys, key
            symbols[pid] = key
            portkeys[key] = pid
    assert len(nodes) == len(locations) == 56374
    return nodes, symbols, portkeys


def main():
    assert not OUT.exists()
    OUT.mkdir()
    before, bsymbols, bports = load('baseline')
    after, asymbols, aports = load('after')
    assert before.keys() == after.keys()
    inverse = {v:k for k,v in bsymbols.items()}
    assert len(inverse) == len(bsymbols)
    missing = [list(k) for k in aports.keys() - bports.keys()]
    removed = [list(k) for k in bports.keys() - aports.keys()]
    symbols = {k:inverse[v] for k,v in asymbols.items() if v in inverse}
    changed = []
    property_counts = collections.Counter()
    location_changes = []
    id_changes = []
    types = collections.Counter()
    for name, old in before.items():
        new = after[name]
        assert old['type'] == new['type'], name
        types[old['type']] += 1
        if old['location'] != new['location']:
            location_changes.append(dict(name=name, before=old['location'], after=new['location']))
        if old['node_id'] != new['node_id']:
            id_changes.append(dict(name=name, before=old['node_id'], after=new['node_id']))
        # IDs are export handles, not logic. Bind each replacement through exact
        # full node name plus port direction/formal pin; no property is deleted.
        n = TOKEN.sub(lambda m: symbols.get(m[1], m[1]), new['block'])
        n = re.sub(r'(?m)^\tUINT_ID = \d+$', '\tUINT_ID = ' + old['node_id'][1:], n)
        if old['location'] != new['location']:
            n = n.replace('\tLOCATION = ' + new['location'] + '\n',
                          '\tLOCATION = ' + old['location'] + '\n')
        if old['block'] != n:
            # Unordered line multisets are diagnostic only. They are NOT an
            # equivalence proof: LUT maps and port-vector order remain material.
            a = collections.Counter(old['block'].splitlines())
            b = collections.Counter(n.splitlines())
            deleted, added = list((a-b).elements()), list((b-a).elements())
            for line in deleted + added:
                property_counts[line.strip().split(' = ', 1)[0]] += 1
            changed.append(dict(name=name, type=old['type'],
                before_sha256=hashlib.sha256(old['block'].encode()).hexdigest(),
                canonical_after_sha256=hashlib.sha256(n.encode()).hexdigest(),
                deleted_lines=deleted, added_lines=added,
                only_line_order_changed=not deleted and not added))
    # Source/configuration inputs must remain byte exact even when stale timing
    # database/report files have been replaced or deleted by the vendor flow.
    prep = json.loads((BASE / 'prepared.json').read_text())
    checked_inputs = []
    for name, digest in prep['source_db_sha256'].items():
        if Path(name).suffix.lower() in {'.v','.sv','.vhd','.qsf','.qpf','.sdc','.qip','.mif','.hex','.tcl','.bin'} and '/db/' not in name and '/incremental_db/' not in name:
            assert sha(BASE / 'run-job' / name) == digest, name
            checked_inputs.append(name)
    result = dict(
        source_and_constraints_unchanged=True, checked_source_inputs=len(checked_inputs),
        complete_node_names_and_types_equal=True, node_count=len(before),
        node_types=dict(types), baseline_ports=len(bports), after_ports=len(aports),
        additional_ports=missing, removed_ports=removed,
        node_id_changes=id_changes, actual_location_changes=location_changes,
        differing_node_blocks_after_name_based_handle_canonicalization=len(changed),
        remaining_changed_property_names=dict(property_counts),
        full_logical_equivalence_established=not changed and not missing and not removed,
        global_non_node_metadata_reviewed=False,
        limitation='Strict per-node comparison preserves all configuration and port vectors. Remaining differences are reported, not dismissed as metadata; global non-node object references need separate review if qualification remains relevant.',
        deployment_ready=False)
    (OUT / 'node-differences.json').write_text(json.dumps(changed, indent=2)+'\n')
    (OUT / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
    paths = [BASE / (l + ext) for l in ('baseline', 'after') for ext in ('.atm','-nodes.tsv')]
    paths += [BASE/'prepared.json',BASE/'eco-execute.log',BASE/'physical-result.json',Path(__file__),OUT/'node-differences.json',OUT/'result.json']
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        deployment_ready=False,evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in paths}),indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('node_id_changes','remaining_changed_property_names')}))
    print('manifest_sha256',sha(OUT/'manifest.json'))


if __name__ == '__main__':
    main()
