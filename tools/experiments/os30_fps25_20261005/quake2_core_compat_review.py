#!/usr/bin/env python3
"""Read-only Quake2 compatibility audit of existing and qualified core inputs."""
import hashlib,json,re
from pathlib import Path
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-20261008'
Q=P/'work/baseline'
O=P/'core-compat-v1'
OLD=R/'docs/measurements/os30-dual-20261004/fit-rvc-skid-pointer-packer-coord-v1/jobs/pocket-os30-s2'
NEW=R/'docs/measurements/os30-fps25-20261005/phase10/endpoint-control-fit-v1/fit-seed2/job'
E={}
def text(p):
 E[str(p.relative_to(R))]=hashlib.sha256(p.read_bytes()).hexdigest()
 return p.read_text(errors='replace')
def digest(p):
 text(p);return E[str(p.relative_to(R))]
def uncomment(s):return re.sub(r'/\*.*?\*/|//[^\n]*','',s,flags=re.S)
def preprocess(s,defs):
 out=[];stack=[];active=True;at=0
 for m in re.finditer(r'`(ifdef|ifndef|elsif)\s+(\w+)|`(else|endif)\b',s):
  if active:out.append(s[at:m.start()])
  op,name=m.group(1),m.group(2)
  if op in ('ifdef','ifndef'):
   match=(name in defs)==(op=='ifdef');stack.append([active,match]);active=active and match
  elif op=='elsif':
   parent,used=stack[-1];match=not used and name in defs;stack[-1][1]|=match;active=parent and match
  elif m.group(3)=='else':
   parent,used=stack[-1];active=parent and not used;stack[-1][1]=True
  else:active=stack.pop()[0]
  at=m.end()
 if active:out.append(s[at:])
 assert not stack
 return ''.join(out)
