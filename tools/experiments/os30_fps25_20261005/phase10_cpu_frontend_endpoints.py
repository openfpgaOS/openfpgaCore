#!/usr/bin/env python3
"""Private exact stage-3 hazard factors at frontend capture/update endpoints."""
import difflib, hashlib, itertools, json, re, shutil, subprocess, sys
from pathlib import Path

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
B = P / 'cpu-parallel-control-v1/VexiiRiscv.v'
O = P / 'cpu-frontend-endpoints-v1'
PARENT = '3bbe3886be4edceb73b4e338314ee22c6f0777b9197689ba184d5c14d85cba96'
H = [f'DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_{i}_hazard' for i in range(3)]
LAST = ' && '.join('!' + h for h in H)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def save(p, x):
    with p.open('x') as f: json.dump(x, f, indent=2, sort_keys=True); f.write('\n')

def replace(s, old, new):
    assert s.count(old) == 1, (old[:100], s.count(old))
    return s.replace(old, new, 1)

def assigned(s, name):
    return re.search(r'^  assign ' + re.escape(name) + r' = .*;\n', s, re.M).group()

def block(s, name):
    return re.search(r'  always @\(\*\) begin\n    ' + re.escape(name) + r' = .*?\n  end\n', s, re.S).group()

def prepare():
    assert sha(B) == PARENT
    s = B.read_text()
    for name, value in [('fetch_logic_ctrls_3_down_isCancel', "1'b0"), ('decode_ctrls_0_up_isCancel', "1'b0"),
                        ('fetch_logic_ctrls_3_down_valid', 'fetch_logic_ctrls_3_up_valid')]:
        assert assigned(s, name) == f'  assign {name} = {value};\n'
    cap_old = assigned(s, 'when_AlignerPlugin_l256')
    cap_new = '''  // Precompute occupancy/qualification before the three late RS hazards.
  // Original ready/fire and capture data/flush ordering remain unchanged.
  (* keep *) wire os30_aligner_capture_free = fetch_logic_ctrls_3_down_valid &&
      fetch_ready_allow && (fetch_ready_empty || os30_decode2_ready_free);
  (* keep *) wire os30_aligner_capture_allow = fetch_logic_ctrls_3_down_valid &&
      fetch_ready_allow && os30_decode2_ready_allow;
  assign when_AlignerPlugin_l256 = os30_aligner_capture_free ||
      (os30_aligner_capture_allow && ''' + LAST + ''');
  (* keep *) wire os30_aligner_mask_last_free = os30_frontend_ready_free ||
      (fetch_logic_ctrls_3_down_valid && !AlignerPlugin_api_haltIt &&
       !(|AlignerPlugin_logic_buffer_mask));
  (* keep *) wire os30_aligner_mask_last_update = os30_aligner_mask_last_free ||
      (os30_decode2_ready_allow && ''' + LAST + ''');
'''
    ready_old = block(s, 'decode_ctrls_0_down_ready')
    ready_new = '''  // Exact empty-stage bypass, expanded before the late hazards.
  (* keep *) wire os30_frontend_ready_free = !decode_ctrls_1_up_isValid ||
      !decode_ctrls_2_up_isValid || os30_decode2_ready_free;
  always @(*) begin
    decode_ctrls_0_down_ready = os30_frontend_ready_free ||
        (os30_decode2_ready_allow && ''' + LAST + ''');
  end
'''
    state_old = '''      if(AlignerPlugin_logic_buffer_downFire) begin
        AlignerPlugin_logic_buffer_mask <= (AlignerPlugin_logic_buffer_mask & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
        AlignerPlugin_logic_buffer_last <= (AlignerPlugin_logic_buffer_last & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
      end
      if(when_AlignerPlugin_l256) begin
        AlignerPlugin_logic_buffer_mask <= (fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[3 : 2] : 2'b00)));
        AlignerPlugin_logic_buffer_trap <= fetch_logic_ctrls_3_down_TRAP;
        AlignerPlugin_logic_buffer_last <= fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST;
      end'''
    state_new = '''      if(os30_aligner_mask_last_update) begin
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
    hunks = [dict(name='capture_and_mask_enable',old=cap_old,new=cap_new), dict(name='decode0_ready',old=ready_old,new=ready_new), dict(name='mask_last_update_grouping',old=state_old,new=state_new)]
    outputs = []
    for order in itertools.permutations(hunks):
        t = s
        for h in order: t = replace(t, h['old'], h['new'])
        outputs.append(t)
        for h in reversed(order): t = replace(t, h['new'], h['old'])
        assert t == s
    assert len(set(outputs)) == 1
    t = outputs[0]
    decls = lambda a: re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$', a, re.M)
    assert decls(t) == decls(s)
    assert sorted(l.strip() for l in t.splitlines() if '<=' in l) == sorted(l.strip() for l in s.splitlines() if '<=' in l)
    O.mkdir(exist_ok=False)
    shutil.copyfile(B, O/'baseline.v'); shutil.copyfile(__file__, O/'recipe.py')
    (O/'VexiiRiscv.v').write_text(t)
    save(O/'hunks.json',hunks)
    (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='3bbe/VexiiRiscv.v',tofile='frontend-endpoints/VexiiRiscv.v')))
    save(O/'prepared.json',dict(parent_sha256=PARENT,candidate_sha256=sha(O/'VexiiRiscv.v'),recipe_sha256=sha(Path(__file__)),added_state=0,added_cycles=0,hunks=3,all6_orders_reversible=True,scope='Only capture equation, equivalent mask/last write-enable organization, and decode0 ready companion. All decode1/2/3, dispatcher, fetch-ready, reset and flush equations unchanged.',native_pending=True,physical_timing_unmeasured=True))
    literal_control(s, t, hunks)
    literal_state(s, t, hunks)
    monitor(s)
    print('SOURCE',sha(O/'VexiiRiscv.v'),flush=True)

def literal_control(s,t,hunks):
    d=O/'control';d.mkdir()
    # Keep all generated aliases/stage-links and actual scanner expression.
    # Independent free/allow scalars cover every upstream dispatch qualifier.
    primary=[('os30_decode2_ready_free',1),('os30_decode2_ready_allow',1)]+[(h,1) for h in H]+[
      ('decode_ctrls_1_up_isValid',1),('decode_ctrls_2_up_isValid',1),('fetch_logic_ctrls_3_up_valid',1),
      ('AlignerPlugin_api_haltIt',1),('AlignerPlugin_logic_buffer_mask',2),('AlignerPlugin_logic_scanners_0_valid',1),
      ('AlignerPlugin_logic_scanners_0_checker_1_required',1),('AlignerPlugin_logic_scanners_1_valid',1),
      ('AlignerPlugin_logic_scanners_1_checker_1_required',1)]
    names=['fetch_logic_ctrls_3_down_valid','fetch_logic_ctrls_3_down_isValid','fetch_logic_ctrls_3_down_isReady',
      'fetch_logic_ctrls_3_down_isCancel','decode_ctrls_0_up_isCancel','decode_ctrls_0_up_isReady','decode_ctrls_0_up_ready',
      'decode_ctrls_0_down_isReady','decode_ctrls_1_up_ready','decode_ctrls_1_down_isReady','decode_ctrls_2_up_ready',
      'decode_ctrls_2_down_isReady','when_StageLink_l71_2','when_StageLink_l71_3','AlignerPlugin_logic_buffer_downFire']
    common=''.join(assigned(s,n) for n in names)+block(s,'decode_ctrls_1_down_ready')+block(s,'decode_ctrls_2_down_ready')
    common+=s[s.index('  (* keep *) wire fetch_ready_allow ='):s.index(hunks[0]['old'])]
    declarations=re.findall(r'^  (?:wire|reg)\s+(?:\[[^\]]+\]\s+)?\w+\s*;\n',s,re.M)
    modules=[]
    for kind,body in [('old',hunks[1]['old']+hunks[0]['old']),('new',hunks[1]['new']+hunks[0]['new']),
                      ('bad',hunks[1]['new']+hunks[0]['new'].replace('!'+H[0],"1'b1"))]:
        used=common+body+' '.join(n for n,w in primary)
        selected=[line for line in declarations if re.search(r'\b'+re.escape(re.search(r'(\w+);',line).group(1))+r'\b',used)]
        extra='  wire os30_decode2_ready_free,os30_decode2_ready_allow;\n'
        # buffer mask is an abstract input here, although reg in the CPU.
        selected=[re.sub(r'\breg\b','wire',l) if 'AlignerPlugin_logic_buffer_mask;' in l else l for l in selected]
        assignments='';offset=0
        for n,w in primary:
            assignments+=f'  assign {n} = controls[{offset+w-1}:{offset}];\n';offset+=w
        assert offset==15
        enable='(AlignerPlugin_logic_buffer_downFire || when_AlignerPlugin_l256)' if kind=='old' else 'os30_aligner_mask_last_update'
        modules.append(f'module endpoint_{kind}(input [14:0] controls,output [2:0] y);\n'+''.join(selected)+extra+assignments+common+body+
          f'  assign y={{decode_ctrls_0_down_ready,when_AlignerPlugin_l256,{enable}}};\nendmodule\n')
    modules.append('module proof(input [14:0] controls,output[2:0] old_y,new_y,bad_y);\nendpoint_old o(controls,old_y);endpoint_new n(controls,new_y);endpoint_bad b(controls,bad_y);\nendmodule\n')
    (d/'proof.v').write_text(''.join(modules))
    (d/'main.cpp').write_text('''#include "Vproof.h"
