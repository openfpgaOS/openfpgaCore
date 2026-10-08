#!/usr/bin/env python3
"""Cycle-exact old/new texture cache miter on the retained dual-port workload."""
import json
import re
import shutil
import sys
from pathlib import Path
from phase10_texture_read_address import OUT, ROOT, sha, run, write


def main():
    bits = int(sys.argv[1]); assert bits in (4,10,11)
    d = OUT / f'protocol-{bits}'; d.mkdir(exist_ok=False)
    r = (OUT / 'baseline.v').read_text(); n = (OUT / 'gpu_tex_cache.v').read_text()
    (d / 'reference.v').write_text(r.replace('module gpu_tex_cache #(', 'module texture_reference #(', 1))
    (d / 'candidate.v').write_text(n.replace('module gpu_tex_cache #(', 'module texture_candidate #(', 1))
    header = r[r.index('module gpu_tex_cache #('):r.index('\n);')+3]
    ports = re.findall(r'\b(input|output)\s+(wire|reg)\s*(\[[^\]]+\])?\s*(\w+)', header)
    ins = [v[3] for v in ports if v[0]=='input']; outs = [(v[2],v[3]) for v in ports if v[0]=='output']
    assert len(ins)==15 and len(outs)==9
    body=[]
    for prefix,module in [('r','texture_reference'),('c','texture_candidate')]:
        body += [f'wire {w} {prefix}_{n};' for w,n in outs]
        connections=[f'.{n}({n})'for n in ins]+[f'.{n}({prefix}_{n})'for _,n in outs]
        body.append(f'{module} #(.SET_BITS(SET_BITS)) {prefix}('+','.join(connections)+');')
    body += [f'assign {n}=r_{n};'for _,n in outs]
    cat=lambda p:'{'+','.join(p+'_'+n for _,n in outs)+'}'
    body += ['reg[63:0]checks=0,resets=0,reads=0,writes=0,prime_a=0,prime_b=0,accept_fill=0,idle_diff=0,held=0,flushes=0;',
      'always @(posedge clk) begin',
      f'if({cat("r")}!=={cat("c")})$fatal(1,"cache outputs/cycles differ");',
      'checks=checks+1;if(!reset_n)resets=resets+1;',
      'if(r.state!==c.state||r.read_en_a!==c.read_en_a||r.read_en_b!==c.read_en_b||r.data_write!==c.data_write||r.tag_write!==c.tag_write)$fatal(1,"ownership changed");',
      'if(r.read_en_a||r.data_write)begin if(r.data_address_a!==c.data_address_a)$fatal(1,"owned data address differs");end',
      'if(r.read_en_a||r.tag_write)begin if(r.tag_address_a!==c.tag_address_a)$fatal(1,"owned tag address differs");end',
      'if(reset_n)begin reads=reads+r.read_en_a;writes=writes+r.data_write+r.tag_write;',
      'prime_a=prime_a+r.prime_a;prime_b=prime_b+r.prime_b;held=held+r.held_valid_b;',
      'accept_fill=accept_fill+((r.state==3)&&req_valid&&req_ready);flushes=flushes+flush;',
      'if(!r.read_en_a&&!r.data_write&&r.data_address_a!==c.data_address_a)idle_diff=idle_diff+1;',
      'end end',
      'final $display("TEXTURE_ADDRESS_PAIR checks=%0d resets=%0d reads=%0d writes=%0d prime_a=%0d prime_b=%0d accept_fill=%0d idle_diff=%0d held=%0d flushes=%0d",checks,resets,reads,writes,prime_a,prime_b,accept_fill,idle_diff,held,flushes);']
    (d / 'paired.v').write_text(header+'\n'+'\n'.join(body)+'\nendmodule\n')
    cpp=ROOT/'src/fpga/test/tb_gpu_tex_cache_main.cpp'
    text=cpp.read_text();assert text.count('    return failures != 0;')==1
    directed = r"""
    // Consecutive fill completion and replacement request, all byte/halfword
    // offsets. The random workload waits for completion before offering its
    // next request and therefore does not exercise this acceptance edge.
    for (unsigned lane=0; lane<8; ++lane) {
        m.reset_n=0;m.flush=0;m.resp_flush_b=0;m.resp_pop_b=0;
        m.req_valid=m.req_valid_b=m.axi_arready=m.axi_rvalid=m.axi_rlast=0;
        tick(m);tick(m);m.reset_n=1;
        for(unsigned i=0;i<(1u<<CACHE_SET_BITS)+2;++i)tick(m);
        uint32_t base=0x200+32*lane;
        Request first{base+(lane&3),bool(lane&4)};
        Request second{base+4+(lane&3),bool(lane&4)};
        m.req_addr=first.address;m.req_wide=first.wide;m.req_valid=1;m.eval();
        if(!m.req_ready)++failures;
        tick(m);m.req_valid=0;tick(m);m.eval();
        if(!m.axi_arvalid)++failures;
        m.axi_arready=1;tick(m);m.axi_arready=0;
        for(unsigned beat=0;beat<4;++beat){
            m.axi_rvalid=1;m.axi_rdata=word(base+4*beat);m.axi_rlast=beat==3;tick(m);
        }
        m.axi_rvalid=m.axi_rlast=0;
        m.req_valid=1;m.req_addr=second.address;m.req_wide=second.wide;m.eval();
        if(!m.req_ready||!m.resp_valid||m.resp_data!=expected(first))++failures;
        tick(m);m.req_valid=0;m.eval();
        if(!m.resp_valid||m.resp_data!=expected(second))++failures;
        tick(m);
    }
    std::printf("Directed fill-out replacements: 8, %d failures\n",failures);
    m.final();
    return failures != 0;
"""
    (d/'main.cpp').write_text(text.replace('    return failures != 0;',directed))
    shutil.copy2(__file__,d/'recipe.py')
    cmd=['verilator','--cc','--exe','--build','-j','2','--assert','-Wno-fatal','--top-module','gpu_tex_cache',
      f'-GSET_BITS={bits}','-CFLAGS',f'-DCACHE_SET_BITS={bits}','--Mdir',d/'obj',d/'reference.v',d/'candidate.v',d/'paired.v',d/'main.cpp']
    write(d/'command.json',list(map(str,cmd)))
    run(cmd,d/'build.log');run([d/'obj/Vgpu_tex_cache'],d/'run.log')
    text=(d/'run.log').read_text();assert '0 failures' in text and 'FATAL' not in text
    match=re.search(r'TEXTURE_ADDRESS_PAIR (.*)',text);assert match
    counts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',match[1])}
    assert all(v>0 for v in counts.values()),counts
    write(d/'result.json',dict(passed=True,set_bits=bits,counts=counts,
      candidate_sha256=sha(OUT/'gpu_tex_cache.v'),reference_sha256=sha(OUT/'baseline.v'),
      original_workload_sha256=sha(cpp),outputs_including_invalid_payloads_exact=True))


if __name__=='__main__':main()
