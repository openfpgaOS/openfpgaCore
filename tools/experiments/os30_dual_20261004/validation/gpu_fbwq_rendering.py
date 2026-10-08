#!/usr/bin/env python3
"""Compare a private GPU candidate against countNZ with frozen os30 fixtures."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
FROZEN = ROOT / 'docs/measurements/os30-dual-20261004/validation/gpu-count-nonzero'

MONITOR = r'''
// Observation only: valid-qualified payload and AXI activity are compared.
reg mon_was_reset_n = 1'b1;
reg mon_reset_sampled = 1'b0;
reg [63:0] mon_cycles = 0, mon_hard = 0, mon_hard_nonzero = 0;
reg [63:0] mon_soft = 0, mon_soft_nonzero = 0, mon_observed = 0;
reg [63:0] mon_invalid_nonzero = 0, mon_aw = 0, mon_w = 0;
reg [63:0] mon_payload_hash = 64'hcbf29ce484222325;
reg [63:0] mon_axi_hash = 64'hcbf29ce484222325;
always @(posedge clk) begin
    mon_cycles = mon_cycles + 1;
    if (!reset_n && mon_was_reset_n) begin
        mon_hard = mon_hard + 1;
        if ({gpu.fbwq_req_addr,gpu.fbwq_req_data,gpu.fbwq_req_strb} != 0)
            mon_hard_nonzero = mon_hard_nonzero + 1;
    end
    mon_was_reset_n = reset_n;
    if (reset_n && gpu.soft_reset) begin
        mon_soft = mon_soft + 1;
        if ({gpu.fbwq_req_addr,gpu.fbwq_req_data,gpu.fbwq_req_strb} != 0)
            mon_soft_nonzero = mon_soft_nonzero + 1;
    end
    mon_reset_sampled = !reset_n || gpu.soft_reset;
    if (reset_n && !gpu.soft_reset) begin
        if (gpu.fbwq_req_valid || gpu.zw_snoop_pending || gpu.cbw_snoop_pending) begin
            mon_observed = mon_observed + 1;
            mon_payload_hash = (mon_payload_hash ^ mon_cycles) * 64'h100000001b3;
            mon_payload_hash = (mon_payload_hash ^ {gpu.fbwq_req_addr,gpu.fbwq_req_data,gpu.fbwq_req_strb}) * 64'h100000001b3;
            mon_payload_hash = (mon_payload_hash ^ {gpu.fbwq_req_valid,gpu.zw_snoop_pending,gpu.cbw_snoop_pending}) * 64'h100000001b3;
        end else if ({gpu.fbwq_req_addr,gpu.fbwq_req_data,gpu.fbwq_req_strb} != 0)
            mon_invalid_nonzero = mon_invalid_nonzero + 1;
        if (gpu.m_wr_awvalid) begin
            mon_aw = mon_aw + 1;
            mon_axi_hash = (mon_axi_hash ^ mon_cycles) * 64'h100000001b3;
            mon_axi_hash = (mon_axi_hash ^ {gpu.m_wr_awaddr,gpu.m_wr_awlen,gpu.m_wr_awready}) * 64'h100000001b3;
        end
        if (gpu.m_wr_wvalid) begin
            mon_w = mon_w + 1;
            mon_axi_hash = (mon_axi_hash ^ mon_cycles) * 64'h100000001b3;
            mon_axi_hash = (mon_axi_hash ^ {gpu.m_wr_wdata,gpu.m_wr_wstrb,gpu.m_wr_wlast,gpu.m_wr_wready}) * 64'h100000001b3;
        end
    end
end
always @(negedge clk) if (mon_reset_sampled)
    if (gpu.fbwq_req_valid || gpu.zw_snoop_pending || gpu.cbw_snoop_pending)
        $fatal(1,"reset failed to invalidate framebuffer request/snoop observers");
final begin
    $display("FBWQ_MON cycles=%0d hard=%0d hard_nonzero=%0d soft=%0d soft_nonzero=%0d observed=%0d invalid_nonzero=%0d aw=%0d w=%0d payload_hash=%016x axi_hash=%016x", mon_cycles,mon_hard,mon_hard_nonzero,mon_soft,mon_soft_nonzero,mon_observed,mon_invalid_nonzero,mon_aw,mon_w,mon_payload_hash,mon_axi_hash);
end
'''

CACHE_MONITOR = r'''
reg [63:0] cachemon_cycles=0, cachemon_hot0=0, cachemon_hot1=0;
reg [63:0] cachemon_reads=0, cachemon_writes=0, cachemon_both=0;
reg cachemon_reset=0;
always @(posedge clk) begin
    cachemon_cycles=cachemon_cycles+1;
    cachemon_reset=!gpu_render_cache.reset_n;
    if (gpu_render_cache.reset_n && gpu_render_cache.state==gpu_render_cache.IDLE) begin
        if (gpu_render_cache.hot0_hit !== (gpu_render_cache.hot0_valid && gpu_render_cache.hot0_line==gpu_render_cache.lookup_addr[25:6]))
            $fatal(1,"hot0 lookup differs in IDLE");
        if (gpu_render_cache.hot1_hit !== (gpu_render_cache.hot1_valid && gpu_render_cache.hot1_line==gpu_render_cache.lookup_addr[25:6]))
            $fatal(1,"hot1 lookup differs in IDLE");
        if (gpu_render_cache.s_awvalid && gpu_render_cache.s_awready) begin
            cachemon_writes=cachemon_writes+1;
            if (gpu_render_cache.s_arvalid) cachemon_both=cachemon_both+1;
        end
        if (gpu_render_cache.s_arvalid && gpu_render_cache.s_arready) cachemon_reads=cachemon_reads+1;
        if ((gpu_render_cache.s_awvalid && gpu_render_cache.s_awready) || (gpu_render_cache.s_arvalid && gpu_render_cache.s_arready)) begin
            if (gpu_render_cache.hot0_hit) cachemon_hot0=cachemon_hot0+1;
            if (gpu_render_cache.hot1_hit) cachemon_hot1=cachemon_hot1+1;
        end
    end
end
always @(negedge clk) if(cachemon_reset)
    if(gpu_render_cache.hot0_valid || gpu_render_cache.hot1_valid)
        $fatal(1,"reset retained hot-line validity");
final $display("CACHEHOT_MON cycles=%0d hot0=%0d hot1=%0d reads=%0d writes=%0d simultaneous=%0d",cachemon_cycles,cachemon_hot0,cachemon_hot1,cachemon_reads,cachemon_writes,cachemon_both);
'''

RING_MONITOR = r'''
reg ringmon_write=0;
reg [11:0] ringmon_addr=0;
reg [31:0] ringmon_data=0;
reg [63:0] ringmon_cpu=0, ringmon_dma=0, ringmon_wrap=0, ringmon_full=0;
reg [63:0] ringmon_hash=64'hcbf29ce484222325;
always @(posedge clk) begin
    ringmon_write=gpu.dma_ring_wr_raw || gpu.cpu_ring_write;
    ringmon_addr=gpu.ring_wr_addr;
    // The original accepted-write data expression is the independent oracle.
    ringmon_data=gpu.cpu_ring_write ? reg_wdata : gpu.dma_ring_wdata_raw;
    if (gpu.dma_ring_wr_raw && gpu.cpu_ring_write)
        $fatal(1,"simultaneous CPU and DMA ring write");
    if (reset_n) begin
        if(gpu.cpu_ring_req && gpu.cpu_ring_full) ringmon_full=ringmon_full+1;
        if(gpu.cpu_ring_write) ringmon_cpu=ringmon_cpu+1;
        if(gpu.dma_ring_wr_raw) ringmon_dma=ringmon_dma+1;
        if(ringmon_write) begin
            if(ringmon_addr==12'hfff) ringmon_wrap=ringmon_wrap+1;
            ringmon_hash=(ringmon_hash ^ {ringmon_addr,ringmon_data})*64'h100000001b3;
        end
    end
end
always @(negedge clk) if(ringmon_write)
    if(gpu.ring_bram[ringmon_addr] !== ringmon_data)
        $fatal(1,"ring RAM write disagrees with original data selection");
final $display("RING_MON cpu=%0d dma=%0d wrap=%0d full_reject=%0d hash=%016x",ringmon_cpu,ringmon_dma,ringmon_wrap,ringmon_full,ringmon_hash);
'''

TEXTURE_OFFER_MONITOR = r'''
reg texmon_reset_seen=0;
reg [63:0] texmon_cycles=0, texmon_fill_a=0, texmon_offer=0, texmon_no_offer=0, texmon_unqualified_offer=0;
wire texmon_offered=(gpu.state==gpu.S_FRAG_PIPE) && (gpu.tex_stream.req_count!=0) && (gpu.tex_stream.res_reserved<3'd4);
always @(posedge clk) begin
    texmon_cycles=texmon_cycles+1;
    if(!reset_n) texmon_reset_seen=1;
    if(reset_n && texmon_reset_seen) begin
        if(gpu.tex_cache.fill_resp_valid_a !== ((gpu.tex_cache.state==gpu.tex_cache.S_FILL_OUT) && !gpu.tex_cache.lat_port))
            $fatal(1,"texture A fill response invariant");
        if(gpu.tex_cache.fill_resp_valid_b !== ((gpu.tex_cache.state==gpu.tex_cache.S_FILL_OUT) && gpu.tex_cache.lat_port))
            $fatal(1,"texture B fill response invariant");
        if(texmon_offered && !gpu.tex_req_valid) texmon_unqualified_offer=texmon_unqualified_offer+1;
        if(gpu.tex_cache.state==gpu.tex_cache.S_FILL_OUT && !gpu.tex_cache.lat_port) begin
            texmon_fill_a=texmon_fill_a+1;
            if(gpu.tex_req_valid !== texmon_offered) $fatal(1,"texture offer differs from qualified request during A fill output");
            if(texmon_offered) texmon_offer=texmon_offer+1;
            else texmon_no_offer=texmon_no_offer+1;
        end
        if(gpu.tex_cache.prime_a !== (gpu.tex_cache.state_fillout_prime && !gpu.tex_cache.lat_port && !gpu.tex_cache.flush_block && !texmon_offered))
            $fatal(1,"texture prime differs from proven early offer selection");
    end
end
final $display("TEXOFFER_MON cycles=%0d fill_a=%0d offer=%0d no_offer=%0d unqualified_offer=%0d errors=0",texmon_cycles,texmon_fill_a,texmon_offer,texmon_no_offer,texmon_unqualified_offer);
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as stream:
        subprocess.run(list(map(str, command)), check=True, stdout=stream,
                       stderr=subprocess.STDOUT, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--reference', type=Path, default=FROZEN/'gpu_core.v')
    parser.add_argument('--cache-candidate', type=Path,
                        help='Optional cache derivative; cache geometry stays frozen at os30 2x64x16 words.')
    parser.add_argument('--cache-reference', type=Path,
                        help='Optional already-qualified reference cache; preserves frozen os30 geometry.')
    parser.add_argument('--ring-monitor', action='store_true',
                        help='Check every actual ring RAM write against the original data expression.')
    parser.add_argument('--texture-cache-candidate',type=Path)
    parser.add_argument('--texture-offer-monitor',action='store_true')
    parser.add_argument('--texture-queues-bram',action='store_true',help='Match the selected fit: remove only INCLUDE_TEX_QUEUE_MLAB.')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    dest = args.output.resolve()
    dest.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__), dest/'runner-snapshot.py')
    candidate = args.candidate.resolve()
    common, test = dest / 'common', dest / 'test'
    shutil.copytree(FROZEN / 'rendering/common', common)
    shutil.copytree(FROZEN / 'rendering/test', test)
    if args.cache_reference:
        shutil.copy2(args.cache_reference.resolve(),common/'gpu_color_depth_cache.sv')
    shutil.copy2(args.reference.resolve(), dest / 'baseline-gpu_core.v')
    shutil.copy2(candidate, dest / 'candidate-gpu_core.v')
    config = json.loads((FROZEN / 'rendering/manifest.json').read_text())
    if args.texture_queues_bram:
        definitions=config['variant']['defs'].split();assert definitions.count('INCLUDE_TEX_QUEUE_MLAB')==1
        definitions.remove('INCLUDE_TEX_QUEUE_MLAB');config['variant']['defs']=' '.join(definitions)
    fixture = test / 'tb_gpu.v'
    text = fixture.read_text()
    at = text.rindex('endmodule')
    fixture.write_text(text[:at] + MONITOR + (CACHE_MONITOR if args.cache_candidate else '')
                       + (RING_MONITOR if args.ring_monitor else '')
                       + (TEXTURE_OFFER_MONITOR if args.texture_offer_monitor else '') + '\n' + text[at:])
    if args.texture_cache_candidate:
        shutil.copy2(args.texture_cache_candidate.resolve(),dest/'candidate-gpu_tex_cache.v')
    if args.cache_candidate:
        assert 'INCLUDE_GPU_RENDER_CACHE' in config['variant']['defs'].split()
        shutil.copy2(args.cache_candidate.resolve(), dest/'candidate-gpu_color_depth_cache.sv')
    harness = test / 'tb_gpu_retained_main.cpp'
    text = harness.read_text()
    assert text.count('    delete tb;') == 1
    harness.write_text(text.replace('    delete tb;', '    tb->final(); // Emit observation-only monitor summary.\n    delete tb;'))
    manifest = dict(frozen_config_path=str(FROZEN / 'rendering/manifest.json'),
                    frozen_config_sha256=sha(FROZEN / 'rendering/manifest.json'),
                    variant=config['variant'], parameters=config['parameters'],
                    flags=config['flags'], cflags=config['cflags'],
                    candidate_original=str(candidate), script_sha256=sha(Path(__file__)),
                    baseline_original=str(args.reference.resolve()),
                    cache_reference_original=str(args.cache_reference.resolve()) if args.cache_reference else None,
                    ring_monitor=args.ring_monitor,
                    texture_offer_monitor=args.texture_offer_monitor,texture_queues_bram=args.texture_queues_bram,
                    baseline_sha256=sha(dest/'baseline-gpu_core.v'),
                    candidate_sha256=sha(dest/'candidate-gpu_core.v'),
                    sources={str(p.relative_to(dest)):sha(p) for p in
                             [*common.glob('*.*'), *test.glob('*.*')] if p.is_file()})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    if args.cache_candidate:
        manifest['cache_candidate_original']=str(args.cache_candidate.resolve())
        manifest['cache_candidate_sha256']=sha(dest/'candidate-gpu_color_depth_cache.sv')
        manifest['cache_baseline_sha256']=sha(common/'gpu_color_depth_cache.sv')
        (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if args.texture_cache_candidate:
        manifest['texture_cache_candidate_original']=str(args.texture_cache_candidate.resolve())
        manifest['texture_cache_candidate_sha256']=sha(dest/'candidate-gpu_tex_cache.v')
        manifest['texture_cache_baseline_sha256']=sha(common/'gpu_tex_cache.v')
        (dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    scenarios = [('quiet', []), ('delayed', ['+gpu_rd_latency=24', '+gpu_rd_latency_var=1', '+gpu_wr_latency=17']),
                 ('gapped', ['+gpu_rd_latency=24', '+gpu_rd_latency_var=1', '+gpu_wr_latency=17', '+gpu_rd_beat_gap=7'])]

    def one(label):
        job = dest / label
        job.mkdir()
        sources = [test/'tb_gpu.v', dest/(label+'-gpu_core.v'), common/'gpu_geometry_worker.v',
                   common/'gpu_edge_walker.v', dest/'candidate-gpu_tex_cache.v' if args.texture_cache_candidate and label=='candidate' else common/'gpu_tex_cache.v',
                   dest/'candidate-gpu_color_depth_cache.sv' if args.cache_candidate and label=='candidate' else common/'gpu_color_depth_cache.sv',
                   test/'tb_gpu_retained_main.cpp']
        command = ['verilator', '--cc', '--exe', '--build', '--trace', '-j', '2', '--assert',
                   '-Wall', '-Wno-fatal', '-Wno-BADVLTPRAGMA', '--top-module', 'tb_gpu',
                   '--Mdir', str(job/'obj'), '-I'+str(common), '-CFLAGS', ' '.join(config['cflags']),
                   *['+define+'+d for d in config['variant']['defs'].split()], *config['flags'], *map(str,sources)]
        run(command, job/'build.log')
        (job/'manifest.json').write_text(json.dumps(dict(command=command,
             simulator_sha256=sha(job/'obj/Vtb_gpu')),indent=2)+'\n')
        results = {}
        for name, plus in scenarios:
            log = job/(name+'.log')
            run([job/'obj/Vtb_gpu', *plus], log, cwd=job, timeout=300)
            text = log.read_text()
            match = re.search(r'Acceptance Results: (\d+) passed, (\d+) failed',text)
            assert match and int(match[1]) == 79 and int(match[2]) == 0, log
            cycles = {k:int(v) for k,v in re.findall(r'^CYCLES (\w+) (\d+)$',text,re.M)}
            assert len(cycles) == 38, (log,len(cycles))
            monitor = dict(re.findall(r'(\w+)=(\w+)',re.search(r'^FBWQ_MON (.+)$',text,re.M)[1]))
            for field in ['hard_nonzero','soft_nonzero','observed','aw','w']:
                assert int(monitor[field]) > 0, (log,field)
            results[name] = dict(passed=int(match[1]),cycles=cycles,monitor=monitor,log_sha256=sha(log))
            if args.cache_candidate:
                hot=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^CACHEHOT_MON (.+)$',text,re.M)[1]))
                for field in ['hot0','hot1','reads','writes']:assert int(hot[field])>0,(log,field)
                results[name]['cache_monitor']=hot
            if args.ring_monitor:
                ring=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^RING_MON (.+)$',text,re.M)[1]))
                assert int(ring['cpu'])>0,(log,'no accepted CPU ring writes')
                results[name]['ring_monitor']=ring
            if args.texture_offer_monitor:
                texture=dict(re.findall(r'(\w+)=(\w+)',re.search(r'^TEXOFFER_MON (.+)$',text,re.M)[1]))
                assert int(texture['fill_a'])>0 and int(texture['errors'])==0,(log,texture)
                results[name]['texture_offer_monitor']=texture
            print(label, name, 'PASS', monitor, flush=True)
        return label,results

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(one,['baseline','candidate']))
    for name,_ in scenarios:
        for field in ['passed','cycles']:
            assert results['baseline'][name][field] == results['candidate'][name][field], (name,field)
        for field,value in results['baseline'][name]['monitor'].items():
            if field not in ['invalid_nonzero','hard_nonzero','soft_nonzero']:
                assert value == results['candidate'][name]['monitor'][field], (name,field)
        if args.cache_candidate:
            assert results['baseline'][name]['cache_monitor']==results['candidate'][name]['cache_monitor'],name
        if args.ring_monitor:
            assert results['baseline'][name]['ring_monitor']==results['candidate'][name]['ring_monitor'],name
        if args.texture_offer_monitor:
            assert results['baseline'][name]['texture_offer_monitor']==results['candidate'][name]['texture_offer_monitor'],name
    (dest/'results.json').write_text(json.dumps(dict(identical_pixels_cycles_valid_payload_and_axi=True,
                                                  runs=results),indent=2)+'\n')
    print('PASS: 38 workloads x 3 modes; pixels, cycles, valid request/snoop payload and AXI activity match.',flush=True)


if __name__ == '__main__':
    main()
