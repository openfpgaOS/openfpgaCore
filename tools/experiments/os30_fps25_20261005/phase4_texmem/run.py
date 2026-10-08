#!/usr/bin/env python3
"""Isolated retained-A CRAM1 resident-texture replay; no hardware publication."""
import argparse,hashlib,json,shutil,struct,subprocess,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase4/texmem'
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase3/arbiter'
FIT=ROOT/'docs/measurements/os30-dual-20261004/fit-single128-v1'
PRIOR=ROOT/'docs/measurements/os30-fps25-20261005/validation/replays-single-A/phase2-exact-v1-fps/2'
FRAMES=(360,600,720)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def replace(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b)

def records(data):
    p=0
    while p<len(data):
        begin=p;tag=data[p:p+4];p+=4;words=None
        if tag==b'MEM1':
            _,n=struct.unpack_from('<II',data,p);p+=8+((n+3)&~3)
        elif tag==b'CMDS':
            n,=struct.unpack_from('<I',data,p);p+=4;words=struct.unpack_from(f'<{n}I',data,p);p+=4*n
        elif tag==b'TLUT':p+=32768
        elif tag==b'PALB':p+=4
        elif tag==b'DUMP':p+=8
        elif tag==b'RAND':p+=12
        elif tag in (b'FLSH',b'FENC',b'HASH',b'END.'):pass
        else:raise AssertionError((tag,p))
        assert p<=len(data)
        yield begin,p,tag,words

def packets(words):
    i=0
    while i<len(words):
        cmd=words[i]>>24;n=words[i]&0xffffff;payload=words[i+1:i+1+n]
        assert len(payload)==n
        yield i,cmd,payload
        i+=n+1
    assert i==len(words)

def textures(data):
    tex={};targets=[]
    for _,_,tag,words in records(data):
        if tag!=b'CMDS':continue
        for _,cmd,p in packets(words):
            assert cmd in (0x11,0x4a,0x50,0x56,0x54,0x4e,0x42),hex(cmd)
            if cmd!=0x4a:continue
            assert len(p)==17 and p[6]&128,'Truecolor only in this experiment'
            a=p[3]&0x3ffffff;w=p[4];hm=p[5]>>16
            h=(p[10]>>16)+1 if hm==65535 else hm+1
            assert 0<w<4096 and 0<h<4096
            tex[a]=max(tex.get(a,0),w*h*2)
            targets.append((p[0]&0x3ffffff,320*240*2))
            if (p[6]>>24)&15:targets.append((p[11]&0x3ffffff,320*240*2))
    for a,n in tex.items():
        assert all(a+n<=b or b+m<=a for b,m in targets),('Texture overlaps render target',hex(a),n)
    return tex

