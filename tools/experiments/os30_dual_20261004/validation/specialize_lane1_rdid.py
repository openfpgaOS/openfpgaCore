#!/usr/bin/env python3
"""Specialize the immediate lane's destination RF type with guarded proofs."""
import argparse
import difflib
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
REF=BASE/'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
REF_SHA='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
EXTRACTOR=ROOT/'tools/check_vexii_os20_timing.py'
EXTRACTOR_SHA='80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd,log,**kw):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),check=True,stdout=f,stderr=subprocess.STDOUT,**kw)
def strip(x):
    x=x.strip()
    while x.startswith('('):
        depth=0
        for i,c in enumerate(x):
            depth+=(c=='(')-(c==')')
            if not depth:break
        if i!=len(x)-1:break
        x=x[1:-1].strip()
    return x
def split(x,op):
    depth=0;out=[];last=0;i=0
    while i<len(x):
        c=x[i];depth+=(c in '({[')-(c in ')}]')
        if not depth and x[i:i+len(op)]==op:
            out.append(x[last:i].strip());last=i+len(op);i=last
        else:i+=1
    if out:out.append(x[last:].strip())
    return out

class MaskCubes:
    """Exact parser for positive OR trees of masked instruction equalities.

    Fail closed on any other operation. Concatenation/reduction trees retain
    every bit as an OR term; named [0] aliases must have exactly one bit.
    Each cube denotes all 32-bit words matching its (mask,value) pair.
    """
    def __init__(self,source,lane):
        self.assign=dict(re.findall(r'^  assign (\w+) = ([^;]+);',source,re.M))
        self.width={n:int(w)+1 if w else 1 for w,n in re.findall(r'^  (?:wire|reg)\s+(?:\[(\d+):0\]\s+)?(\w+);$',source,re.M)}
        self.inst=f'decode_ctrls_2_down_Decode_INSTRUCTION_{lane}'
        self.visited=set()
    def word(self,x):
        x=strip(x)
        if x==self.inst:return ('inst',0xffffffff)
        if x in self.assign:
            self.visited.add(x);return self.word(self.assign[x])
        m=re.fullmatch(r"32'h([0-9a-f]+)",x)
        if m:return ('constant',int(m[1],16))
        p=split(x,'&');assert len(p)==2,x
        a,b=self.word(p[0]),self.word(p[1]);assert a[0]=='inst' and b[0]=='constant',x
        return ('inst',a[1]&b[1])
    def cubes(self,x):
        x=strip(x)
        if x in self.assign:
            self.visited.add(x);return self.cubes(self.assign[x])
        m=re.fullmatch(r'(\w+)\[0\]',x)
        if m:
            assert self.width[m[1]]==1,x
            return self.cubes(m[1])
        if x.startswith('|'):return self.cubes(x[1:])
        if x.startswith('{') and x.endswith('}'):
            parts=split(x[1:-1],',');assert parts,x
            return [c for p in parts for c in self.cubes(p)]
        p=split(x,'==');assert len(p)==2,x
        a,b=self.word(p[0]),self.word(p[1]);assert a[0]=='inst' and b[0]=='constant',x
        assert not b[1]&~a[1],x
        return [(a[1],b[1])]

