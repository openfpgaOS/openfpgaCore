#!/usr/bin/env python3
"""Private v6: share identical span arithmetic write enables across owners."""
import difflib
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'span-lookahead-v5'
OUT = P / 'span-lookahead-v6'


def main():
    original = (BASE / 'gpu_core.v').read_text()
    parent = json.loads((BASE / 'prepared.json').read_text())['source_sha256']
    assert hashlib.sha256(original.encode()).hexdigest() == parent
    text = original
    # The two arithmetic dispatch tasks occur only in the standalone and the
    # lookahead WAIT/CAPTURE arms. Their destination registers have no other
    # writes in these arms. A shared guard preserves edge and NBA ordering.
    launch = 'spanprod_launch_step_mul(spanprod_launch_step);'
    advance = 'spanprod_launch_step<=spanprod_next_calc(spanprod_launch_step);'
    capture = 'spanprod_capture_step;'
    assert text.count(launch) == 4
    assert text.count(advance) == 2
    assert text.count('spanprod_launch_step <= spanprod_next_calc(spanprod_launch_step);') == 2
    assert len(re.findall(r'^\s*spanprod_capture_step;\s*$', text, re.M)) == 2
    for needle in [launch, advance,
                   'spanprod_launch_step <= spanprod_next_calc(spanprod_launch_step);']:
        text = text.replace(needle, '')
    text = re.sub(r'(?m)^([ \t]*)spanprod_capture_step;', r'\1', text)
    block = '''
        // Both span schedulers use the same staging registers and arithmetic.
        // One enable for each task avoids expressing identical wide register
        // writes under separate main-FSM and nested fragment-FSM priority cones.
        if (state == S_SPANPROD_MUL_WAIT || state == S_SPANPROD_CAPTURE
`ifdef INCLUDE_SPAN_LOOKAHEAD
            || (state == S_FRAG_PIPE && (span_la == LA_WAIT || span_la == LA_CAPTURE))
`endif
            ) begin
            spanprod_launch_step_mul(spanprod_launch_step);
            spanprod_launch_step <= spanprod_next_calc(spanprod_launch_step);
        end
        if (spanprod_capture_phase)
            spanprod_capture_step;

'''
    marker = '        case (state)\n'
    assert text.count(marker) == 1
    text = text.replace(marker, block + marker)
    OUT.mkdir(exist_ok=False)
    (OUT / 'gpu_core.v').write_text(text)
    (OUT / 'recipe.py').write_bytes(Path(__file__).read_bytes())
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), text.splitlines(True),
        fromfile='v5/gpu_core.v', tofile='v6/gpu_core.v')))
    (OUT / 'prepared.json').write_text(json.dumps(dict(
        private_only=True, parent_sha256=parent,
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        recipe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        functional_proof=False, fitted=False, deployed=False,
        purpose='Factor identical span arithmetic dispatch; unchanged scheduling and registers.'
    ), indent=2) + '\n')
    print(OUT)


if __name__ == '__main__':
    main()
