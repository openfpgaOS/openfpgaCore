#!/usr/bin/env python3
"""Private v17 count-mask encoding and terminal span-step predicate."""
import difflib,json,re,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,sha,write
P=CAMP/'phase10'; BASE=P/'gpu-ring-equality-v16/gpu_core.v'; OUT=P/'gpu-span-control-v17'
TERMINAL='''// This is the Boolean terminal condition of spanprod_next_calc, without
// routing control through that function's encoded next-step data mux.
(* keep *) wire spanprod_calc_terminal =
    ((spanprod_calc_step == 4'd2) && !spanprod_attr_persp
        && !spanprod_flags[SPAN_COLORMAP] && !spanprod_truecolor)
 || ((spanprod_calc_step == 4'd3)
        && !spanprod_flags[SPAN_COLORMAP] && !spanprod_truecolor)
 || ((spanprod_calc_step == 4'd4) && !cmd_is_draw_vert_tri_rgb
        && !cmd_is_draw_xform_tri_rgb && !cmd_is_draw_indexed_tri && !cmd_is_draw_clip_tri)
 || ((spanprod_calc_step == SPANPROD_STEP_DEPTH) && !spanprod_cd_combine)
 || (spanprod_calc_step == 4'd6) || (spanprod_calc_step == SPANPROD_STEP_NONE)
 || (spanprod_calc_step >= SPANPROD_STEP_DB);
'''
def prepare():
 assert sha(BASE)=='32de956cdfc6a85e627aed6772c805494c4475a92d9193ece5673d7650aff177'
 old=BASE.read_text(); new=old; sites=[]
 pattern=r'(store_count\(([^,;]+),\s*([^;]+)\);[^\n]*?spanprod_cnt_valid\[([^\n]+?)\]\s*<=\s*)1\'b1;'
 # Some setters start on the next line; preserve all surrounding text/priority.
 pattern=r'(store_count\(([^,;]+),\s*([^;]+)\);(?:[^\n]*?|\s*)spanprod_cnt_valid\[([^\n]+?)\]\s*<=\s*)1\'b1;'
 def replace(m):
  addr,value,flagaddr=m.group(2).strip(),m.group(3).strip(),m.group(4).strip()
  assert addr==flagaddr,(addr,flagaddr)
  sites.append(dict(address=addr,value=value,parent_line=old[:m.start()].count('\n')+1))
  return m.group(1)+'('+value+" != 16'd0);"
 new,n=re.subn(pattern,replace,new); assert n==10,n
 old_nz="assign spanprod_cur_nonzero = (spanprod_cur_count != 16'd0);"
 assert new.count(old_nz)==1
 new=new.replace(old_nz,'assign spanprod_cur_nonzero = spanprod_cur_valid && spanprod_cur_count_valid;')
 new=new.replace("//   cnt_valid[L]  — lane L's count word written since the last clear site\n//                   (compact w0 / long-form w29 / prepare_next_record_chunk).\n//                   Clear = the old count[L]<=0; SELECT reads 0 when clear.","//   cnt_valid[L]  — lane L's count word was written AND is nonzero since\n//                   the last clear (compact w0 / long w29 / next chunk).\n//                   Zero writes clear it too. SELECT's count remains exact,\n//                   and its nonzero flag no longer depends on the count RAM.")
 marker='// Launch dispatch — the SAME dedicated launch tasks'
 assert new.count(marker)==1;new=new.replace(marker,TERMINAL+'\n'+marker)
 new,n=re.subn(r'spanprod_next_calc\(spanprod_calc_step\)\s*==\s*SPANPROD_STEP_NONE','spanprod_calc_terminal',new);assert n==2
 OUT.mkdir(exist_ok=False);(OUT/'gpu_core.v').write_text(new)
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='v16/gpu_core.v',tofile='v17/gpu_core.v')))
 shutil.copy2(__file__,OUT/'recipe.py')
 write(OUT/'prepared.json',dict(candidate_sha256=sha(OUT/'gpu_core.v'),parent_sha256=sha(BASE),count_write_sites=sites,count_whole_mask_clears=4,terminal_control_sites=2,added_flip_flops=0,added_states=0,added_cycles=0,deployment_ready=False))
 print(sha(OUT/'gpu_core.v'))
