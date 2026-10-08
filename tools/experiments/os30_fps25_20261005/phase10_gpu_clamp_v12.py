#!/usr/bin/env python3
"""Exact reciprocal shrink comparison without serial absolute-value addition."""
import difflib
import json
from pathlib import Path
from phase10_experiment import CAMP, sha, write

P = CAMP / 'phase10'
BASE = P / 'gpu-paths-v11/gpu_core.v'
OUT = P / 'gpu-clamp-v12'


def rep(t, a, b):
    assert t.count(a) == 1, (a, t.count(a))
    return t.replace(a, b)


def main():
    assert sha(BASE) == '7e47d69ea809961251b675e860ede887458cde7b583202ad273165df301ed9a6'
    OUT.mkdir(exist_ok=False)
    old = t = BASE.read_text()
    t = rep(t, 'reg        [31:0] pss_zinv_abs_na_r;', '''// Store floor(abs(old_zi)/4)-1 modulo2^30. Bit29 is set only
// for a zero threshold; all nonzero thresholds are at most2^29.
reg        [29:0] pss_zinv_shrink_limit_r;
wire [29:0] pss_zinv_limit_base = sp_zinv[31]
    ? ~sp_zinv[31:2] : sp_zinv[31:2];
wire pss_zinv_limit_decrement = !sp_zinv[31] || (|sp_zinv[1:0]);
wire [29:0] pss_zinv_shrink_limit = pss_zinv_limit_base
    - {29'd0, pss_zinv_limit_decrement};
// Ones-complement magnitude is abs(new_zi)-sign(new_zi), so the
// negative case uses strict comparison and the positive case allows equality.
// This removes a32-bit negate from the late clamp comparator input.
wire [31:0] pss_zinv_ones_magnitude = sp_zinv ^ {32{sp_zinv[31]}};
wire pss_zinv_shrunk = !pss_zinv_shrink_limit_r[29]
    && !(|pss_zinv_ones_magnitude[31:29])
    && ((pss_zinv_ones_magnitude[28:0] < pss_zinv_shrink_limit_r[28:0])
        || (!sp_zinv[31]
            && pss_zinv_ones_magnitude[28:0] == pss_zinv_shrink_limit_r[28:0]));''')
    t = rep(t, 'pss_zinv_abs_na_r <= 0;', 'pss_zinv_shrink_limit_r <= 0;')
    t = rep(t, 'pss_zinv_abs_na_r <= persp_zinv_abs_na;',
            'pss_zinv_shrink_limit_r <= pss_zinv_shrink_limit;')
    t = rep(t, '(pss_zinv_adv_abs_r == 32\'d0)', '(pss_zinv_adv_r == 32\'d0)')
    t = rep(t, '(pss_zinv_adv_abs_r < (pss_zinv_abs_na_r >> 2))', 'pss_zinv_shrunk')
    assert 'pss_zinv_abs_na_r' not in t
    (OUT / 'gpu_core.v').write_text(t)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), t.splitlines(True),
        fromfile='gpu-paths-v11/gpu_core.v', tofile='gpu-clamp-v12/gpu_core.v')))
    write(OUT / 'prepared.json', dict(parent_sha256=sha(BASE),
        candidate_sha256=sha(OUT / 'gpu_core.v'), helper_sha256=sha(Path(__file__)),
        scope='Exact clamp shrink comparison; same existing register lifetime, '
              '32 threshold bits become30. No states or cycles added. '
              'Q29 zero/sign rule and every other absolute-value consumer unchanged.',
        deployment_ready=False))
    print(sha(OUT / 'gpu_core.v'))


if __name__ == '__main__':
    main()
