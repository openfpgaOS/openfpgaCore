Audio runs in hardware while the CPU and GPU continue working. The CPU updates voice parameters and supplies decoded stream data; the mixer handles all 32 logical voice slots, sample-rate interpolation, stereo volume, ramps and summation. It shares arithmetic across voices instead of duplicating a mixer for every channel.

  CPU voice commands -----------------------> compact 32-voice mixer
                                                       ^
  SDRAM <-> shared arbiter <-> per-voice sample cache ---+
                   ^                    stream bypass --+
                   |
              GPU memory traffic

  mixer -> 1024-frame PCM FIFO -> audio serializer -> Pocket speakers/headphones

Each cached voice keeps one 32-byte line in BRAM. Repeated reads of static sound effects usually hit there; a miss fetches eight 32-bit words in one burst. Streams bypass the cache so newly written PCM remains visible. Voice-register writes invalidate the corresponding cached line. The app enables caching only after detecting the new capability; older software retains uncached behavior. Cached sample storage must remain immutable while its voice is active unless software invalidates it through the voice registers.

This reduces competition for SDRAM rather than removing it. In the 400 ms synthetic 32-voice Quake2-rate tests, caching reduced source reads from about 48.5 to 9.5 words per output frame. All six busy cases at 90/100 MHz and modeled memory latencies 8/16/32 sustained 48 kHz without underruns; synthetic GPU bus work retained about 92–97% of the silent baseline. These are memory-contention tests, not measured game FPS or physical SDRAM qualification. The deliberately adverse 32-stereo-short-loop case at 90 MHz/latency 32 still underruns. Real-game performance and audio need a device trial after physical timing/source selection.

Evidence: contention/integrated-final-ring-v1/{receipt,summary}.json and contention/integrated-final-ring-all22-v1/; mixer c6d2517af662f356b44617230c27aa880c449928282e738c374a2db86663f73a, sample cache ac018efd17a7e1012d1456f12375575a4c4f57784ab6a03c2132bb00795c111d. The diagram shows shared resources, not arbitration priority or exact clock boundaries.