def audit(job):
 qsf=text(job/'src/fpga/targets/pocket/ap_core.qsf')
 defs=set(re.findall(r'^set_global_assignment -name VERILOG_MACRO (\S+)$',qsf,re.M))
 top=text(job/'src/fpga/targets/pocket/core_top.v')
 inst=uncomment(preprocess(top.split('axi_periph_slave #(',1)[1].split(') periph',1)[0],defs))
 params={k:int(v.replace('_','').split("'d")[-1]) for k,v in re.findall(r'\.(\w+)\(\s*([0-9_\x27d]+)\s*\)',inst)}
 rtl=uncomment(preprocess(text(job/'src/fpga/common/axi_periph_slave.v'),defs))
 expr=rtl.split('HW_FEATURES_RESOLVED =',1)[1].split(';',1)[0]
 expr=re.sub(r"32'h([0-9a-fA-F_]+)",lambda m:'0x'+m.group(1).replace('_',''),expr)
 params['FEAT_LINK']=0
 value=0
 for term in expr.split('|'):
  term=term.strip()
  if '?' in term:
   cond,tail=term[1:-1].split('?');yes,no=tail.split(':')
   cond=cond.replace('&&',' and ')
   assert re.fullmatch(r'[A-Z_0-9()!=> <a-z]+',cond),cond
   value|=int(yes.strip() if eval(cond,{'__builtins__':{}},params) else no.strip(),16)
  else:value|=params[term] if term in params else int(term,16)
 gpu=text(job/'src/fpga/common/gpu_core.v')
 commands=dict(re.findall(r'localparam (CMD_\w+)\s*=\s*8\x27h([0-9a-fA-F]+)',gpu))
 assert 'INCLUDE_GPU_SM64_GEOMETRY' not in defs and 'INCLUDE_GPU_GEOMETRY_WORKER' not in defs
 assert 'INCLUDE_CPU_RING' in defs and 'EXCLUDE_GPU_COMMAND_DMA' in defs
 assert re.search(r'HW_CONTRACT_REV\s*=\s*32\x27d2;',rtl)
 sets=int(re.search(r'gpu_color_depth_cache #\(.*?\.SET_BITS\((\d+)\)',top,re.S).group(1))
 return dict(macros=sorted(defs),peripheral_parameters=params,hw_features=f'0x{value:08x}',
   gpu_status_geometry_bit10=0,gpu_status_worker_bit11=0,cpu_ring_supported=True,
   gpu_cache_kib=(2*(1<<sets)*64)//1024,commands=commands,contract_revision=2)
a,b=audit(OLD),audit(NEW)
assert a['hw_features']==b['hw_features']=='0x2b136753'
assert a['peripheral_parameters']==b['peripheral_parameters']
assert a['commands']==b['commands']
oldhash=digest(Q/'runtime/pocket/os30.rbf_r');assert oldhash=='b4d55466283320c798047effa1dd9895d990fd6f297857e52ad99ec9bbf3778d'
newrbf=R/'docs/measurements/os30-fps25-20261005/phase10/final-candidate-delivery-v3/payload/Cores/ThinkElastic.SM64/os30.rbf_r'
newhash=digest(newrbf);assert newhash=='b1083c26b99c063684b411af1299e3b820c69f025ce6ee0003f6d7d4ab93a8e4'
oshash=digest(Q/'runtime/pocket/os.bin');assert oshash=='054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
sdk=text(Q/'src/sdk/include/of_gpu.h');emit=text(Q/'src/quake2/openfpga/of_emit_q2.c')
assert 'of_gpu_use_cpu_ring();' in sdk and 'GPU_STATUS_CPU_RING_CAP' in sdk
assert 'GPU_STATUS & (1u << 10)' in emit and 'OF_HW_GPU_CLIP_LOAD' in emit
assert 'if (n > 3) n = 3;' in emit
for n in ['src/sdk/include/of_caps.h','src/sdk/include/of_cache.h','src/sdk/include/of_texture.h','src/quake2/openfpga/of_gl1.c','src/quake2/openfpga/of_emit_q2.h']:
 text(Q/n)
platform=text(R/'src/firmware/os/targets/pocket/target_platform.h')
assert '#define OF_TARGET_RUNTIME_STACK_TOP    0x13F80000u' in platform
assert '0x13F80000-0x13FC0000  speculation headroom / guard' in platform
assert '#define OF_TARGET_TEX_FAST_SIZE        (16u * 1024u * 1024u)' in platform
ld=text(Q/'src/sdk/app.ld');assert 'ORIGIN = 0x10400000, LENGTH = 0x03600000' in ld
text(R/'src/firmware/os/kernel/syscall.c');text(R/'src/firmware/os/kernel/caps_table.c')
alias=text(NEW/'src/fpga/common/phase10_bram_alias.v')
assert "BASE=32'h13f80000" in alias and "s_araddr&32'hbffffc00" in alias
load=text(NEW/'src/fpga/common/phase10_load_alias.v')
assert "32'h4c4f4405" in load
phys=R/'docs/measurements/os30-fps25-20261005/phase10/endpoint-control-fit-v1/result.json'
qualification=json.loads(text(phys));assert qualification['comparative_physical_gate_pass']
result=dict(passed_source_compatibility=True,read_only_by_reviewer=True,no_cad_or_new_simulation=True,
 old_core_sha256=oldhash,new_core_sha256=newhash,unchanged_os_sha256=oshash,
 old=a,new=b,macro_additions=sorted(set(b['macros'])-set(a['macros'])),macro_removals=sorted(set(a['macros'])-set(b['macros'])),
 memory=dict(lod5_alias_cached='0x13f80000..0x13f803ff',lod5_alias_uncached='0x53f80000..0x53f803ff',
  alias_inside_existing_os_guard=True,app_link_end='0x13a00000',app_heap_below_os_reservations=True,
  runtime_stack_grows_down_from='0x13f80000',texture_cram1_bytes=16*1024*1024,
  lod5_mmio='0x4b000000',lod5_signature='0x4c4f4405',q2_lod5_client_present=False),
 direct_benefits=['GPU render cache grows8KiB to32KiB without changing GPU packet/MMIO ABI.',
  'Single-issue I64KiB/D64KiB CPU replaces the old limited-dual CPU; real Quake2 effect needs its own measurement.',
  'Same-triangle span lookahead and equivalent timing/control factors are active transparently.',
  'Fast16MiB CRAM textures, CPU ring, clip-fed vertex cache, RGB565, depth, combiner and hardware mixer remain available.'],
 required_firmware_change_for_existing_q2_paths=False,
 geometry_scope='Both installed and selected cores have GEO0 in GPU_STATUS bit10. Existing Q2 raw0x59/indexed0x5b paths stay gated off; CPU/clip-fed and screen-triangle paths remain selected. Separate LOD5 is not automatically used.',
 lod5_future_requirements=['Explicit signature-gated MMIO client and compatible input/output layout.',
  'Reserve accelerator source allocations in the SAME16MiB texture arena; no fixed SM64 source address may overlap live Q2 textures.',
  'Prove Quake2 floating-point transforms/lighting/clipping and fallback behavior; SM64 vertex format and FMA result are not a drop-in Q2 replacement.',
  'Fence source uploads, accelerator jobs, BRAM alias cache visibility and texture-domain transitions.'],
 available_compatibility_tests=['Current captured Q2 CPU/GEO0 command streams on the selected5e9 GPU/ab1 cache; old geometry-enabled3D fixture is not the actual selected core.',
  'Existing renderer/texture/SDK CPU-ring host regression suites in frozen snapshot; peer owns these runs.',
  'Focused Pocket boot, gameplay, texture reload/vid_restart, audio and save/exit trial after guarded core/app deployment.'],
 physical=qualification,limitations=['No Quake2 frame replay or performance measurement was run by this audit.',
  'The source contract passes; SM64 timing/native evidence does not establish Quake2 FPS.',
  'Configured100MHz still fails setup despite90.33/91.75MHz reported slow-corner limits.',
  'Sibling checkout is already dirty; preserve captured preimages and update only explicitly selected files.'])
O.mkdir(exist_ok=False)
(O/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
text(O/'result.json');text(Path(__file__))
(O/'manifest.json').write_text(json.dumps(dict(passed_source_compatibility=True,evidence_sha256=E),indent=2,sort_keys=True)+'\n')
print('PASS',hashlib.sha256((O/'manifest.json').read_bytes()).hexdigest(),a['hw_features'],b['hw_features'])
