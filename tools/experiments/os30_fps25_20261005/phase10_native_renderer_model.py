#!/usr/bin/env python3
"""Private actual-CPU renderer fixture. No production files are changed."""
import argparse, hashlib, json, re, shutil, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OLD = P / 'lod5-live-compact-v3/candidate'
CORE = P / 'compact-lod5-core-v7/frozen'
OUT = P / 'native-renderer-window-v1/model-v3'
REL = Path('docs/measurements/os30-dual-20261004/validation/baseline128')
CPU = Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
CACHE64 = False
RENDER32 = False
RENDER16 = False
RVC19 = False
TIMING_COMBO = False
RVC_PIPELINE = False
CACHE_STORAGE = False
LOAD_FORWARD = False
TEXTURE_READ = False

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, value):
    with p.open('x') as f: json.dump(value, f, indent=2); f.write('\n')
def once(s, a, b):
    assert s.count(a) == 1, (a, s.count(a))
    return s.replace(a, b)

PORTS = '''
    input wire cpu_run,
    input wire inj_audio_valid,
    output wire obs_audio_ready, obs_audio_rvalid, obs_audio_rlast,
    output wire [31:0] obs_audio_rdata,
    input wire bd_gpu_wr, input wire [3:0] bd_gpu_addr,
    input wire [31:0] bd_gpu_data,
    input wire bd_geo_wr, input wire [9:0] bd_geo_addr,
    input wire [31:0] bd_geo_data,
    output wire [31:0] obs_gpu_rdata,
    output wire obs_gpu_busy, obs_bcr_ready,
    output wire [31:0] obs_fence,
    output wire obs_retire, output wire [31:0] obs_retire_pc, obs_retire_uop,
    output wire obs_freeze, obs_hazard,
    output wire obs_upload, output wire [31:0] obs_upload_word,
    output wire obs_gpu_mmio, output wire [3:0] obs_gpu_mmio_addr,
    output wire [31:0] obs_gpu_mmio_data,
    output wire obs_geo_mmio, output wire [9:0] obs_geo_mmio_addr,
    output wire [31:0] obs_geo_mmio_data,
    output wire [15:0] obs_cram_errors,
'''

