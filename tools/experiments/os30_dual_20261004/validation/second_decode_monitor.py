"""Observation-only monitor for the private age1025 decoded rotating buffer."""
import json
from pathlib import Path


def instrument(tb, cpp, payload_map):
    fields = json.loads(Path(payload_map).read_text())
    assert len(fields) == 90 and sum(x['bits'] for x in fields) == 380
    p = 'cpu_sys.cpu.'
    controls = [
        'decode_ctrls_3_up_valid', 'decode_dispatch_skid_full',
        'decode_dispatch_read_bank', 'decode_dispatch_write_bank',
        'decode_ctrls_2_down_isValid', 'decode_ctrls_2_down_isReady',
        'decode_ctrls_3_up_isReady', 'decode_dispatch_head_ready',
        'decode_dispatch_flush', 'decode_dispatch_up_cancel',
        'decode_dispatch_up_forget', 'reset',
        'decode_ctrls_2_down_LANE_SEL_0', 'decode_ctrls_2_down_LANE_SEL_1',
        'decode_ctrls_3_up_LANE_SEL_0', 'decode_ctrls_3_up_LANE_SEL_1',
        'decode_dispatch_tail_mask[0]', 'decode_dispatch_tail_mask[1]',
        'DispatchPlugin_logic_feeds_0_sent', 'DispatchPlugin_logic_feeds_1_sent',
        'DispatchPlugin_logic_feeds_0_sending', 'DispatchPlugin_logic_feeds_1_sending',
        'decode_ctrls_3_up_isMoving', 'decode_dispatch_tail_trap_pending',
        'decode_logic_trapPending[0]', 'TrapPlugin_logic_harts_0_trap_fsm_inflightTrap',
        'DecoderPlugin_logic_laneLogic_0_flushPort_valid',
        'DecoderPlugin_logic_laneLogic_1_flushPort_valid',
        'decode_ctrls_2_up_isMoving', 'decode_ctrls_2_up_isValid',
        'decode_ctrls_2_up_isCanceling',
    ]
    words = 12
    payloads = dict(bank0=p+'decode_dispatch_bank0', bank1=p+'decode_dispatch_bank1',
        input=p+'decode_dispatch_input', head=p+'decode_dispatch_head', tail=p+'decode_dispatch_tail',
        fields='{'+','.join(p+x['head'] for x in reversed(fields))+'}')
    ports = '    output wire [30:0] dbg_dsk_status,\n'
    ports += ''.join(f'    output wire [379:0] dbg_dsk_{name},\n' for name in payloads)
    marker = '    input  wire        clk_cpu,'
    assert tb.count(marker) == 1
    tb = tb.replace(marker, ports+marker)
    assigns = [f'assign dbg_dsk_status[{i}] = {p}{name};' for i, name in enumerate(controls)]
    assigns += [f'assign dbg_dsk_{name} = {value};' for name, value in payloads.items()]
    assert tb.count('endmodule') == 1
    tb = tb.replace('endmodule', '\n'.join(assigns)+'\nendmodule')
    trapbits = [next(x['lsb'] for x in fields if x['head'] == f'decode_ctrls_3_up_TRAP_{i}') for i in range(2)]
    header = '''
struct DecodedSkidSample {
    uint32_t status;
    uint32_t bank0[12],bank1[12],input[12],head[12],tail[12],fields[12];
};
static uint64_t dsk_cycles=0,dsk_captures=0,dsk_tail_drain=0,dsk_blocked=0;
static uint64_t dsk_full_flush=0,dsk_blocked_flush=0,dsk_flush_waiting=0;
static uint64_t dsk_partial_capture=0,dsk_partial_issue_hold=0,dsk_tail_trap=0;
static uint64_t dsk_younger_flush_held=0,dsk_cancel_accept=0,dsk_rotations=0;
static DecodedSkidSample decoded_skid_sample() {
    DecodedSkidSample s;s.status=tb->dbg_dsk_status;
    for(unsigned i=0;i<12;i++) {
        s.bank0[i]=tb->dbg_dsk_bank0[i];s.bank1[i]=tb->dbg_dsk_bank1[i];
        s.input[i]=tb->dbg_dsk_input[i];s.head[i]=tb->dbg_dsk_head[i];
        s.tail[i]=tb->dbg_dsk_tail[i];s.fields[i]=tb->dbg_dsk_fields[i];
    }
    return s;
}
static bool dsk_bit(const uint32_t *data,unsigned index) {return (data[index/32]>>(index%32))&1;}
static void decoded_skid_check(const DecodedSkidSample &s) {
    const uint32_t v=s.status,n=tb->dbg_dsk_status;
    const bool h=v&1,full=v&2,p=v&4,w=v&8,iv=v&16,ir=v&32,d=v&64,r=v&128;
    const bool flush=v&256,cancel=v&512,forget=v&1024,reset=v&2048;
    const unsigned im=(v>>12)&3,hm=(v>>14)&3,tm=(v>>16)&3,sent=(v>>18)&3,sending=(v>>20)&3;
    const bool moving=v&(1u<<22),trap=v&(1u<<23),young=v&(3u<<26);
    ++dsk_cycles;
    dsk_captures+=iv&&ir&&!r&&!flush;dsk_tail_drain+=full&&r&&!flush;
    dsk_blocked+=full&&!r&&!flush;dsk_full_flush+=full&&flush;
    dsk_blocked_flush+=full&&flush&&!r;dsk_flush_waiting+=full&&flush&&iv;
    dsk_partial_capture+=iv&&ir&&!r&&!flush&&(im==1||im==2);
    dsk_partial_issue_hold+=h&&!d&&(sent||sending);dsk_tail_trap+=trap;
    dsk_younger_flush_held+=full&&!r&&!flush&&young;dsk_cancel_accept+=cancel&&iv;
    dsk_rotations+=!reset&&h&&r;
    skid_require(!full||h,"decoded skid full without head");
    skid_require(ir==!full,"decoded skid upstream ready is not registered occupancy");
    skid_require(r==(!h||d),"decoded skid head ready mismatch");
    skid_require(w==(p^h),"decoded skid free-bank pointer mismatch");
    skid_require(cancel==(flush&&!full)&&forget==cancel,"decoded skid cancel/forget propagation");
    skid_require(!cancel||ir,"decoded cancel must imply acceptance");
    skid_require(iv||!im,"invalid decoded input retained a lane mask");
    skid_require(h||!hm,"invalid decoded head retained a lane mask");
    skid_require(moving==(h&&d),"partial issue changed whole-frame movement");
    skid_require(!(sent&sending),"decoded lane issued twice while its sent bit remains set");
    skid_require(bool(v&(1u<<28))==(bool(v&(1u<<29))&&ir),"decoded movement differs from ready acceptance");
    skid_require(bool(v&(1u<<30))==(bool(v&(1u<<29))&&cancel),"decoded cancellation observation mismatch");
    skid_require(trap==(full&&!flush&&((tm&1&&dsk_bit(s.tail,TRAP0))||(tm&2&&dsk_bit(s.tail,TRAP1)))),"decoded tail trap mapping");
    skid_require(!trap||((v&(1u<<24))&&(v&(1u<<25))),"decoded buffered trap absent from an aggregate");
    const bool nh=reset?false:(r?((full||iv)&&!flush):h);
    const bool nf=!reset&&!flush&&!r&&(full||iv);
    const bool np=reset?false:(p^(h&&r));
    unsigned nm=r?(flush?0:(full?tm:im)):hm;
    if(reset||(!d&&flush))nm=0;
    const unsigned ntm=full?tm:im;
    const unsigned nsent=reset?0:(moving?0:(sent|sending));
    skid_require(bool(n&1)==nh&&bool(n&2)==nf,"decoded occupancy/reset recurrence");
    skid_require(bool(n&4)==np,"decoded pointer recurrence");
    skid_require(((n>>14)&3)==nm,"decoded head lane capture/flush recurrence");
    skid_require(((n>>16)&3)==ntm,"decoded saved lane write/hold recurrence");
    skid_require(((n>>18)&3)==nsent,"decoded partial-issue sent recurrence");
    for(unsigned i=0;i<12;i++) {
        skid_require(s.head[i]==(p?s.bank1[i]:s.bank0[i]),"decoded head bank alias");
        skid_require(s.tail[i]==(p?s.bank0[i]:s.bank1[i]),"decoded tail bank alias");
        skid_require(s.fields[i]==s.head[i],"decoded 90-field packed-head mapping");
        skid_require(tb->dbg_dsk_bank0[i]==((!full&&!w)?s.input[i]:s.bank0[i]),"decoded bank0 write/hold");
        skid_require(tb->dbg_dsk_bank1[i]==((!full&&w)?s.input[i]:s.bank1[i]),"decoded bank1 write/hold");
        if(!reset)skid_require(tb->dbg_dsk_head[i]==(r?(full?s.tail[i]:s.input[i]):s.head[i]),"decoded head differs from S2M+StageLink recurrence");
    }
}
static void decoded_skid_report() {
    std::printf("DECODED SKID COVERAGE cycles=%llu captures=%llu tail_drain=%llu blocked=%llu full_flush=%llu blocked_flush=%llu flush_waiting=%llu partial_capture=%llu partial_issue_hold=%llu tail_trap=%llu younger_flush_held=%llu cancel_accept=%llu rotations=%llu errors=0\\n",
      (unsigned long long)dsk_cycles,(unsigned long long)dsk_captures,(unsigned long long)dsk_tail_drain,
      (unsigned long long)dsk_blocked,(unsigned long long)dsk_full_flush,(unsigned long long)dsk_blocked_flush,
      (unsigned long long)dsk_flush_waiting,(unsigned long long)dsk_partial_capture,(unsigned long long)dsk_partial_issue_hold,
      (unsigned long long)dsk_tail_trap,(unsigned long long)dsk_younger_flush_held,(unsigned long long)dsk_cancel_accept,
      (unsigned long long)dsk_rotations);
}
'''.replace('TRAP0', str(trapbits[0])).replace('TRAP1', str(trapbits[1]))
    marker = 'static uint64_t cpu_cycles = 0;'
    assert cpp.count(marker) == 1
    cpp = cpp.replace(marker, header+'\n'+marker)
    marker = '        const SkidSample skid_before = skid_sample();'
    assert cpp.count(marker) == 1
    cpp = cpp.replace(marker, marker+'\n        const DecodedSkidSample decoded_before = decoded_skid_sample();')
    marker = '        pingpong_check(pingpong_before,skid_before);'
    assert cpp.count(marker) == 1
    cpp = cpp.replace(marker, marker+'\n        decoded_skid_check(decoded_before);')
    marker = '    pingpong_report();'
    assert cpp.count(marker) == 1
    cpp = cpp.replace(marker, marker+'\n    decoded_skid_report();')
    return tb, cpp
