SM64 immutable triangle-cache trial

Evidence and private source:
  docs/measurements/sm64-triangle-cache-20261004/
  REPORT.txt, results.json, renderer.patch, published.json

The selected software change caches decoded immutable display-list batches and
model-space face normals. Visibility, material, alpha and matrix state remain
dynamic. The conservative CPU cull stays enabled. Last-load ownership avoids
per-vertex identity lookups; mutable inputs and partial reloads use the original
path. The cache uses about 152 KiB of SDRAM and adds no FPGA resources.

The normal build reduces modeled CPU time by 3.06%; the app with P2 probes by
2.14%. Hardware FPS is unmeasured. The model exercises a scripted first-level
route, real audio preparation and simplified voice lifetimes; it does not model
cycle-accurate CPU/GPU/display overlap. See REPORT.txt for rejected trials.

prepare.py --stage PATH reproduces the exact selected source in a private copy
of the pre-cache tree, guarded by before.json hashes. It does not copy SDK files
or assemble a runtime. The existing SDK Docker build is used for app.elf; target
the ELF explicitly to avoid replacing the worker runtime in a whole-package
assembly. Restoring older source snapshots must update dependency timestamps
(copyfile, not copy2) or explicitly force recompilation.

capture.py LABEL --elf PATH --audio captures 721 frames and GPU replay vectors.
  --no-geometry-cap exercises the ordinary software geometry fallback.
  --panel exercises the frozen P2 display; final heading is PERF P2 CACHE.
replay.py LABEL [...] replays the vectors with the fitted worker RTL and Pocket
SDRAM contention. App pointers move across links, so color/depth bytes are
compared independently of the full-memory hash.

check_boundaries.py compiles actual old/new renderer functions with a capture
backend, under ASan/UBSan. An immutable fixture replaces only the ELF address
range. It checks randomized culling/submission equivalence, table/pool limits,
TRI2 boundaries, slot ownership, deferred attributes, alpha/fog fallback and
GPU command reservation/order. check_visibility.py covers the retained bounds,
input normalization and frustum machinery.

analyze.py verifies the final app hashes, source/test correspondence, gameplay
records, all-frame geometry counters and word counts, 22 color/depth replay
pairs and 77 frozen snapshot records. It produces results.json.

publish.py defaults to a guarded dry run. --apply publishes only renderer
source, the diagnostic title, deployment notes, both app ELFs and the packaged
diagnostic ELF into ../SM64. --verify checks those files, the rollback backups
and unchanged SDK/runtime hashes. Backup:
  ../SM64/.obj/triangle-cache-backup-20261004/

The Pocket card was disconnected during publication. Copy only
  ../SM64/build/pocket/sm64/Assets/sm64/common/sm64.elf
to Assets/sm64/common/sm64.elf on the card. Preserve the installed worker and OS;
the sibling runtime folder contains an older bitstream. No FPGA fit or sweep
was run in this trial. The FPS test remains a required next device measurement.
