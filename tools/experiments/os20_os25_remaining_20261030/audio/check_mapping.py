#!/usr/bin/env python3
"""Inspect real Quartus map reports for the two storage-only candidates."""
import argparse,hashlib,json,re
from pathlib import Path
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def parse(path,name):
 text=path.read_text(errors="replace")
 marker="Parameter Settings for Inferred Entity Instance: core_top:ic|"+name+" ;"
 start=text.index(marker)
 end=text.index("Note:",start)
 excerpt=text[start:end]
 params={}
 for line in excerpt.splitlines():
  match=re.match(r"^;\s*([A-Z0-9_]+)\s*;\s*([^;]*?)\s*;",line)
  if match:params[match[1]]=match[2]
 return params,excerpt
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument("map_report",type=Path);p.add_argument("--output",type=Path,required=True)
 a=p.parse_args();result={"map_report":str(a.map_report.resolve()),"map_report_sha256":digest(a.map_report),"memories":{}}
 for key,instance,width,depth in [
   ("audio_pos","audio_mixer:audio_mixer_inst|altsyncram:pos_latch_rtl_0",22,32),
   ("sdram_wbuf","axi_sdram_slave:sdram_axi_slave|altsyncram:wbuf_rtl_0",36,16)]:
  params,excerpt=parse(a.map_report,instance)
  expected={"RAM_BLOCK_TYPE":"M10K","OPERATION_MODE":"DUAL_PORT","WIDTH_A":str(width),"WIDTH_B":str(width),"NUMWORDS_A":str(depth),"NUMWORDS_B":str(depth),"ADDRESS_REG_B":"CLOCK0","OUTDATA_REG_B":"UNREGISTERED","READ_DURING_WRITE_MODE_MIXED_PORTS":"OLD_DATA","ADDRESS_ACLR_B":"NONE","OUTDATA_ACLR_B":"NONE","INIT_FILE":"UNUSED"}
  for name,value in expected.items():
   if params.get(name)!=value:raise ValueError(f"{key} {name}: {params.get(name)} != {value}")
  result["memories"][key]={"parameters":params,"verified":expected,"excerpt":excerpt}
 result["passed"]=True
 result["scope"]="Mapped parameter proof: one-clock synchronous read, original depth/width, OLD_DATA. Fitter/timing/gameplay not established."
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+"\n")
 print(json.dumps({"passed":True,"receipt_sha256":digest(a.output)},indent=2))
if __name__=="__main__":main()
