#!/usr/bin/env python3
"""One link-policy correction for GCC13 mixed-profile LTO; no recompile/retrain."""
import json, shutil
from pathlib import Path
from elftools.elf.elffile import ELFFile
import renderer_pgo_v2 as b

def main():
    p=b.guard();r=b.commands();d=b.OUT/'use-link-v3';d.mkdir(exist_ok=False)
    for name in b.NAMES:
        log=(b.OUT/'use'/(name+'-compile.log')).read_text().split('\n',1)[1]
        assert 'error:'not in log and 'warning:'not in log
        o=Path(r['compile'][name][r['compile'][name].index('-o')+1]);shutil.copy2(o,d/(name+'.o'))
    before={str(f.relative_to(b.OUT)):b.sha(f)for f in (b.PROFILE).rglob('*.gcda')}
    c=r['link'][:];c[c.index('-o')+1]=str(d/'app.elf')
    c+=b.USE+['-fdump-ipa-profile-details='+str(d/'link-profile.txt')]
    b.docker(c,d/'link.log')
    assert before=={str(f.relative_to(b.OUT)):b.sha(f)for f in (b.PROFILE).rglob('*.gcda')}
    assert not any(n.startswith('__gcov')or n=='pgo_export'for n in b.symbols(d/'app.elf'))
    with (d/'app.elf').open('rb')as f:assert ELFFile(f).get_section_by_name('.gcov_info')is None
    log=(d/'link.log').read_text().split('\n',1)[1];assert 'error:'not in log and 'warning:'not in log
    changed=[n for n,h in p['baseline_objects'].items()if b.sha(b.WORK/n)!=h]
    assert sorted(Path(n).stem for n in changed)==sorted(b.NAMES)
    b.guard();b.write(d/'result.json',dict(passed=True,elf_sha256=b.sha(d/'app.elf'),link_command=c,
        only_link_policy_retry=True,recompiled_objects=False,changed_objects=changed,
        source_exact=True,LTO_preserved=True,instrumentation_absent=True,profiles_unmodified=before,
        failed_prior_link_sha256=b.sha(b.OUT/'use/link.log'),recipe_sha256=b.sha(__file__),
        linked_counter_check_sha256=b.sha(b.OUT/'training/linked-counter-check.json')))
    shutil.copy2(__file__,d/'recipe.py');print(b.sha(d/'app.elf'))
if __name__=='__main__':main()