def decoder_proof(source):
    results=[];contracts={}
    for lane in range(2):
        c=MaskCubes(source,lane);legal=f'decode_ctrls_2_down_Decode_LEGAL_{lane}'
        block=re.findall(r'^  always @\(\*\) begin\n    '+legal+r' = [^;]+;.*?^  end',source,re.M|re.S)
        assert len(block)==1
        expr=re.search(re.escape(legal)+r' = ([^;]+);',block[0])[1]
        parts=split(strip(expr),'&&');assert len(parts)==2
        assert parts[1]==f'(! decode_ctrls_2_down_Decode_DECOMPRESSION_FAULT_{lane})'
        priv='when_DecoderPlugin_l187'+('_1' if lane else '')
        expected=f'''  always @(*) begin
    {legal} = {expr};
    if(DecoderPlugin_logic_laneLogic_{lane}_fp_triggered) begin
      {legal} = 1'b0;
    end
    if({priv}) begin
      {legal} = 1'b0;
    end
  end'''
        assert block[0]==expected
        legal_cubes=c.cubes(parts[0])
        layer_cubes=c.cubes(f'decode_ctrls_2_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_{lane}')
        rdid_cubes=c.cubes(f'decode_ctrls_2_down_RD_RFID_{lane}')
        rs1_cubes=c.cubes(f'decode_ctrls_2_down_RS1_RFID_{lane}')
        counts={}
        for label,cubes in [('RD',rdid_cubes),('RS1_readonly_followon',rs1_cubes)]:
            count=0
            for terms in itertools.product(legal_cubes,layer_cubes,cubes):
                # Pairwise consistency of masked bit assignments is necessary
                # and sufficient for their intersection to contain a word.
                compatible=all(not((x[1]^y[1])&(x[0]&y[0])) for x,y in itertools.combinations(terms,2))
                assert not compatible,(lane,label,terms)
                count+=1
            counts[label]=count
        results.append(dict(lane=lane,legal_cubes=legal_cubes,layer_cubes=layer_cubes,
            rdid_cubes=rdid_cubes,rs1_cubes=rs1_cubes,unsatisfiable_intersections=counts,
            exact_all_instruction_words=2**32,arbitrary_fault_fp_and_privilege_rejection_gates=True))
        contracts[legal]=block[0]
        contracts.update({n:'  assign '+n+' = '+c.assign[n]+';' for n in c.visited})
    assert results[0]['legal_cubes']==results[1]['legal_cubes']
    assert results[0]['layer_cubes']==results[1]['layer_cubes']
    assert results[0]['rdid_cubes']==results[1]['rdid_cubes']
    return results,contracts

