#!/usr/bin/env python3
"""Exercise the TRI1 scanner's shipping GBI2 word layout in the existing oracle."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
DEST=CAMP/'phase9/tri1-scan-v1/tests-gbi2'
HELPER=Path(__file__).with_name('phase7_eligibility_tests.py')
source=HELPER.read_text()
old="assert results['candidate']['rejection_evaluations'] < results['baseline']['rejection_evaluations']"
assert source.count(old)==1
source=source.replace(old,"assert results['candidate']['rejection_evaluations'] == results['baseline']['rejection_evaluations']")
namespace=dict(__file__=str(Path(__file__).resolve()),__name__='phase9_gbi2_suite')
exec(compile(source,str(HELPER),'exec'),namespace)
namespace['PREFIX']=namespace['PREFIX'].replace('#define F3DEX_GBI 1',
    '#define F3DEX_GBI 1\n#define F3DEX_GBI_2 1')
tail=namespace['TAIL']
anchor='    if(warm){bool f,a,t;'
assert tail.count(anchor)==1
tail=tail.replace(anchor,'''    /* GBI2 carries TRI1's three indices in w0; TRI2 is unchanged. */
    for(unsigned i=0;i<100;i++) if((commands[i].words.w0>>24)==G_TRI1) {
        commands[i].words.w0 |= commands[i].words.w1 & 0x00ffffffu;
        commands[i].words.w1 = 0;
    }
'''+anchor)
namespace.update(TAIL=tail,BASE=CAMP/'phase7/combined-v1/gfx_pc.c',
    CAND=DEST.parent/'gfx_pc.c',OUT=DEST)
sys.argv=[str(Path(__file__))]
namespace['main']()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(DEST/'adaptation.json').write_text(json.dumps(dict(helper_sha256=sha(HELPER),
    wrapper_sha256=sha(Path(__file__)),result_sha256=sha(DEST/'RESULT.json'),
    adaptation='Preserve every original scenario but encode TRI1 indices in GBI2 w0 and require equal rejection counts.'),indent=2)+'\n')
