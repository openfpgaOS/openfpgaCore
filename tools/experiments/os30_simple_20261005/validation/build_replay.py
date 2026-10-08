#!/usr/bin/env python3
"""Freeze/build exact A single-issue core GPU with Pocket SDRAM replay."""
from common import *
import sys
source=(OLD_TOOLS/'build_replay.py').read_text()
old="HERE=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'"
assert source.count(old)==1
source=source.replace(old,"HERE=ROOT/'docs/measurements/os30-simple-20261005/validation'")
source=source.replace("default='replay-selected-dual'","default='replay-single-A'")
source=source.replace("ROOT/'docs/measurements/os30-dual-20261004/fit-rvc-skid-pointer-packer-coord-v1/frozen/src/fpga/common'","ROOT/'docs/measurements/os30-dual-20261004/fit-single128-v1/frozen/src/fpga/common'")
source=source.replace("ROOT/'docs/measurements/os30-dual-20261004/validation/gpu-coordinate-stage-v1/rendering/manifest.json'","HERE/'gpu-A-config.json'")
source=source.replace("report['selected_fit']='fit-rvc-skid-pointer-packer-coord-v1'","report['selected_fit']='fit-single128-v1'")
# The retained builder derives ROOT from its path. Execute with its original
# __file__, while all output/default source paths above are explicitly rebound.
(OUT/'replay-build-expanded.py').write_text(source)
exec(compile(source,str(OLD_TOOLS/'build_replay.py'),'exec'),{'__file__':str(OLD_TOOLS/'build_replay.py'),'__name__':'__main__'})
