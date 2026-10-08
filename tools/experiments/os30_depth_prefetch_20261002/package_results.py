"""Package a reviewed seed-2 prefetch candidate with explicit timing status."""
import argparse,difflib,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-depth-prefetch-20261002'
sys.path.insert(0,str(ROOT/'tools'))
from sweep_target_matrix import inventory
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--candidate',default='shared');a=p.parse_args()
fit=OUT/('fit-'+a.candidate)
r=json.loads((fit/'results.json').read_text())[0]
assert r['valid'] and r['state']=='complete' and r['seed']==2
actual=next(v for v in inventory() if v['variant']=='os30')
fitted=json.loads((fit/'inventory.json').read_text())[0]
selected='INCLUDE_Z_PREFETCH' in actual['defs'].split()
assert set(actual['defs'].split()) | {'INCLUDE_Z_PREFETCH'} == set(fitted['defs'].split())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def tokens(path,defs):
    s=subprocess.check_output(['verilator','-E','-P','-I'+str(path.parent),*['+define+'+x for x in defs],str(path)],text=True)
    s=re.sub(r'/\*.*?\*/|//[^\n]*','',s,flags=re.S)
    return re.findall(r'[A-Za-z_$][\w$]*|\d+|[^\s]',s)
file='src/fpga/common/gpu_core.v'
assert tokens(ROOT/file,fitted['defs'].split())==tokens(fit/'frozen'/file,fitted['defs'].split())
cpu='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
assert sha(ROOT/cpu)==fitted['cpu_sha256']
performance=json.loads((OUT/'performance.json').read_text())
assert performance['candidate']==a.candidate and len(performance['samples'])==78
assert all(row['identical'] and row['after_cycles']<=row['before_cycles'] for row in performance['samples'])
art=OUT/'artifacts';art.mkdir(exist_ok=True)
proj=Path(r['directory']);qout=proj/'output_files'
for name in ('ap_core.rbf','ap_core.sof'):
    shutil.copy2(qout/name,art/name.replace('ap_core','os30'))
subprocess.run([str(ROOT/'tools/reverse_bits'),str(art/'os30.rbf'),str(art/'os30.rbf_r')],check=True)
reverse=bytes(int(f'{i:08b}'[::-1],2) for i in range(256))
assert (art/'os30.rbf_r').read_bytes()==(art/'os30.rbf').read_bytes().translate(reverse)
for name in ('ap_core.fit.rpt','ap_core.fit.summary','ap_core.map.rpt','ap_core.sta.rpt','ap_core.asm.rpt'):
    shutil.copy2(qout/name,art/name)
for name in ('ap_core.qsf','core_constraints.sdc'):shutil.copy2(proj/name,art/name)
for name in ('inventory.json','frozen-sources.json'):shutil.copy2(fit/name,art/name)
shutil.copytree(fit/'results/pocket-os30-s2/timing',art/'timing',dirs_exist_ok=True)
fmax=[float(v) for v in re.findall(r';\s*([\d.]+) MHz\s*;\s*[\d.]+ MHz\s*;\s*ic\|mp_ram\|altera_pll_i\|general\[0\]',(qout/'ap_core.sta.rpt').read_text(errors='replace'))]
assert len(fmax)==2
manifest=dict(variant='os30',seed=2,selected_fit=fit.name,selected_by_default=selected,
    clocks_mhz=dict(cpu=100,gpu=100,sdram=100),timing_closed=r['all_checks_pass'],
    hardware_test_pending=True,full_seed_sweep_deferred=True,alms=r['alms'],m10ks=r['m10ks'],
    worst_slack_ns=r['worst'],main_clock_fmax_mhz=fmax,cpu_sha256=fitted['cpu_sha256'],
    current_gpu_matches_fitted_preprocessed_rtl=True,
    files={str(x.relative_to(art)):dict(bytes=x.stat().st_size,sha256=sha(x)) for x in sorted(art.rglob('*')) if x.is_file() and x.name!='manifest.json'})
(art/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
summary=dict(candidate=a.candidate,selected_by_default=selected,
    behavior='Prefetch the first depth line at span start using the existing depth window and idle P3 address register. Preserve write drain, accumulator forwarding and fill invalidation.',
    baseline=dict(alms=16993,m10ks=306,worst_setup_ns=-0.902,main_clock_fmax_mhz=91.73),
    candidate_fit={k:manifest[k] for k in ('alms','m10ks','worst_slack_ns','main_clock_fmax_mhz','timing_closed')},
    performance='performance.json',validation=performance['validation'],
    artifact='artifacts/os30.rbf_r',artifact_sha256=sha(art/'os30.rbf_r'),
    limitations=['No hardware boot/FPS test this turn.', 'GPU replay cycles do not include CPU submission timing or presentation pacing; background traffic uses proxies.', 'The full seed sweep remains deferred.'],
    rejected={'p2':'Negligible synthetic improvement; removed.',
              'span_plus_p2':'Similar game improvement to span start alone; removed extra tail-stage lookup logic.',
              'opaque_only':'SM64 marks its tested surfaces skip-zero; the gate removed all benefit.',
              'fit_early':'17,090 ALMs; -1.118 ns setup, 89.94 MHz minimum main-clock Fmax.',
              'fit_armed':'17,789 ALMs; -1.118 ns setup, 89.94 MHz minimum main-clock Fmax.',
              'fit_reuse':'Canceled after assertions detected empty pipeline shifts overwriting the borrowed address; corrected in the shared implementation.'})
(OUT/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
# Incremental change from this turn, independent of the preexisting dirty tree.
pairs=[(OUT/'baseline-common/gpu_core.v',ROOT/'src/fpga/common/gpu_core.v'),
       (OUT/'gpu-p2/frozen/src/fpga/test/tb_gpu.v',ROOT/'src/fpga/test/tb_gpu.v'),
       (OUT/'os30-before.mk',ROOT/'src/fpga/targets/pocket/variants/os30.mk')]
patch=''
for before,after in pairs:
    name=str(after.relative_to(ROOT))
    patch+=''.join(difflib.unified_diff(before.read_text().splitlines(True),after.read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
(OUT/'implementation.patch').write_text(patch)
print(json.dumps(summary['candidate_fit'],indent=2));print('artifact',manifest['files']['os30.rbf_r'])
