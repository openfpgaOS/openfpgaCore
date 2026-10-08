#!/usr/bin/env python3
import alias_positions_tests as v1
v1.W=v1.P/'work/alias_positions2';v1.O=v1.P/'components/alias-positions-v2'
v1.G=v1.W/'src/quake2';v1.SDK=v1.W/'src/sdk'
original_prepare=v1.prepare
def prepare():
    original_prepare()
    p=v1.O/'render_test.c';s=p.read_text()
    before='ofgl_geometry_cv->value=1;assert(!OFGL_AliasPositionsEnabled());ofgl_geometry_cv->value=0;'
    after='ofgl_geometry_cv->value=1;gpu_geometry=1;assert(!OFGL_AliasPositionsEnabled());gpu_geometry=0;assert(OFGL_AliasPositionsEnabled());ofgl_geometry_cv->value=0;'
    assert s.count(before)==1;p.write_text(s.replace(before,after))
v1.prepare=prepare
if __name__=='__main__':v1.main()
