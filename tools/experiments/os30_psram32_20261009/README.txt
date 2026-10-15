The os30 SDRAM-save / paired-PSRAM experiment is isolated in this worktree:

../../.. / docs/measurements/os30-psram32-20261009/work/core

From /home/alberto/Repos/openfpgaCore, change directory with:
  cd docs/measurements/os30-psram32-20261009/work/core

Use tools/experiments/os30_psram32_20261009 inside that worktree.
The build.py beside this README is an earlier helper copy; it is not a production
build entry point. Experimental RTL, matching firmware, test apps and build
inputs live in the isolated worktree. The original production source is unchanged.

Final test packages and qualification notes will be placed in:
  docs/measurements/os30-psram32-20261009/delivery

Only os30 and its saved fitter seed are used. No full seed sweep is requested.
