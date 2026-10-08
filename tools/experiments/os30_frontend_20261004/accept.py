#!/usr/bin/env python3
"""Run the full os30 GPU acceptance suite from one fitted source snapshot."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-frontend-20261004'
sys.path.insert(0,str(ROOT/'tools'))
import check_gpu_target_matrix as check


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fit',help='Saved-seed fit label, without fit-')
    args=parser.parse_args()
    fit=OUT/('fit-'+args.fit)
    manifest=json.loads((fit/'frozen-sources.json').read_text())
    for rel,digest in manifest.items():
        assert hashlib.sha256((fit/'frozen'/rel).read_bytes()).hexdigest()==digest,rel
    rows=json.loads((fit/'inventory.json').read_text())
    assert len(rows)==1 and rows[0]['variant']=='os30'
    source=OUT/('accept-source-'+args.fit)
    shutil.copytree(fit/'frozen',source)
    test=source/'src/fpga/test';test.mkdir()
    for p in [ROOT/'src/fpga/test/tb_gpu.v', ROOT/'src/fpga/test/tb_gpu_acceptance_main.cpp',
              *sorted((ROOT/'src/fpga/test').glob('*.h'))]:
        shutil.copy2(p,test/p.name)
    check.ROOT=source
    check.inventory=lambda **kwargs:copy.deepcopy(rows)
    sys.argv=[sys.argv[0],'--variant','os30','--jobs','1','--output',str(OUT/('accept-'+args.fit))]
    check.main()


if __name__=='__main__':main()
