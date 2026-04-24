// ============================================================================
// gpu_config.vh — optional GPU build knobs (debug / stats only)
// ----------------------------------------------------------------------------
// Design policy: the GPU core has ONE hardware implementation.  Triangles,
// perspective-correct spans, and the pipelined fragment processor are all
// first-class and unconditional.  Replaces the old gpu_features.vh with its
// GPU_VARIANT_LITE / GPU_VARIANT_FULL / GPU_FEAT_* matrix — that matrix had
// become a source of bugs (silently-disabled persp path on hardware,
// compile failures in partial configurations, dead DSPs in LITE, stale
// commentary).
//
// The only knobs that remain are optional, off by default, and only affect
// debug-visibility / counters — never the datapath.  Turn them on by
// passing +define+<NAME> to Verilator or --verilog_macro=<NAME> to Quartus.
// ============================================================================

// GPU_DEBUG — enables the GPU_DBG_BADWR / GPU_DBG_BADCNT MMIO registers and
// the underlying stray-write latch.  Off by default for synth so the latch
// FFs and compare logic don't burn ALMs in production builds.
// `define GPU_DEBUG

// GPU_STATS — enables the stat_pixels / stat_spans / stat_triangles
// counters (readable via GPU_STAT_* MMIO).  Useful for profiling but not
// required for correct rendering; ~100 FFs saved with it off.
// `define GPU_STATS
