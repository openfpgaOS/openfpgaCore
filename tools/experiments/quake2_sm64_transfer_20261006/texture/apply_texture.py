#!/usr/bin/env python3
"""Apply the reviewed texture lifecycle patch to a supplied Quake2 candidate."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Expected exactly one texture patch anchor: {old!r}")
    return text.replace(old, new, 1)


def transform(rel, text):
    if rel.endswith("of_texture.h"):
        return replace_once(text,
            "    _of_gpu_fast_tex_upload(off, (const uint32_t *)src, (nbytes + 3u) >> 2);",
            """    uint32_t words = nbytes >> 2;
    if (words)
        _of_gpu_fast_tex_upload(off, (const uint32_t *)src, words);
    /* A 1x1 RGB565 image has only two allocated bytes. Never read beyond
     * the caller's source to round it up to the hardware's word width. */
    if (nbytes & 3u) {
        const uint8_t *tail = (const uint8_t *)src + (words << 2);
        uint32_t last = 0;
        for (uint32_t i = 0; i < (nbytes & 3u); ++i)
            last |= (uint32_t)tail[i] << (i * 8u);
        _of_gpu_fast_tex_upload(off + (words << 2), &last, 1u);
    }""")
    if rel.endswith("of_gl1.c"):
        for line in (
            "\tOFQ2_CacheClean(white, sizeof(white));\n",
            "\t\t\tOFQ2_CacheClean(ct->texels, (size_t)ct->w * (size_t)ct->h * 2);\n",
            "\t\tOFQ2_CacheClean(t->texels, n * 2);\n",
        ):
            text = replace_once(text, line, "")
        return text
    if rel.endswith("of_emit_q2.c"):
        text = replace_once(text, "int\nOFQ2_Init(int fb_stride)", """/* The texture route survives GPU soft reset and shutdown. Normalize it only
 * with an idle GPU and a reported fast-memory tier; SDRAM-only cores need no
 * route-register access. This does not recreate or migrate texture handles. */
static void
ofq2_texture_route_sdram(void)
{
\tif (of_texture_has_fast_mem())
\t{
\t\t_of_gpu_route_fast_tex(0);
\t\t_of_tex_bound = 0;
\t}
}

int
OFQ2_Init(int fb_stride)""")
        text = replace_once(text,
            "\t * (no-op / SDRAM-only when the target has no fast texture tier). */\n\tof_texture_init();\n\tofq2_tex_generation = 1;",
            "\t * (no-op / SDRAM-only when the target has no fast texture tier). */\n\tof_texture_init();\n\tofq2_texture_route_sdram();\n\tofq2_tex_generation = 1;")
        text = replace_once(text,
            "OFQ2_TexInit(void)\n{\n\tif (!ofq2_ready)\n\t\treturn;\n\tof_texture_init();",
            "OFQ2_TexInit(void)\n{\n\tif (!ofq2_ready)\n\t\treturn;\n\t/* Reinitialization invalidates all handles: finish their draws first. */\n\tOFQ2_Finish();\n\tof_texture_init();\n\tofq2_texture_route_sdram();")
        text = replace_once(text,
            "OFQ2_TexReset(void)\n{\n\tif (!ofq2_ready)\n\t\treturn;\n\tof_texture_reset();",
            "OFQ2_TexReset(void)\n{\n\tif (!ofq2_ready)\n\t\treturn;\n\t/* Drain before new uploads reuse offsets referenced by queued draws. */\n\tOFQ2_Finish();\n\tof_texture_reset();")
        return replace_once(text,
            "\t\tOFQ2_Finish();\n\t\tof_gpu_shutdown();",
            "\t\tOFQ2_Finish();\n\t\tofq2_texture_route_sdram();\n\t\tof_gpu_shutdown();")
    raise ValueError(rel)


FILES = ["src/sdk/include/of_texture.h", "src/quake2/openfpga/of_emit_q2.c",
         "src/quake2/openfpga/of_gl1.c"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate_root", type=Path)
    parser.add_argument("--diff-only", action="store_true")
    args = parser.parse_args()
    changes = []
    for rel in FILES:
        path = args.candidate_root / rel
        before = path.read_text()
        after = transform(rel, before)
        changes.append((path, rel, before, after))
    if args.diff_only:
        for _, rel, before, after in changes:
            print("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                              fromfile="a/" + rel, tofile="b/" + rel)), end="")
        return
    for path, _, _, after in changes:
        path.write_text(after)
    print(json.dumps({rel: {"before": hashlib.sha256(before.encode()).hexdigest(),
                            "after": hashlib.sha256(after.encode()).hexdigest()}
                      for _, rel, before, after in changes}, indent=2))


if __name__ == "__main__":
    main()
