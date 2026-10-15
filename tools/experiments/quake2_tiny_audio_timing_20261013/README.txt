Quake2 compact audio: targeted OS30 timing qualification
======================================================

This is a separate timing experiment following quake2_tiny_audio_20261013.
It does not replace that package, alter its completed 30-seed results, or claim
a new full 30-seed sweep/release selection.

Chosen candidate: physical-corrected-cache-mixer
Chosen seed: 1
Worst-corner restricted main-clock Fmax estimate: 90.07 MHz
Configured main clock: 100 MHz
Timing closed at configured 100 MHz: false
Resources: {"ALMs": {"available": 18480, "used": 18202}, "DSPs": {"available": 66, "used": 42}, "LABs": {"available": 1848, "used": 1847}, "M10Ks": {"available": 308, "used": 305}}
Frozen 128-source fingerprint: 9a4e8b063a2ac21c59d7dbfc47335155760666d22b10095dda27559573329a87

The chosen seed passes the existing strictly above 90 MHz estimate gate,
including clean used input/cross-clock setup, non-main setup and non-setup
checks. Raw four-corner reports and exact DDIO topology evidence are included.
An estimated limit above 90 MHz is not a claim of 100 MHz timing closure.

core/os30.rbf_r is the exact chosen, bit-reversed FPGA image. core/source.tar.gz
contains its frozen 128-source snapshot; core/source.patch is the complete
text diff from prior audio source f63613. The saved source seed remains 2;
the chosen fitter seed and all exact commands are recorded in timing/.

app/app.elf is unchanged from the earlier compact-audio package:
10581bdb05e98c87dcd08abb2798549267793920f6c91e1c6be964b0dd9fb636.
Its source archive and clean-build provenance remain in the earlier package.

Dock, PLL and SDC sources are unchanged. Exact changed source files:
  src/fpga/common/gpu_geometry_worker.v
  src/fpga/common/os30/audio_mixer.v
  src/fpga/common/os30/gpu_color_depth_cache.sv
  src/fpga/common/os30/gpu_core.v
The CPU source is unchanged from the prior audio package.

Only the listed RTL and global physical-synthesis settings differ. The GPU
rewrites add no processing cycles: worker lockstep and actual GPU replay
evidence preserve exact frame/depth output, counters, cycles and fences.
The palette shadow is removed only for the already-disabled palette feature.
The mixer predicate rewrite has exact cycle/output equivalence evidence;
no audio format, voice, rate or buffer capacity changed.
Render-cache metadata payload routing also preserves all enabled writes,
protocol behavior, output data and processing cycles in the bundled checks.

Pocket audio, physical Dock behavior and gameplay FPS have not yet been tested
with this image. No FPS improvement is inferred from simulation or Fmax.
The audio MMIO layout and immutable-SFX cache behavior are unchanged from the prior
package; its synthetic audio limitations still apply. Cache CTRL
bit 1 at 0x48000820 defaults off; capability 0x48000834 is SCF1. Streams bypass
the cache and live sample writes require invalidation/immutability rules.

Evidence is a narrow review bundle. Some inherited receipts reference larger
fixtures and captured vectors in the repository or prior package; they are
not represented as a self-contained replay environment here. No commercial
game assets or proprietary Quartus simulation models are included.

Verify all bundled hashes and the selected timing reports:
  python3 verify.py .

To reproduce this chosen seed, use the same Quartus Prime Lite 25.1std compiler
recorded in timing/. Run from this package directory, with at least 20 GiB
available RAM and no competing fitter jobs. Set a NEW empty build directory:

  PACKAGE_DIR="$(pwd)"
  REBUILD_DIR=/tmp/quake2-audio-chosen-seed
  QUARTUS_BIN=/path/to/altera_lite/25.1std/quartus/bin
  mkdir "$REBUILD_DIR"
  tar -xzf core/source.tar.gz -C "$REBUILD_DIR"
  cd "$REBUILD_DIR/src/fpga/targets/pocket"
  "$QUARTUS_BIN/quartus_map" ap_core --write_settings_files=off --parallel=4
  python3 - <<'PY'
from pathlib import Path
p = Path('ap_core.qsf')
s = p.read_text()
old = 'set_global_assignment -name SEED 2\n'
assert s.count(old) == 1
p.write_text(s.replace(old, 'set_global_assignment -name SEED 1\n'))
PY
  "$QUARTUS_BIN/quartus_fit" ap_core --recompile=off --write_settings_files=off --seed=1 --parallel=4
  "$QUARTUS_BIN/quartus_asm" ap_core
  "$QUARTUS_BIN/quartus_sta" ap_core
  "$QUARTUS_BIN/quartus_sta" -t "$PACKAGE_DIR/recipes/report_sweep_timing.tcl" ap_core "$REBUILD_DIR/timing"

Mapping deliberately uses saved seed 2, followed by the chosen seed 1 in
both the copied QSF and fitter command, matching the recorded build. A plain
GUI compile with the saved source would use seed 2 and is not this qualification.
The final timing command can return 3 because configured 100 MHz is not closed;
that status alone neither accepts nor rejects the separate >90 MHz estimate gate.
Reparse fresh reports and reverse each RBF byte only after that gate passes:

  python3 - "$PACKAGE_DIR" "$REBUILD_DIR" <<'PY'
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'recipes'))
import results
measured = results.parse(Path.cwd(), Path(sys.argv[2]) / 'timing')
print(json.dumps(measured, indent=2))
assert measured['eligible_above_90_mhz_estimate']
assert measured['minimum_main_restricted_fmax_mhz'] > 90
reverse = bytes(int(format(i, '08b')[::-1], 2) for i in range(256))
image = Path('output_files/ap_core.rbf').read_bytes().translate(reverse)
Path('output_files/os30.rbf_r').write_bytes(image)
print('Rebuilt image SHA256:', hashlib.sha256(image).hexdigest())
PY

The bundled image and its raw reports remain the qualified artifacts. A new
build must pass its own fresh timing checks; do not infer qualification from
the seed alone or overwrite this bundle with a new build.

manifest.json records the exact file hashes. This export does not deploy to
the Pocket, update another project, change production sources or commit files.
