#!/usr/bin/env python3
"""Review candidate RV32 culling operation graphs without running sealed scripts."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

from phase9_experiment import HERE, PARENT, directory, sha, write


def retained_definitions():
    source = HERE/'phase7_cull_codegen_review.py'
    tree = ast.parse(source.read_text())
    definitions = [node for node in tree.body if isinstance(node,ast.FunctionDef)]
    namespace = {'re':re,'hashlib':hashlib}
    # Deliberately exclude all module-level mkdir, cases, loops and writes.
    exec(compile(ast.Module(body=definitions,type_ignores=[]),str(source),'exec'),namespace)
    return namespace,source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate')
    parser.add_argument('--mode',choices=['normal','diagnostic'],default='normal')
    args = parser.parse_args()
    candidate = directory(args.candidate)
    output = candidate/('codegen-'+args.mode)
    output.mkdir(exist_ok=False)
    api,prior_script = retained_definitions()
    filename = 'app.elf' if args.mode == 'normal' else 'diagnostic.elf'
    cases = [('F9',PARENT/filename),('candidate',candidate/filename)]
    results = {}
    for label,elf in cases:
        assert sha(elf) == json.loads((elf.parent/f'build-{args.mode}.json').read_text())['elf_sha256']
        entries = {}
        for name in ('gfx_sp_tri1','gfx_cpu_indexed_run'):
            command = ['llvm-objdump','-d','--mattr=+f,+c,+m,+a','--disassemble-symbols='+name,str(elf)]
            result = subprocess.run(command,check=True,text=True,capture_output=True)
            path = output/(label+'-'+name+'.asm')
            path.write_text(result.stdout)
            operations = Counter(row[1] for row in api['insns'](result.stdout))
            try:
                blocks = api['analyze'](result.stdout)
            except Exception as error:
                write(output/'INCOMPLETE.json',dict(passed=False,label=label,function=name,
                    disassembly_sha256=sha(path),error_type=type(error).__name__,error=str(error),
                    scope='Parser did not establish rounding equivalence. Inspect actual assembly; this is not a numerical pass.'))
                raise
            entries[name] = dict(command=command,disassembly_sha256=sha(path),blocks=blocks,
                instruction_sites=sum(operations.values()),opcode_counts=dict(sorted(operations.items())),
                stack_adjustments=[int(x,0) for x in re.findall(r'addi\s+sp, sp, (-?0x[0-9a-f]+)',result.stdout)])
        results[label] = dict(elf=str(elf),elf_sha256=sha(elf),functions=entries)
    comparisons = {}
    for name in results['F9']['functions']:
        base = results['F9']['functions'][name]
        new = results['candidate']['functions'][name]
        assert len(base['blocks']) == len(new['blocks']) > 0
        comparisons[name] = dict(culling_block_count=len(base['blocks']),
            same_rounding_graph_contract=True,
            instruction_sites_delta=new['instruction_sites']-base['instruction_sites'],
            relevant_opcode_deltas={op:new['opcode_counts'].get(op,0)-base['opcode_counts'].get(op,0)
                for op in ('flw','lw','flt.s','fle.s','feq.s','fmul.s','fmsub.s','fsub.s','fmadd.s','fmv.x.w')})
    report = dict(passed=True,mode=args.mode,candidate=args.candidate,
        helper_sha256=sha(Path(__file__)),definitions_source_sha256=sha(prior_script),
        sealed_module_top_level_executed=False,comparisons=comparisons,results=results,
        claim='Every checked culling block retains two rounded central-coordinate products, four fused differences, two separately rounded cross products and opposite separate subtractions for parity. No projected-coordinate memo or altered FMA rounding.',
        limit='Operation-graph proof covers culling arithmetic. Integer W-predicate equivalence, exceptional-value/flag policy, behavioral outputs and timing require separate checks. Static instruction sites are not execution counts.')
    write(output/'review.json',report)
    print(json.dumps(dict(passed=True,candidate=args.candidate,mode=args.mode,comparisons=comparisons),indent=2))


if __name__ == '__main__':main()