def prepare():
    OUT.mkdir(parents=True, exist_ok=False)
    shutil.copytree(OLD / 'frozen', OUT / 'frozen')
    manifest = json.loads((P / 'compact-lod5-core-v7/prepared.json').read_text())
    for rel, h in manifest['source_sha256'].items():
        src = CORE / rel
        assert sha(src) == h, rel
        dst = OUT / 'frozen' / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    if CACHE64:
        replacement=P/'cpu-cache64-resource-v1/frozen'/CPU
        assert sha(replacement)=='ab3205dff8e38211a223234e237a4ee083b55ecd0a02cb915c58850a34ce322e'
        shutil.copyfile(replacement,OUT/'frozen'/CPU)
    if RVC19:
        replacement=P/'cpu-cache64-rvc19-v1/VexiiRiscv.v'
        assert sha(replacement)=='32cf8619ebd6beb9b7c62e593e300ba85486daf9c70c199ad0f978a23b80b3ea'
        shutil.copyfile(replacement,OUT/'frozen'/CPU)
    if RVC_PIPELINE:
        replacement=P/'cpu-cache64-rvcpipe-v1/VexiiRiscv.v'
        assert sha(replacement)=='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
        review=P/'cpu-cache64-rvcpipe-source-review-v1/manifest.json'
        assert sha(review)=='5917e78ef2fdd0364ebcdcda845e7c243afb696b706283083c89b5df04f18f85'
        shutil.copyfile(replacement,OUT/'frozen'/CPU)
    if TIMING_COMBO or RVC_PIPELINE:
        for name,src,h in [
            ('gpu_core.v','gpu-recip-write-v1/gpu_core.v','238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb'),
            ('audio_mixer.v','audio-ramp-flat-v1/audio_mixer.v','f9cbfe37da81ae6d5564df076e75c6d28f46feabb992bb695392d06aa55e0ba4')]:
            replacement=P/src
            assert sha(replacement)==h
            shutil.copyfile(replacement,OUT/'frozen/src/fpga/common'/name)
    if LOAD_FORWARD:
        assert sha(OUT/'frozen'/CPU)=='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
        replacement=P/'cpu-load-forward-v1/VexiiRiscv.v'
        assert sha(replacement)=='82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
        shutil.copyfile(replacement,OUT/'frozen'/CPU)
    if CACHE_STORAGE:
        replacement=P/'render-cache-storage-v3/gpu_color_depth_cache.sv'
        assert sha(replacement)=='25343436b682e7875a170c144b32ee06803c53177bf0c999626043587741ee3a'
        shutil.copyfile(replacement,OUT/'frozen/src/fpga/common/gpu_color_depth_cache.sv')
    if TEXTURE_READ:
        target=OUT/'frozen/src/fpga/common/gpu_tex_cache.v'
        assert sha(target)=='3999efcfd3c233e4b866c8462dfa211c25ae9ac2e0f4e7ca4929b64efc6b3fef'
        replacement=P/'texture-read-address-v1/gpu_tex_cache.v'
        assert sha(replacement)=='c3fdc0adfe5ed583c37bf09ad3dbbbecb1e3c6ae1f5714cab5064eacc328e51c'
        shutil.copyfile(replacement,target)
    top = OUT / 'frozen' / REL / 'tb_irq.v'
    s = top.read_text()
    s = once(s, 'module tb_system (', 'module tb_system (\n' + PORTS)
    start = s.index('// Test-only controls live')
    end = s.index('wire geometry_reg_wr;', start)
    s = s[:start] + '''// Reset-held initialization is excluded from measurement.
wire fixture_cpu_reset_n=reset_n&&cpu_run;
assign dbg_test_reset=!fixture_cpu_reset_n;
wire alias_hold=1'b0;
wire alias_raw_rvalid;
assign cpu_sdram_rvalid=alias_raw_rvalid;
assign dbg_native_uploads=native_upload_words;
assign dbg_native_loads=native_load_bursts;
assign dbg_native_alias=native_alias_beats;
assign dbg_native_stalls=native_alias_stalls;
assign dbg_native_texture=0;
assign dbg_native_resets=0;
assign dbg_native_geo_wait=0;
wire gpu_mmio_wr=cpu_run?gpu_reg_wr:bd_gpu_wr;
wire [3:0] gpu_mmio_addr=cpu_run?gpu_reg_addr:bd_gpu_addr;
wire [31:0] gpu_mmio_data=cpu_run?gpu_reg_wdata:bd_gpu_data;
wire geo_mmio_wr=cpu_run?geometry_reg_wr:bd_geo_wr;
wire [9:0] geo_mmio_addr=cpu_run?geometry_reg_addr:bd_geo_addr;
wire [31:0] geo_mmio_data=cpu_run?geometry_reg_wdata:bd_geo_data;
assign obs_gpu_rdata=gpu_reg_rdata;
assign obs_bcr_ready=bcr_st==B_DONE;
assign obs_cram_errors=cram_errors;
assign obs_retire=cpu_sys.cpu.WhiteboxerPlugin_logic_commits_ports_0_valid;
assign obs_retire_pc=cpu_sys.cpu.WhiteboxerPlugin_logic_commits_ports_0_pc;
assign obs_retire_uop=cpu_sys.cpu.WhiteboxerPlugin_logic_commits_ports_0_uop;
assign obs_freeze=cpu_sys.cpu._zz_WhiteboxerPlugin_logic_perf_executeFreezedCounter;
assign obs_hazard=cpu_sys.cpu._zz_WhiteboxerPlugin_logic_perf_dispatchHazardsCounter;
assign obs_upload=gpu.cpu_ring_write;
assign obs_upload_word=gpu_mmio_data;
assign obs_gpu_mmio=gpu_reg_wr&&cpu_run;
assign obs_gpu_mmio_addr=gpu_reg_addr;
assign obs_gpu_mmio_data=gpu_reg_wdata;
assign obs_geo_mmio=geometry_reg_wr&&cpu_run;
assign obs_geo_mmio_addr=geometry_reg_addr;
assign obs_geo_mmio_data=geometry_reg_wdata;
always @(posedge clk_cpu) if(cpu_run && (bd_gpu_wr || bd_geo_wr))
    $fatal(1,"Host MMIO initialization after native CPU release");
''' + s[end:]
    # All original alias/CRAM/AXI logic is retained, without artificial stalls.
    s = once(s, '.reg_wr(geometry_reg_wr),.reg_addr(geometry_reg_addr),.reg_wdata(geometry_reg_wdata),.reg_wstrb(geometry_reg_wstrb),.reg_rdata(geometry_reg_rdata),',
             '.reg_wr(geo_mmio_wr),.reg_addr(geo_mmio_addr),.reg_wdata(geo_mmio_data),.reg_wstrb(cpu_run?geometry_reg_wstrb:4\'hf),.reg_rdata(geometry_reg_rdata),')
    s = once(s, 'assign tex_arready=0;assign tex_rvalid=0;assign tex_rdata=0;assign tex_rlast=0;\n', '')
    s = once(s, '.s0_arvalid(probe_arvalid),.s0_arready(probe_arready),.s0_araddr(probe_araddr),.s0_arlen(8\'d15),.s0_rvalid(probe_rvalid),.s0_rdata(probe_rdata),.s0_rlast(probe_rlast),',
             '.s0_arvalid(tex_arvalid),.s0_arready(tex_arready),.s0_araddr(tex_araddr),.s0_arlen(tex_arlen),.s0_rvalid(tex_rvalid),.s0_rdata(tex_rdata),.s0_rlast(tex_rlast),')
    # Keep peripherals active during initialization; only the CPU is held.
    a = s.index('axi_periph_slave periph ('); b = s.index('// gpu_core', a)
    s = s[:a] + s[a:b].replace('.reset_n(fixture_cpu_reset_n)', '.reset_n(reset_n)') + s[b:]
    coretop = (CORE / 'src/fpga/targets/pocket/core_top.v').read_text()
    aa=coretop.index('axi_sdram_arbiter #(\n'); ab=coretop.index(') sdram_arb (',aa)+len(') sdram_arb (')
    s=once(s,'axi_sdram_arbiter sdram_arb (',coretop[aa:ab])
    s=once(s,'    // Slave side\n', '''    // Captured synthetic audio read pressure, not native audio execution.
    .m3_arvalid(inj_audio_valid), .m3_arready(obs_audio_ready),
    .m3_araddr(32'h02c00000), .m3_arlen(8'd15),
    .m3_rvalid(obs_audio_rvalid), .m3_rdata(obs_audio_rdata),
    .m3_rlast(obs_audio_rlast), .m3_rready(1'b1),
    // Slave side
''')
    pa=coretop.index('axi_periph_slave #(\n'); pb=coretop.index(') periph (',pa)+len(') periph (')
    s=once(s,'axi_periph_slave periph (',coretop[pa:pb])
    a = coretop.index('gpu_core #(\n'); b = coretop.index(') gpu (', a) + len(') gpu (')
    s = once(s, 'gpu_core #(.INCLUDE_CPU_RING(1),.INCLUDE_COMMAND_DMA(0)) gpu (', coretop[a:b])
    a = s.index(') gpu ('); b = s.index('// SRAM Model', a)
    g = s[a:b]
    for direction in ('rd', 'wr'):
        g = re.sub(r'\bgpu_' + direction + r'_(\w+)\b', r'gpu_s_' + direction + r'_\1', g)
    for a0, b0 in [('.reg_wr      (gpu_reg_wr)', '.reg_wr      (gpu_mmio_wr)'),
                   ('.reg_addr    (gpu_reg_addr)', '.reg_addr    (gpu_mmio_addr)'),
                   ('.reg_wdata   (gpu_reg_wdata)', '.reg_wdata   (gpu_mmio_data)'),
                   ('.reg_rdata   (gpu_reg_rdata)', '.reg_rdata   (gpu_reg_rdata_core)'),
                   ('.busy        ()', '.busy        (gpu_busy_core)'),
                   ('.fence_reached()', '.fence_reached(obs_fence),\n    .memory_barrier_done(gpu_cache_barrier_done),\n    .memory_barrier_req(gpu_cache_barrier_req),\n    .cache_clear_bypass(gpu_cache_clear_bypass),\n    .cache_soft_reset(gpu_cache_soft_reset),\n    .cache_tex_flush(gpu_cache_tex_flush)')]:
        g = once(g, a0, b0)
    acceptance = (P / 'renderer-pgo-coupled-v1/model/frozen/tb_gpu.v').read_text()
    c0 = acceptance.index('wire        gpu_s_rd_arvalid'); c1 = acceptance.index('\n`endif', c0)
    cache = acceptance[c0:c1]
    cache = cache.replace('posedge clk)', 'posedge clk_cpu)').replace('.clk(clk)', '.clk(clk_cpu)')
    cache = cache.replace('assign busy =', 'assign obs_gpu_busy =').replace('assign reg_rdata =', 'assign gpu_reg_rdata =')
    cache = cache.replace('(reg_addr == 4\'d5 && busy)', '(gpu_mmio_addr == 4\'d5 && obs_gpu_busy)')
    s = s[:a] + g + cache + '\n\n' + s[b:]
    if RENDER32:
        s=once(s,'.WAYS(2), .SET_BITS(6), .WORD_BITS(4)', '.WAYS(2), .SET_BITS(8), .WORD_BITS(4)')
    if RENDER16:
        s=once(s,'.WAYS(2), .SET_BITS(6), .WORD_BITS(4)', '.WAYS(2), .SET_BITS(7), .WORD_BITS(4)')
    top.write_text(s)
    # Fresh DQ split of the exact selected controller. No old behavioral edit.
    io = (CORE / 'src/fpga/targets/pocket/io_sdram.v').read_text()
    io = once(io, 'inout   wire    [15:0]  phy_dq,', 'input wire [15:0] phy_dq_in,\noutput wire [15:0] phy_dq_out_port,\noutput wire phy_dq_oe_port,')
    io = once(io, "    assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;", '    assign phy_dq_out_port=phy_dq_out;\n    assign phy_dq_oe_port=phy_dq_oe;')
    io = io.replace('(phy_dq)', '(phy_dq_in)').replace('<= phy_dq;', '<= phy_dq_in;')
    (OUT / 'frozen' / REL / 'io_sdram_pocket_test.v').write_text(io)
    qsf = P / 'compact-lod5-core-v7/fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
    defs = sorted(set(re.findall(r'^set_global_assignment -name VERILOG_MACRO (\S+)', qsf.read_text(), re.M)))
    rawcmd = json.loads((OLD / 'prepared.json').read_text())['command']
    cmd = [x.replace(str(OLD), str(OUT)) for i,x in enumerate(rawcmd)
           if not x.startswith('-D') and not x.startswith('+define+')
           and not (x=='-CFLAGS' and i+1<len(rawcmd) and rawcmd[i+1]=='-DSYSTEM_NO_REGFILE_PEEK')]
    # Initial elaboration builds the model library; the runner is added later.
    cmd.remove('--exe'); cmd.remove(str(OUT / 'frozen' / REL / 'main.cpp'))
    cmd[cmd.index('--cc'):cmd.index('--cc')] = ['-D' + d for d in defs]
    cmd += ['../common/gpu_color_depth_cache.sv']
    save(OUT / 'prepared.json', dict(command=cmd, defs=defs, cpu_sha256=sha(OUT/'frozen'/CPU),
         cache_layout='I64/D64' if CACHE64 else 'I32/D128',
         gpu_cache_kib=32 if RENDER32 else 16 if RENDER16 else 8,
         rvc_bit19_plane=RVC19,
         equivalent_timing_components=TIMING_COMBO or RVC_PIPELINE,
         **(dict(render_cache_storage='Synchronous victim RAM; data banks at most 2048x40, unchanged transaction latency') if CACHE_STORAGE else {}),
         **(dict(cpu_load_forward_reassociated=True) if LOAD_FORWARD else {}),
         **(dict(texture_owned_read_address=True) if TEXTURE_READ else {}),
         **(dict(rvc_pipeline=dict(decompress_at=1,decoder_at=2,dispatcher_at=3,
              source_review_sha256=sha(P/'cpu-cache64-rvcpipe-source-review-v1/manifest.json'),
              author_manifest_sha256=sha(P/'cpu-cache64-rvcpipe-v1/manifest.json'),
              whole_cpu_replacement=True,cycle_equivalence_claim=False)) if RVC_PIPELINE else {}),
         source_sha256={str(f.relative_to(OUT)):sha(f) for f in (OUT/'frozen').rglob('*') if f.is_file()},
         recipe_sha256=sha(Path(__file__)), selected_qsf_sha256=sha(qsf),
         limitations=['Cold CPU/cache start; guest restore warms its own instructions/data.',
          'Guest service bridge replaces OS services. No game/audio execution or native full-frame FPS claim.',
          'Physical DRAM and CRAM use retained behavioral pin models; display scanout is injected.']))
    shutil.copyfile(__file__, OUT/'recipe-prepare.py')
    print(OUT, flush=True)