#include "verilated.h"
#include <cstdio>
int main(){Vproof t;unsigned neg=0;for(unsigned c=0;c<32768;c++){t.controls=c;t.eval();if(t.old_y!=t.new_y){printf("FAIL control %u old%u new%u\\n",c,t.old_y,t.new_y);return 1;}neg+=t.old_y!=t.bad_y;}if(!neg)return 2;printf("{\\"passed\\":true,\\"checks\\":32768,\\"missing_RS1_negative\\":%u}\\n",neg);}
''')
    save(d/'extraction.json',dict(primary_inputs=primary,generated_aliases=names,arbitrary_scanner_mask_combinations=True,source_bodies_literal=True))

def literal_state(s,t,hunks):
    d=O/'next-state';d.mkdir()
    mask='AlignerPlugin_logic_buffer_mask';last='AlignerPlugin_logic_buffer_last';trap='AlignerPlugin_logic_buffer_trap'
    modules=[]
    for kind,body in [('old',hunks[2]['old']),('new',hunks[2]['new'])]:
        # The source NBA statements are projected into combinational next-state
        # outputs with every RHS reading the original arbitrary state.
        for name,new in [(mask,'next_mask'),(last,'next_last'),(trap,'next_trap')]:
            body=body.replace(name+' <=',new+' =')
        reset_block=f"if(reset)begin next_mask=2'b00;next_last=2'b00;end\nelse begin\n"+body+'\nif(flush)begin next_mask=0;next_last=0;end\nend\n'
        # Trap is not reset by the original reset branch; preserve held value.
        for literal in [f"      {mask} <= 2'b00;",f"      {last} <= 2'b00;"]:
            assert literal in s and literal in t
        assert '      if(AlignerPlugin_logic_buffer_flushIt) begin\n        '+mask+" <= 2'b00;\n        "+last+" <= 2'b00;\n      end" in s
        pre='''wire AlignerPlugin_logic_buffer_downFire=bits[0],fetch=bits[1],halt=bits[2],consumable=bits[3];
