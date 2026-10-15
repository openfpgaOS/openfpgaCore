#!/usr/bin/env python3
"""Use the validated replay recipe with fresh outputs and a passive observer."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
Q = HERE.parent / 'queued-front-v1'
spec = importlib.util.spec_from_file_location('queued_replay', Q/'replay_v3.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
replay.G = HERE
replay.OBSERVER = HERE/'observer-v3'

p = argparse.ArgumentParser()
p.add_argument('action', choices=['build', 'run'])
p.add_argument('label')
p.add_argument('--model', type=Path, default=replay.BASE)
p.add_argument('--config', type=Path, default=Q/'fixture-v2/input.cfg')
p.add_argument('--elf', type=Path,
               default=Q/'work/sm64-v2/.obj/sm64-queued-front-v2-perfdiag/app.elf')
p.add_argument('--evidence', type=Path, nargs='*', default=[])
p.add_argument('--max-cycles', type=int, default=15000000)
a = p.parse_args()
assert Path(a.label).name == a.label
a.evidence.append(Path(__file__))
getattr(replay, a.action)(a)
