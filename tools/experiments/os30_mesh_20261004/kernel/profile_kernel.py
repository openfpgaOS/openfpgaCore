#!/usr/bin/env python3
"""Attribute the verified ISA cycles by generated kernel block, without edits."""
from collections import Counter
import inspect
import json
from pathlib import Path

from check_microcode import records
from microcode import build
import emulator

HERE=Path(__file__).resolve().parent


def main():
    program=build(fused=True);words=program.finish();labels=program.labels
    cycles=Counter();instructions=Counter();opcodes=Counter()
    reverse={v:k for k,v in emulator.OP.items()}
    def trace(pc,op,cost):
        if pc>=labels['slope']:block='edges'
        elif pc>=labels['plane']:block='planes_and_emit'
        elif pc>=labels['normalized']:block='reciprocal'
        elif pc>=labels['attributes_no_additive']+5:block='normalization'
        elif pc>=labels['attributes']-8:block='attribute_unpack'
        elif pc>=labels['mid_done']:block='edge_packet_emit'
        elif pc>=labels['det_done']:block='edges'
        elif pc>=labels['sort2']:block='determinant'
        elif pc>=labels['no_combine']:block='sorting'
        else:block='constants_and_state'
        cycles[block]+=cost;instructions[block]+=1;opcodes[reverse[op]]+=cost
    namespace=dict(emulator.__dict__,trace=trace)
    source=inspect.getsource(emulator.run)
    source=source.replace('word=program[pc]','instruction_pc=pc;word=program[pc]')
    source=source.replace('cycles+=cost','trace(instruction_pc,op,cost);cycles+=cost')
    exec(source,namespace)
    count=total=0
    for inputs,expected in records(HERE/'results/reference/final-hidden-m1-f360/kernel.bin'):
        result=namespace['run'](words,bank=inputs+[0]*232)
        assert result['output']==expected
        total+=result['cycles'];count+=1
    result=dict(scene='final-hidden-m1-f360',triangles=count,total_cycles=total,
                accounted_cycles=sum(cycles.values()),constant_start_halt_cycles=2*count,
                blocks=[dict(name=name,cycles=value,percent_of_total=100*value/total,
                             instructions=instructions[name]) for name,value in cycles.most_common()],
                opcode_cycles=dict(opcodes.most_common()))
    assert total==sum(cycles.values())+2*count
    (HERE/'results/profile-v2.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
