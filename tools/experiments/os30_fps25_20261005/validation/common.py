"""Exact shared inputs for the private single-issue software experiment."""
import hashlib, importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/validation'
OLD=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
OLD_TOOLS=ROOT/'tools/experiments/sm64_batch_cull_20261005/validation'
FIT=ROOT/'docs/measurements/os30-dual-20261004/fit-single128-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def load(name):
 spec=importlib.util.spec_from_file_location('retained_'+name,OLD_TOOLS/(name+'.py'))
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
PREVIOUS=ROOT/'docs/measurements/os30-simple-20261005/validation'
BASELINES={'baseline-diagnostic':PREVIOUS/'capture-simple-v4-diagnostic','baseline-normal':PREVIOUS/'capture-simple-v4-normal'}
def capture_path(label):return BASELINES.get(label,OUT/('capture-'+label))
def check_capture(path):
 m=read(path/'inputs.json');assert m['success'] and m['hw_features']=='0x2b136753'
 assert m['audio'] and not m['geometry_capability'] and not m['command_dma'] and not m['panel']
 for rel,digest in m['results_sha256'].items():assert sha(path/rel)==digest,(path,rel)
 return m