def build():
    d=json.loads((OUT/'prepared.json').read_text())
    for n,h in d['source_sha256'].items(): assert sha(OUT/n)==h,n
    start=time.monotonic()
    with (OUT/'build.log').open('x') as f:
        r=subprocess.run(d['command'],cwd=OUT/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT)
    save(OUT/'build.json',dict(returncode=r.returncode,seconds=time.monotonic()-start))
    print('native model library build',r.returncode,flush=True)
    assert r.returncode==0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build']);p.add_argument('--cache64',action='store_true');p.add_argument('--render32',action='store_true');p.add_argument('--render16',action='store_true');p.add_argument('--rvc19',action='store_true');p.add_argument('--timing-combo',action='store_true');p.add_argument('--rvc-pipeline',action='store_true');p.add_argument('--cache-storage',action='store_true');p.add_argument('--load-forward',action='store_true');p.add_argument('--texture-read',action='store_true');a=p.parse_args()
    if a.cache64:
        CACHE64=True
        OUT=P/'native-renderer-window-v1/model-cache64-v1'
    if a.render32:
        assert a.cache64 and not a.render16
        RENDER32=True
        OUT=P/'native-renderer-window-v1/model-cache64-render32-v1'
    if a.render16:
        assert a.cache64 and not a.render32
        RENDER16=True
        OUT=P/'native-renderer-window-v1/model-cache64-render16-v1'
    if a.rvc19:
        assert a.cache64
        RVC19=True
        OUT=P/('native-renderer-window-v1/model-cache64-render'+('32' if a.render32 else '16' if a.render16 else '8')+'-rvc19-v1')
    if a.timing_combo:
        assert a.cache64 and a.rvc19 and not a.render16 and not a.render32
        TIMING_COMBO=True
        OUT=P/'native-renderer-window-v1/model-cache64-timing-v1'
    if a.rvc_pipeline:
        assert a.cache64 and not any((a.rvc19,a.render16,a.timing_combo))
        RVC_PIPELINE=True
        OUT=P/('native-renderer-window-v1/model-cache64-rvcpipe-render32-v1' if a.render32 else 'native-renderer-window-v1/model-cache64-rvcpipe-v1')
    if a.cache_storage:
        assert a.rvc_pipeline
        CACHE_STORAGE=True
        OUT=P/('native-renderer-window-v1/model-cache64-rvcpipe-render32-storage-v3' if a.render32 else 'native-renderer-window-v1/model-cache64-rvcpipe-render8-storage-v3')
    if a.load_forward:
        assert a.rvc_pipeline and a.cache_storage and a.render32
        LOAD_FORWARD=True
        OUT=P/'native-renderer-window-v1/model-cache64-rvcpipe-render32-storage-v3-forward-v1'
    if a.texture_read:
        assert a.load_forward
        TEXTURE_READ=True
        OUT=P/'native-renderer-window-v1/model-cache64-render32-forward-texture-v1'
    globals()[a.action]()