wire[1:0] AlignerPlugin_logic_buffer_mask=bits[5:4],AlignerPlugin_logic_buffer_last=bits[7:6];
wire[3:0] AlignerPlugin_logic_buffer_usedMask=bits[11:8];
wire[1:0] fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK=bits[13:12],fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST=bits[15:14];
wire flush=bits[16],reset=bits[17],AlignerPlugin_logic_buffer_trap=bits[18],fetch_logic_ctrls_3_down_TRAP=bits[19];
wire empty=!(|AlignerPlugin_logic_buffer_mask),allow=!halt&&(empty||consumable);
wire when_AlignerPlugin_l256=fetch&&(!fetch||(allow&&(empty||AlignerPlugin_logic_buffer_downFire)));
wire os30_aligner_mask_last_update=AlignerPlugin_logic_buffer_downFire||(fetch&&!halt&&empty);
always @* begin
next_mask=AlignerPlugin_logic_buffer_mask;next_last=AlignerPlugin_logic_buffer_last;next_trap=AlignerPlugin_logic_buffer_trap;
'''
        modules.append(f'module state_{kind}(input[19:0]bits,output reg[1:0]next_mask,next_last,output reg next_trap);\n'+pre+reset_block+'end\nendmodule\n')
    modules.append('module proof(input[19:0]bits,output[4:0]old_y,new_y);state_old o(bits,old_y[1:0],old_y[3:2],old_y[4]);state_new n(bits,new_y[1:0],new_y[3:2],new_y[4]);endmodule\n')
    (d/'proof.v').write_text(''.join(modules))
    (d/'main.cpp').write_text('''#include "Vproof.h"
#include <cstdio>
int main(){Vproof t;for(unsigned c=0;c<(1u<<20);c++){t.bits=c;t.eval();if(t.old_y!=t.new_y){printf("FAIL state %u old%u new%u\\n",c,t.old_y,t.new_y);return 1;}}puts("{\\"passed\\":true,\\"checks\\":1048576}");}
''')
    save(d/'extraction.json',dict(source_NBA_bodies_literal=True,projection='Only LHS NBA changes to named next-state output; all RHS read arbitrary original state.',composition='Control proof independently establishes actual candidate downFire/capture/enable for all15inputs. State proof abstracts downFire/scanner-consumability and covers every original/incoming mask,last,used,trap/reset/flush combination, including impossible scanner/used-mask combinations.',other_capture_payloads='Identical RHS and qualified capture equality give bitwise exact PC/metadata/data update and hold. Decode1 payload/valid/lane writes stay literal under proven decode0 ready equality.'))

def monitor(s):
    # Optional passive original-next-state oracle for the root combined native
    # model; does not enter the candidate CPU or any synthesis source.
    text='''wire endpoint_old_ready0 = decode_ctrls_1_up_ready || !decode_ctrls_1_up_isValid;
