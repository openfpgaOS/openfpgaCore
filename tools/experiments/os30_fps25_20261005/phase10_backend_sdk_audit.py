#!/usr/bin/env python3
"""Read-only SDK ownership audit and duplicate-upload count."""
import collections
import json
import struct
from pathlib import Path
from phase10_experiment import CAMP, ROOT, module, sha, write
from phase8_experiment import canonical

P = CAMP / 'phase10'
APP = P / 'load-submit-overlap-v1'
CAP = P / 'load-submit-functional-v1'
OUT = P / 'backend-sdk-audit-v1'


def main():
    OUT.mkdir(exist_ok=False)
    parser = module('backend_audit_vector', CAP / 'vector16.py')
    counts = {}
    evidence = [APP / 'gfx_gpu.c', APP / 'of_gpu.h', APP / 'gfx_pc.c',
                CAP / 'manifest.json', CAP / 'vector16.py',
                P / 'batch-cursor-v1/candidate.patch',
                P / 'batch-cursor-coupled-v1/result.json',
                P / 'batch-reserve-v1/candidate.patch', Path(__file__)]
    for vector in sorted((CAP / 'capture-normal/gpuvec').glob('*.vec')):
        count = collections.Counter()
        pending = {}
        for event in canonical(parser.vector(vector.read_bytes()))[0]:
            if event[:4] != b'CMD1':
                continue
            header, = struct.unpack_from('<I', event, 4)
            opcode = header >> 24
            if opcode == 0x56:
                slot, = struct.unpack_from('<I', event, 8)
                count['slot_uploads'] += 1
                if slot in pending:
                    count['repeated_slot_before_draw'] += 1
                    count['repeated_identical'] += pending[slot] == event
                pending[slot] = event
            else:
                if opcode == 0x54:
                    count['indexed_draws'] += 1
                pending.clear()
        counts[vector.name] = dict(count)
        evidence.append(vector)
    assert sum(x.get('repeated_slot_before_draw', 0) for x in counts.values()) == 0
    write(OUT / 'result.json', dict(
        read_only=True, no_candidate_implemented=True, deployment_ready=False,
        duplicate_counts=counts,
        findings=[
            'The existing batch-cursor experiment already amortizes reserve/claim/commit using the safe known-free bound. Repeating it is not a distinct architecture.',
            'Duplicate dirty corners are required by the current exact packet contract, but occur zero times in the eight selected streams. Removing them cannot improve these frames.',
            'g_vc_kick is shared across calls. Exact kick boundaries are the first 32-g_vc_kick triangles followed by groups of 32, with advisory threshold and sync-token behavior unchanged.',
            'Known ring free is conservative because the sole consumer advances independently and staged words already advance the SDK producer pointer. A local cursor is invalid across reserve/flush/kick/another emitter until its words are committed.',
            'One untested CPU-ring-only simplification is reusing its existing staging buffer after synchronous upload, instead of alternating two buffers. All source words have been read before STATUS/PUBLISH and no external DMA owns the buffer. This halves the used staging footprint from 32 KiB to 16 KiB and removes buffer-selector updates; DMA mode must retain its original alternating buffers.',
            'That simplification has no measured frame-time bound: benefit depends on real cache conflicts and cannot be presented as a substantial gain. No prototype is justified solely by ownership proof.',
            'Direct packet stores into the hardware append port would need a new SDK invariant for uploaded-but-unpublished words and transitions back to staged emitters. It is more than a local reserve cleanup and is not proposed here.',
        ],
        prior_batch_cursor_ms_saved=0.11019625,
        limitations='Eight captured command streams, no new CPU/RTL execution or performance measurement. No source, model, FPGA, production or SD mutation.'))
    (OUT / 'REPORT.txt').write_text(
        'Backend SDK ownership audit\n\n'
        'No distinct >=0.5 ms improvement is established. Prior cursor batching already saved only 0.110196 ms.\n'
        'No repeated dirty-corner slot upload occurs in the eight selected streams.\n'
        'The shared 32-triangle kick counter and commit-before-SDK-call rules remain mandatory.\n\n'
        'A CPU-ring-only reuse of the same completed staging buffer is ownership-safe in principle,\n'
        'and could reduce its active cache footprint from 32 KiB to 16 KiB. It is untested and has\n'
        'no quantified performance benefit. Keep DMA double buffering unchanged.\n\n'
        'No implementation, build or fit was performed for this read-only audit.\n')
    write(OUT / 'manifest.json', dict(read_only=True, deployment_ready=False,
        files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence}))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