def literal():
 d=OUT/'literal';d.mkdir(exist_ok=False);old=BASE.read_text();new=(OUT/'gpu_core.v').read_text()
 funcs=old[old.index('localparam [3:0] SPANPROD_STEP_NONE'):old.index('// Launch dispatch')]
 assign_old=re.search(r'assign spanprod_cur_count =[^;]+;',old).group(0)
 assert assign_old in new and TERMINAL in new
 tb='''module tb_span(input[3:0]spanprod_calc_step,input[9:0]ctl,input cur_valid,old_valid,input[15:0]raw,
 output old_terminal,new_terminal,output[15:0]old_count,new_count,output old_nonzero,new_nonzero);
localparam SPAN_COLORMAP=0;
wire spanprod_z_write=ctl[0],spanprod_z_test=ctl[1],spanprod_attr_persp=ctl[2];
wire[0:0]spanprod_flags=ctl[3];wire spanprod_truecolor=ctl[4];
wire cmd_is_draw_vert_tri_rgb=ctl[5],cmd_is_draw_xform_tri_rgb=ctl[6],cmd_is_draw_indexed_tri=ctl[7],cmd_is_draw_clip_tri=ctl[8];
wire spanprod_cd_combine=ctl[9];
'''+funcs+TERMINAL+'''
assign old_terminal=spanprod_next_calc(spanprod_calc_step)==SPANPROD_STEP_NONE;
assign new_terminal=spanprod_calc_terminal;
wire spanprod_cur_valid=cur_valid,spanprod_cur_count_valid=old_valid&&(raw!=16'd0);
wire[15:0]spanprod_cur_count_raw=raw,spanprod_cur_count;
'''+assign_old+'''
assign old_count=(cur_valid&&old_valid)?raw:16'd0;
assign new_count=spanprod_cur_count;
assign old_nonzero=(old_count!=16'd0);
'''+re.search(r'assign spanprod_cur_nonzero =[^;]+;',new).group(0).replace('spanprod_cur_nonzero','new_nonzero')+'\nendmodule\n'
 (d/'tb.v').write_text(tb)
 (d/'main.cpp').write_text(r'''#include "Vtb_span.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
int main(){Vtb_span t;unsigned checks=0,counts=0,negative=0;
for(unsigned c=0;c<1024;c++)for(unsigned s=0;s<16;s++){
 t.ctl=c;t.spanprod_calc_step=s;t.eval();if(t.old_terminal!=t.new_terminal)return 1;checks++;
 // An intentionally omitted default terminal arm must fail.
 negative+=(s>=13)&&t.old_terminal;
}
for(unsigned r=0;r<65536;r++)for(unsigned f=0;f<4;f++){
 t.raw=r;t.cur_valid=f&1;t.old_valid=f>>1;t.eval();
 if(t.old_count!=t.new_count||t.old_nonzero!=t.new_nonzero)return 2;counts++;
}
// Full four-slot state-transition oracle: stale RAM, reset/clear, both clear-
// write priorities, zero overwrite and all read slots. All count values tested.
uint16_t ram[4]={0xaaaa,0xbbbb,0xcccc,0xdddd};unsigned ov=0,nv=0;uint64_t seq=0,zero=0,reuse=0;
for(unsigned value=0;value<65536;value++)for(unsigned slot=0;slot<4;slot++)for(unsigned mode=0;mode<4;mode++){
 // Establish a nonzero old slot before each overwrite (slot reuse).
 ram[slot]=1;ov|=1u<<slot;nv|=1u<<slot;reuse++;
 if(mode==1||mode==3){ov=0;nv=0;}
 ram[slot]=value;ov|=1u<<slot;nv=(nv&~(1u<<slot))|((value!=0)<<slot);zero+=value==0;
 if(mode==2||mode==3){ov=0;nv=0;}
 for(unsigned read=0;read<4;read++){
  uint16_t a=((ov>>read)&1)?ram[read]:0,b=((nv>>read)&1)?ram[read]:0;
  if(a!=b||bool(a)!=bool((nv>>read)&1))return 3;
  t.raw=ram[read];t.old_valid=(ov>>read)&1;t.cur_valid=1;t.eval();
  if(t.old_count!=b||t.new_count!=b||t.new_nonzero!=bool(b))return 4;seq++;
 }
}
t.final();printf("PASS terminal=%u counts=%u transitions=%llu zero=%llu reuse=%llu negative=%u\n",checks,counts,(unsigned long long)seq,(unsigned long long)zero,(unsigned long long)reuse,negative);
}
''')
 cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','tb_span','--Mdir',str(d/'obj'),'-CFLAGS','-O2',str(d/'tb.v'),str(d/'main.cpp')]
 write(d/'command.json',cmd)
 for name,command in [('build',cmd),('run',[str(d/'obj/Vtb_span')])]:
  with(d/(name+'.log')).open('x')as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
 line=(d/'run.log').read_text();assert line.startswith('PASS '),line
 counts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',line)}
 write(d/'result.json',dict(passed=True,**counts,candidate_sha256=sha(OUT/'gpu_core.v'),parent_sha256=sha(BASE),scope='Literal source Boolean/count read equations; exhaustive two-state controls/counts and reference four-slot clear/write state transitions. Native instrumentation separately checks actual write-site conditions and priority.'))
 print(line,end='')
if __name__=='__main__':
 for name in sys.argv[1:]:globals()[name]()
