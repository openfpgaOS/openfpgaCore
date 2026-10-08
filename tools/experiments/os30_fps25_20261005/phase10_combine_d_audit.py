#!/usr/bin/env python3
"""Read-only additive-D interpolation necessity audit; no source/RTL changes."""
from pathlib import Path
import collections,json,struct
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OUT=P/'combine-d-audit-v1';OUT.mkdir(exist_ok=False)
CAP=P/'load-alias-functional-v2/capture-baseline'
v=module('d_audit_vector',Path(__file__).with_name('phase6_validate.py'))
frames=[];examples={}
for f in sorted((CAP/'gpuvec').glob('*.vec')):
 vv=v.vector(f.read_bytes());counts=collections.Counter();combine=False;state=None;ordinal=0
 for raw in vv['hardware']:
  if raw[:4]!=b'CMDS':continue
  pos=8
  while pos<len(raw):
   h=struct.unpack_from('<I',raw,pos)[0];op=h>>24;n=h&8191;a=struct.unpack_from('<%dI'%n,raw,pos+4);packet=raw[pos:pos+4*(n+1)];pos+=4*(n+1);ordinal+=1
   if op==0x4a:combine=bool(a[6]&(1<<30));state=list(a)
   if op!=0x4e:continue
   counts['triangles']+=1;counts['payload_'+str(n)]+=1
   if n!=19:
    assert not combine;continue
   assert combine and state is not None
   cs=[a[12]&65535,a[12]>>16,a[13]&65535];ds=[a[17]&65535,a[17]>>16,a[18]&65535]
   counts['combine_triangles']+=1;counts['D_uniform'if len(set(ds))==1 else'D_variable']+=1
   rec=dict(frame=int(f.stem.split('_')[1]),command_ordinal=ordinal,C_rgb565=cs,D_rgb565=ds,control_word=state[6],packet_words=[h]+list(a),state_words=state)
   for k,s,mask in [('r',11,31),('g',5,63),('b',0,31)]:
    var=len({(d>>s)&mask for d in ds})>1;counts['D_'+k+('_variable'if var else'_uniform')]+=1
    if var and k not in examples:examples[k]=rec
   if any(cs[i]==cs[j]and ds[i]!=ds[j]for i in range(3)for j in range(i+1,3))and'same_C_different_D'not in examples:examples['same_C_different_D']=rec
 frames.append(dict(frame=int(f.stem.split('_')[1]),counts=dict(counts),vector_sha256=sha(f)))
assert len(frames)==8 and examples.keys()>={'r','g','b','same_C_different_D'}
q=ROOT.parent/'Quake2/src';sm=ROOT.parent/'SM64/src/sm64/sm64'
files=[P/'load-cram-driver-v1/gfx_gpu.c',P/'load-cram-driver-v1/gfx_pc.c',P/'load-cram-driver-v1/of_gpu.h',P/'span-lookahead-v3/gpu_core.v',sm/'include/PR/gbi.h',sm/'src/goddard/renderer.c',q/'quake2/openfpga/of_gl1.c',q/'quake2/openfpga/of_emit_q2.c',q/'quake2/openfpga/of_emit_q2.h',q/'quake2/src/client/refresh/soft/sw_scan.c',q/'quake2/src/client/refresh/soft/sw_polyset.c']
write(OUT/'result.json',dict(read_only=True,decision='Do not replace sharedos30 additiveD planes with uniformD; observedrequiredvaryingRGB.',frames=frames,counterexamples=examples,coverage='NativeFMA721frame capture has aggregate scene/count data and8 detailedcommandstreams; these8 streams are decoded. No claim to inspectall721 pertrianglepayloads.',quake2='Current four tri-state construction paths zeroinitialize cd_combine and never setit; current RGB emitter alwayspasses rgb_d=NULL. Sourceproof, not Quake2 rendercapture.',sources={str(p):sha(p)for p in files},capture_records_sha256={n:sha(CAP/n)for n in('scene.csv','geometry.csv','culling.csv','frames.csv')}))
(OUT/'REPORT.txt').write_text('''Constant additive-D cannot replace the existing os30 interpolation exactly.

Native retained SM64 frame120 emits684 combine triangles;423 have different
D colors within a triangle. Red varies in296, green335, blue299. Allthree
D planes are required. Example packed D={0x9b69,0xdcac,0x9b49}, corresponding
C={0xe7ff,0xaf7f,0xe7ff}; corners0/2 have identical encodedC but differentD.
Consequently D cannot generally be recovered from encodedC plus a uniform
material value: C saturation/quantization discarded relevant information.

The source contract independently explains this. gbi.h G_CC_HILITERGBA is
(PRIMITIVE-SHADE)*TEXEL0+SHADE. Goddard's face-shine displaylist selectsit.
gfx_pc maps CC_SHADE to each vertex color. gfx_gpu classifies this equation,
resolves eachcorner's D input separately and encodes independentRGB565 values.
Thus D is lit pervertex color, not a uniform primitive/environment constant.

All eight detailedstreams were inspected. Sampled gameplay360/400/500/600/720
and menu40/200 contain no combine triangles; this is not a global gameplay
absence proof. The721frame aggregate records do not contain every triangle's
D payload. One actuallyused nonuniform triangle is sufficient to disprove the
proposed alwaysuniform assumption; the recommendation does not infer safety
from title-only or gameplay-only coverage.

Current Quake2 source does not use C+D: allfour OFQ2_SetTriState construction
paths memset the descriptor and leave cd_combine zero; the sole Quake2 RGB
triangle emitter passes rgb_d=NULL. It can use a Quake2-only no-combine build,
but this does not justify the same cut in shared SM64 os30. No Quake2 pixel
capture was performed in this read-only audit.

The retained GPU already detects equal pervertex attributes at DRV_DA_PREP
and gives them exactzero gradients, bypassing numerator/scale products. A
runtime constant-D fastpath would still have to retain varyingD hardware;
that existing optimization is not an area-elimination opportunity.

No RTL, application, assets, production files, or build settings changed.
''')
write(OUT/'manifest.json',dict(files={p.name:sha(p)for p in OUT.iterdir()if p.is_file()},recipe_sha256=sha(Path(__file__))))
print(OUT)