def source_contracts(source):
    contracts={}
    def once(text):
        assert source.count(text)==1,(text,source.count(text));contracts[text.splitlines()[0]]=text
    # All execute lanes advance together; cancellation only clears valid/en.
    for stage in range(13):
        once(f'  assign execute_ctrl{stage}_down_isReady = execute_ctrl{stage}_down_ready;')
        once(f'  assign execute_ctrl{stage}_up_ready = execute_ctrl{stage}_down_isReady;')
        if stage<12:once(f'  assign execute_ctrl{stage}_down_ready = execute_ctrl{stage+1}_up_ready;')
        else:once('  assign execute_ctrl12_down_ready = (! execute_freeze_valid);')
    # Reject any additional writer to one of these producer state fields.
    for stage in range(1,6):
        for field in ['RD_RFID','RD_ENABLE']:
            name=f'execute_ctrl{stage}_up_{field}_lane1'
            expected=f'      {name} <= execute_ctrl{stage-1}_down_{field}_lane1;'
            assert re.findall(r'^.*\b'+name+r' <=[^;]+;',source,re.M)==[expected]
            # The whole unreset payload block uses per-stage ready branches.
            matches=re.findall(r'^    if\(execute_ctrl'+str(stage-1)+r'_down_isReady\) begin\n(?:(?!^    end).)*?^    end',source,re.M|re.S)
            assert sum(expected in m for m in matches)==1
            contracts[name]=expected
        sel=f'execute_ctrl{stage}_up_LANE_SEL_lane1'
        writes=re.findall(r'^.*\b'+sel+r' <=[^;]+;',source,re.M)
        assert writes==[f"      {sel} <= 1'b0;",f'        {sel} <= execute_ctrl{stage-1}_down_LANE_SEL_lane1;'],writes
        reset_blocks=re.findall(r'^  always @\(posedge clk or posedge reset\) begin\n.*?^  end',source,re.M|re.S)
        assert sum(f"      {sel} <= 1'b0;" in b.split('    end else begin',1)[0] for b in reset_blocks)==1
        once(f'      if(execute_ctrl{stage-1}_down_isReady) begin\n        execute_ctrl{stage}_up_LANE_SEL_lane0 <= execute_ctrl{stage-1}_down_LANE_SEL_lane0;\n        {sel} <= execute_ctrl{stage-1}_down_LANE_SEL_lane1;'+ ('\n      end' if stage not in [3,4] else f'\n        execute_ctrl{stage}_up_LsuL1_SEL_lane0 <= execute_ctrl{stage-1}_down_LsuL1_SEL_lane0;\n      end'))
    for stage in range(5):
        for field in ['LANE_SEL','RD_ENABLE']:
            stem=f'execute_ctrl{stage}_{field}_lane1_bypass'
            block=f'''  always @(*) begin
    {stem} = execute_ctrl{stage}_up_{field}_lane1;
    if(when_ExecuteLanePlugin_l306_{stage+5}) begin
      {stem} = 1'b0;
    end
  end'''
            once(block)
            once(f'  assign execute_ctrl{stage}_down_{field}_lane1 = {stem};')
    # Destination RF type has no consumer besides these guarded comparisons,
    # dispatch assignment, declarations and five pure state transfers/aliases.
    lines=[line for line in source.splitlines() if re.search(r'\bexecute_ctrl\d+_(?:up|down)_RD_RFID_lane1\b',line)]
    compare=[line for line in lines if '==' in line]
    assert len(compare)==21,len(compare)
    direct=[line for line in compare if 'bypassEnables[' in line or 'youngerHits_1' in line]
    assert len(direct)==9
    for line in direct:
        stage=int(re.search(r'execute_ctrl(\d+)_down_RD_RFID_lane1',line)[1])
        assert f'(execute_ctrl{stage}_up_LANE_SEL_lane1 && execute_ctrl{stage}_up_RD_ENABLE_lane1)' in line
    pre=[line for line in compare if 'assign int_rs2_preselect_' in line];assert len(pre)==8
    caps=[line for line in compare if 'int_rs2_write_match_' in line];assert len(caps)==4
    for stage in range(2,6):
        match=f'int_rs2_write_match_{stage}_1'
        block=re.search(r'  reg '+match+r';\n  always @\(posedge clk\) if\(execute_ctrl0_down_isReady\)\n    '+match+r' <= [^;]+;',source)[0]
        assert f'{match} <= execute_ctrl{stage-1}_down_RD_ENABLE_lane1 && ' in block
        for c in range(2):assert f'int_rs2_preselect_{stage}_1_candidate{c}' in block
        once(f'    execute_lane0_bypasser_integer_RS2_bypassEnables[{(stage-2)*2+1}] = execute_ctrl{stage}_up_LANE_SEL_lane1 && {match};')
        assert len(re.findall(r'\b'+match+r'\b',source))==3
        contracts[match]=block
    for line in lines:
        assert line in compare or re.fullmatch(r'  (reg|wire)\s+\[0:0\]\s+execute_ctrl\d+_(up|down)_RD_RFID_lane1;',line) or re.fullmatch(r'  assign execute_ctrl\d+_down_RD_RFID_lane1 = execute_ctrl\d+_up_RD_RFID_lane1;',line) or 'execute_ctrl0_up_RD_RFID_lane1 =' in line or re.fullmatch(r'      execute_ctrl\d+_up_RD_RFID_lane1 <= execute_ctrl\d+_down_RD_RFID_lane1;',line),line
    return contracts,lines

def induction():
    # State predicates: valid&&write_enable implies RD type is integer.
    # All binary unreset payload values are admitted when the slot is invalid.
    count=0
    for old,input_,ready,reset,cancel in itertools.product(itertools.product(range(2),repeat=4),itertools.product(range(2),repeat=4),range(2),range(2),range(2)):
        ov,oe,oi,on=old;iv,ie,ii,inn=input_
        if ov and oe and (oi or on):continue
        if iv and ie and (ii or inn):continue
        nv=0 if reset else ((iv and not cancel) if ready else ov)
        ne=(ie and not cancel) if ready else oe
        ni=ii if ready else oi;nn=inn if ready else on
        assert not(nv and ne and (ni or nn))
        count+=1
    return count

