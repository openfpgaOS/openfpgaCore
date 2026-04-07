// ============================================================================
// gpu_features.vh — GPU variant → feature flag derivation
// ----------------------------------------------------------------------------
// Define exactly one of:
//   GPU_VARIANT_LITE   — Quake/Doom/Duke3D path: span renderer only
//   GPU_VARIANT_FULL   — Everything: triangles, vcolor, bilinear, alpha
//
// Default (no variant macro defined) is GPU_VARIANT_LITE because the only
// hardware target right now is the Pocket and the Pocket only ships Lite.
// The Full path is kept alive for Verilator coverage and future hardware
// targets — define GPU_VARIANT_FULL on the synthesis/sim command line to
// enable it.
//
// Per-feature flags below are derived from the variant macro. Don't define
// these directly from outside; pick a variant and let this file expand it.
// ============================================================================

`ifndef GPU_VARIANT_LITE
  `ifndef GPU_VARIANT_FULL
    `define GPU_VARIANT_LITE
  `endif
`endif

// ----------------------------------------------------------------------------
// LITE variant: span renderer for Quake/Doom/Duke3D
// ----------------------------------------------------------------------------
`ifdef GPU_VARIANT_LITE
  `define GPU_FEAT_PERSP_SPAN      // Quake-style 16-px perspective subdivision
  `define GPU_FEAT_FRAG_PIPELINE   // 1-pixel/cycle pipelined fragment processor
  // Triangles, vcolor, bilinear, alpha intentionally absent.
`endif

// ----------------------------------------------------------------------------
// FULL variant: everything in Lite, plus 3D triangles and extra blend/filter
// ----------------------------------------------------------------------------
// Note: GPU_FEAT_FRAG_PIPELINE is intentionally absent here. Full's triangle
// rasterizer (S_TRI_PIX → S_TRI_FRAG → shared S_SPAN_*) currently relies on
// the sequential fragment FSM. Refactoring it to feed the pipelined fragment
// processor is deferred — Full keeps the old fragment path, which still
// benefits from the new pipelined texture cache (its handshake is
// backward-compatible, see gpu_tex_cache.v).
`ifdef GPU_VARIANT_FULL
  `define GPU_FEAT_TRIANGLE        // S_TRI_* edge-function rasterizer
  `define GPU_FEAT_VCOLOR          // R gradient (Gouraud-shaded triangles)
  `define GPU_FEAT_BILINEAR        // 4-tap texture filter (placeholder)
  `define GPU_FEAT_ALPHA           // alpha / additive blend modes (placeholder)
  `define GPU_FEAT_PERSP_SPAN      // also available in Full
`endif

// ----------------------------------------------------------------------------
// Derived: GPU_PERSP_IMPL — perspective span path is actually implemented.
// The implementation requires the pipelined fragment processor (S_FRAG_PIPE)
// so it currently only exists in LITE. Full has GPU_FEAT_PERSP_SPAN visible
// to the rest of the system but no functional path — this will be addressed
// when Full's triangle rasterizer is refactored to feed S_FRAG_PIPE.
// ----------------------------------------------------------------------------
`ifdef GPU_FEAT_PERSP_SPAN
  `ifdef GPU_FEAT_FRAG_PIPELINE
    `define GPU_PERSP_IMPL
  `endif
`endif

// ----------------------------------------------------------------------------
// Derived: any feature that needs the registered DSP + reciprocal LUT
// (currently triangle setup and perspective span 1/z lookup)
// ----------------------------------------------------------------------------
`ifdef GPU_FEAT_TRIANGLE
  `define GPU_HAS_RECIP_LUT
`else
  `ifdef GPU_PERSP_IMPL
    `define GPU_HAS_RECIP_LUT
  `endif
`endif
