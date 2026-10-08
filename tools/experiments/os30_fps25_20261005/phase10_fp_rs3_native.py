#!/usr/bin/env python3
"""Actual CPU replay of native-FMA lighting corpus with direct forwarding assertion."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cpu-fp-rs3-mux-v1';D=OUT/'native';Q=P/'cpu-queues-v1/cpu-model'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
    assert json.loads((OUT/'combinational/result.json').read_text())['passed']
    D.mkdir(exist_ok=False);shutil.copytree(Q/'frozen',D/'frozen')
    candidate=(OUT/'VexiiRiscv.v').read_text();old=(OUT/'original-assignment.txt').read_text().strip()
    reference=old.replace('assign execute_ctrl1_down_float_RS3_lane0 =','wire [31:0] fp_rs3_reference =')
    monitor=reference+r'''
 reg [63:0] fp_rs3_checks=0,fp_rs3_freezes=0,fp_rs3_redirects=0,fp_rs3_multiple=0;
 reg [9:0] fp_rs3_seen=0;
 always @(posedge clk) if(!reset) begin
  assert(fp_rs3_reference==execute_ctrl1_down_float_RS3_lane0);
  fp_rs3_checks<=fp_rs3_checks+1;
  if(execute_freeze_valid)fp_rs3_freezes<=fp_rs3_freezes+1;
  if(early0_BranchPlugin_logic_flushPort_valid)fp_rs3_redirects<=fp_rs3_redirects+1;
  if(execute_ctrl1_up_LANE_SEL_lane0)begin
   fp_rs3_seen<=fp_rs3_seen|execute_lane0_bypasser_float_RS3_sel;
   if((execute_lane0_bypasser_float_RS3_bypassEnables[8:0] & (execute_lane0_bypasser_float_RS3_bypassEnables[8:0]-9'd1))!=0)fp_rs3_multiple<=fp_rs3_multiple+1;
  end
 end
 final $display("FP_RS3_COVER checks=%0d freezes=%0d redirects=%0d multiple=%0d seen=%0h",fp_rs3_checks,fp_rs3_freezes,fp_rs3_redirects,fp_rs3_multiple,fp_rs3_seen);
'''
    e=candidate.index('\nendmodule',candidate.index('module VexiiRiscv ('));candidate=candidate[:e]+'\n'+monitor+candidate[e:]
    (D/'frozen'/CPU).write_text(candidate)
    # The monitor is simulation-only; selected candidate remains uninstrumented.
    host=D/'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/main.cpp'
    s=host.read_text();assert s.count('    delete tb;')==1
    s=s.replace('    delete tb;','    tb->final();\n    delete tb;')
    # Native corpus writes1227 result rows; preserve complete UART for the oracle.
    s=s.replace('if (uart_buf.size() > 8192) uart_buf.erase(0, 4096);','if (uart_buf.size() > 1048576) uart_buf.erase(0, 524288);')
    host.write_text(s)
    cmd=[x.replace(str(Q),str(D)) for x in json.loads((Q/'build.json').read_text())['command']]
    cmd.insert(1,'--assert')
    save(D/'prepared.json',dict(command=cmd,candidate_sha256=sha(OUT/'VexiiRiscv.v'),
        source_sha256={str(p.relative_to(D)):sha(p) for p in sorted((D/'frozen').rglob('*')) if p.is_file()}))
    shutil.copytree(P/'cpu-audit/lighting-native/firmware',D/'firmware')
    save(D/'firmware.json',{p.name:sha(p) for p in sorted((D/'firmware').iterdir()) if p.is_file()})
    shutil.copy2(__file__,D/'recipe.py')
def model():
    p=json.loads((D/'prepared.json').read_text())
    for n,h in p['source_sha256'].items():assert sha(D/n)==h,n
    with (D/'build.log').open('x') as f:subprocess.run(p['command'],cwd=D/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT,check=True)
    save(D/'build.json',dict(simulator_sha256=sha(D/'obj/Vtb_system'),passed=True))
def run(mode):
    import re
    assert mode in ('quiet','nominal')
    env=dict(SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_PERIOD='6361',SCAN_LEN='160',SCAN_ACTIVE='240',SCAN_TOTAL='262')
    cmd=[str(D/'obj/Vtb_system'),'kernel.bin','30000000'];t=time.monotonic()
    save(D/(mode+'-invocation.json'),dict(command=cmd,environment=env,build_sha256=sha(D/'build.json')))
    with (D/(mode+'.log')).open('x') as f:r=subprocess.run(cmd,cwd=D/'firmware',env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT,timeout=300)
    text=(D/(mode+'.log')).read_text()
    ok=r.returncode==0 and 'NATIVE_LIGHTING_COMPLETE' in text and 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
    m=re.search(r'FP_RS3_COVER checks=(\d+) freezes=(\d+) redirects=(\d+) multiple=(\d+) seen=([0-9a-f]+)',text);assert m
    cover=dict(zip(('checks','freezes','redirects','multiple','seen'),[int(x,16 if i==4 else 10) for i,x in enumerate(m.groups())]))
    assert all(cover[k]>0 for k in ('checks','freezes','redirects'))
    # The kernel emits every result and an exact pre-existing native oracle.
    def parse_rows(t):
        t=t.split('=== UART BEGIN ===')[0]
        t=re.sub(r'\n?\[c=\d+\][^\n]*\n    m1_ar:[^\n]*\n    arb_st=[^\n]*\n','',t)
        rows={}
        for m in re.finditer(r'LIGHT ((?:[0-9a-f]{8} ){3})',t):
            i,a,b=[int(x,16) for x in m.group(1).split()]
            assert i not in rows or rows[i]==(a,b)
            rows[i]=(a,b)
        assert set(rows)==set(range(1227)),len(rows)
        return rows
    rows=parse_rows(text)
    oldtext=(P/'cpu-audit/lighting-native'/f'{mode}.log').read_text()
    oldrows=parse_rows(oldtext)
    assert rows==oldrows
    save(D/(mode+'-result.json'),dict(passed=ok,returncode=r.returncode,seconds=time.monotonic()-t,coverage=cover,native_rows=len(rows),old_native_rows_equal=rows==oldrows,log_sha256=sha(D/(mode+'.log'))))
    print(ok,cover,len(rows));assert ok
if __name__=='__main__':globals()[sys.argv[1]](*sys.argv[2:])
