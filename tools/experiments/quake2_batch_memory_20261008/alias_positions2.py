#!/usr/bin/env python3
"""Correct activation: preserve supported hardware geometry requests only."""
import alias_positions as v1
original_gl=v1.gl
def corrected_gl(s):
    s=original_gl(s)
    return v1.once(s,'return !ofgl_geometry_cv || !ofgl_geometry_cv->value;',
        'return !(ofgl_geometry_cv && ofgl_geometry_cv->value &&\n\t\tOFQ2_Supports(OFQ2_CAP_GEOMETRY));')
v1.gl=corrected_gl
v1.W=v1.P/'work/alias_positions2'
v1.O=v1.P/'components/alias-positions-v2'
if __name__=='__main__':v1.main()
