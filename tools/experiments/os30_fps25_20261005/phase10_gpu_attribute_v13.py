#!/usr/bin/env python3
"""Decode the owned derivative attribute in parallel with its existing counter."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP, sha, write

P = CAMP / 'phase10'
BASE = P / 'gpu-clamp-v12/gpu_core.v'
OUT = P / 'gpu-attribute-v13'


def rep(t, a, b):
    assert t.count(a) == 1, (a, t.count(a))
    return t.replace(a, b)


def main():
    assert sha(BASE) == 'e7ddaa402f17a4c621afd8f2277d714a56a6df9d5a5734a090b1286b36797ff2'
    OUT.mkdir(exist_ok=False)
    old = t = BASE.read_text()
    marker = 'reg [3:0]         dv_attr;'
    assert t.count(marker) == 1
    end = t.index('\n', t.index(marker)) + 1
    t = t[:end] + '''// Parallel decode for the owned attribute mux. Initialized with dv_attr,
// then advanced by the same event; every consumed attribute is0..9.
reg [9:0] dv_attr_onehot;
''' + t[end:]
    t = rep(t, "dv_attr <= 4'd0;        // attr 0 = szi",
            "dv_attr <= 4'd0;        // attr 0 = szi\n                            dv_attr_onehot <= 10'b1;")
    t = rep(t, "dv_attr <= dv_attr + 4'd1;",
            "dv_attr <= dv_attr + 4'd1;\n                        dv_attr_onehot <= {dv_attr_onehot[8:0], 1'b0};")
    start = t.index('    case (dv_attr)')
    end = t.index('    deriv_a0 = deriv_attr[dv_ord[0]];', start)
    assert t[start:end].count('endcase') == 1
    lines = ['    // No binary attribute decode precedes the sorted-slot mux.']
    for vertex in range(3):
        v = str(vertex)
        inputs = [f'dv_szi[{v}]', f'dv_tzi[{v}]', f'vt_zi[{v}]',
                  f"{{10'd0, vt_lrow[{v}], 16'd0}}", f"{{11'd0, vt_rrow[{v}], 16'd0}}",
                  f"{{11'd0, vt_brow[{v}], 16'd0}}", f'vt_depth[{v}]',
                  f"{{11'd0, vt_Drrow[{v}], 16'd0}}", f"{{10'd0, vt_Dgrow[{v}], 16'd0}}",
                  f"{{11'd0, vt_Dbrow[{v}], 16'd0}}"]
        lines.append(f'    deriv_attr[{v}] =')
        for i, value in enumerate(inputs):
            lines.append(f'        ({{32{{dv_attr_onehot[{i}]}}}} & {value})' + (';' if i == 9 else ' |'))
    t = t[:start] + '\n'.join(lines) + '\n' + t[end:]
    (OUT / 'gpu_core.v').write_text(t)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), t.splitlines(True),
        fromfile='gpu-clamp-v12/gpu_core.v', tofile='gpu-attribute-v13/gpu_core.v')))
    write(OUT / 'prepared.json', dict(parent_sha256=sha(BASE),
        candidate_sha256=sha(OUT / 'gpu_core.v'), helper_sha256=sha(Path(__file__)),
        scope='Ten parallel decoded attribute FFs; identical counter/update and '
              'owned0..9 mux values. No state or cycle added; invalid/unowned '
              'counter values are outside the equivalence contract.',
        deployment_ready=False))
    print(sha(OUT / 'gpu_core.v'))


if __name__ == '__main__':
    main()
