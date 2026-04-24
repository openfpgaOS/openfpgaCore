// ============================================================================
// gpu_features.vh — GPU variant → feature flag derivation
// ----------------------------------------------------------------------------
// Define exactly one of:
//   GPU_VARIANT_LITE   — 2.5D software-renderer path: span renderer only
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
// LITE variant: minimal GPU framework for software-rendering engines
// ----------------------------------------------------------------------------
// Doom + Duke3D both render in software (CPU writes to framebuffer) and
// never dispatch GPU commands.  GPU_FEAT_PERSP_SPAN + GPU_FEAT_FRAG_PIPELINE
// were 2.5D-engine helpers (perspective subdivision + 1-px/cycle fragment
// writer); nothing in the current app set uses them, so they're disabled
// here to free ~500–800 ALMs + 1 M10K (recip_lut) for the rest of the
// design.
//
// Important: the FULL variant intentionally does NOT use FRAG_PIPELINE — its
// triangle rasterizer relies on the sequential S_SPAN_* fragment FSM.  So
// turning these off in LITE puts the GPU on the same fragment path that a
// future HW triangle rasterizer would build on.  Re-enable the defines
// below when an app actually needs them, or switch to GPU_VARIANT_FULL.
`ifdef GPU_VARIANT_LITE
  // `define GPU_FEAT_PERSP_SPAN      // 16-px perspective-subdivision span renderer
  // `define GPU_FEAT_FRAG_PIPELINE   // 1-pixel/cycle pipelined fragment processor
  // Triangles, vcolor, bilinear, alpha intentionally absent.
`endif

// ----------------------------------------------------------------------------
// FULL variant: everything in Lite, plus 3D triangles and extra blend/filter
// ----------------------------------------------------------------------------
// The FMax push (commit 9221830) rewrote the triangle rasterizer to emit
// spans directly into S_FRAG_PIPE (the pipelined fragment processor) — the
// old S_TRI_PIX → S_TRI_FRAG → S_SPAN_* chain is gone.  Because of that,
// FULL now requires GPU_FEAT_FRAG_PIPELINE; without it, S_TRI_PIX
// references src_mode/src_done/SRC_SPAN which only exist under the
// frag-pipeline guard and the build fails.
//
// GPU_FEAT_VCOLOR / BILINEAR / ALPHA are kept as flags for future use but
// don't currently steer code — vertex-colour was dropped during FMax
// (grad_r_dx/dy removed), and bilinear/alpha are still placeholders.
`ifdef GPU_VARIANT_FULL
  `define GPU_FEAT_TRIANGLE        // S_TRI_* edge-function rasterizer
  `define GPU_FEAT_PERSP_SPAN      // perspective span setup (PSS) sub-FSM
  `define GPU_FEAT_FRAG_PIPELINE   // pipelined fragment processor (required by FULL)
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
