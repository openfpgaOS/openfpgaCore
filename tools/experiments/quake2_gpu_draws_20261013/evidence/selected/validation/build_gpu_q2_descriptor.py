#!/usr/bin/env python3
"""Build descriptor-compacted, Quake2-only no-combine GPU with exact QSF macros."""
from pathlib import Path
import hashlib,json,re,shutil,subprocess

R=Path(__file__).resolve().parent
C=R.parent/'rtl-quake2-compact-descriptor-candidate'
B=R/'paced-gpu/particles-compact-v1'
D=R/'paced-gpu/particles-q2-descriptor-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert not D.exists()
D.mkdir();shutil.copytree(B/'rtl',D/'rtl')
for src,dst in [('src/fpga/common/gpu_geometry_worker.v','common/gpu_geometry_worker.v'),
                ('src/fpga/common/os30/gpu_core.v','common/gpu_core.v')]:
    shutil.copy2(C/src,D/'rtl'/dst)
shutil.copy2(C/'src/fpga/targets/pocket/ap_core.qsf',D/'candidate.qsf')
assert sha(D/'rtl/common/gpu_geometry_worker.v')=='164d5ce2eeeb478eb7504660b8a4afef4d8f6a16cf75372ba5eff26917305892'
assert sha(D/'rtl/common/gpu_core.v')=='452b36860161c1d3c73c7cdaaf2d1d3d5006096027169f101273a297d3e2ab43'
assert sha(D/'candidate.qsf')=='3fc898b5110f1a47c73c93ddbfd903f46274de2ea1863328bffd2fb0dd76b4e8'
macros=set(re.findall(r'^set_global_assignment -name VERILOG_MACRO (\S+)',(D/'candidate.qsf').read_text(),re.M))
old=json.loads((B/'build-command.json').read_text())
command=[str(x).replace(str(B.resolve()),str(D.resolve())) for x in old if not x.startswith('+define+')]
command[1:1]=['+define+'+x for x in sorted(macros)]
assert command.count('-GINCLUDE_COMBINE=1')==1
command[command.index('-GINCLUDE_COMBINE=1')]='-GINCLUDE_COMBINE=0'
assert {x[len('+define+'):] for x in command if x.startswith('+define+')}==macros
assert 'INCLUDE_COMBINE' not in macros
for parameter in ['-GINCLUDE_DIRECT_COLOR=1','-GINCLUDE_DEPTH=1','-GINCLUDE_PERSPECTIVE=1','-GINCLUDE_XFORM_RGB=1','-GGPU_CB_READ_WINDOW=4']:
    assert parameter in command
audit=dict(qsf_sha256=sha(D/'candidate.qsf'),macros_exact=True,macros=sorted(macros),
    combine_parameter=0,other_parameters_unchanged=[x for x in command if x.startswith('-G') and not x.startswith('-GINCLUDE_COMBINE=')],
    scope='Full QSF macro set matches; wrapper parameter INCLUDE_COMBINE explicitly zero. Direct color, RGB modulation, depth, blending and CB4 remain enabled.')
(D/'configuration-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
(D/'build-command.json').write_text(json.dumps(command,indent=2)+'\n')
(D/'source-pins.json').write_text(json.dumps({str(p.relative_to(D)):sha(p) for p in (D/'rtl').rglob('*') if p.is_file()},indent=2)+'\n')
with (D/'build.log').open('w')as log:
    result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
assert result.returncode==0
(D/'build-result.json').write_text(json.dumps(dict(passed=True,executable_sha256=sha(D/'obj/Vtb_gpu'),configuration=audit),indent=2)+'\n')
print('Built',D.name,sha(D/'obj/Vtb_gpu'))
