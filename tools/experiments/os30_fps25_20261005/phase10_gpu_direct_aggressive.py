#!/usr/bin/env python3
"""One aggressive-area follow-up after the balanced-area fit misses750 ALMs."""
import shutil,sys
from pathlib import Path
import phase10_gpu_direct_area as area

area.OUT=area.P/'gpu-direct-core-aggressive-v1'
area.SETTINGS=area.SETTINGS.replace('OPTIMIZATION_MODE BALANCED',
                                  'OPTIMIZATION_MODE "AGGRESSIVE AREA"')
if __name__=='__main__':
    if sys.argv[1:]==['prepare']:
        area.prepare()
        shutil.copy2(Path(__file__),area.OUT/'aggressive-recipe.py')
        area.fit.save(area.OUT/'aggressive-setting.json',dict(
            recipe_sha256=area.fit.sha(Path(__file__)),
            compared_with='gpu-direct-core-area-v1',
            only_setting_difference='OPTIMIZATION_MODE AGGRESSIVE AREA instead of BALANCED',
            private_only=True,seed=2,seed_sweep=False))
    elif sys.argv[1:]==['fit']:
        area.fit.OUT=area.OUT;area.fit.fitrun()
    else:raise SystemExit('prepare|fit')
