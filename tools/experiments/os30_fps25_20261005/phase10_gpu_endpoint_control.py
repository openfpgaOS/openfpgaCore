#!/usr/bin/env python3
"""Compose disjoint blend-half and perspective-step endpoint corrections."""
from pathlib import Path
import difflib,hashlib,itertools,json,shutil,subprocess
import phase10_gpu_span_step_enable as step
import phase10_gpu_blend_half_select_v2 as blend
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'gpu-parallel-control-v1/gpu_core.v';O=P/'gpu-endpoint-control-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def main():
 assert sha(B)==step.BASE_SHA==blend.PARENT
 proofs={step.O/'manifest.json':'6d77ccefbe528425adcafe4ae1ea468c85cfc8f2f3e463d41710f94d5ef5e025',blend.O/'manifest.json':'6c96a45d31690d59c74f1355c0599c7af8fa3f407636eabbb745a01ed4a3e791'}
 for p,h in proofs.items():
  assert sha(p)==h
  d=json.loads(p.read_text());assert d.get('passed',True)
  for n,v in d.get('evidence_sha256',{}).items():assert sha(R/n)==v,n
  for n,v in d.get('files',{}).items():assert sha(p.parent/n)==v,n
 head='wire persp_issue_stall = persp_active && !persp_seg_a_ready;\n'
 anchor="                        sp_seg_left <= sp_seg_left - 4'd1;\n"
 hunks=[dict(old=blend.OLD,new=blend.NEW),dict(old=head,new=head+'\n'+step.EARLY),dict(old=step.OLD_WRITES+anchor,new=anchor),dict(old=step.SWAP,new=step.NEW_WRITES+step.SWAP)]
 def apply(s,hs):
  for h in hs:
   assert s.count(h['old'])==1;s=s.replace(h['old'],h['new'],1)
  return s
 base=B.read_text();candidate=apply(base,hunks)
 assert apply(base,hunks[:1])==(blend.O/'gpu_core.v').read_text()
 assert apply(base,hunks[1:])==(step.O/'gpu_core.v').read_text()
 for order in itertools.permutations(hunks):
  assert apply(base,order)==candidate
  rev=candidate
  for h in reversed(order):
   assert rev.count(h['new'])==1;rev=rev.replace(h['new'],h['old'],1)
  assert rev==base
 O.mkdir(exist_ok=False);(O/'gpu_core.v').write_text(candidate);put(O/'hunks.json',hunks)
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(base.splitlines(True),candidate.splitlines(True),fromfile='aae8/gpu_core.v',tofile='endpoints/gpu_core.v')))
 shutil.copyfile(__file__,O/'recipe.py')
 # Same selected complete GPU lint configuration, preserving passive probes.
 prev=P/'gpu-parallel-control-v1/lint';lint=O/'lint';lint.mkdir()
 (lint/'gpu_core.v').write_text(apply((prev/'gpu_core.v').read_text(),hunks))
 cmd=[x.replace(str(prev),str(lint)) for x in json.loads((prev/'command.json').read_text())]
 put(lint/'command.json',cmd)
 with (lint/'run.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 put(O/'result.json',dict(passed=True,parent_sha256=sha(B),candidate_sha256=sha(O/'gpu_core.v'),hunks=4,composition_orders=24,new_state=0,new_cycles=0,full_gpu_replay_pending=True,native_and_physical_pending=True))
 evidence=[Path(__file__),Path(step.__file__),Path(blend.__file__),B,*proofs]+[p for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts]
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
 print('SOURCE',sha(O/'gpu_core.v'));print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':main()
