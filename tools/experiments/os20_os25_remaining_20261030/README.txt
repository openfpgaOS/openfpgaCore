Remaining OS20 / OS25 optimizations — 2026-10-30

Purpose
Evaluate the remaining small CPU, GPU, audio and RAM-placement opportunities
without reducing cache capacity, removing audio/Dock features or changing the
working OS30 Quake2 and SM64 pairs. Each candidate is compared with a fresh
current-fabric control. Historical fits are provenance, not matched controls.

Retained implementation
OS20 DIV_RADIX=4: fewer integer/FP division iterations, same ISA and accuracy
settings. CPU/IRQ stress improves from 1,630,748 to 1,540,793 cycles; integer
dependent-divide loops drop from approximately 37.2 to 21.2 cycles/op. This is
a synthetic result, not a measured Diablo/ScummVM FPS improvement. See
os20_divider/README.txt and its pinned functional/structural receipts.

The exact OS20 seed 35 fit estimates 95.26 MHz, uses 16,468 ALMs/228 M10Ks, has
setup -0.498 ns and hold +0.042 ns. CPU/GPU/SDRAM stay configured at 100 MHz,
so this does NOT close main-clock setup. Used input/cross-clock setup and all
other checks must pass. Only proven disconnected SDRAM DDR-high endpoints
are excluded from the estimate; routed proof and raw failures remain retained.

OS25 retains the control CPU/GPU at seed 20: 96.87 MHz estimated timing,
setup -0.323 ns, hold +0.078 ns, 15,490 ALMs and 296 M10Ks. The fetch/FPU,
clamp, RAM-placement and audio options did not provide a sufficient benefit
with clean timing. The final clamp-only fit failed a used video hold path.
No optional feature from those rejected trials is enabled in production.

Physical qualification
prepare.py freezes the selected CPU, target/common HDL, ROM, variant settings
and macros before using the existing isolated sweep_target_matrix.py runner.
audit.py uses the retained strict four-corner timing parser and routed-DDIO
proof. Its exit status means reports are valid; eligibility above 90 MHz is NOT
the campaign's selection threshold. Require at least 94 MHz, clean used timing
checks and a useful benefit relative to the matched baseline before promotion.
All trials use four Quartus processors and the unchanged 100 MHz constraints.
These are targeted saved-seed fits (plus audio seed 34), not a full seed sweep.
physical-results.json records measurements, decisions and proof hashes.

Example isolated OS20 reproduction (new output directories required):
  python3 tools/experiments/os20_os25_remaining_20261030/os20_divider/run.py \
    --output build/os20-divider-validation
Use the radix4 CPU emitted by that recipe with:
  python3 tools/experiments/os20_os25_remaining_20261030/prepare.py \
    --variant os20 --cpu <generated-radix4.v> --seeds 35 \
    --output build/os20-radix4-fit
  python3 tools/sweep_target_matrix.py --output build/os20-radix4-fit \
    --resume --jobs 1 --processors 4
  python3 tools/experiments/os20_os25_remaining_20261030/audit.py \
    build/os20-radix4-fit
Quartus 25.1, the installed openfpgaos-quartus container and initialized vendor
submodules are required. No proprietary tool binaries are included.

Other functional recipes
os25_cpu/ covers fetch capture/ready factoring and FPU-match registers, with
complete CPU/IRQ equivalence plus focused stall/collision checks.
os25_clamp/ checks the production GPU macro/parameter set, including dedicated
span multiplication and translucency: 276 normal/277 delayed-memory checks,
exact pixels, acceptance cycles and column performance.
audio/ checks full 32-voice behavior, stream/W1C races, all mixer positions,
write-buffer collision semantics, actual M10K inference and CPU/GPU/scanout/
audio contention. It also records why the compact OS30 mixer and per-bank
tracking cannot simply be copied into OS20. Each recipe documents tools,
inputs and bounded results; no simulator cycle count is labeled a game FPS.

SDK handoff
export_sdk.py stages the exact qualified FPGA outputs and runtime manifest,
then can apply the narrowly scoped update after checking the saved SDK
inventory. It does not regenerate firmware or call broad 'make sdk'. The
current OS payload and loader were verified against the fitted boot ROM.
Inspect the export plan before applying; its hashes identify the actual
fitted CPU and bitstreams, including comment-only generator provenance.
See export_sdk.README.txt for prepare/apply commands. The completed update is
recorded in sdk-update.json, including exact bitstream hashes, preserved
runtime files, active-to-fitted source equivalence and five image-assembly
checks using the installed SDK artifacts. All 12 runtime manifest entries and
the SDK's 10 offline packaging groups pass. No device was programmed.

Raw generated CPUs, fits, full timing reports, simulator objects and integration
receipts are in the ignored local measurement directory:
  docs/measurements/os20-os25-remaining-20261030/
The portable recipes, compact JSON evidence and release/OS20-OS25.txt are the
maintained record. No script in this campaign writes an SD card or publishes.
