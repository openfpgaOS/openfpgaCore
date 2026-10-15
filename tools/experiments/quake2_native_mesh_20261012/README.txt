Quake2 native MD2 mesh offload experiment — 2026-10-12

Selected: app-v3 + core-v4. See REPORT.txt and selection.json for final evidence.
The selected pair is deployed to POCKETDEV with sound disabled; readback and
backup checks pass and the card has been safely unmounted.
Main-clock Fmax is 92.27/91.47 MHz; configured 100 MHz setup still fails.
Historical package STATUS fields describe their creation time. The final
selection.json and physical reports are authoritative for this trial.

The GPU now fetches immutable MD2 frame and meshlet data from fast memory,
interpolates/transforms/projects vertices, culls triangles, and feeds its
rasterizer directly. The CPU submits one owned 165-word descriptor per accepted
model, then continues without waiting for that model to finish. Existing
world-v1 batching remains. Clipped or unsupported draws keep their CPU fallback.

Model frames/meshlets are uploaded once and reused. Textures grow upward in
CRAM1 while native geometry grows downward; map registration fences outstanding
work and reclaims only native storage. All mutable per-draw inputs are copied
into the descriptor, so CPU/GPU overlap cannot overwrite a pending job.

The hardware mixer was removed after the pipelined native design exceeded LAB
capacity. The silent experiment also removes the PCM output FIFO, keeps valid
MCLK/LRCK with zero DAC data, and reports FIFO full. Together with the app audio
startup gate, this prevents the unchanged OS from doing silent sample work.
Normal builds and the deployed world-v1 core are not modified.

Portable source:
  app/       final app-v3; package.py verifies or stages native/control source
  core/      historical worker-v1, rejected for timing
  core-v2/   historical worker-v2, mixer-on placement failure
  core-v3/   worker-v2 plus silent audio; rejected for timing
  core-v4/   SELECTED: narrowed EMIT selector, same words/cycles
  core-v5/   pointer-path simplification; cycle-identical, worse physical timing

Qualification evidence is in qualification/. The measurements folder contains
complete local replay and physical evidence with private game inputs kept
outside the source bundles. No game assets or device bitstreams are bundled
with the portable source. Reproduction commands live in each package README.

No full seed sweep was performed. SD deployment is tracked in selection.json
and the deployment package receipt.