def prepare():
    manifest=json.loads((BASE/'inputs.json').read_text())
    for rel,h in manifest['sources'].items():assert sha(BASE/rel)==h,rel
    fit=json.loads((FIT/'frozen-sources.json').read_text())
    OUT.mkdir(parents=True,exist_ok=True);dest=OUT/'frozen';assert not dest.exists()
    shutil.copytree(BASE/'frozen',dest)
    added={}
    for name in ('cram1_controller.v','cram1_phy.sv','gpu_cram1_tex_adapter.v'):
        rel='src/fpga/targets/pocket/'+name;assert sha(FIT/'frozen'/rel)==fit[rel]
        shutil.copy2(FIT/'frozen'/rel,dest/name);added[name]=dict(origin=str((FIT/'frozen'/rel).relative_to(ROOT)),sha256=fit[rel],production_exact_A=True)
    for name in ('cram_chip_model.v','altddio_out_stub.v'):
        p=ROOT/'src/fpga/test'/name;shutil.copy2(p,dest/name);added[name]=dict(origin=str(p.relative_to(ROOT)),sha256=sha(p),simulation_only=True)
    for name in ('texture_memory.sv','texture_observer.hpp'):
        shutil.copy2(HERE/name,dest/name)
    alltex={};frameinfo={}
    for frame in FRAMES:
        p=PRIOR/f'frame_{frame:05d}.vec';t=textures(p.read_bytes());frameinfo[frame]=dict(vector_sha256=sha(p),texture_bases=len(t),texture_extent_sum=sum(t.values()))
        for a,n in t.items():alltex[a]=max(n,alltex.get(a,0))
    pages=set()
    for a,n in alltex.items():pages.update(range(a&~16383,(a+n+16383)&~16383,16384))
    page_map={a:i*16384 for i,a in enumerate(sorted(pages))}
    assert len(page_map)*16384<=0x200000
    # Contiguous original pages get contiguous fast pages; preserve every low14bit.
    for a,n in alltex.items():
        for off in range(0,n,16):
            x=a+off;assert page_map[x&~16383]+(x&16383)==page_map[a&~16383]+(a&16383)+off
    (dest/'texture_map.hpp').write_text('struct TexturePage { uint32_t original,fast; };\nstatic const TexturePage texture_pages[]={\n'+''.join(f'    {{0x{a:08x}u,0x{b:08x}u}},\n' for a,b in page_map.items())+'};\n')
    vectors=OUT/'vectors';vectors.mkdir(exist_ok=False)
    for frame in FRAMES:
        data=(PRIOR/f'frame_{frame:05d}.vec').read_bytes();new=bytearray(data);changed=[]
        for begin,end,tag,words in records(data):
            if tag!=b'CMDS':continue
            for i,cmd,p in packets(words):
                if cmd==0x4a:
                    a=p[3]&0x3ffffff;mapped=page_map[a&~16383]+(a&16383)
                    offset=begin+8+(i+4)*4
                    assert struct.unpack_from('<I',data,offset)[0]==p[3]
                    struct.pack_into('<I',new,offset,mapped);changed.append(dict(offset=offset,before=p[3],after=mapped))
        out=vectors/f'frame_{frame:05d}.vec';out.write_bytes(new)
        frameinfo[frame].update(remapped_sha256=sha(out),changes=changed)
    p=dest/'tb_gpu.v';s=p.read_text()
    s=replace(s,'    parameter CPU_FAIR_THRESHOLD = 8,','    parameter CPU_FAIR_THRESHOLD = 8,\n    parameter CRAM_REFRESH_EXTRA = 0,')
    s=replace(s,'    // Status outputs\n','''    output wire obs_tex_arvalid,obs_tex_arready,obs_tex_rvalid,obs_tex_rlast,
    output wire [31:0] obs_tex_araddr,obs_tex_rdata,
    output wire [7:0] obs_tex_arlen,
    output wire obs_c1_bcr_done,obs_c1_route,
    output wire [15:0] obs_c1_errors,
    // Status outputs
''')
    start=s.index('    // Fast texture memory: no tb model')
    end=s.index('`ifdef INCLUDE_GPU_RENDER_CACHE',start)
    s=s[:start]+'''    .gpu_tex_mem_arvalid(c1_arvalid),.gpu_tex_mem_arready(c1_arready),
    .gpu_tex_mem_araddr(c1_araddr),.gpu_tex_mem_arlen(c1_arlen),
    .gpu_tex_mem_rvalid(c1_rvalid),.gpu_tex_mem_rdata(c1_rdata),.gpu_tex_mem_rlast(c1_rlast),
    .gpu_tex_mem_up_wr(c1_up_wr),.gpu_tex_mem_up_addr(c1_up_addr),
    .gpu_tex_mem_up_wdata(c1_up_data),.gpu_tex_mem_up_busy(c1_up_busy),
'''+s[end:]
    chain='''wire c1_arvalid,c1_arready,c1_rvalid,c1_rlast,c1_up_wr,c1_up_busy;
wire [31:0] c1_araddr,c1_rdata,c1_up_data;
wire [7:0] c1_arlen;
wire [21:0] c1_up_addr;
texture_memory #(.REFRESH_EXTRA_CYCLES(CRAM_REFRESH_EXTRA)) texture_memory(
    .clk(clk),.reset_n(reset_n),.arvalid(c1_arvalid),.arready(c1_arready),
    .araddr(c1_araddr),.arlen(c1_arlen),.rvalid(c1_rvalid),.rdata(c1_rdata),.rlast(c1_rlast),
    .up_wr(c1_up_wr),.up_addr(c1_up_addr),.up_data(c1_up_data),.up_busy(c1_up_busy),
    .bcr_done(obs_c1_bcr_done),.errors(obs_c1_errors)
);
assign obs_c1_route=gpu.gpu_tex_mem_route;
assign obs_tex_arvalid=gpu.tex_axi_arvalid;
assign obs_tex_arready=gpu.tex_axi_arready;
assign obs_tex_araddr=gpu.tex_axi_araddr;
assign obs_tex_arlen=gpu.tex_axi_arlen;
assign obs_tex_rvalid=gpu.tex_axi_rvalid;
assign obs_tex_rdata=gpu.tex_axi_rdata;
assign obs_tex_rlast=gpu.tex_axi_rlast;
'''
    s=replace(s,'gpu_core #(\n',chain+'\ngpu_core #(\n');p.write_text(s)
    p=dest/'replay.cpp';s=p.read_text()
    s=replace(s,'#include "observer.hpp"','#include "observer.hpp"\n#include "texture_observer.hpp"')
    s=replace(s,'        observe_tick();','        observe_tick();\n        texture_observe_tick();')
    s=replace(s,'    mmio_write(0, 1);               // enable (no-op in tb; gpu_enable tied 1)','    mmio_write(14,fast_tex?1:0);     // same one-cycle slot; enable was a no-op')
    s=replace(s,'    tick(2000);                     // tex cache + recip cache init walks','    tick(2000);                     // tex cache + recip cache init walks\n    if(!tb->obs_c1_bcr_done || bool(tb->obs_c1_route)!=fast_tex)throw std::runtime_error("CRAM1 init/route failed");')
    s=replace(s,'            uint32_t n = rd32();\n            if (n > 4095)','            preload_texture_pages();\n            uint32_t n = rd32();\n            if (n > 4095)')
    s=replace(s,'    observe_report();','    observe_report();\n    texture_report();')
    s=replace(s,'    Verilated::commandArgs(argc, argv);','    Verilated::commandArgs(argc, argv);\n    for(int a=1;a<argc;++a)if(!strcmp(argv[a],"+fast_tex=1"))fast_tex=true;')
    p.write_text(s)
    report=dict(scope=__doc__,phase3_inputs_sha256=sha(BASE/'inputs.json'),fit_sources_sha256=sha(FIT/'frozen-sources.json'),added=added,
        frames=frameinfo,page_map=page_map,pool_bytes=len(page_map)*16384,referenced_extent_sum=sum(alltex.values()),
        cache_index_bits_preserved=14,model_address_limit=0x200000,host_preload_cost_excluded=True,
        sources={str(p.relative_to(OUT)):sha(p) for p in sorted(dest.rglob('*')) if p.is_file()},
        scripts={str(p.relative_to(ROOT)):sha(p) for p in sorted(HERE.glob('*')) if p.is_file()})
    write(OUT/'inputs.json',report)

