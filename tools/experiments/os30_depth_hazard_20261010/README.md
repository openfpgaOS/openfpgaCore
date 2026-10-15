# os30 depth-read hazard experiment

Correct in bounded native simulation; rejected for promotion because it did
not produce a useful frame-time improvement. No Quartus run or deployment.

| Variant | Warm frame cycles | Change from baseline |
| --- | ---: | ---: |
| Unchanged baseline | 4,366,717 | — |
| Existing selective-read feature | 4,358,469 | −0.189% |
| Smaller depth-only hazard tracker | 4,358,613 | −0.186% |
| Tracker plus guarded cache read priority | 4,367,768 | +0.024% |

The tracker lets a depth read pass unrelated queued writes. It stores six-bit
folded line tags for the framebuffer FIFO and uses the existing serialized
render cache to order writes that have already left that FIFO. Hash collisions
only add waits. Local holding registers remain conservative, and color/blend
reads, fences, flips and clears retain their existing drain rules.

The second patch admits a pending read ahead of an unaccepted write only when
both complete bursts occupy different single cache lines. All other cases keep
write priority. It requires registered read lookup and adds no stored state.
Much of the removed barrier time moved into cache-response waits. Improving
the named wait counter did not materially improve the completed frame.

The two patches are independent diffs against the exact cached CPU baseline
listed in `manifest.json`. Apply them to that model's `frozen` source root,
where the paths are `src/fpga/common/...`. They are not production patches or
an FPGA-qualified release. This package contains no ROM, game assets or binaries.

Validation covers full frame/depth/register comparisons and separate actual
cache/queue stress tests: 723,153 queue checks, 477,748 baseline cache checks and
645,637 guarded-priority checks. The latter includes line aliases, partial
masks, crossing and maximum-length bursts, dirty replacement, delayed channels,
and local reset with posted writes. See `REPORT.txt`, `results.json` and the
bound receipts under `evidence` for scope and limitations.

Verify locally:

```sh
python3 tools/experiments/os30_depth_hazard_20261010/verify.py --patches --evidence
```

Reproduce a fresh model without changing the baseline (choose a new output path):

```sh
python3 tools/experiments/os30_depth_hazard_20261010/build_native.py \
  --variant depth --out /tmp/os30-depth-reproduction
```

Use `priority` for both patches, `legacy` for the pre-existing feature, or
`baseline` for unchanged sources. Add `--prepare-only` to verify and stage
sources without compiling. The recipe preserves the baseline's exact compile
arguments except for output paths and the selected feature defines.

`recipes` archives the original build/replay/observer and focused-test scripts.
Their original measurement-tree locations and dependencies are recorded in
the manifest; they require the corresponding external fixtures and locally
available game inputs. They are evidence recipes, not standalone ROM captures.
The validated depth run uses observer-v3; earlier observer attempts are not
used for the reported wait-category conclusions. No sustained hardware FPS,
physical area, timing or broad-scene performance claim is made.
