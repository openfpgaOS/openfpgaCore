#!/usr/bin/env python3
"""Private cost screen: shorten matrix-register lifetime in the CPU loader."""
import difflib,json
from phase10_experiment import CAMP,parent_inputs,write,sha
from pathlib import Path

HERE=Path(__file__).resolve().parent
BASE=CAMP/'phase9/checks-state-f11'
OUT=CAMP/'phase10/vertex-split-v1'

def main():
    parent=parent_inputs()
    OUT.mkdir(exist_ok=False)
    for name in parent['inputs']:
        (OUT/name).write_bytes((BASE/name).read_bytes())
    p=OUT/'gfx_pc.c';original=p.read_text();s=original
    start=s.index('    // Hoist the loop-invariant MVP matrix into locals:')
    end=s.index('\nstatic inline struct ColorCombiner *gfx_pick_combiner',start)
    body=s[start:end]
    loop='    for (size_t i = 0; i < n_vertices; i++, dest_index++) {'
    begin=body.index(loop)
    calculations=body[body.index('        const float ox ='):body.index('        short U =')]
    clip_start=body.index('        // trivial clip rejection')
    clip_end=body.index('\n#if GFX_CPU_DEFERRED_FOG',clip_start)
    clip=body[clip_start:clip_end]
    first='''    /* Separate transform/reciprocal work from attributes so the sixteen
     * matrix values need not remain live through memoization and lighting.
     * Private cost probe: native arithmetic and full frame timing still need
     * independent qualification before this can be selected. */
    for (size_t i = 0; i < n_vertices; ++i) {
        const Vtx_t *v = &vertices[i].v;
        struct LoadedVertex *d = &rsp.loaded_vertices[dest_index+i];
'''+calculations+clip+'\n    }\n'
    body=body[:begin]+first+body[begin:]
    # Replace only the original loop's transform, not the new first pass.
    second=body.index(loop,begin+len(first))
    head,tail=body[:second],body[second:]
    assert tail.count(calculations)==1 and tail.count(clip)==1
    tail=tail.replace(calculations,'''        const float w=d->w, z=d->z;

''').replace(clip,'')
    s=s[:start]+head+tail+s[end:]
    p.write_text(s)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='F11/gfx_pc.c',tofile='vertex-split-v1/gfx_pc.c')))
    write(OUT/'preparation.json',dict(private_only=True,production_changed=False,
        parent_elf_sha256=parent['elf_sha256'],recipe_sha256=sha(Path(__file__)),
        scope='CPU loop distribution cost screen; no geometry hardware or deployment. Original full arithmetic expressions retained; native arithmetic equivalence still requires checking.'))
    print(OUT)

if __name__=='__main__':main()
