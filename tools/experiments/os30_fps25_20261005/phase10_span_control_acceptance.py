#!/usr/bin/env python3
"""Apply the established independent resume/chunk proof to factored v6 RTL."""
import sys
from pathlib import Path
import phase10_span_resume_acceptance as review
review.a.OUT=review.a.P/'span-lookahead-review-v4'
review.a.CAND=review.a.P/'span-lookahead-v6/gpu_core.v'
def prepare():
    review.prepare()
    out=review.a.OUT
    (out/'control-recipe.py').write_text(Path(__file__).read_text())
    review.a.save(out/'control-inputs.json',dict(recipe_sha256=review.a.sha(Path(__file__)),
        inherited_recipe_sha256=review.a.sha(Path(review.__file__)),
        candidate_sha256=review.a.sha(review.a.CAND)))
if __name__=='__main__':
    for action in sys.argv[1:]:
        if action=='prepare':prepare()
        elif hasattr(review,action):getattr(review,action)()
        else:getattr(review.a,action)()
