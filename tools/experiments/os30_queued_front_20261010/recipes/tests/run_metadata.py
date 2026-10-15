#!/usr/bin/env python3
import hashlib,json,os,subprocess,sys
from pathlib import Path
T=Path(__file__).resolve().parent;C=T.parent/'model/frozen/src/fpga/common'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out=T/sys.argv[1];assert out.parent==T and not out.exists();out.mkdir()
paths=[Path(__file__),T/'tb_metadata.v',T/'test_metadata.cpp',C/'geometry_raw_load.v',C/'geometry_transform_light.v']
before={str(p):sha(p)for p in paths};report={'status':'RUNNING','inputs_sha256':before,'scope':'Actual raw LOAD SCAN/metadata with test-only synchronous transform output RAM and external DMA completion','commands':[]}
header=(C/'geometry_transform_light.v').read_text().split('\n);',1)[0]+'\n);\n'
body='''
 reg [31:0] words[0:255],q;reg test_unsupported;reg [1:0] latency;
 assign output_data=q,output_position_data=q,output_rgb=0;
 assign transform_cycles=0,lighting_cycles=0;
 assign fma_request_valid=0,fma_request_a=0,fma_request_b=0,fma_request_c=0,fma_request_multiply=0;
 assign scratch_read_data=0;
 assign attribute_read_valid=0,attribute_read_addr=0,attribute_rgb_write=0,attribute_rgb_addr=0,attribute_rgb_channel=0,attribute_rgb_data=0;
 always @(posedge clk)begin
  q<=words[output_addr];done<=0;
  if(reset)begin busy<=0;unsupported<=0;latency<=0;cycles<=0;end
  else if(start)begin busy<=1;unsupported<=0;latency<=2;end
  else if(busy)begin
   if(latency==0)begin busy<=0;done<=1;unsupported<=test_unsupported;end
   else latency<=latency-1'b1;
  end
 end
endmodule
'''
stub=out/'geometry_transform_light_stub.v';stub.write_text(header+body)
def run(argv,name):
 with (out/(name+'.log')).open('x')as f:r=subprocess.run(list(map(str,argv)),stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1'))
 report['commands'].append({'argv':list(map(str,argv)),'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))})
 (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 assert r.returncode==0,(name,(out/(name+'.log')).read_text()[-6000:])
run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','tb_metadata','--Mdir',out/'obj','-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',T/'tb_metadata.v',C/'geometry_raw_load.v',stub,T/'test_metadata.cpp'],'build')
run([out/'obj/Vtb_metadata'],'run')
assert before=={str(p):sha(p)for p in paths},'Inputs changed during tests'
report.update(status='PASS_ACTUAL_QGF_LOAD_ADMISSION_METADATA',result=json.loads((out/'run.log').read_text()),test_transform_sha256=sha(stub),executable_sha256=sha(out/'obj/Vtb_metadata'))
(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['result']))
