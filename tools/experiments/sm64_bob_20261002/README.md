# SM64 first-level profiling

Bob-omb Battlefield act 1, using the unchanged application ELF. Generated
artifacts live in `docs/measurements/sm64-bob-20261002`.

```sh
python3 tools/experiments/sm64_bob_20261002/prepare.py
python3 tools/experiments/sm64_bob_20261002/capture.py
python3 tools/experiments/sm64_bob_20261002/capture.py --label verified --vectors 360,720
python3 tools/experiments/sm64_bob_20261002/replay.py
python3 tools/experiments/sm64_bob_20261002/analyze.py
```

Requires the preceding depth-prefetch campaign's frozen qsim and two RTL replay
executables, local sibling SM64 ELF/assets, Python/pyelftools/NumPy/Pillow and GCC.
Capture labels must be fresh; the capture helper refuses to overwrite them.
The first recorded full capture was invoked directly with the same qsim arguments
as `capture.py`. A second run through that helper reproduces all scene/frame rows
and the first/last sampled vectors.

The private qsim selects `LEVEL_BOB` at the normal save initialization call,
skips the act menu, then restores ordinary play with act 1's entrance warp.
It supplies Mario's nonzero pre-warp action because a painting entry normally
follows an already initialized castle session. Each debugger hook removes itself
and resumes the original first instruction. No application instructions are
replaced, and the original game handles the warp, object initialization, physics,
camera, animation and rendering. All recorded gameplay rows are checked for
level 9/area 1/act 1 and a nonzero Mario action.

The model uses one game tick per frame and disables sound. CPU cache/predictor
sizes are adjusted to current os30; its timing remains approximate. GPU commands
and SDRAM updates are replayed through actual os30 GPU/cache/arbiter/SDRAM RTL.
The C raster model does not implement os30 triangles and is not an image oracle.
CPU work and GPU work are measured separately, without actual presentation timing,
CPU submission overlap or ring backpressure. Do not interpret their sum or inverse
as measured game FPS.

No production source, game source, ELF, bitstream or fitter seed is changed.
