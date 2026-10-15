# Quake2 GPU particle experiment — native6

Ready for a device trial; not deployed or measured on Pocket. The app submits compact particle records for GPU projection while the CPU continues. Existing world/MD2 paths, 320x240 resolution, particle count/order, alpha and depth-write states are preserved. The canonical fixed-point particle reference and actual GPU outputs agree exactly.

Eight paced GPU samples model 4.4–34.7% whole-frame throughput gains; the three busy samples together improve 22.89%. These are simulation results, not measured Pocket FPS, and not every frame gains 20%. The scalar CPU model does not fully couple CPU caches, video and audio traffic.

Appearance differs from the old floating-point path at localized particle texture samples: at most 252/76800 pixels (0.328125%) in measured frames and at most 1/16 pixel in projected vertices. The reviewed worst-frame panel and exact baseline/reference dump pins are in `evidence/`. Depth memory agrees; particles do not write depth, so this is not a claim of identical depth-test inputs.

Seed 2 fits at 17948/18480 ALMs, 296 M10Ks and 32 DSPs. Slow-corner Fmax estimates are 90.74 / 90.13 MHz (85 / 0 °C). Configured clock remains 100 MHz; worst setup slack is -1.095 ns. This does not close timing at 100 MHz. Positive hold/recovery/removal/pulse checks and both slow estimates above 90 MHz satisfy the selected experimental trial gate. Historical validation receipts and the historical report describe preceding equivalent RTL or pending physical work; `selection.json` and `evidence/validation.json` bind the selected worker/GPU/QSF pins to the fitted artifact and records the completed trial checks.

This core is Quake2-only: the unused additive color combiner was removed to fit. RGB modulation, lightmapped world/alias rendering, translucency and particle blending remain. Audio remains intentionally disabled. Do not use this image as an SM64 replacement.

Run `python3 -B verify.py` to verify this portable source bundle. Add `--deployment /path/to/deployment-native-v6` to verify the separate deployment payloads. `app/package.py` stages complete app sources; `core/package.py` stages exact fitted RTL. Their build helpers and instructions are inside each package. The app clean rebuild matched the selected normal ELF, and packaged worker tests were freshly compiled and replayed exactly.

This bundle contains complete app/core source archives plus selected validation summaries, logs, vector/dump hashes, feature audits, physical reports and the appearance review. It does not contain the full raw capture/replay vector archives needed to rerun every frame. Keep this source bundle immutable; installation backups and receipts are written only to the separately maintained `deployment-native-v6` directory. No installer was run during assembly.
