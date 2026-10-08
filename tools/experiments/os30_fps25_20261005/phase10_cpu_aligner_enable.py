#!/usr/bin/env python3
"""Exact absorbed aligner mask/last enable for the measured v7 endpoint."""
import difflib,hashlib,importlib.util,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-rvc-plane-v7';O=P/'cpu-aligner-enable-v1'
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
ANCHOR='  assign when_AlignerPlugin_l256 = ((fetch_logic_ctrls_3_down_isValid && fetch_logic_ctrls_3_down_isReady) && (! fetch_logic_ctrls_3_down_isCancel));'
ADDITION='''
  // The two old write enables reduce exactly to this local expression:
  // downFire || capture = downFire || (fetchValid && !haltIt && emptyMask).
  // Capture with a nonempty mask already requires downFire. Keep the late
  // enable independent of the shared fetch-capture/PLRU qualification.
  (* keep *) wire aligner_mask_last_update = AlignerPlugin_logic_buffer_downFire ||
      (fetch_logic_ctrls_3_down_valid && !AlignerPlugin_api_haltIt &&
       !(|AlignerPlugin_logic_buffer_mask));'''
OLD='''      if(AlignerPlugin_logic_buffer_downFire) begin
        AlignerPlugin_logic_buffer_mask <= (AlignerPlugin_logic_buffer_mask & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
        AlignerPlugin_logic_buffer_last <= (AlignerPlugin_logic_buffer_last & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
      end
      if(when_AlignerPlugin_l256) begin
        AlignerPlugin_logic_buffer_mask <= (fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[3 : 2] : 2'b00)));
        AlignerPlugin_logic_buffer_trap <= fetch_logic_ctrls_3_down_TRAP;
        AlignerPlugin_logic_buffer_last <= fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST;
      end'''
NEW='''      if(aligner_mask_last_update) begin
        if(when_AlignerPlugin_l256) begin
          AlignerPlugin_logic_buffer_mask <= (fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[3 : 2] : 2'b00)));
          AlignerPlugin_logic_buffer_last <= fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST;
        end else begin
          AlignerPlugin_logic_buffer_mask <= (AlignerPlugin_logic_buffer_mask & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
          AlignerPlugin_logic_buffer_last <= (AlignerPlugin_logic_buffer_last & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
        end
      end
      if(when_AlignerPlugin_l256) begin
        AlignerPlugin_logic_buffer_trap <= fetch_logic_ctrls_3_down_TRAP;
      end'''
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'tools/experiments/os30_fps25_20261005'/('phase10_'+name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def prepare():
 assert sha(BASE/'VexiiRiscv.v')=='8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
 s=(BASE/'VexiiRiscv.v').read_text()
 for a in ("  assign fetch_logic_ctrls_3_down_isCancel = 1'b0;","  assign decode_ctrls_0_up_isCancel = 1'b0;",'  assign AlignerPlugin_logic_buffer_downFire = (decode_ctrls_0_up_isReady || decode_ctrls_0_up_isCancel);'):
  assert s.count(a)==1
 t=rep(rep(s,ANCHOR,ANCHOR+ADDITION),OLD,NEW);O.mkdir(exist_ok=False)
 (O/'VexiiRiscv.v').write_text(t);shutil.copy2(__file__,O/'recipe.py')
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='cpu-v7',tofile='aligner-enable')))
 save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),added_state=0,added_cycles=0,
  scope='Only mask/last sequential update grouping and equivalent absorbed write enable; capture/trap/data/flush priorities unchanged.',
  timing_evidence_sha256=sha(P/'compact-timing-audit-v7/reports/slow_85_below90.rpt')))
