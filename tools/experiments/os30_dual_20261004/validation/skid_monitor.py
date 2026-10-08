"""Testbench-only taps and protocol assertions for the real decode skid buffer."""
import json
from pathlib import Path

def instrument(tb, cpp, mapping_path):
    mapping=json.loads(Path(mapping_path).read_text())
    signals=mapping['signals']
    names=['full','up_valid','up_ready','down_valid','down_ready','drop','flush']
    for name in [*names,'up_mask','down_mask','input_payload','saved_payload','output_payload','aligner_forget_without_cancel']:
        if name not in signals:raise ValueError('Missing skid observation: '+name)
    width=mapping['payload_width'];words=(width+31)//32
    assert width>0
    diagnose_stage1='stage1_frame' in signals
    ports=f'''    output wire [11:0] dbg_skid_status,
    output wire [{width-1}:0] dbg_skid_input,
    output wire [{width-1}:0] dbg_skid_saved,
    output wire [{width-1}:0] dbg_skid_output,
'''
    if diagnose_stage1:ports+='    output wire [2:0] dbg_skid_stage1,\n'
    marker='    input  wire        clk_cpu,'
    assert tb.count(marker)==1
    tb=tb.replace(marker,ports+marker)
    assignments=[]
    for i,name in enumerate(names):assignments.append(f'assign dbg_skid_status[{i}] = {signals[name]};')
    assignments += [f'assign dbg_skid_status[8:7] = {signals["up_mask"]};',
                    f'assign dbg_skid_status[10:9] = {signals["down_mask"]};',
                    f'assign dbg_skid_status[11] = {signals["aligner_forget_without_cancel"]};',
                    f'assign dbg_skid_input = {signals["input_payload"]};',
                    f'assign dbg_skid_saved = {signals["saved_payload"]};',
                    f'assign dbg_skid_output = {signals["output_payload"]};']
    if diagnose_stage1:assignments.append(f'assign dbg_skid_stage1 = {signals["stage1_frame"]};')
    assert tb.count('endmodule')==1
    tb=tb.replace('endmodule','\n'.join(assignments)+'\nendmodule')
    header=f'''
struct SkidSample {{
    unsigned status;
    uint32_t input[{words}], saved[{words}], output[{words}];
}};
static uint64_t skid_cycles=0, skid_occupied=0, skid_captures=0, skid_blocked=0;
static uint64_t skid_full_flush=0, skid_full_flush_blocked=0, skid_full_flush_waiting_input=0;
static uint64_t skid_empty_flush_input=0, skid_partial_captures=0, skid_partial_deliveries=0;
static SkidSample skid_sample() {{
    SkidSample s; s.status=tb->dbg_skid_status;
    for (unsigned i=0;i<{words};i++) {{s.input[i]=tb->dbg_skid_input[i];s.saved[i]=tb->dbg_skid_saved[i];s.output[i]=tb->dbg_skid_output[i];}}
    return s;
}}
static void skid_require(bool condition,const char *what) {{
    if (!condition) {{std::fprintf(stderr,"SKID INVARIANT FAIL: %s\\n",what);std::abort();}}
}}
static void skid_check(const SkidSample &s) {{
    const bool full=s.status&1, in_valid=s.status&2, in_ready=s.status&4;
    const bool out_valid=s.status&8, out_ready=s.status&16, drop=s.status&32, flush=s.status&64;
    const unsigned in_mask=(s.status>>7)&3, out_mask=(s.status>>9)&3;
    const bool next_full=tb->dbg_skid_status&1;
    ++skid_cycles;skid_occupied+=full;skid_blocked+=full&&!out_ready&&!drop;
    skid_full_flush+=full&&drop;
    skid_full_flush_blocked+=full&&drop&&!out_ready;
    skid_full_flush_waiting_input+=full&&drop&&in_valid;
    skid_empty_flush_input+=!full&&drop&&in_valid;
    skid_require(in_ready==!full,"upstream ready must depend only on registered occupancy");
    skid_require(!(s.status&2048),"aligner forgetOne asserted without cancellation");
    skid_require(drop==flush,"flush did not cancel skid frame");
    skid_require(out_valid==((full||in_valid)&&!drop),"skid output valid/cancellation mismatch");
    if(out_valid)for(unsigned i=0;i<{words};i++)skid_require(s.output[i]==(full?s.saved[i]:s.input[i]),"skid output payload/context selection mismatch");
    if(full&&!out_ready&&!drop) {{
        skid_require(next_full,"blocked buffered frame disappeared without cancellation");
        for(unsigned i=0;i<{words};i++)skid_require(s.saved[i]==tb->dbg_skid_saved[i],"blocked saved payload changed");
    }}
    if(drop)skid_require(!next_full,"cancelled frame retained occupancy");
    if(full&&out_ready&&!drop)skid_require(!next_full,"drained frame retained occupancy");
    if(in_valid&&in_ready&&!out_ready&&!drop) {{
        ++skid_captures;skid_partial_captures+=(in_mask==1||in_mask==2);
        skid_require(next_full,"accepted stalled frame was not buffered");
        for(unsigned i=0;i<{words};i++)skid_require(s.input[i]==tb->dbg_skid_saved[i],"captured payload/context mismatch");
    }}
    skid_partial_deliveries+=out_valid&&out_ready&&!drop&&(out_mask==1||out_mask==2);
    (void)flush;
}}
static void skid_report() {{
    std::printf("SKID COVERAGE cycles=%llu occupied=%llu captures=%llu blocked=%llu full_flush=%llu full_flush_blocked=%llu full_flush_waiting_input=%llu empty_flush_input=%llu partial_captures=%llu partial_deliveries=%llu errors=0\\n",
        (unsigned long long)skid_cycles,(unsigned long long)skid_occupied,(unsigned long long)skid_captures,
        (unsigned long long)skid_blocked,(unsigned long long)skid_full_flush,(unsigned long long)skid_full_flush_blocked,
        (unsigned long long)skid_full_flush_waiting_input,(unsigned long long)skid_empty_flush_input,
        (unsigned long long)skid_partial_captures,(unsigned long long)skid_partial_deliveries);
}}
'''
    if diagnose_stage1:
        header=header.replace('    (void)flush;', '''    static unsigned ghost_traces=0;
    if(full&&flush&&out_ready&&out_mask&&!(tb->dbg_skid_stage1&4)&&(tb->dbg_skid_stage1&3)&&ghost_traces<8) {
        ++ghost_traces;
        std::printf("SKID GHOST cycle=%llu flushed_mask=%u next_stage1_valid=0 next_stage1_mask=%u\\n",
            (unsigned long long)skid_cycles,out_mask,unsigned(tb->dbg_skid_stage1&3));
    }
    (void)flush;''')
        if mapping.get('require_clean_flush_stage1'):
            header=header.replace('    (void)flush;', '''    if(flush&&out_ready) {
        skid_require(!(tb->dbg_skid_stage1&4),"flushed frame became valid at stage1");
        skid_require(!(tb->dbg_skid_stage1&3),"flushed lane mask survived into invalid stage1");
    }
    (void)flush;''')
    marker='static uint64_t cpu_cycles = 0;'
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,header+'\n'+marker)
    marker='        const bool aw_take = tb->dbg_aw_valid && tb->dbg_aw_ready;'
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,'        const SkidSample skid_before = skid_sample();\n'+marker)
    marker='        tick_half(true); cpu_cycles++;'
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,marker+'\n        skid_check(skid_before);')
    marker='    uint32_t final_pc ='
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,'    skid_report();\n'+marker)
    if mapping.get('pingpong'):
        tb,cpp=instrument_pingpong(tb,cpp,mapping['pingpong'])
    return tb,cpp

