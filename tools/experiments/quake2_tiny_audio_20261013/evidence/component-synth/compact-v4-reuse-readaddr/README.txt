Isolated optional V4 speculative-read candidate; not physically qualified.

S_FETCH_TAP0_CALC writes VTBL_ADDR unconditionally. A terminating voice ignores
the speculative BASE read. This removes the length-result condition from the
inferred RAM-read address enable without adding any cycle or changing state.

All177 inherited checks,18432 independent samples/96scenarios,64 W1C races,
2048 registered position reads, and343 capacity rows pass. The entire inherited
and reference logs and capacity CSV match V4 fabric byte-for-byte.

See component-receipt.json for this exact candidate's resource estimate. The
combined reuse-readaddr version also includes the documented disjoint register
lifetime merges from compact-v4-reuse2. Same10DSP/4M10K and all32voices.
A full-core fit must determine packing and timing; no FPS/audio-device claim.
