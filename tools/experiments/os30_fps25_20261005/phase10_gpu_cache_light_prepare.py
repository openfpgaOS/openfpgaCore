#!/usr/bin/env python3
"""Freeze two disjoint, zero-cycle private GPU/cache timing candidates."""
import difflib
import hashlib
import json
import shutil
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def once(s, old, new):
    assert s.count(old) == 1, (old, s.count(old))
    return s.replace(old, new)


def freeze(label, filename, base, expected, transform, scope):
    assert sha(base) == expected
    old = base.read_text()
    new = transform(old)
    out = P / label
    out.mkdir(exist_ok=False)
    (out / filename).write_text(new)
    (out / ('baseline' + base.suffix)).write_text(old)
    (out / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='parent/' + filename,
        tofile=label + '/' + filename)))
    shutil.copy2(__file__, out / 'recipe.py')
    (out / 'prepared.json').write_text(json.dumps(dict(
        parent=str(base.relative_to(R)), parent_sha256=expected,
        source_sha256=sha(out / filename), recipe_sha256=sha(Path(__file__)),
        added_registers=0, added_cycles=0, scope=scope,
        production_modified=False, quartus_invoked=False, deployment_ready=False,
        pending=['Literal source/protocol proof', 'Matched native replay', 'Map and physical timing']),
        indent=2, sort_keys=True) + '\n')
    print(label, sha(out / filename))


PREFIX = '''// Full unsigned six-bit increment, expressed as independent prefix bits.
// Each output depends on at most six inputs; preserve 63 -> 64 without a
// ripple carry before the color DSP. Raw p1_light remains unchanged for
// the signed combiner and palette consumers.
function [6:0] rgb565_light_scale;
    input [5:0] light;
    begin
        rgb565_light_scale[0] = ~light[0];
        rgb565_light_scale[1] = light[1] ^ light[0];
        rgb565_light_scale[2] = light[2] ^ (&light[1:0]);
        rgb565_light_scale[3] = light[3] ^ (&light[2:0]);
        rgb565_light_scale[4] = light[4] ^ (&light[3:0]);
        rgb565_light_scale[5] = light[5] ^ (&light[4:0]);
        rgb565_light_scale[6] = &light[5:0];
    end
endfunction

'''


def light(s):
    s = once(s, 'function [15:0] rgb565_modulate;', PREFIX + 'function [15:0] rgb565_modulate;')
    s = once(s, "        scale = {1'b0, light} + 7'd1;", '        scale = rgb565_light_scale(light);')
    return once(s, "        sg = {1'b0, g} + 7'd1;", '        sg = rgb565_light_scale(g);')


def cache(s):
    return once(s, "        R_WORD: if(word_valid==4'hf && left!=1 && same_line)\n",
        '// An invalid word enters FILL_CHECK/FILL_AR, which do not consume\n'
        '        // this speculative read. FILL_AR fetches word zero before FILL_R\n'
        '        // can merge any data. Keep validity in the response/FSM decision,\n'
        '        // but remove the RAM-output feedback from next-word read address.\n'
        '        R_WORD: if(left!=1 && same_line)\n')


if __name__ == '__main__':
    freeze('render-cache-speculative-read-v1', 'gpu_color_depth_cache.sv',
        P / 'render-cache-storage-v3/gpu_color_depth_cache.sv',
        '25343436b682e7875a170c144b32ee06803c53177bf0c999626043587741ee3a', cache,
        'Remove only word_valid from R_WORD speculative read-ahead address; state, responses, writes, RAM organization and every sequential assignment are unchanged. Unowned RAM output can differ before refill.')
    freeze('gpu-light-prefix-v1', 'gpu_core.v', P / 'gpu-control-owner-v1/gpu_core.v',
        'e75ac93db5483ee8525c44ef1aeed4e610f8deecf8fa42765997367873dd4f15', light,
        'Replace only the shared six-bit light increment in modulate/Gouraud-green with explicit prefix equations. Five-bit R/B increments, all raw values, products, truncations and sequential logic stay identical.')
