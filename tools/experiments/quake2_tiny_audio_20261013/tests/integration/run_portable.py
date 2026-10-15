#!/usr/bin/env python3
"""Run selected audio MMIO and cache-connected sample gates; no fits/device I/O."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess,re
D=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def invoke(cmd,cwd,log):
    with log.open('w')as f:subprocess.run(cmd,cwd=cwd,stdout=f,stderr=subprocess.STDOUT,check=True)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--core',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    core=a.core.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    rel={'axi_periph_slave.v':'src/fpga/common/os30/axi_periph_slave.v','audio_mixer.v':'src/fpga/common/os30/audio_mixer.v','audio_sample_cache.v':'src/fpga/common/os30/audio_sample_cache.v','sync_fifo.v':'src/fpga/common/sync_fifo.v','snac_shifter.v':'src/fpga/targets/pocket/snac_shifter.v','common.v':'src/fpga/targets/pocket/apf/common.v','core_top.v':'src/fpga/targets/pocket/os30/core_top.v','ap_core.qsf':'src/fpga/targets/pocket/ap_core.qsf'}
    pins={r:sha(core/r)for r in rel.values()};(out/'source-pins.json').write_text(json.dumps(pins,indent=2)+'\n')
    per=out/'periph';s=per/'sources';shutil.copytree(D/'periph/sources',s)
    for n in ['axi_periph_slave.v','sync_fifo.v','snac_shifter.v','common.v']:shutil.copy2(core/rel[n],s/n)
    results=[]
    for enabled in [0,1]:
        obj=per/f'obj-{enabled}'
        cmd=['verilator','--cc','--exe','--build','--assert','-j','3','-Wno-fatal','--top-module','tb_axi_periph',f'-GINCLUDE_AUDIO_SAMPLE_CACHE={enabled}','--Mdir',str(obj),*[str(s/n)for n in ['tb_axi_periph.v','axi_periph_slave.v','altsyncram_stub.v','snac_shifter.v','sync_fifo.v','common.v','tb_axi_periph_main.cpp']]]
        (per/f'build-command-{enabled}.json').write_text(json.dumps(cmd,indent=2)+'\n');invoke(cmd,per,per/f'build-{enabled}.log')
        log=per/f'audio-result-{enabled}.log';invoke([str(obj/'Vtb_axi_periph'),'--audio-only'],per,log)
        assert '39 passed, 0 failed' in log.read_text()
        results.append({'cache_present':enabled,'passed':39,'failed':0,'log_sha256':sha(log)})
    helper=out/'mixer-cache-harness';shutil.copytree(D/'mixer-cache-harness',helper,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(core/rel['audio_sample_cache.v'],helper/'sources/audio_sample_cache.v')
    shutil.copy2(core/rel['sync_fifo.v'],helper/'sources/sync_fifo.v')
    invoke(['python3',str(helper/'run.py'),str(core/rel['audio_mixer.v']),str(out/'mixer-cache')],out,out/'mixer-cache-run.log')
    receipt={'passed':True,'source_pins':pins,'mmio':results,'mixer_cache':json.loads((out/'mixer-cache/receipt.json').read_text()),'limits':['Synthetic responder; real shared-arbiter performance has separate receipts.','The rearm address bound is asserted on upstream mixer requests; physical cache fills cover32-byte lines.','Physical FPGA timing, Pocket audio CDC and device playback require separate qualification.']}
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
