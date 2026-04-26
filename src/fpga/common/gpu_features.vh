// DEPRECATED — kept only so stray `include "gpu_features.vh"` directives
// don't break the build during the variant-flattening transition.  The GPU
// has ONE hardware implementation (triangles + perspective spans + pipelined
// fragment processor, always on).  Delete this file once every reference
// to GPU_FEAT_* is gone from the RTL and build scripts.

// Forced-on flags pending Phase 1.4 RTL strip pass.
`define GPU_FEAT_TRIANGLE
`define GPU_FEAT_PERSP_SPAN
`define GPU_FEAT_FRAG_PIPELINE
`define GPU_PERSP_IMPL
`define GPU_HAS_RECIP_LUT
