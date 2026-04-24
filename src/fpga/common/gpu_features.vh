// DEPRECATED — kept only so legacy +define+GPU_VARIANT_* flags and stray
// `include "gpu_features.vh"` directives don't break the build during the
// variant-flattening transition.  All real configuration now lives in
// gpu_config.vh; the GPU has ONE hardware implementation (triangles +
// perspective spans + pipelined fragment processor, always on).  Delete
// this file once every reference to GPU_VARIANT_* / GPU_FEAT_* is gone
// from the RTL and build scripts.
`include "gpu_config.vh"

// Forced-on flags: the old gpu_features.vh matrix is collapsed to a
// single configuration.  These defines will be removed from RTL in a
// follow-up pass that strips the `ifdef GPU_FEAT_* / `ifndef wrappers
// (the wrapped blocks all become unconditional, and the
// `ifndef GPU_FEAT_FRAG_PIPELINE` sequential S_SPAN_* fallback path
// disappears since it's dead code in a single-config world).
`define GPU_FEAT_TRIANGLE
`define GPU_FEAT_PERSP_SPAN
`define GPU_FEAT_FRAG_PIPELINE
`define GPU_PERSP_IMPL
`define GPU_HAS_RECIP_LUT
