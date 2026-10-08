#!/usr/bin/env python3
"""Upgrade historical memo-hit RGB expectations with independent actual-A fresh-lighting oracle."""
import hashlib,json,os,re,shutil,struct,subprocess,sys,time
from pathlib import Path
import phase10_lod5_live_native as N
ROOT=N.ROOT;P=N.P;BASE=P/'lod5-live-native-v2';OUT=BASE/'full-rgb';sha=N.sha

def prepare():
 OUT.mkdir(exist_ok=False);f=OUT/'firmware';shutil.copytree(BASE/'firmware',f)
 b=(P/'unified-load-components-v2/frozen/native.bin').read_bytes();w=list(struct.unpack('<%dI'%(len(b)//4),b));pos=1
 rows=[[int(x,16)for x in l.split()]for l in(P/'cpu-audit/lighting-native/native-golden.txt').read_text().splitlines()]
 oracle={tuple(r[1:7]):r[7]for r in rows};s=(f/'vectors.h').read_text();updates=[]
 for job in range(w[0]):
  h=w[pos:pos+27];pos+=27;rgb=[]
  for v in range(h[0]):
   d=w[pos:pos+15];pos+=15
   if h[24]:
    key=((d[4]>>16)|((d[5]&255)<<16),*h[19:22],h[22],h[23]);want=oracle[key]|(d[12]&0xff000000)
    if d[14]:assert want==d[12]
    else:updates.append(dict(job=job,vertex=v,key=list(key),fresh_native_rgba=want,historical_memo_rgba=d[12]))
    rgb.append(want)
   else:rgb.append(d[12])
  for kind in ['expected','mask']:
   pat=r'(static const uint32_t lod5_vector_%02d_%s\[\][^=]*= \{)(.*?)(\n\};)'%(job,kind)
   m=re.search(pat,s,re.S);assert m
   values=[int(x,16)for x in re.findall(r'0x([0-9a-f]+)u',m[2])];assert len(values)==h[0]*9
   for v in range(h[0]):values[v*9+7]=rgb[v]if kind=='expected' else 0xffffffff
   body='\n'+''.join('  '+','.join('0x%08xu'%x for x in values[i:i+9])+',\n'for i in range(0,len(values),9))
   s=s[:m.start(2)]+body.rstrip()+s[m.end(2):]
 (f/'vectors.h').write_text(s)
 cmd=[x.replace(str(BASE/'firmware'),str(f))for x in json.loads((BASE/'firmware-command.json').read_text())]
 N.save(OUT/'firmware-command.json',cmd)
 N.save(OUT/'oracle.json',dict(upgraded_rgbs=len(updates),updates=updates,expected_historical_qualified_rgb_unchanged=True,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in [P/'cpu-audit/lighting-native/native-golden.txt',P/'cpu-audit/lighting-native/final-manifest.json',P/'unified-load-components-v2/frozen/native.bin',BASE/'firmware/vectors.h',f/'vectors.h',Path(__file__)]}))
def build():
 with(OUT/'build.log').open('x')as f:r=subprocess.run(json.loads((OUT/'firmware-command.json').read_text()),stdout=f,stderr=subprocess.STDOUT)
 assert r.returncode==0
 N.OUT=OUT;N.firmware_image()
def run(mode,traffic):
 exe=BASE/mode/'obj/Vtb_system';env=dict(SCAN_ENABLE='0'if traffic=='quiet'else'1',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_PERIOD='6361',SCAN_LEN='160',SCAN_ACTIVE='240',SCAN_TOTAL='262')
 label=mode+'-'+traffic;cmd=[str(exe),'kernel.bin','15000000'];t=time.monotonic()
 with(OUT/(label+'.log')).open('x')as f:r=subprocess.run(cmd,cwd=OUT/'firmware',env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT,timeout=120)
 log=OUT/(label+'.log');s=log.read_text();ok=r.returncode==0 and 'LIVE_LOD5_COMPLETE'in s and 'KERNEL FAIL'not in s and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0'in s
 N.save(OUT/(label+'.json'),dict(passed=ok,command=cmd,environment=env,returncode=r.returncode,elapsed_seconds=time.monotonic()-t,simulator_sha256=sha(exe),log_sha256=sha(log),firmware_sha256=sha(OUT/'firmware/kernel.bin')));print(label,ok);assert ok
if __name__=='__main__':globals()[sys.argv[1]](*sys.argv[2:])
