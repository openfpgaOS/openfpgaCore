#!/usr/bin/env python3
"""Native-first private single-issue RAW/RVC/ISA pipeline; no map or fitter."""
import difflib,hashlib,json,os,re,shutil,subprocess,sys,time
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-cache64-rvcpipe-v1';B=P/'cpu-cache64-resource-v1';Q=P/'cpu-queues-v1';V=R/'src/fpga/vendor/vexriscv'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def rep(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def sources(src):
 out={str(p.relative_to(src)):sha(p) for p in src.rglob('*.scala') if 'target' not in p.relative_to(src).parts}
 for n in ['build.sbt','project/build.properties']:out[n]=sha(src/n)
 return out
def prepare():
 for n,h in json.loads((B/'manifest.json').read_text())['evidence_sha256'].items():assert sha(R/n)==h,n
 O.mkdir(exist_ok=False);src=O/'source';shutil.copytree(Q/'source',src,ignore=shutil.ignore_patterns('target','.git','VexiiRiscv*.v','vexii_active.v'));old=sources(src);assert old==json.loads((Q/'sources.json').read_text())
 param='src/main/scala/vexiiriscv/Param.scala';align='src/main/scala/vexiiriscv/decode/AlignerPlugin.scala';patch=''
 for n in [param,align]:
  f=src/n;a=f.read_text();s=a
  if n==param:
   s=rep(s,'  var decoderAt = 1\n','  var decoderAt = 1\n  var rvcDecompressAt = 0\n')
   s=rep(s,'    opt[Int]("decoder-at") action { (v, c) => decoderAt = v }\n','    opt[Int]("decoder-at") action { (v, c) => decoderAt = v }\n    opt[Int]("rvc-decompress-at") action { (v, c) => rvcDecompressAt = v }\n')
   s=rep(s,'    if(withLateAlu) assert(allowBypassFrom == 0)\n','    if(withLateAlu) assert(allowBypassFrom == 0)\n    require(rvcDecompressAt >= 0 && rvcDecompressAt <= decoderAt,\n      "RVC decompression must precede or share ISA decode stage")\n')
   s=rep(s,'      withBuffer = withAlignerBuffer\n','      withBuffer = withAlignerBuffer,\n      decompressAt = rvcDecompressAt\n')
  else:
   s=rep(s,'                    withBuffer : Boolean) extends FiberPlugin','                    withBuffer : Boolean, decompressAt : Int = 0) extends FiberPlugin')
   x='''        lane.up(Decode.INSTRUCTION) := extractor.ctx.instruction
        lane.up(Decode.DECOMPRESSION_FAULT) := False
        lane.up(Decode.INSTRUCTION_RAW) := extractor.ctx.instruction
        val isRvc = extractor.ctx.instruction(1 downto 0) =/= 3
        val withRvc = Riscv.RVC.get generate new Area {
          val dec = RvcDecompressor(extractor.ctx.instruction, rvf = Riscv.RVF, rvd = Riscv.RVD, Riscv.XLEN)
          when(isRvc) {
            lane.up(Decode.INSTRUCTION) := dec.inst
            lane.up(Decode.DECOMPRESSION_FAULT) := dec.illegal
          }
        }
'''
   y='''        val decLane = dpp.ctrl(decompressAt).lane(laneId)
        lane.up(Decode.INSTRUCTION_RAW) := extractor.ctx.instruction
        val decRaw = if(decompressAt == 0) extractor.ctx.instruction else decLane.up(Decode.INSTRUCTION_RAW)
        decLane.up(Decode.INSTRUCTION) := decRaw; decLane.up(Decode.DECOMPRESSION_FAULT) := False
        val isRvc = extractor.ctx.instruction(1 downto 0) =/= 3
        val withRvc = Riscv.RVC.get generate new Area {
          val dec = RvcDecompressor(decRaw, rvf = Riscv.RVF, rvd = Riscv.RVD, Riscv.XLEN)
          when(decRaw(1 downto 0) =/= 3) {
            decLane.up(Decode.INSTRUCTION) := dec.inst; decLane.up(Decode.DECOMPRESSION_FAULT) := dec.illegal
          }
        }
'''
   s=rep(s,x,y);assert len(a.splitlines())==len(s.splitlines())
  f.write_text(s);patch+=''.join(difflib.unified_diff(a.splitlines(True),s.splitlines(True),fromfile='a/'+n,tofile='b/'+n))
 (O/'generator.patch').write_text(patch);new=sources(src);assert sorted(n for n in new if new[n]!=old[n])==sorted([param,align])
 oldflags=re.search(r'^\[info\] running \(fork\) vexiiriscv.Generate (.*)$',(B/'regenerate.log').read_text(),re.M).group(1).split();flags=[('--dispatcher-at=3' if x=='--dispatcher-at=2' else x) for x in oldflags]+['--rvc-decompress-at=1','--decoder-at=2'];assert oldflags.count('--dispatcher-at=2')==1
 assert '--decoders=2' not in flags and '--lanes=2' not in flags
 shutil.copytree(B/'vendor',O/'helpers',ignore=shutil.ignore_patterns('VexiiRiscv'))
 subprocess.run(['cp','-a','--reflink=auto',str(R/'tools/.vexii-home'),str(O/'dependency-cache')],check=True)
 image=json.loads((Q/'generated.json').read_text())['docker_image']
 cmd=['docker','run','--rm','--name','phase10-cpu-cache64-rvcpipe-v1','--network','none','--user',f'{os.getuid()}:{os.getgid()}','--env','HOME=/vexiihome','--env','VEXIIRISCV_DEBUG_PARAM=0','--env','SPINALHDL_FROM_SOURCE=1','--env','JAVA_TOOL_OPTIONS=-Duser.home=/vexiihome -Dsbt.boot.directory=/vexiihome/.sbt/boot -Dsbt.global.base=/vexiihome/.sbt -Dsbt.ivy.home=/vexiihome/.ivy2','-v',f'{src}:{src}','-v',f'{O}/dependency-cache:/vexiihome','--tmpfs','/tmp:exec','-w',str(src),image,'sbt','-Dsbt.server.forcestart=true','--batch','Test/runMain vexiiriscv.Generate '+' '.join(flags)]
 prod=json.loads((B/'prepared.json').read_text())['production_guard']
 for n,h in prod.items():assert sha(R/n)==h,n
 put(O/'prepared.json',dict(scope=__doc__,base_manifest_sha256=sha(B/'manifest.json'),baseline_cpu_sha256=sha(B/'VexiiRiscv.v'),original_scala=old,candidate_scala=new,base_flags=oldflags,flags=flags,command=cmd,helper_sha256={str(p.relative_to(O/'helpers')):sha(p) for p in (O/'helpers').rglob('*') if p.is_file()},production_guard=prod,recipe_sha256=sha(Path(__file__)),map_fit_authorized=False,bit19_overlay=False,production_modified=False,prior_patch_sha256=sha(R/'tools/experiments/os30_dual_20261004/generator/rvc-pipeline.patch')))
 shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def guard():
 d=json.loads((O/'prepared.json').read_text());assert sha(Path(__file__))==d['recipe_sha256'];assert sources(O/'source')==d['candidate_scala']
 for n,h in d['production_guard'].items():assert sha(R/n)==h,n
 for n,h in d['helper_sha256'].items():assert sha(O/'helpers'/n)==h,n
 return d
def generate():
 d=guard();start=time.monotonic()
 with (O/'generate.log').open('x') as f:r=subprocess.run(d['command'],stdout=f,stderr=subprocess.STDOUT,timeout=900)
 raw=O/'source/VexiiRiscv.v';out=dict(returncode=r.returncode,seconds=time.monotonic()-start,raw_exists=raw.exists(),actual_scala=True)
 if raw.exists():shutil.copy2(raw,O/'raw-emitted.v');out['raw_sha256']=sha(raw)
 guard();put(O/'generation.json',out);print(json.dumps(out,indent=2),flush=True);assert r.returncode==0 and raw.exists()
if __name__=='__main__':globals()[sys.argv[1]]()
