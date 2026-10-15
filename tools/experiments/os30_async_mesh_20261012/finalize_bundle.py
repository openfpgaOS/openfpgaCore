#!/usr/bin/env python3
"""Seal measured decision into an assembled source-only AJ01 bundle."""
from pathlib import Path
import argparse,hashlib,json
p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);a=p.parse_args();b=a.bundle.resolve();m=json.loads((b/'manifest.json').read_text());assert m['status']=='EXPERIMENTAL_NATIVE_REVIEW_NOT_FITTED_OR_DEPLOYED';assert len(m['strict_receipts'])==6 and len(m['fixtures'])==3
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();conclusion=json.loads((b/'results.json').read_text());assert conclusion['decision']=='RETAIN_QUALIFIED_BASELINE_NO_PHYSICAL_FIT_OR_DEPLOYMENT';best=conclusion['revisions'][-1]
readme=f'''Retain the qualified baseline. AJ01 is correct in native simulation but slower:
the selected F22 JOB3 trial uses {best['total_change_percent']:.2f}% more total cycles than its same-ELF
control, and {best['different_ELF_prior_total_change_percent']:.2f}% more than the prior qualified app/core. Its warm
interval still uses {best['warm_change_percent']:.2f}% more cycles with zero CPU clipping repairs.
No physical compile or deployment was performed.

This source-only bundle selects app v3 plus frozen native-v2 RTL, preserves
app v1/v2 source patches and all six paired strict receipts, and binds all
181 native input files and all three prepared fixtures. REPORT.txt and
results.json contain the measured decision, exact intervals and limitations.
The physical recipe is prepared-only; no area, timing or sustained FPS claim.

Verify without altering the baseline:
  python3 verify.py --patches --evidence
Run portable actual-RTL tests into a new external directory:
  python3 run_tests.py --output /tmp/aj01-rtl-tests
Run portable repair bounds/format/cancellation/FENV checks:
  python3 run_repair_host.py --output /tmp/aj01-repair-tests
Exact repair arithmetic was checked separately on the actual native CPU.
Full frame and scene replay also requires separately held game assets/tools;
those inputs are hash-bound, not redistributed in this source bundle.

native.patch targets the exact cached-CPU native baseline. core.patch targets
the qualified physical frozen tree; recipes/prepare_physical.py records the
isolated QSF/source integration. sm64.patch targets the explicit app baseline;
sm64-from-predecessor.patch targets app v2. Historical variants are in apps/.
Original workspace recipes and paths are retained for audit. Assembly uses
build_bundle.py followed by finalize_bundle.py; manifest records both stages.
No script automatically fits, deploys or changes the installed core.
'''
assembly=(b/'manifest.json').read_bytes();(b/'assembly-manifest.json').write_bytes(assembly);(b/'README.txt').write_text(readme)
m['status']='CORRECT_NATIVE_PROTOTYPE_REJECTED_FOR_PERFORMANCE';m['decision']=conclusion['decision'];m['selected_comparison']=best;m['assembly_manifest_sha256']=sha(b/'assembly-manifest.json');m['finalizer_sha256']=sha(b/'finalize_bundle.py');m['files']={str(p.relative_to(b)):sha(p)for p in sorted(b.rglob('*'))if p.is_file()and str(p.relative_to(b))not in m['excluded_inventory']and '__pycache__'not in p.parts};(b/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(json.dumps({'status':m['status'],'files':len(m['files']),'manifest_sha256':sha(b/'manifest.json')},indent=2))
