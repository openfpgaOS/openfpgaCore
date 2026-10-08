#!/usr/bin/env python3
"""Independent all-command source audit of the SDRAM address decode hoist."""
from pathlib import Path
import hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'sdram-address-mux-v1';OUT=P/'sdram-address-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 old=(BASE/'baseline.v').read_text();new=(BASE/'io_sdram.v').read_text()
 pattern=r'^\s*phy_a(?:\[10\])? <= [^;]+;[^\n]*\n'
 assert len(re.findall(pattern,old,re.M))==22
 stripped=re.sub(pattern,'\n',old,flags=re.M)
 prefix,decode=new.split('// Decode address sources independently from command-valid and delay counters.',1)
 assert prefix.rstrip()==stripped.rsplit('endmodule',1)[0].rstrip()
 assert decode.rstrip().endswith('end\n\nendmodule')
 assert len(re.findall(r'\bphy_a\b',stripped))==1 # output declaration only, no internal consumer
 states={}
 matches=list(re.finditer(r'^    (ST_\w+): begin',old,re.M))
 for i,m in enumerate(matches):
  body=old[m.start():matches[i+1].start() if i+1<len(matches) else old.index('// catch incoming events')]
  cmds=re.findall(r'cmd <= (CMD_\w+);',body)
  if cmds:states[m[1]]=dict(commands=cmds,addresses=re.findall(r'phy_a(?:\[10\])? <= [^;]+;',body))
 expected={
 'ST_BOOT_0':('CMD_PRECHG','A10=1'), 'ST_BOOT_3':('CMD_LMR','13b000000_011_0_001'),
 'ST_BOOT_4':('CMD_LMR','13b00000_010_00_000'), 'ST_IDLE':('CMD_PRECHG','A10=1'),
 'ST_REQ_READ':('CMD_PRECHG','A10=0'), 'ST_REQ_WRITE':('CMD_PRECHG','A10=0'),
 'ST_REQ_BURST_READ':('CMD_PRECHG','A10=0'), 'ST_REQ_BURST_WRITE':('CMD_PRECHG','A10=0'),
 'ST_WRITE_0':('CMD_ACT','addr[22:10]'), 'ST_WRITE_2':('CMD_WRITE','zero-extended addr[9:0]'),
 'ST_WRITE_4_NEWROW':('CMD_PRECHG','A10=BANK_ROW_TRACK && nr_prechg_bank != addr[24:23]'),
 'ST_WRITE_4_NR_PRECHG':('CMD_ACT','addr[22:10]'),
 'ST_READ_0':('CMD_ACT','addr[22:10]'), 'ST_READ_2':('CMD_READ','zero-extended addr[9:0]'),
 'ST_READ_6':('CMD_PRECHG','A10=BANK_ROW_TRACK && phy_ba != addr[24:23]'),
 'ST_BURSTWR_0':('CMD_ACT','addr[22:10]'), 'ST_BURSTWR_3':('CMD_WRITE','zero-extended addr[9:0]'),
 'ST_BURSTWR_5':('CMD_PRECHG','A10=0')}
 consumed={s:v for s,v in states.items() if any(c not in ('CMD_NOP','CMD_AUTOREF') for c in v['commands'])}
 assert set(consumed)==set(expected),(set(consumed),set(expected))
 for state,(cmd,expression)in expected.items():
  assert consumed[state]['commands']==[cmd],state
  consumed[state]['reviewed_candidate_expression']=expression
 # Hash-bind the two completed independent physical schedules in both modes.
 evidence=[BASE/'baseline.v',BASE/'io_sdram.v',BASE/'prepared.json',
           BASE/'result.json',BASE/'manifest.json',BASE/'bank-test/result.json',BASE/'bank-test/manifest.json',
           ROOT/'tools/experiments/os30_fps25_20261005/phase10_sdram_address_mux.py']
 for directory in (BASE,BASE/'bank-test'):
  result=json.loads((directory/'result.json').read_text());assert result['passed']
  for name,digest in json.loads((directory/'manifest.json').read_text()).items():
   assert sha(directory/name)==digest,name
  assert set(result['cases'])=={'0','1'}
 assert all(c['banks']==15 for c in json.loads((BASE/'bank-test/result.json').read_text())['cases'].values())
 OUT.mkdir(exist_ok=False);shutil.copyfile(__file__,OUT/'recipe.py')
 data=dict(passed_source_review=True,all_18_address_bearing_state_branches_reviewed=True,
           no_internal_address_consumers=True,other_rtl_byte_identical_after_22_address_assignment_removals=True,
           command_states=consumed,deployment_ready=False,
           reset_boot='Registered commands, CKE, bank, DQM and state/reset updates unchanged; address bits are ignored while CKE is low or command is NOP/AUTOREF.',
           limits=['PRECHARGE equivalence covers A10 and unchanged bank selection; other address bits intentionally differ.',
                   'This is command/data/cycle equivalence, not routing/timing qualification. Legacy burst-write branches are source-reviewed even if not issued by the mixed-master fixture.'],
           evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in evidence})
 (OUT/'review.json').write_text(json.dumps(data,indent=2)+'\n')
 (OUT/'manifest.json').write_text(json.dumps({p.name:sha(p)for p in OUT.iterdir()if p.is_file()},indent=2)+'\n')
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
