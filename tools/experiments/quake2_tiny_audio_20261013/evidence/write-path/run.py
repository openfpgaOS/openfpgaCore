from pathlib import Path
import subprocess,shutil,json,hashlib,re,time
R=Path(__file__).resolve().parent
REPO=Path('/home/alberto/Repos/openfpgaCore')
SOURCE=R.parent/'sweep-final-ring-audio/frozen'
I=R/'inputs';I.mkdir(exist_ok=False)
paths={n:REPO/'src/fpga/test'/n for n in ['tb_gpu_wrpath.v','tb_gpu_wrpath_main.cpp','sdram_model_full.v','altsyncram_stub.v','io_sdram_test.v']}
paths['gpu_core.v']=SOURCE/'src/fpga/common/os30/gpu_core.v'
paths['gpu_color_depth_cache.sv']=SOURCE/'src/fpga/common/os30/gpu_color_depth_cache.sv'
paths['core_top.v']=SOURCE/'src/fpga/targets/pocket/os30/core_top.v'
paths['ap_core.qsf']=SOURCE/'src/fpga/targets/pocket/ap_core.qsf'
for n in ['gpu_edge_walker.v','gpu_tex_cache.v','gpu_geometry_worker.v','axi_sdram_arbiter.v','sync_fifo.v','axi_sdram_slave.v']:paths[n]=SOURCE/'src/fpga/common'/n
paths['gpu_tex_cache.v']=SOURCE/'src/fpga/common/os30/gpu_tex_cache.v'
paths['synch_3.v']=REPO/'src/fpga/targets/mister/synch_3.v'
for name,p in paths.items():shutil.copyfile(p,I/name)
core=(I/'core_top.v').read_text();qsf=(I/'ap_core.qsf').read_text()
macros=re.findall(r'^set_global_assignment -name VERILOG_MACRO (\S+)',qsf,re.M)
assert 'INCLUDE_GPU_WQ16' in macros and 'INCLUDE_GPU_RENDER_CACHE' in macros
assert 'INCLUDE_OS30_SDRAM166' not in macros
params=core[core.index('gpu_core #('):core.index(') gpu (',core.index('gpu_core #('))+len(') gpu (')]
wrapper=(I/'tb_gpu_wrpath.v').read_text()
wrapper=re.sub(r'gpu_core #\(.*?\) gpu \(',lambda m:params,wrapper,count=1,flags=re.S)
# Match the live core's barrier/idle-flush render-cache wiring exactly, with
# only local clock/reset/output signal names adapted to the test top.
cache=core[core.index('wire        gpu_mem_rd_arvalid'):core.index('`else\nassign gpu_mem_rd_arvalid',core.index('wire        gpu_mem_rd_arvalid'))]
cache=cache.replace('`ifdef INCLUDE_GPU_RENDER_CACHE\n','')
for old,new in [('reset_n_cpu_media','reset_n'),('clk_cpu','clk'),('gpu_reg_rdata','reg_rdata'),('gpu_reg_addr','reg_addr'),('gpu_busy','busy')]:cache=re.sub(r'\b'+old+r'\b',new,cache)
wrapper=wrapper.replace('.busy(busy), .fence_reached(fence_reached)', '.busy(gpu_busy_core), .fence_reached(fence_reached)')
wrapper=wrapper.replace('.reg_rdata(reg_rdata)', '.reg_rdata(gpu_reg_rdata_core)')
# reg_rdata identifier in extracted cache includes core suffix; adapt separately.
cache=cache.replace('gpu_reg_rdata_core','gpu_reg_rdata_core')
extra='''    .memory_barrier_done(gpu_cache_barrier_done), .memory_barrier_req(gpu_cache_barrier_req),
    .cache_clear_bypass(gpu_cache_clear_bypass), .cache_soft_reset(gpu_cache_soft_reset),
    .cache_tex_flush(gpu_cache_tex_flush), .mesh_reset_n(reset_n),
    .mesh_arready(1'b0), .mesh_rvalid(1'b0), .mesh_rdata(32'd0), .mesh_rlast(1'b0),
    .gpu_tex_mem_arready(1'b0), .gpu_tex_mem_rvalid(1'b0),
    .gpu_tex_mem_rdata(32'd0), .gpu_tex_mem_rlast(1'b0), .gpu_tex_mem_up_busy(1'b0),
'''
wrapper=wrapper.replace('    .slave_swap_pending(1\'b0),',extra+"    .slave_swap_pending(1'b0),")
start=wrapper.index('axi_sdram_arbiter sdram_arb (');end=wrapper.index('\n);',start)
arb=wrapper[start:end].replace('axi_sdram_arbiter sdram_arb (','axi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH(16)) sdram_arb (')
for pre in ['rd','wr']:arb=arb.replace('gpu_'+pre+'_','gpu_mem_'+pre+'_')
wrapper=wrapper[:start]+arb+wrapper[end:]
wrapper=wrapper.replace('endmodule',cache+'\nendmodule')
(I/'tb_gpu_wrpath.v').write_text(wrapper)
cpp=(I/'tb_gpu_wrpath_main.cpp').read_text()
cpp=cpp.replace('FB_W = 320, FB_H = 200','FB_W = 320, FB_H = 240').replace('FB_BYTES = FB_W * FB_H','FB_BYTES = FB_W * FB_H * 2')
start=cpp.index('static void gpu_kick() {');end=cpp.index('\nstatic uint32_t next_fence',start)
cpp=cpp[:start]+'''static void gpu_kick() {
    if (pending_stream.empty()) return;
    for (uint32_t word: pending_stream) {
        while (((ring_wrptr/4u + 1u) & 4095u) == (mmio_read(REG_RING_RDPTR) & 4095u)) tick();
        mmio_write(REG_RING_WRPTR, word);
        ring_wrptr = (ring_wrptr+4u)&ring_mask;
    }
    mmio_write(REG_CTRL, 8);
    if (mmio_read(REG_STATUS)&(1u<<9)) { fprintf(stderr,"CPU ring overflow\\n"); g_harness_error=true; }
    pending_stream.clear();
}
''' +cpp[end:]
cpp=cpp.replace('    mmio_write(REG_RING_WRPTR, 0);','    mmio_write(REG_CTRL, 16);')
start=cpp.index('// ============================================================\n// Affine span-group encoder')
end=cpp.index('// Byte write into SDRAM',start)
cpp=cpp[:start]+'''// Exact-current-QSF triangle and clear commands; no disabled compact-span op.
static uint32_t g_rows = FB_H;
static void emit_state(uint32_t fb_base) {
    uint32_t w[16]={};
    w[0]=fb_base; w[1]=FB_W*2; w[2]=2; w[3]=TEX_BASE;
    w[4]=1; w[5]=0; w[6]=0xA0u | (3u<<24); // RGB565 perspective, depth test+write
    w[11]=fb_base+0x40000; w[12]=FB_W*2; w[13]=2;
    w[14]=FB_W<<16; w[15]=FB_H<<16;
    ring_cmd(0x4A,16); for(uint32_t v:w)ring_write(v);
}
static void emit_tri(int x0,int y0,int x1,int y1,int x2,int y2,uint16_t color,int depth) {
    uint32_t w[17]={};
    w[0]=((uint32_t)y0<<16)|(x0*16); w[1]=((uint32_t)y1<<16)|(x1*16); w[2]=((uint32_t)y2<<16)|(x2*16);
    w[9]=w[10]=w[11]=1u<<16; w[12]=(uint32_t)color*0x10001u;w[13]=color;
    w[14]=w[15]=w[16]=depth;
    ring_cmd(0x4E,17);for(uint32_t v:w)ring_write(v);
}
static void render_scene(uint32_t fb_base) {
    rng_state=0x5EED1234u;
    // Arbitrary byte-aligned clears exercise mixed first/last strobes and
    // the no-allocate cache path before normal cached triangle writebacks.
    for(unsigned k=0;k<16;k++) {
        ring_cmd(0x11,3);ring_write(fb_base+k*FB_W*2+(k&3));
        ring_write(((33+k*7)<<16)|2);ring_write((FB_W*2<<16)|(0x40+k));
    }
    emit_state(fb_base);
    for(unsigned y=0;y<g_rows;y+=4) {
        int x0=rnd_range(1,31),x1=rnd_range(260,319),y1=y+4;
        uint16_t color=(uint16_t)(rnd()|0x0841u);
        emit_tri(x0,y,x1,y,x0,y1,color,65536+y*128);
        emit_tri(x1,y,x1,y1,x0,y1,color,65536+y*128);
        if((y&31)==28) {if(!submit_and_wait())return;}
    }
    submit_and_wait();
}

''' +cpp[end:]
# White single texel makes vertex RGB nonzero, but still exercises the real
# shared SDRAM texture-read/cache path. Clear depth independently each pass.
start=cpp.index('static void upload_texture() {');end=cpp.index('\nint main(',start)
cpp=cpp[:start]+'''static void upload_texture() { sdram_write(TEX_BASE/4,0xffffffffu); }
''' +cpp[end:]
cpp=cpp.replace('sdram_fill(FB_A_BASE, FB_BYTES, 0x00);','sdram_fill(FB_A_BASE, FB_BYTES, 0x00);\n    sdram_fill(FB_A_BASE+0x40000,FB_BYTES,0x00);')
cpp=cpp.replace('sdram_fill(FB_B_BASE, FB_BYTES, 0x00);','sdram_fill(FB_B_BASE, FB_BYTES, 0x00);\n    sdram_fill(FB_B_BASE+0x40000,FB_BYTES,0x00);')
needle='    total_diff += ndiff;'
extra='''    uint32_t nonzero=0,depthdiff=0;uint64_t fbhash=14695981039346656037ull,zhash=fbhash;
    for(uint32_t off=0;off<FB_BYTES;off++) {
        uint8_t a=sdram_read_byte(FB_A_BASE+off),z=sdram_read_byte(FB_A_BASE+0x40000+off);
        if(a)nonzero++;
        fbhash=(fbhash^a)*1099511628211ull; zhash=(zhash^z)*1099511628211ull;
        if(z!=sdram_read_byte(FB_B_BASE+0x40000+off))depthdiff++;
    }
    printf("QSF gate: nonzero=%u depthdiff=%u fbhash=%016llx zhash=%016llx\\n",nonzero,depthdiff,(unsigned long long)fbhash,(unsigned long long)zhash);
    if(nonzero<10000||depthdiff)g_harness_error=true;
'''
cpp=cpp.replace(needle,needle+'\n'+extra)
(I/'tb_gpu_wrpath_main.cpp').write_text(cpp)
obj=R/'obj'
rtl=[n for n in paths if n not in ['core_top.v','ap_core.qsf']]
cmd=['verilator','--cc','--exe','--build','--assert','-j','2','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_gpu_wrpath','--Mdir',str(obj),f'-I{I}',*[f'-D{m}'for m in macros],'-CFLAGS','-std=c++17 -O2',*[str(I/n)for n in rtl]]
receipt={'source_pins':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths.values()},'test_input_pins':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in I.iterdir()},'qsf_macros':macros,'build_command':cmd,'start':time.time(),'scope':'Current QSF GPU/cache/WQ16 parameters with real arbiter, slave and SDRAM model; directed triangle/clear workload, not game FPS.'}
(R/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
with(R/'build.log').open('w')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
with(R/'run.log').open('w')as log:subprocess.run([str(obj/'Vtb_gpu_wrpath')],cwd=R,stdout=log,stderr=subprocess.STDOUT,check=True)
receipt.update(status='PASS',elapsed_seconds=time.time()-receipt['start'])
(R/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('Current QSF triangle/cache/WQ16 real SDRAM contention PASS')
