#!/usr/bin/env python3
"""Retained actual cache workload, comparing legacy and enabled early-offer RTL."""
import argparse,hashlib,json,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd,log):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),check=True,stdout=f,stderr=subprocess.STDOUT)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    ref=ROOT/'src/fpga/common/gpu_tex_cache.v';new=BASE/'texture-offer-v1/gpu_tex_cache.v';r=ref.read_text();n=new.read_text()
    (out/'reference.v').write_text(r.replace('module gpu_tex_cache #(','module gpu_tex_cache_reference #(',1))
    (out/'candidate.v').write_text(n.replace('module gpu_tex_cache #(','module gpu_tex_cache_candidate #(',1))
    header=r[r.index('module gpu_tex_cache #('):r.index('\n);')+3]
    ports=re.findall(r'\b(input|output)\s+(wire|reg)\s*(\[[^\]]+\])?\s*(\w+)',header)
    ins=[x[3] for x in ports if x[0]=='input'];outs=[(x[2],x[3]) for x in ports if x[0]=='output'];assert len(ins)==15 and len(outs)==9,(ins,outs)
    body=[]
    for prefix in ['r','l','e']:
        for width,name in outs:body.append(f'wire {width} {prefix}_{name};')
        connections=[f'.{name}({name})' for name in ins]+[f'.{name}({prefix}_{name})' for _,name in outs]
        if prefix=='r':module='gpu_tex_cache_reference';params='.SET_BITS(SET_BITS)'
        else:
            module='gpu_tex_cache_candidate';params=f'.SET_BITS(SET_BITS),.EARLY_REQ_OFFER({1 if prefix=="e" else 0})'
            # Enabled consumer obeys the only required contract. Elsewhere
            # its offered value deliberately differs from qualified valid.
            offer="((r.state==3 && !r.lat_port) ? req_valid : !req_valid)" if prefix=='e' else '!req_valid'
            connections.append('.req_offer_a('+offer+')')
        body.append(f'{module} #({params}) {prefix} ('+','.join(connections)+');')
    for _,name in outs:body.append(f'assign {name}=r_{name};')
    concat=lambda prefix:'{'+','.join(prefix+'_'+name for _,name in outs)+'}'
    body+=['reg [63:0] checks=0,resets=0,fill_a=0,fill_b=0,prime_a=0,prime_b=0,offer_different=0;',
           'always @(negedge clk) begin',
           f'if({concat("r")} !== {concat("l")}) $fatal(1,"legacy outputs/cycles differ");',
           f'if({concat("r")} !== {concat("e")}) $fatal(1,"early outputs/cycles differ");',
           'checks=checks+1;if(!reset_n)resets=resets+1;',
           'if(reset_n)begin',
           'if(r.fill_resp_valid_a !== ((r.state==3)&&!r.lat_port))$fatal(1,"A invariant");',
           'if(r.fill_resp_valid_b !== ((r.state==3)&&r.lat_port))$fatal(1,"B invariant");',
           'fill_a=fill_a+r.fill_resp_valid_a;fill_b=fill_b+r.fill_resp_valid_b;',
           'prime_a=prime_a+r.prime_a;prime_b=prime_b+r.prime_b;',
           'offer_different=offer_different+(!(r.state==3&&!r.lat_port));',
           'end end',
           'final $display("TEXCACHE_PAIR checks=%0d resets=%0d fill_a=%0d fill_b=%0d prime_a=%0d prime_b=%0d offer_different=%0d errors=0",checks,resets,fill_a,fill_b,prime_a,prime_b,offer_different);']
    (out/'paired.v').write_text(header+'\n'+'\n'.join(body)+'\nendmodule\n')
    cpp=ROOT/'src/fpga/test/tb_gpu_tex_cache_main.cpp';text=cpp.read_text();assert text.count('    return failures != 0;')==1
    (out/'main.cpp').write_text(text.replace('    return failures != 0;','    m.final();\n    return failures != 0;'))
    shutil.copy2(__file__,out/'runner.py')
    cmd=['verilator','--cc','--exe','--build','-j','2','--assert','-Wno-fatal','--top-module','gpu_tex_cache',
         '-GSET_BITS=10','-CFLAGS','-DCACHE_SET_BITS=10','--Mdir',out/'obj',out/'reference.v',out/'candidate.v',out/'paired.v',out/'main.cpp']
    run(cmd,out/'build.log');run([out/'obj/Vgpu_tex_cache'],out/'run.log')
    text=(out/'run.log').read_text();assert '0 failures' in text and 'errors=0' in text
    counters={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'TEXCACHE_PAIR (.+)',text)[1])}
    assert all(counters[x]>0 for x in ['checks','resets','fill_a','fill_b','prime_a','prime_b','offer_different'])
    result=dict(passed=True,reference_sha256=sha(ref),candidate_sha256=sha(new),set_bits=10,bytes=16384,
        direct_comparisons='Every output, including invalid payloads, on every observed cycle; original, default-disabled and enabled candidates share the retained input workload.',
        workload_origin=str(cpp),workload_sha256=sha(cpp),command=list(map(str,cmd)),
        counters=counters,simulator_sha256=sha(out/'obj/Vgpu_tex_cache'),files_sha256={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(text)
if __name__=='__main__':main()
