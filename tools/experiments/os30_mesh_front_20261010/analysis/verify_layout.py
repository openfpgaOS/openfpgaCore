#!/usr/bin/env python3
"""Compile actual extracted renderer record definitions for the RV32 ABI."""
from pathlib import Path
import hashlib,json,re,subprocess
H=Path(__file__).resolve().parent;A=H.parents[1]/'queued-front-v1/work/sm64-v2'
src=A/'src/sm64/sm64/src/pc/gfx/gfx_pc.c';gbi=A/'src/sm64/sm64/include/PR/gbi.h'
s=src.read_text();g=gbi.read_text()
def record(start,end):
    i=s.index(start);return s[i:s.index(end,i)+len(end)]
end=g.index('} Light_t;')+len('} Light_t;');begin=g.rfind('typedef struct {',0,end)
body='typedef unsigned char uint8_t; typedef unsigned short uint16_t; typedef short int16_t; typedef unsigned uint32_t; typedef _Bool bool; typedef float rspv_t;\n#define MAX_LIGHTS 2\n#define MAX_VERTICES 64\n'
body+=g[begin:end]+'\n'
for a,b in [('struct RGBA {','};'),('struct XYWidthHeight {','};'),('struct LoadedVertex {','};'),('static struct RSP {','} rsp;'),('static struct RDP {','} rdp;')]:body+=record(a,b)+'\n'
fields={'RSP_size':'sizeof(struct RSP)','RDP_size':'sizeof(struct RDP)','RSP_geometry_mode':'__builtin_offsetof(struct RSP,geometry_mode)','RSP_loaded_vertices':'__builtin_offsetof(struct RSP,loaded_vertices)','RSP_MP_matrix':'__builtin_offsetof(struct RSP,MP_matrix)','RSP_fog_mul':'__builtin_offsetof(struct RSP,fog_mul)','RDP_other_mode_l':'__builtin_offsetof(struct RDP,other_mode_l)','RDP_combine_mode':'__builtin_offsetof(struct RDP,combine_mode)'}
body+='_Static_assert(sizeof(void*)==4,"RV32 pointer ABI");\n'
body+='\n'.join('const unsigned layout_'+k+'='+v+';' for k,v in fields.items())+'\n'
c=H/'layout.c';asm=H/'layout.s';c.write_text(body)
cmd=['clang','--target=riscv32-unknown-elf','-march=rv32imafdc','-mabi=ilp32d','-S','-O2',str(c),'-o',str(asm)]
r=subprocess.run(cmd,capture_output=True,text=True);(H/'layout-build.log').write_text(r.stdout+r.stderr);r.check_returncode()
a=asm.read_text();values={k:int(re.search(r'layout_'+k+r':\s*\n\s*\.word\s+(\d+)',a).group(1)) for k in fields}
assert values['RSP_size']==0xd3c and values['RDP_size']==0x68
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out={'passed':True,'command':cmd,'fields':values,'inputs_sha256':{str(p):sha(p) for p in [Path(__file__).resolve(),src,gbi,c,asm]},'scope':'Actual extracted unchanged structs compiled for RV32; sizes match exact ELF symbols. No renderer function executed.'}
(H/'layout.json').write_text(json.dumps(out,indent=2)+'\n');print(values)
