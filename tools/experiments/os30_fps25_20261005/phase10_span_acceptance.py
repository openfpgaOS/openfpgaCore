#!/usr/bin/env python3
"""Independent, private span-overlap ownership and exact-output replay checks."""
import concurrent.futures, hashlib, json, re, shutil, struct, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'span-lookahead-review-v1'
BASE=P/'gpu-critical-bound-v2/replay/model'
CAND=P/'span-lookahead-v2/gpu_core.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):
    assert s.count(a)==1,(a[:80],s.count(a));return s.replace(a,b)

# This observer is testbench-only. It neither writes nor forces GPU state.
PROBE=r'''
wire audit_handoff = gpu.state==gpu.S_FRAG_PIPE && gpu.span_la==gpu.LA_READY
 &&gpu.span_la_eligible&&gpu.src_done&&!gpu.p0a_valid&&!gpu.p0_valid
 &&!gpu.persp_active&&!gpu.persp_swap_pending&&gpu.persp_pss==gpu.PSS_IDLE;
// These live fields can be consumed by the older tail after the handoff.
// PERSP is source-only and may have been cleared by the constant-Z shortcut.
wire [511:0] audit_constants={gpu.sp_truecolor,gpu.sp_rgb,gpu.sp_blend,
 gpu.sp_const_alpha,gpu.sp_a6,gpu.cb_inv_a6,gpu.sp_cd_combine,
 gpu.sp_flags&4'b1011,gpu.sp_tex_width,gpu.sp_z_test_enable,
 gpu.sp_z_write_enable,gpu.sp_z_step,gpu.sp_zc_step,gpu.sp_zinv_step,
 gpu.spanprod_attr0_step_pre,gpu.spanprod_attr1_step_pre,
 gpu.sp_tex_w_mask,gpu.sp_tex_h_mask,gpu.sp_mirror_s,gpu.sp_mirror_t,
 gpu.sp_s_clamp_min,gpu.sp_s_clamp_max,gpu.sp_t_clamp_min,gpu.sp_t_clamp_max};
reg audit_previous_handoff=0;
reg [511:0] audit_old_constants;
reg [1:0] audit_old_index;
reg [31:0] audit_step0,audit_step1;
reg audit_previous_owned=0;
reg [63:0] audit_counts[0:15];
integer ai;
initial for(ai=0;ai<16;ai=ai+1)audit_counts[ai]=0;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  audit_previous_handoff<=0;audit_previous_owned<=0;
 end else begin
  if(audit_previous_handoff)begin
   if(gpu.state!=gpu.S_FRAG_PIPE)$fatal(1,"handoff left response collector");
   if(audit_old_constants!==audit_constants)$fatal(1,"handoff changed tail constants");
   if(gpu.spanprod_idx!=audit_old_index+2'd1)$fatal(1,"handoff index did not advance once");
   if(gpu.src_done)$fatal(1,"handoff failed to restart source");
  end
  // Prefetch may rewrite these current-PSS operands, but only identically.
  if(audit_previous_owned&&gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_owned)begin
   if(audit_step0!==gpu.spanprod_attr0_step_pre||audit_step1!==gpu.spanprod_attr1_step_pre)
    $fatal(1,"lookahead changed live PSS plane steps");
  end
  audit_previous_handoff<=audit_handoff;
  audit_previous_owned<=gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_owned;
  audit_step0<=gpu.spanprod_attr0_step_pre;audit_step1<=gpu.spanprod_attr1_step_pre;
  if(audit_handoff)begin
   audit_old_constants<=audit_constants;audit_old_index<=gpu.spanprod_idx;
   if(!gpu.tex_route||gpu.spanprod_direct_affine||gpu.spanprod_idx>=gpu.spanprod_last_idx)
    $fatal(1,"handoff crossed excluded command boundary");
   if(gpu.sp_z_write_enable&&!gpu.sp_z_test_enable)$fatal(1,"write-only depth handed off");
   audit_counts[0]<=audit_counts[0]+1;
   if(gpu.stream_pending)audit_counts[1]<=audit_counts[1]+1;
   if(gpu.tex_stream.req_count!=0)audit_counts[2]<=audit_counts[2]+1;
   if(gpu.tex_stream.lookup_pending)audit_counts[3]<=audit_counts[3]+1;
   if(gpu.tex_stream.res_count!=0)audit_counts[4]<=audit_counts[4]+1;
   if(gpu.p1_valid||gpu.p2_valid||gpu.p2b_valid||gpu.p3_valid)audit_counts[5]<=audit_counts[5]+1;
   if(gpu.fbss!=gpu.FBSS_IDLE)audit_counts[6]<=audit_counts[6]+1;
   if(gpu.zw_fill_busy)audit_counts[7]<=audit_counts[7]+1;
   if(gpu.blend_arvalid)audit_counts[8]<=audit_counts[8]+1;
   if(gpu.sp_blend)audit_counts[9]<=audit_counts[9]+1;
   if(gpu.z_src_pending_valid)audit_counts[10]<=audit_counts[10]+1;
   if(gpu.spanprod_cur_count==1)audit_counts[11]<=audit_counts[11]+1;
   if(gpu.spanprod_span_axis)audit_counts[12]<=audit_counts[12]+1;
  end
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la==gpu.LA_SETUP&&!gpu.spanprod_cur_nonzero)
   audit_counts[13]<=audit_counts[13]+1;
  if(gpu.state==gpu.S_SPANPROD_EMIT&&!gpu.tex_route)audit_counts[14]<=audit_counts[14]+1;
  if(gpu.state==gpu.S_SPANPROD_EMIT&&gpu.spanprod_z_write&&!gpu.spanprod_z_test)
   audit_counts[15]<=audit_counts[15]+1;
 end
end
final for(ai=0;ai<16;ai=ai+1)$display("SPAN_AUDIT %0d %0d",ai,audit_counts[ai]);
'''

