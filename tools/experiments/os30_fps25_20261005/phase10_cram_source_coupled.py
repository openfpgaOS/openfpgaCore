#!/usr/bin/env python3
"""Bound CRAM1 vertex-source routing with actual RTL; preload/remap cost excluded."""
import hashlib, json, shutil, subprocess, sys
from pathlib import Path
from elftools.elf.elffile import ELFFile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'load-alias-coupled-v1'
OUT=P/'cram-source-coupled-v1'
F=OUT/'model/frozen'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):
    assert s.count(a)==1,(a[:150],s.count(a));return s.replace(a,b)
def symbols(path):
    with path.open('rb') as f:
        elf=ELFFile(f)
        return [(s.name,int(s['st_value']),int(s['st_size'])) for s in elf.get_section_by_name('.symtab').iter_symbols() if s['st_info']['type']=='STT_OBJECT' and s['st_size']]
def elf_bytes(path,addr,size):
    with path.open('rb') as f:
        elf=ELFFile(f)
        for segment in elf.iter_segments():
            start=int(segment['p_vaddr']);end=start+int(segment['p_filesz'])
            if start<=addr and addr+size<=end:return segment.data()[addr-start:addr-start+size]
    raise AssertionError((path,hex(addr),size))
def prepare():
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE/'model/frozen',F)
    shutil.copytree(BASE/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    oldelf=P/'raw-frontend-observer/capture-normal/sm64.elf'
    newelf=P/'load-alias-driver-v1/app.elf'
    oldsyms=symbols(oldelf);newsyms={n:(a,z) for n,a,z in symbols(newelf)}
    keys=json.loads((P/'cram1-source-inventory-v1/result.json').read_text())['all721']['keys']
    mappings=[]
    for address,count in keys:
        choices=[(n,a,z) for n,a,z in oldsyms if a<=address and address+24*count<=a+z]
        assert len(choices)==1,(hex(address),count,choices)
        name,start,size=choices[0];newstart,newsize=newsyms[name];assert size==newsize
        dest=newstart+address-start
        assert elf_bytes(oldelf,address,24*count)==elf_bytes(newelf,dest,24*count)
        mappings.append((dest,dest+24*count))
    ranges=[]
    for start,end in sorted(mappings):
        if ranges and start<=ranges[-1][1]:ranges[-1][1]=max(end,ranges[-1][1])
        else:ranges.append([start,end])
    cursor=0x100000;table=[]
    for start,end in ranges:
        cursor=(cursor+63)&~63;table.append(dict(source=start,bytes=end-start,cram=cursor));cursor+=end-start
    assert cursor<0x200000
    h='// Immutable ranges relinked by ELF symbol and byte-checked against F11.\n'
    h+='struct SourceRange {uint32_t source,bytes,cram;};\nstatic const SourceRange source_ranges[]={\n'
    h+=''.join(' {%du,%du,%du},\n'%(x['source'],x['bytes'],x['cram']) for x in table)+'};\n'
    (F/'source_pool.h').write_text(h)
    write(OUT/'source-pool.json',dict(old_elf_sha256=sha(oldelf),new_elf_sha256=sha(newelf),ranges=table,pool_end=cursor,
        limitations='Observed immutable pool only; startup upload and CPU address-remap cost excluded. Entire source span is checked unchanged on every LOAD.'))
    p=F/'tb_gpu.v';s=p.read_text()
    s=rep(s,'    input wire gd_reg_wr,','''    input wire gd_cram_route,
    output wire obs_c1_ce,obs_c1_burst_busy,obs_c1_up_busy,
    output wire [31:0] obs_gd_rdata,
    output wire [4:0] obs_c1_ctrl_state,
    input wire gd_reg_wr,''')
    s=rep(s,'texture_memory #(.REFRESH_EXTRA_CYCLES(CRAM_REFRESH_EXTRA)) texture_memory(','''wire shared_arvalid,shared_arready,shared_rvalid,shared_rlast;
wire [31:0] shared_araddr,shared_rdata;
wire [7:0] shared_arlen;
wire cram_gd_arready,cram_gd_rvalid,cram_gd_rlast;
wire [31:0] cram_gd_rdata;
wire sdram_gd_arready,sdram_gd_rvalid,sdram_gd_rlast;
wire [31:0] sdram_gd_rdata;
wire [1:0] sdram_gd_rresp;
assign gd_arready=gd_cram_route?cram_gd_arready:sdram_gd_arready;
assign gd_rvalid=gd_cram_route?cram_gd_rvalid:sdram_gd_rvalid;
assign gd_rdata=gd_cram_route?cram_gd_rdata:sdram_gd_rdata;
assign gd_rlast=gd_cram_route?cram_gd_rlast:sdram_gd_rlast;
assign gd_rresp=gd_cram_route?2'b0:sdram_gd_rresp;
geometry_cram1_read_arbiter source_arbiter(
 .clk(clk),.reset_n(reset_n),
 .s0_arvalid(c1_arvalid),.s0_arready(c1_arready),.s0_araddr(c1_araddr),.s0_arlen(c1_arlen),
 .s0_rvalid(c1_rvalid),.s0_rdata(c1_rdata),.s0_rlast(c1_rlast),
 .s1_arvalid(gd_arvalid&&gd_cram_route),.s1_arready(cram_gd_arready),.s1_araddr(gd_araddr),.s1_arlen(gd_arlen),
 .s1_rvalid(cram_gd_rvalid),.s1_rdata(cram_gd_rdata),.s1_rlast(cram_gd_rlast),
 .m_arvalid(shared_arvalid),.m_arready(shared_arready),.m_araddr(shared_araddr),.m_arlen(shared_arlen),
 .m_rvalid(shared_rvalid),.m_rdata(shared_rdata),.m_rlast(shared_rlast));
assign obs_gd_rdata=gd_rdata;
assign obs_c1_ce=!texture_memory.ce0_n||!texture_memory.ce1_n;
assign obs_c1_burst_busy=texture_memory.burst_busy;
assign obs_c1_up_busy=c1_up_busy;
assign obs_c1_ctrl_state=texture_memory.ctrl.state;
always @(posedge clk) if(reset_n&&gd_cram_route&&gd_rvalid&&!gd_rready)
 $fatal(1,"Raw DMA must accept every CRAM1 response");
always @(posedge clk) if(reset_n&&gd_cram_route&&c1_up_wr&&c1_up_addr>=22'h40000)
 $fatal(1,"Texture upload overlaps reserved geometry pool");
texture_memory #(.REFRESH_EXTRA_CYCLES(CRAM_REFRESH_EXTRA)) texture_memory(''')
    s=rep(s,'.clk(clk),.reset_n(reset_n),.arvalid(c1_arvalid),.arready(c1_arready),\n    .araddr(c1_araddr),.arlen(c1_arlen),.rvalid(c1_rvalid),.rdata(c1_rdata),.rlast(c1_rlast),',
        '.clk(clk),.reset_n(reset_n),.arvalid(shared_arvalid),.arready(shared_arready),\n    .araddr(shared_araddr),.arlen(shared_arlen),.rvalid(shared_rvalid),.rdata(shared_rdata),.rlast(shared_rlast),')
    for n in ['arready','rvalid','rdata','rlast','rresp']:
        s=rep(s,'.m2_'+n+'(gd_'+n+')','.m2_'+n+'(sdram_gd_'+n+')')
    s=rep(s,'.m2_arvalid(gd_arvalid)','.m2_arvalid(gd_arvalid&&!gd_cram_route)')
    p.write_text(s)
    shutil.copy2(P/'cram1-share-v1/frozen/phase10_cram1_read_arbiter.v',F/'common/geometry_cram1_read_arbiter.v')
    shutil.copy2(HERE/'phase10_cram_source_coupled.cpp',F/'coupled.cpp')
    cmd=[x.replace(str(BASE),str(OUT)) for x in json.loads((BASE/'build-command.json').read_text())]
    cmd.append(str(F/'common/geometry_cram1_read_arbiter.v'))
    write(OUT/'build-command.json',cmd)
    write(OUT/'inputs.json',dict(parent=str(BASE),parent_inputs_sha256=sha(BASE/'inputs.json'),
        sources={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()},scope=__doc__))
def build():
    with (OUT/'build-qsim.log').open('x') as log:
        subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=log,stderr=subprocess.STDOUT,check=True)
    cmd=json.loads((OUT/'build-command.json').read_text())
    with (OUT/'model/build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    write(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu'),scope=__doc__))
if __name__=='__main__':
    for action in sys.argv[1:]:globals()[action]()
