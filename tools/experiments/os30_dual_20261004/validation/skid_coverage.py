#!/usr/bin/env python3
"""Collect observed skid corner coverage from the unchanged whole-CPU fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
import re

OUT=Path(__file__).resolve().parents[4]/'docs/measurements/os30-dual-20261004/validation'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    args=parser.parse_args();out=OUT/args.label
    records=[];totals={}
    for program in ['stress','i2f','kernels','icache','frontend','decode-traps','fp-legality']:
        for mode in ['quiet','scanout']:
            path=out/f'{program}-{mode}.log';text=path.read_text()
            uart=text.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
            assert 'PASS' in uart and 'FAIL' not in uart,path
            matches=re.findall(r'^SKID COVERAGE (.+)$',text,re.M)
            assert len(matches)==1,path
            counters={key:int(value) for key,value in re.findall(r'(\w+)=(\d+)',matches[0])}
            assert counters['errors']==0 and 'SKID GHOST' not in text,path
            for key,value in counters.items():totals[key]=totals.get(key,0)+value
            records.append(dict(program=program,mode=mode,counters=counters,
                log_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    required=['occupied','captures','blocked','full_flush','full_flush_blocked',
        'full_flush_waiting_input','empty_flush_input','partial_captures','partial_deliveries']
    assert all(totals[key]>0 for key in required),totals
    report=dict(passed=True,scope=__doc__,runs=records,totals=totals,
        notes=['Observations and assertions do not modify CPU RTL.',
        'All 278 payload bits are checked on capture, hold and valid output selection.',
        'Flushed stage1 validity and lane masks, registered upstream ready, and aligner cancellation are checked.',
        'Waiting input during a full-buffer flush is not accepted in that cycle.'])
    (out/'skid-coverage.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(totals,sort_keys=True))

if __name__=='__main__':main()