def prepare():
    OUT.mkdir(exist_ok=False)
    d=OUT/'model';shutil.copytree(BASE/'frozen',d/'frozen')
    shutil.copyfile(CAND,d/'frozen/common/gpu_core.v')
    p=d/'frozen/tb_gpu.v';p.write_text(rep(p.read_text(),'endmodule',PROBE+'\nendmodule'))
    cmd=[s.replace(str(BASE),str(d)) for s in json.loads((BASE/'command.json').read_text())]
    cmd.insert(1,'+define+INCLUDE_SPAN_LOOKAHEAD')
    save(d/'command.json',cmd)
    save(OUT/'inputs.json',dict(candidate_sha256=sha(CAND),baseline_simulator_sha256=sha(BASE/'obj/Vtb_gpu'),
        recipe_sha256=sha(Path(__file__)),sources={str(p.relative_to(OUT)):sha(p) for p in d.rglob('*') if p.is_file()}))
def build():
    d=OUT/'model';cmd=json.loads((d/'command.json').read_text())
    with (d/'build.log').open('x') as f:subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
    save(d/'build.json',dict(simulator_sha256=sha(d/'obj/Vtb_gpu'),command=cmd))
def run():
    jobs=[]
    for traffic,frames in [(0,[360,367]),(1,list(range(360,368))),(2,[360,367])]:
        for frame in frames:
            for kind,exe in [('candidate',OUT/'model/obj/Vtb_gpu'),('baseline',BASE/'obj/Vtb_gpu')]:
                # Existing oracle is exact for nominal traffic; keep it intact.
                if kind=='baseline' and traffic==1:continue
                d=OUT/'runs'/f'{kind}-t{traffic}-f{frame}';d.mkdir(parents=True,exist_ok=False)
                vec=P/f'gpu-critical-bound-v2/replay/frame_{frame:05}.vec'
                jobs.append((d,[str(exe),f'+memory_traffic={traffic}','+fast_tex=1','+tagged_tex=1',str(vec),str(d/'output.bin')]))
    def one(j):
        d,cmd=j
        with (d/'run.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
        save(d/'result.json',dict(command=cmd,returncode=r.returncode,
            log_sha256=sha(d/'run.log'),output_sha256=sha(d/'output.bin')))
        assert r.returncode==0,(d,r.returncode)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(one,jobs))
def analyze():
    results=[];totals=[0]*16
    for d in sorted((OUT/'runs').glob('candidate-*')):
        traffic,frame=map(int,re.search(r'-t(\d)-f(\d+)',d.name).groups())
        oracle=(P/f'gpu-critical-bound-v2/replay/frame_{frame:05}.bin') if traffic==1 else OUT/'runs'/f'baseline-t{traffic}-f{frame}'/'output.bin'
        assert (d/'output.bin').read_bytes()==oracle.read_bytes(),d
        log=(d/'run.log').read_text();assert 'FATAL' not in log
        counts={int(k):int(v) for k,v in re.findall(r'SPAN_AUDIT (\d+) (\d+)',log)}
        for k,v in counts.items():totals[k]+=v
        results.append(dict(traffic=traffic,frame=frame,output_sha256=sha(d/'output.bin'),
            oracle_sha256=sha(oracle),counts=counts))
    assert totals[0]>0 and totals[1]>0 and totals[5]>0 and totals[6]>0 and totals[7]>0,totals
    save(OUT/'result.json',dict(passed=True,scope='Exact color/depth/fence replay, independent ownership assertions; not a device/fit qualification',
        counters=totals,runs=results,candidate_sha256=sha(CAND)))