def instrument_pingpong(tb,cpp,mapping):
    """Observe physical banks and the removed StageLink's exact data recurrence."""
    width=mapping['payload_width'];words=(width+31)//32
    signals=mapping['signals']
    controls=['head_valid','full','read_bank','write_bank','write0','write1','ready','reset']
    payloads=['bank0','bank1','input','head','tail','output']
    assert width==276
    ports='    output wire [7:0] dbg_pp_status,\n'
    ports+=''.join(f'    output wire [{width-1}:0] dbg_pp_{name},\n' for name in payloads)
    marker='    input  wire        clk_cpu,'
    assert tb.count(marker)==1
    tb=tb.replace(marker,ports+marker)
    assigns=[f'assign dbg_pp_status[{i}] = {signals[name]};' for i,name in enumerate(controls)]
    assigns += [f'assign dbg_pp_{name} = {signals[name]};' for name in payloads]
    tb=tb.replace('endmodule','\n'.join(assigns)+'\nendmodule')
    header=f'''
struct PingpongSample {{
    unsigned status;
    uint32_t bank0[{words}],bank1[{words}],input[{words}],head[{words}],tail[{words}],output[{words}];
}};
static uint64_t pp_cycles=0,pp_write0=0,pp_write1=0,pp_rotate=0,pp_empty_head=0;
static uint64_t pp_full_blocked=0,pp_flush_pop=0,pp_reset=0;
static PingpongSample pingpong_sample() {{
    PingpongSample s;s.status=tb->dbg_pp_status;
    for(unsigned i=0;i<{words};i++) {{
        s.bank0[i]=tb->dbg_pp_bank0[i];s.bank1[i]=tb->dbg_pp_bank1[i];s.input[i]=tb->dbg_pp_input[i];
        s.head[i]=tb->dbg_pp_head[i];s.tail[i]=tb->dbg_pp_tail[i];s.output[i]=tb->dbg_pp_output[i];
    }}
    return s;
}}
static void pingpong_check(const PingpongSample &s,const SkidSample &skid) {{
    const bool h=s.status&1,full=s.status&2,p=s.status&4,w=s.status&8;
    const bool w0=s.status&16,w1=s.status&32,ready=s.status&64,reset=s.status&128;
    const bool flush=skid.status&64;
    ++pp_cycles;pp_write0+=w0;pp_write1+=w1;pp_rotate+=!reset&&h&&ready;
    pp_empty_head+=!h;pp_full_blocked+=h&&full&&!ready;pp_flush_pop+=flush&&h&&ready;pp_reset+=reset;
    skid_require(bool(skid.status&1)==full,"pingpong occupancy tap differs from skid");
    skid_require(bool(skid.status&16)==ready,"pingpong StageLink ready tap differs from skid");
    skid_require((skid.status&2)||!((skid.status>>7)&3),"invalid input frame retained lane mask");
    skid_require(w==(p^h),"pingpong write bank differs from pointer XOR head valid");
    skid_require(w0==(!full&&!w)&&w1==(!full&&w),"pingpong bank write enable mismatch");
    skid_require(bool(tb->dbg_pp_status&4)==(reset?false:(p^(h&&ready))),"pingpong pointer advance/reset mismatch");
    for(unsigned i=0;i<{words};i++) {{
        skid_require(s.head[i]==(p?s.bank1[i]:s.bank0[i]),"pingpong head field mapping mismatch");
        skid_require(s.tail[i]==(p?s.bank0[i]:s.bank1[i]),"pingpong tail field mapping mismatch");
        skid_require(tb->dbg_pp_bank0[i]==(w0?s.input[i]:s.bank0[i]),"pingpong bank0 write/hold mismatch");
        skid_require(tb->dbg_pp_bank1[i]==(w1?s.input[i]:s.bank1[i]),"pingpong bank1 write/hold mismatch");
        if(!reset)skid_require(tb->dbg_pp_head[i]==(ready?s.output[i]:s.head[i]),"pingpong head differs from original StageLink data recurrence");
    }}
}}
static void pingpong_report() {{
    std::printf("PINGPONG COVERAGE cycles=%llu writes0=%llu writes1=%llu rotations=%llu empty_head=%llu full_blocked=%llu flush_pop=%llu reset=%llu errors=0\\n",
        (unsigned long long)pp_cycles,(unsigned long long)pp_write0,(unsigned long long)pp_write1,
        (unsigned long long)pp_rotate,(unsigned long long)pp_empty_head,(unsigned long long)pp_full_blocked,
        (unsigned long long)pp_flush_pop,(unsigned long long)pp_reset);
}}
'''
    marker='static uint64_t cpu_cycles = 0;'
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,header+'\n'+marker)
    marker='        const SkidSample skid_before = skid_sample();'
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,marker+'\n        const PingpongSample pingpong_before = pingpong_sample();')
    marker='        skid_check(skid_before);'
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,marker+'\n        pingpong_check(pingpong_before,skid_before);')
    marker='    skid_report();'
    assert cpp.count(marker)==1
    cpp=cpp.replace(marker,marker+'\n    pingpong_report();')
    return tb,cpp