def build(extra):
    m=json.loads((OUT/'inputs.json').read_text())
    for rel,h in m['sources'].items():assert sha(OUT/rel)==h,rel
    dest=OUT/f'model-extra{extra}';dest.mkdir(exist_ok=False)
    old=json.loads((BASE/'threshold8/build.json').read_text())
    cmd=[a.replace(str(BASE/'frozen'),str(OUT/'frozen')).replace(str(BASE/'threshold8/obj'),str(dest/'obj')) for a in old['command']]
    for name in ('texture_memory.sv','cram1_controller.v','cram1_phy.sv','gpu_cram1_tex_adapter.v','cram_chip_model.v','altddio_out_stub.v'):cmd.append(str(OUT/'frozen'/name))
    cmd.append(f'-GCRAM_REFRESH_EXTRA={extra}')
    with (dest/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(dest/'build.json',dict(extra=extra,command=cmd,simulator_sha256=sha(dest/'obj/Vtb_gpu'),inputs_sha256=sha(OUT/'inputs.json')))

def run(extra,fast):
    model=OUT/f'model-extra{extra}';exe=model/'obj/Vtb_gpu';m=json.loads((model/'build.json').read_text())
    assert sha(exe)==m['simulator_sha256'];dest=model/('fast' if fast else 'sdram');dest.mkdir(exist_ok=False);rows=[]
    for frame in FRAMES:
        stem=f'frame_{frame:05d}';vec=(OUT/'vectors' if fast else PRIOR)/(stem+'.vec');binary=dest/(stem+'.bin');log=dest/(stem+'.log')
        with log.open('w') as f:subprocess.run([str(exe),str(vec),str(binary),'+memory_traffic=2',f'+fast_tex={int(fast)}'],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
        stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log.read_text())}
        assert binary.read_bytes()==(PRIOR/(stem+'.bin')).read_bytes(),'Output difference'
        assert stats['cycles']==stats['window_cycles'] and stats['cram_errors']==0
        assert stats['beats']==4*stats['fills'] and stats['fills']>0
        assert stats['scan_latency_max']<6361
        if not fast:
            baseline=json.loads((BASE/'threshold8/results.json').read_text());base=next(r for r in baseline if r['frame']==frame)
            for k,v in base['stats'].items():assert stats[k]==v,(k,stats[k],v)
        rows.append(dict(frame=frame,fast=fast,extra=extra,stats=stats,exact_color_depth_fence_memoryhash=True,
            vector_sha256=sha(vec),output_sha256=sha(binary),log_sha256=sha(log),simulator_sha256=sha(exe)))
        write(dest/'results.json',rows);print(extra,fast,frame,stats['cycles'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','build','run']);p.add_argument('--extra',type=int,default=0);p.add_argument('--fast',action='store_true');a=p.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='build':build(a.extra)
    else:run(a.extra,a.fast)