def boundaries():
    """Small triangles cross final/partial chunks, material changes, and excluded modes."""
    d=OUT/'boundaries-v2';d.mkdir(exist_ok=False)
    pack=lambda *w:struct.pack('<'+'I'*len(w),*[x&0xffffffff for x in w])
    def cmd(op,w):return [op<<24|len(w)]+w
    fb,z,tex=0x100000,0x180000,0x10000
    # Initialize the complete cache refill line, not only the sampled texel.
    texture=pack(0xffffffff)+bytes(60)
    data=bytearray(b'TEX1'+pack(tex,len(texture))+texture)
    data+=b'MEM1'+pack(tex,len(texture))+texture
    data+=b'RAND'+pack(fb,128*128*2,0x92134)
    data+=b'MEM1'+pack(z,128*128*2)+bytes(128*128*2)
    words=[];cases=[]
    for axis in (0,1):
      for zmode in range(4):
       for shape in range(12):
        alpha=[0,4,128,255][shape%4];blend=shape%3==0
        flags=0xa0|(2 if blend else 0)
        major,minor=(256,2) if axis==0 else (2,256)
        ctl=flags|1<<12|axis<<16|zmode<<24|1<<31
        state=[fb,major,minor,tex,1,0,ctl,0,0,0,0,z,major,minor,128<<16,128<<16]
        if blend:state.append(alpha)
        words+=cmd(0x4a,state)
        # Height covers 1..8 raster rows, slivers, total rejection and a long
        # final partial chunk; coordinates stay in the 128-square allocation.
        x0=4+(shape*7)%60;y0=3+(shape*11)%60
        height=[1,2,3,4,5,6,7,8,13,23,0,31][shape]
        width=1 if shape==8 else 4+shape*3
        xy=[(x0*16,y0*16),((x0+width)*16+3,y0*16+1),(x0*16+1,(y0+height)*16+2)]
        if shape==10:xy=[(x0*16,y0*16)]*3
        uv=[0,0,0];iv=[65536,32768,49152] if shape&1 else [65536]*3
        rgb=[0xf800,0x07e0,0x001f];depth=[0x20000000,0x60000000,0x40000000]
        tri=[(y&65535)<<16|(x&65535) for x,y in xy]+uv+uv+iv+[rgb[0]|rgb[1]<<16,rgb[2]]+depth
        words+=cmd(0x4e,tri)
        cases.append(dict(axis=axis,zmode=zmode,shape=shape,alpha=alpha,blend=blend))
        if len(words)>1600:
            data+=b'CMDS'+pack(len(words))+pack(*words);words=[]
    words+=cmd(2,[0x915a77])
    data+=b'CMDS'+pack(len(words))+pack(*words)
    data+=b'DUMP'+pack(fb,128*128*2)+b'DUMP'+pack(z,128*128*2)+b'FENCEND.'
    vec=d/'triangles.vec';vec.write_bytes(data)
    save(d/'cases.json',cases)
    results=[]
    for fast in (0,1):
      for traffic in (0,2):
        outputs={}
        for kind,exe in [('baseline',BASE/'obj/Vtb_gpu'),('candidate',OUT/'model/obj/Vtb_gpu')]:
            name=f'{kind}-fast{fast}-t{traffic}';log=d/(name+'.log');output=d/(name+'.bin')
            command=[str(exe),f'+fast_tex={fast}',f'+memory_traffic={traffic}','+tagged_tex=1',str(vec),str(output)]
            with log.open('x') as f:r=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT)
            assert r.returncode==0,(name,r.returncode)
            outputs[kind]=output
            if kind=='candidate':
                counts={int(k):int(v) for k,v in re.findall(r'SPAN_AUDIT (\d+) (\d+)',log.read_text())}
        assert outputs['baseline'].read_bytes()==outputs['candidate'].read_bytes(),(fast,traffic)
        assert counts[15]>0,counts
        if fast:assert counts[0]>0 and counts[12]>0,counts
        else:assert counts[0]==0 and counts[14]>0,counts
        results.append(dict(fast_tex=fast,memory_traffic=traffic,counts=counts,sha256=sha(outputs['candidate'])))
    save(d/'result.json',dict(passed=True,cases=len(cases),comparisons=results,input_sha256=sha(vec)))
if __name__=='__main__':
    for action in sys.argv[1:]:globals()[action]()