def finish_proof(source,candidate,proof_source,cones,out,contracts,dec):
    """Extract every affected consumer, admitting arbitrary invalid payloads."""
    def once(text):
        assert source.count(text)==1,text
        contracts[text.splitlines()[0]]=text
    # Decode stage3 captures its eligibility, RF type and trap fields together.
    for lane in range(2):
        for field in ['RD_RFID','TRAP','DispatchPlugin_logic_LANES_LAYER_HIT_0']:
            name=f'decode_ctrls_3_up_{field}_{lane}'
            expected=f'      {name} <= decode_ctrls_2_down_{field}_{lane};'
            assert re.findall(r'^.*\b'+name+r' <=[^;]+;',source,re.M)==[expected]
            branches=re.findall(r'^    if\(decode_ctrls_2_down_isReady\) begin\n(?:(?!^    end).)*?^    end',source,re.M|re.S)
            assert sum(expected in b for b in branches)==1
            contracts[name]=expected
            once(f'  assign decode_ctrls_3_down_{field}_{lane} = {name};')
        mask=f'decode_ctrls_3_up_LANE_SEL_{lane}'
        assert re.findall(r'^.*\b'+mask+r' <=[^;]+;',source,re.M)==[
            f"      {mask} <= 1'b0;",f'        {mask} <= decode_ctrls_2_down_LANE_SEL_{lane};',f"        {mask} <= 1'b0;"]
        once(f'''  always @(*) begin
    DispatchPlugin_logic_candidates_{lane}_ctx_laneLayerHits = {{decode_ctrls_3_down_DispatchPlugin_logic_LANES_LAYER_HIT_1_{lane},decode_ctrls_3_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_{lane}}};
    if(decode_ctrls_3_down_TRAP_{lane}) begin
      DispatchPlugin_logic_candidates_{lane}_ctx_laneLayerHits = 2'b10;
    end
  end''')
        once(f'  assign DispatchPlugin_logic_candidates_{lane}_ctx_valid = ((decode_ctrls_3_up_isValid && decode_ctrls_3_up_LANE_SEL_{lane}) && (! DispatchPlugin_logic_feeds_{lane}_sent));')
        for field in ['RD_RFID','TRAP']:
            once(f'  assign DispatchPlugin_logic_candidates_{lane}_ctx_hm_{field} = decode_ctrls_3_down_{field}_{lane};')
    once('''  always @(*) begin
    execute_ctrl0_up_RD_ENABLE_lane1 = _zz_execute_ctrl0_up_RD_ENABLE_lane1[0];
    if(when_DispatchPlugin_l444_1) begin
      execute_ctrl0_up_RD_ENABLE_lane1 = 1'b0;
    end
  end''')
    once('  assign when_DispatchPlugin_l444_1 = ((! execute_ctrl0_up_LANE_SEL_lane1) || DispatchPlugin_logic_inserter_1_trap);')
    # Projected induction for the decode capture: T includes any preexisting,
    # decoder, interrupt or prediction trap. A live nontrapped eligible frame
    # has RID0; reset/flush only clear live, and stalls hold all payload.
    decode_transitions=0
    states=[s for s in itertools.product(range(2),repeat=4) if not(s[0] and not s[1] and s[2] and s[3])]
    for previous,incoming,ready,reset,flush in itertools.product(states,states,range(2),range(2),range(2)):
        live=0 if reset or flush else (incoming[0] if ready else previous[0])
        trap,hit,rid=incoming[1:] if ready else previous[1:]
        assert not(live and not trap and hit and rid)
        decode_transitions+=1
    modules='';decl=[];instances=[];tests=[]
    # Both emitted decoders: symbolic cube proof is complete over2^32 words;
    # this cross-check binds the parser's result to independently compiled RTL.
    decl+=['reg [31:0] inst;','reg [2:0] faults;']
    for lane in range(2):
        inputs=[f'decode_ctrls_2_down_Decode_INSTRUCTION_{lane}',f'decode_ctrls_2_down_Decode_DECOMPRESSION_FAULT_{lane}',
                f'DecoderPlugin_logic_laneLogic_{lane}_fp_triggered','when_DecoderPlugin_l187'+('_1' if lane else '')]
        outputs=[f'decode_ctrls_2_down_DispatchPlugin_logic_LANES_LAYER_HIT_0_{lane}',f'decode_ctrls_2_down_RD_RFID_{lane}',f'decode_ctrls_2_down_Decode_LEGAL_{lane}']
        module,width=cones.cone(proof_source,f'decoder{lane}',inputs,outputs);assert width==3;modules+=module
        decl.append(f'wire [2:0] dec{lane};');instances.append(f'decoder{lane} d{lane}(inst,faults[0],faults[1],faults[2],dec{lane});')
    def expr(cubes):return '('+' || '.join(f"((inst & 32'h{m:08x})==32'h{v:08x})" for m,v in cubes)+')'
    decl+=['wire cube_hit='+expr(dec[0]['layer_cubes'])+';', 'wire cube_rid='+expr(dec[0]['rdid_cubes'])+';',
           'wire cube_legal='+expr(dec[0]['legal_cubes'])+' && !(|faults);']
    tests.append('''for(integer n=0;n<262144;n++)begin
      inst={16'((n>>16)==0 ? 0 : (n>>16)==1 ? 16'hffff : (n>>16)==2 ? 16'h5555 : 16'haaaa),16'(n)};
      for(integer f=0;f<8;f++)begin faults=3'(f);#1;
        if(dec0!=={cube_hit,cube_rid,cube_legal} || dec1!==dec0)$fatal(1,"actual decoder/cube mismatch");
        if(dec0==3'b111)$fatal(1,"legal lane1 instruction targets FP RF");
      end
    end
    $display("PASS 2097152 compiled decoder/cube cross-check cases, both lanes; separate cube intersection proves all 2^32 words");''')
    # Actual decoder trap cones: a live nontrap instruction must be legal.
    decl+=['reg [4:0] trap_inputs;']
    for lane in range(2):
        inputs=[f'decode_ctrls_2_up_LANE_SEL_{lane}',f'decode_ctrls_2_up_TRAP_{lane}',f'decode_ctrls_2_down_Decode_LEGAL_{lane}',
                f'DecoderPlugin_logic_laneLogic_{lane}_interruptPending',f'DecoderPlugin_logic_laneLogic_{lane}_fixer_doIt']
        module,width=cones.cone(proof_source,f'trap{lane}',inputs,[f'decode_ctrls_2_down_TRAP_{lane}']);assert width==1;modules+=module
        decl.append(f'wire trap{lane};');instances.append(f'trap{lane} t{lane}('+','.join([f'trap_inputs[{i}]' for i in range(5)]+[f'trap{lane}'])+');')
    tests.append('''for(integer n=0;n<32;n++)begin trap_inputs=5'(n);#1;
      if(trap0!==trap1)$fatal(1,"trap lanes differ");
      if(trap_inputs[0] && !trap0 && !trap_inputs[2])$fatal(1,"live nontrap decoder instruction is illegal");
    end
    $display("PASS all32 actual decoder trap control states, both lanes");''')
    # Actual selected metadata, with selector uniqueness/eligibility proved by
    # the full scheduler, and candidate metadata relation proved above.
    inputs=['DispatchPlugin_logic_inserter_1_oh']
    for lane in range(2):
        inputs += [f'DispatchPlugin_logic_candidates_{lane}_ctx_hm_{f}' for f in ['RD_RFID','RD_ENABLE','TRAP']]
        inputs += [f'DispatchPlugin_logic_candidates_{lane}_cancel']
    inputs+=['DispatchPlugin_api_haltDispatch']
    outputs=['execute_ctrl0_up_LANE_SEL_lane1','execute_ctrl0_up_RD_ENABLE_lane1','execute_ctrl0_up_RD_RFID_lane1']
    old,width=cones.cone(proof_source,'meta_old',inputs,outputs);assert width==3
    newer,_=cones.cone(candidate.replace('  (* maxfan = 16 *) wire execute_freeze_valid;','  wire execute_freeze_valid;'),'meta_new',inputs,outputs)
    modules+=old+newer;decl+=['reg [10:0] meta;','wire [2:0] meta_a,meta_b;']
    ports=['meta[1:0]']+[f'meta[{i}]' for i in range(2,11)]
    instances+=['meta_old ma('+','.join(ports+['meta_a'])+');','meta_new mb('+','.join(ports+['meta_b'])+');']
    tests.append('''for(integer n=0;n<2048;n++)begin meta=11'(n);#1;
      if(meta[1:0]!=3 && !(meta[0] && !meta[4] && meta[2]) && !(meta[1] && !meta[8] && meta[6]))begin
        if(meta_a[2:1]!==meta_b[2:1])$fatal(1,"dispatch metadata live/enable changed");
        if(meta_a[2] && meta_a[1] && (meta_a[0] || meta_b[0]))$fatal(1,"enabled destination type not integer");
      end
    end
    $display("PASS all2048 actual selected-metadata states subject to proved selector/decoded-candidate invariants");''')
    # Complete RFID-consumer closure. Address equality leaves are independent
    # booleans: an over-approximation of all five-bit tag combinations.
    assignments=dict(re.findall(r'^  assign (\w+) = ([^;]+);',source,re.M))
    direct=[line.strip() for line in source.splitlines() if '==' in line and 'RD_RFID_lane1' in line and ('bypassEnables[' in line or 'youngerHits_1' in line)]
    capture=[]
    for stage in range(2,6):
        expression=re.search(r'    int_rs2_write_match_'+str(stage)+r'_1 <= ([^;]+);',source)[1]
        for c in range(2):
            name=f'int_rs2_preselect_{stage}_1_candidate{c}'
            assert len(re.findall(r'\b'+name+r'\b',source))==3,name
            expression=expression.replace(name,'('+assignments[name]+')')
        capture.append(expression)
    def normalize(expression,direct=False):
        expression=re.sub(r'\(execute_ctrl\d+_down_RD_PHYS_lane1 == DispatchPlugin_logic_candidates_0_ctx_hm_RS2_PHYS\)','addr0',expression)
        expression=re.sub(r'\(execute_ctrl\d+_down_RD_PHYS_lane1 == DispatchPlugin_logic_candidates_1_ctx_hm_RS2_PHYS\)','addr1',expression)
        expression=re.sub(r"\(execute_ctrl\d+_down_RD_PHYS_lane1 == 5'h0\)",'addrzero',expression)
        expression=re.sub(r'\(execute_ctrl\d+_down_RD_PHYS_lane1 == execute_ctrl\d+_down_RS[12]_PHYS_lane[01]\)','addr0',expression)
        expression=re.sub(r'execute_ctrl\d+_down_RD_RFID_lane1','rid',expression)
        expression=re.sub(r'execute_ctrl\d+_up_LANE_SEL_lane1','live',expression)
        expression=re.sub(r'execute_ctrl\d+_up_RD_ENABLE_lane1','enable',expression)
        expression=re.sub(r'execute_ctrl\d+_down_RD_ENABLE_lane1','(enable && !cancel)',expression)
        expression=re.sub(r'execute_ctrl\d+_down_RS[12]_RFID_lane[01]','cid0',expression)
        expression=expression.replace('DispatchPlugin_logic_candidates_0_ctx_hm_RS2_RFID','cid0').replace('DispatchPlugin_logic_candidates_1_ctx_hm_RS2_RFID','cid1')
        expression=expression.replace('_zz_execute_ctrl0_up_LANE_AGE_lane0_1','sel1').replace('_zz_execute_ctrl0_up_LANE_AGE_lane0','sel0')
        assert not re.search(r'\b(?:execute_|DispatchPlugin_|_zz_)\w+',expression),expression
        return expression
    outputs_direct=[]
    for line in direct:
        expression=line.split(' = ',1)[1].rstrip(';');outputs_direct.append(normalize(expression,True))
    outputs_capture=[normalize(e) for e in capture]
    assert len(set(outputs_direct))==1 and len(set(outputs_capture))==1
    ports='live,enable,rid,cancel,cid0,cid1,sel0,sel1,addr0,addr1,addrzero'
    modules+='module consumers(input wire '+ports+',output wire [8:0] direct,output wire[3:0] captured);\n'
    modules+='\n'.join(f'assign direct[{i}]={e};' for i,e in enumerate(outputs_direct))+'\n'
    modules+='\n'.join(f'assign captured[{i}]={e};' for i,e in enumerate(outputs_capture))+'\nendmodule\n'
    decl+=['reg [16:0] ctl;','wire [8:0] dir_a,dir_b;','wire [3:0] cap_a,cap_b;','reg next_live;','reg [3:0] next_a,next_b;']
    # ctl live/en/oldID/newID/cancel/ready/reset/previousLive/oldMatch/newMatch/
    # candidate0ID/candidate1ID/select0/select1/address0/address1/addressZero.
    indices=[0,1,2,4,10,11,12,13,14,15,16]
    instances+=['consumers ca('+','.join([f'ctl[{i}]' for i in indices]+['dir_a','cap_a'])+');']
    indices[2]=3
    instances+=['consumers cb('+','.join([f'ctl[{i}]' for i in indices]+['dir_b','cap_b'])+');']
    tests.append('''for(integer n=0;n<131072;n++)begin ctl=17'(n);#1;
      if(!(ctl[0] && ctl[1] && (ctl[2]||ctl[3])) && !(ctl[7] && (ctl[8]!=ctl[9])))begin
        if(dir_a!==dir_b)$fatal(1,"live direct consumer mismatch");
        next_live=ctl[6] ? 1'b0 : (ctl[5] ? (ctl[0] && !ctl[4]) : ctl[7]);
        next_a=ctl[5] ? cap_a : {4{ctl[8]}};
        next_b=ctl[5] ? cap_b : {4{ctl[9]}};
        if(next_live && next_a!==next_b)$fatal(1,"live captured match mismatch after reset/stall/cancel");
      end
    end
    $display("PASS all131072 consumer/transition states with arbitrary address-equality leaves; invalid precompute differences remain unobserved");''')
    tb=modules+'\nmodule tb;\n'+'\n'.join(decl+instances)+'\ninitial begin\n'+'\n'.join(tests)+'\n$finish;end\nendmodule\n'
    (out/'closure.sv').write_text(tb)
    run(['verilator','--binary','--timing','-j','2','--top-module','tb','-Wno-fatal','--Mdir',out/'closure-obj',out/'closure.sv'],out/'closure-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'closure-build.log').read_text())
    run([out/'closure-obj/Vtb'],out/'closure.log')
    return dict(decode_capture_transitions=decode_transitions,execute_capture_transitions=induction(),
        decoder_crosscheck_cases=2097152,trap_cases=32,selected_metadata_cases=2048,consumer_transition_cases=131072,
        address_equalities_independent_overapproximation=True,invalid_match_registers_may_differ_but_all_live_observers_equal=True)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=BASE/'lane1-rdid-constant-v1/immediate');p.add_argument('--resume',action='store_true');args=p.parse_args()
    assert sha(REF)==REF_SHA and sha(EXTRACTOR)==EXTRACTOR_SHA
    source=REF.read_text();dec,contracts=decoder_proof(source)
    state_contracts,census=source_contracts(source);contracts.update(state_contracts)
    old=re.findall(r'^  assign execute_ctrl0_up_RD_RFID_lane1 = [^;]+;',source,re.M);assert len(old)==1
    new="  assign execute_ctrl0_up_RD_RFID_lane1 = 1'b0;"
    candidate=source.replace(old[0],new,1);assert candidate.replace(new,old[0],1)==source
    seq=lambda s:re.findall(r'^  always @\((?:posedge|negedge).*?^  end',s,re.M|re.S)
    assert seq(source)==seq(candidate)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=args.resume)
    assert not (out/'manifest.json').exists(),'Do not overwrite a frozen qualified source'
    (out/'VexiiRiscv.v').write_text(candidate)
    (out/'decoder-cubes.json').write_text(json.dumps(dec,indent=2)+'\n')
    (out/'source-contracts.json').write_text(json.dumps(contracts,indent=2)+'\n')
    (out/'consumer-census.txt').write_text('\n'.join(census)+'\n')
    spec=importlib.util.spec_from_file_location('cones',EXTRACTOR);cones=importlib.util.module_from_spec(spec);spec.loader.exec_module(cones)
    # Actual scheduler: exhaust every control state and establish that each
    # selected instruction is valid and eligible for integer-immediate lane1.
    widths={}
    for lane in range(2):
        for key,width in [('ctx_valid',1),('flushHazards',1),('fenceOlderHazards',1),('ctx_laneLayerHits',2),('rsHazards',2),('reservationHazards',2)]:widths[f'DispatchPlugin_logic_candidates_{lane}_{key}']=width
    for name in ['DispatchPlugin_logic_scheduler_arbiters_1_candHazard','execute_freeze_valid','DispatchPlugin_api_haltDispatch']:widths[name]=1
    annotated='  (* maxfan = 16 *) wire execute_freeze_valid;'
    assert source.count(annotated)==1
    proof_source=source.replace(annotated,'  wire execute_freeze_valid;')
    inputs=list(widths);sched,sw=cones.cone(proof_source,'scheduler',inputs,['DispatchPlugin_logic_inserter_1_oh']);assert sw==2
    tb=sched+'\nmodule tb;\n'+ '\n'.join(f'reg [{w-1}:0] {n};' for n,w in widths.items())+'\nwire[1:0] selected;\nscheduler dut('+','.join(inputs+['selected'])+');\ninitial begin\nfor(integer n=0;n<(1<<21);n++)begin\n{'+','.join(inputs)+"}=21'(n);#1;\n"
    tb+='if(selected==3)$fatal(1,"lane1 selector overlap");\n'
    for lane in range(2):tb+=f'if(selected[{lane}] && (!DispatchPlugin_logic_candidates_{lane}_ctx_valid || !DispatchPlugin_logic_candidates_{lane}_ctx_laneLayerHits[0]))$fatal(1,"lane1 selected ineligible candidate");\n'
    tb+='$display("PASS only-after-loop placeholder");\n' if False else ''
    tb+='end\n$display("PASS all 2097152 actual scheduler states: selected lane1 source is valid, eligible and unique");$finish;end\nendmodule\n'
    (out/'scheduler.sv').write_text(tb)
    run(['verilator','--binary','--timing','-j','2','--top-module','tb','-Wno-fatal','--Mdir',out/'scheduler-obj',out/'scheduler.sv'],out/'scheduler-build.log')
    run([out/'scheduler-obj/Vtb'],out/'scheduler.log')
    closure=finish_proof(source,candidate,proof_source,cones,out,contracts,dec)
    (out/'source-contracts.json').write_text(json.dumps(contracts,indent=2)+'\n')
    patch=out/'lane1-rdid-constant.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
    replay=out/'patch-replay';replay.mkdir(exist_ok=args.resume);(replay/'VexiiRiscv.v').write_text(candidate)
    for label,extra,expected in [('reverse',['-R'],source),('replay',[],candidate)]:
        run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',patch],out/(label+'.log'),cwd=replay)
        assert (replay/'VexiiRiscv.v').read_text()==expected
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',out/'VexiiRiscv.v'],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copyfile(__file__,out/Path(__file__).name)
    shutil.copyfile(EXTRACTOR,out/EXTRACTOR.name)
    manifest=dict(reference_sha256=REF_SHA,candidate_sha256=sha(out/'VexiiRiscv.v'),
       exact_scope='Only execute_ctrl0_up_RD_RFID_lane1 RHS becomes integer zero; all sequential source and all other bytes unchanged.',
       all_32bit_decoder_words=True,rdid_cube_intersections_per_decoder=408,decoder_lanes=2,
       all_RAW_RVC_results_covered_by_arbitrary_32bit_decoded_word=True,
       scheduler_states=2097152,**closure,direct_qualified_consumers=9,registered_precompute_consumers=4,
       all_sequential_source_unchanged=True,expected_removed_constant_registers=5,
       cycles_added=0,lane_capabilities_changed=False,isa_changed=False,
       exact_reverse_replay=True,full_netlist_lint=True,
       whole_cpu_qualification='Pending independent review and selected fourteen-case suite.',
       physical_qualification='Expected simplification of five destination-type FFs and21 comparator occurrences; fit required to measure actual benefit.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    evidence=['decoder-cubes.json','source-contracts.json','consumer-census.txt','scheduler.sv','scheduler.log','closure.sv','closure.log','lint.log','lane1-rdid-constant.patch']
    recipe=dict(script_sha256=sha(out/Path(__file__).name),reference_sha256=REF_SHA,candidate_sha256=manifest['candidate_sha256'],
        extractor_sha256=EXTRACTOR_SHA,evidence={n:sha(out/n) for n in evidence})
    (out/'recipe.json').write_text(json.dumps(recipe,indent=2)+'\n')
    print('PASS frozen lane1 destination type specialization:',manifest['candidate_sha256'])

if __name__=='__main__':main()