wire endpoint_old_capture = fetch_logic_ctrls_3_down_isValid && fetch_logic_ctrls_3_down_isReady && !fetch_logic_ctrls_3_down_isCancel;
reg[1:0] endpoint_ref_mask=0,endpoint_ref_last=0;
reg[63:0] endpoint_checks=0,endpoint_flushes=0,endpoint_empty_captures=0;
always @(posedge clk)begin
 if(reset)begin endpoint_ref_mask<=0;endpoint_ref_last<=0;end
 else begin
  assert(decode_ctrls_0_down_ready==endpoint_old_ready0);
  assert(when_AlignerPlugin_l256==endpoint_old_capture);
  assert(os30_aligner_mask_last_update==(AlignerPlugin_logic_buffer_downFire||endpoint_old_capture));
  assert(AlignerPlugin_logic_buffer_mask==endpoint_ref_mask);
  assert(AlignerPlugin_logic_buffer_last==endpoint_ref_last);
  endpoint_checks<=endpoint_checks+1;
  endpoint_flushes<=endpoint_flushes+AlignerPlugin_logic_buffer_flushIt;
  endpoint_empty_captures<=endpoint_empty_captures+(endpoint_old_capture&&!AlignerPlugin_logic_buffer_downFire);
  if(AlignerPlugin_logic_buffer_downFire)begin
   endpoint_ref_mask<=AlignerPlugin_logic_buffer_mask&~AlignerPlugin_logic_buffer_usedMask[1:0];
   endpoint_ref_last<=AlignerPlugin_logic_buffer_last&~AlignerPlugin_logic_buffer_usedMask[1:0];
  end
  if(endpoint_old_capture)begin
   endpoint_ref_mask<=fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK&~(AlignerPlugin_logic_buffer_downFire?AlignerPlugin_logic_buffer_usedMask[3:2]:2'b0);
   endpoint_ref_last<=fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST;
  end
  if(AlignerPlugin_logic_buffer_flushIt)begin endpoint_ref_mask<=0;endpoint_ref_last<=0;end
 end
end
final $display("FRONTEND_ENDPOINT_COVER checks=%0d empty_capture=%0d flush=%0d",endpoint_checks,endpoint_empty_captures,endpoint_flushes);
'''
    (O/'native-monitor.vh').write_text(text)

def prove():
    prep=json.loads((O/'prepared.json').read_text());assert sha(O/'VexiiRiscv.v')==prep['candidate_sha256'] and sha(Path(__file__))==prep['recipe_sha256']
    commands={'lint':['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(O/'VexiiRiscv.v')]}
    for label in ['control','next-state']:
        d=O/label;commands[label]=['verilator','--cc','--exe','--build','-j','4','--Wno-fatal','--top-module','proof','--Mdir',str(d/'obj'),str(d/'proof.v'),str(d/'main.cpp')]
    save(O/'commands.json',commands)
    for label,cmd in commands.items():
        with (O/(label+'-build.log')).open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    results={}
    for label in ['control','next-state']:
        run=subprocess.run([str(O/label/'obj/Vproof')],text=True,capture_output=True,check=True)
        (O/(label+'-run.log')).write_text(run.stdout+run.stderr);results[label]=json.loads(run.stdout);assert results[label]['passed']
    assert results['control']['checks']==32768 and results['control']['missing_RS1_negative']>0
    assert results['next-state']['checks']==1048576
    save(O/'result.json',dict(passed=True,literal=results,full_CPU_lint_passed=True,zero_added_FF=True,zero_added_cycles=True,native_and_physical_pending=True,proof_scope='Two-state literal extracted control cones and complete local next-state. No native execution or CAD is claimed.'))
    files=[Path(__file__),B,P/'cpu-parallel-control-v1/manifest.json',P/'cpu-aligner-endpoint-audit-v1/manifest.json',P/'parallel-endpoint-census-v1/manifest.json']+[p for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts and p.name!='manifest.json']
    save(O/'manifest.json',dict(passed=True,candidate_sha256=sha(O/'VexiiRiscv.v'),native_and_physical_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files}))
    print('PASS',json.dumps(results));print('MANIFEST',sha(O/'manifest.json'),flush=True)

if __name__=='__main__':
    assert len(sys.argv)==2 and sys.argv[1] in ['prepare','prove']
    globals()[sys.argv[1]]()