def prove():
 d=O/'proof';d.mkdir(exist_ok=False)
 (d/'probe.sv').write_text('''module probe(input[17:0] bits,output old_enable,new_enable,negative_enable,
 output reg[3:0] old_next,new_next);
 wire down=bits[0],fetch=bits[1],halt=bits[2],consumable=bits[3];
 wire[1:0] mask=bits[5:4],last=bits[7:6],inmask=bits[13:12],inlast=bits[15:14];
 wire[3:0] used=bits[11:8];wire flush=bits[16],reset=bits[17];
 wire empty=!(|mask);
 // Original ready allow is !halt && (empty || scanner-consumption terms).
 // Abstracting those terms independently covers every scanner/width case.
 wire allow=!halt&&(empty||consumable);
 wire ready=!fetch||(allow&&(empty||down));
 wire capture=fetch&&ready;
 assign old_enable=down||capture;
 assign new_enable=down||(fetch&&!halt&&empty);
 assign negative_enable=down||(fetch&&empty);
 always @* begin
  old_next={last,mask};new_next={last,mask};
  if(down)old_next={last&~used[1:0],mask&~used[1:0]};
  if(capture)old_next={inlast,inmask&~(down?used[3:2]:2'b0)};
  if(new_enable)begin
   if(capture)new_next={inlast,inmask&~(down?used[3:2]:2'b0)};
   else new_next={last&~used[1:0],mask&~used[1:0]};
  end
  if(reset||flush)begin old_next=0;new_next=0;end
 end
endmodule
''')
 (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdio>
int main(){Vprobe d;unsigned neg=0;for(unsigned b=0;b<(1u<<18);b++){
 d.bits=b;d.eval();if(d.old_enable!=d.new_enable||d.old_next!=d.new_next){printf("FAIL %u\\n",b);return 1;}
 neg+=d.old_enable!=d.negative_enable;}
 if(!neg)return 2;printf("PASS checks=262144 negative=%u\\n",neg);
}
''')
 cmd=['verilator','--cc','--exe','--build','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),str(d/'probe.sv'),str(d/'main.cpp'),'-j','3'];save(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vprobe')],capture_output=True,text=True,check=True);(d/'run.log').write_text(r.stdout+r.stderr)
 m=re.fullmatch(r'PASS checks=262144 negative=(\d+)\n',r.stdout);assert m and int(m[1])
 save(d/'result.json',dict(passed=True,all_binary_state_and_control_combinations=262144,old_new_enable_and_mask_last_next_state_exact=True,
  arbitrary_mask_last_used_and_incoming_fields=True,reset_flush_covered=True,cancel='Selected generated fetch/down cancel are constant0; unchanged flush dominates both implementations.',
  negative_missing_halt_mismatches=int(m[1])))
def native_prepare():
 m=module('cpu_rvc_plane_v7');m.BASE=BASE;m.O=O;m.native_prepare()
 p=O/'native/frozen'/CPU;s=p.read_text();e=s.index('\nendmodule',s.index('module VexiiRiscv ('))
 monitor='''
reg[1:0] aligner_ref_mask=0,aligner_ref_last=0;
reg[63:0] aligner_update_checks=0,aligner_empty_capture=0,aligner_flush_checks=0;
always @(posedge clk)begin
 if(reset)begin aligner_ref_mask<=0;aligner_ref_last<=0;end
 else begin
  assert(aligner_mask_last_update==(AlignerPlugin_logic_buffer_downFire||when_AlignerPlugin_l256));
  assert(AlignerPlugin_logic_buffer_mask==aligner_ref_mask);
  assert(AlignerPlugin_logic_buffer_last==aligner_ref_last);
  aligner_update_checks<=aligner_update_checks+1;
  aligner_empty_capture<=aligner_empty_capture+(when_AlignerPlugin_l256&&!AlignerPlugin_logic_buffer_downFire);
  aligner_flush_checks<=aligner_flush_checks+AlignerPlugin_logic_buffer_flushIt;
  if(AlignerPlugin_logic_buffer_downFire)begin
   aligner_ref_mask<=AlignerPlugin_logic_buffer_mask&~AlignerPlugin_logic_buffer_usedMask[1:0];
   aligner_ref_last<=AlignerPlugin_logic_buffer_last&~AlignerPlugin_logic_buffer_usedMask[1:0];
  end
  if(when_AlignerPlugin_l256)begin
   aligner_ref_mask<=fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK&~(AlignerPlugin_logic_buffer_downFire?AlignerPlugin_logic_buffer_usedMask[3:2]:2'b0);
   aligner_ref_last<=fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST;
  end
  if(AlignerPlugin_logic_buffer_flushIt)begin aligner_ref_mask<=0;aligner_ref_last<=0;end
 end
end
final $display("ALIGNER_UPDATE_COVER checks=%0d empty_capture=%0d flush=%0d",aligner_update_checks,aligner_empty_capture,aligner_flush_checks);
'''
 p.write_text(s[:e]+monitor+s[e:]);prep=json.loads((O/'native/prepared.json').read_text());prep['sources_sha256'][str(p.relative_to(O/'native'))]=sha(p)
 (O/'native/prepared.json').write_text(json.dumps(prep,indent=2)+'\n')
def native_model():
 m=module('cpu_rvc_native');m.O=O;m.N=O/'native';m.model()
def native_run():
 m=module('cpu_rvc_native');m.O=O;m.N=O/'native';m.OLD=BASE/'native';m.run()
def extra():
 m=module('cpu_final_paths');m.O=O;m.extra()
def seal():
 for n in ('proof/result.json','native/result.json','native/extra/result.json'):assert json.loads((O/n).read_text())['passed']
 logs=list((O/'native/runs').glob('*/*-candidate.log'))+list((O/'native/extra').glob('*/*-candidate.log'));assert len(logs)==10
 coverage={}
 for n in ('READY','FP_RS2','FP_RS3','INTWB','RVC','ALIGNER_UPDATE'):
  coverage[n]=sum(int(re.search(n+r'_COVER checks=(\d+)',p.read_text())[1]) for p in logs)
 save(O/'result.json',dict(passed=True,all10nativeUART_and_cycles_exact=True,live_checks=coverage,added_state=0,added_cycles=0,no_fit=True))
 files=[p for p in O.rglob('*') if p.is_file() and not any(c in p.relative_to(O).parts for c in ('obj','frozen')) and p.name!='manifest.json']
 save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files},parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),production_unchanged=True));print(sha(O/'manifest.json'),sha(O/'VexiiRiscv.v'))
if __name__=='__main__':globals()[sys.argv[1]]()
