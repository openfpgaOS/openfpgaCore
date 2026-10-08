#!/usr/bin/env python3
"""Bind the actual functional output directory without rewriting staged inputs."""
import json
import sys
from pathlib import Path
import phase10_backend_wire_coupled as m

m.FUNCTIONAL = m.P / 'backend-wire-functional-v1'
binding = m.OUT / 'functional-directory-binding.json'
if sys.argv[1:] == ['prepare']:
    proof = json.loads((m.FUNCTIONAL / 'result-normal.json').read_text())
    assert proof['passed'] and proof['elf_sha256'] == m.sha(m.APP)
    m.write(binding, dict(functional_directory=str(m.FUNCTIONAL),
        normal_proof_sha256=m.sha(m.FUNCTIONAL / 'result-normal.json'),
        staged_helper_sha256=m.sha(Path(m.__file__)),
        runner_sha256=m.sha(Path(__file__)), elf_sha256=m.sha(m.APP)))
else:
    assert len(sys.argv) == 2 and sys.argv[1] in ('warm', 'cold')
    b = json.loads(binding.read_text())
    assert b['normal_proof_sha256'] == m.sha(m.FUNCTIONAL / 'result-normal.json')
    assert b['staged_helper_sha256'] == m.sha(Path(m.__file__))
    assert b['runner_sha256'] == m.sha(Path(__file__))
    m.run(sys.argv[1])
