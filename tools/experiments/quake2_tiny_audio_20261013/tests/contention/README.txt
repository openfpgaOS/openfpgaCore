Integrated mixer/cache/SDRAM arbitration qualification

Run:
  python3 build.py ../rtl-candidate NEW_OUTPUT
  python3 analyze.py NEW_OUTPUT

The runner freezes mixer, cache, selected arbiter, QSF, core_top and all test
sources before compiling. The wrapper reproduces core_top's mixer->cache->M3
wiring, invalidates the selected cache voice on every voice write, gates the
opt-in cache enable with mixer enable, and connects the actual WQ16, unbuffered
SDRAM arbiter selected by the QSF. It does not instantiate the peripheral MMIO
decoder or the complete core. MMIO address/capability tests are separate.

Each case programs32voices, prefills700 samples and runs a real48kHz consumer
for400ms at a stated90/100MHz logic clock. Quiet cache-OFF is the PCM reference;
every common produced sample is compared with quiet cache-ON and saturated
GPU/CPU cache-OFF/ON. FIFO conservation, final occupancy, drift and underruns
are measured independently. A stream is always voice31 and every request is
checked uncacheable; its downstream request must never become a line refill.
The stream WPTR is initialized far enough ahead for the entire400ms run.

Quake2-like workload:31 signed16 mono looping SFX at11.025/22.05kHz and one
signed16 stereo44.1kHz stream, with per-voice volume ramps. Adversarial workload:
all32 stereo voices,31 uneven short loops, some almost2x pitch, and the same
stream. Low adversarial gains prevent saturation hiding most PCM differences.
Sample values are a deterministic address-sensitive pattern, not game assets.

The synthetic slave has one outstanding transaction, first-word latencies
8/16/32cycles, a1cycle interbeat gap and periodic bounded stalls. CPU/GPU
traffic is saturated synthetic traffic, not a game trace: GPU8word reads and
posted8word writes, CPU alternating8word reads and singleword writes. Reported
GPU work counts bus beats, including posted-write acceptance, normalized to a
silent run of the same model. It is neither rendered pixels nor measured FPS.
Physical SDRAM timing/CDC, firmware ISR cost and Pocket output pins are outside
this test. Physical fit/Fmax is a separate gate and cannot be inferred here.

Frozen integrated-v1: mixer072f017d... (compactV3 plus metadata ports), selected
tail-v2 cacheac018efd..., real arbiterbf3342a4.... All48 400ms cases passed exact
common PCM comparison. Quake2-like cache-ON sustained all6 busy combinations
without underruns or FIFO drift, reducing SDRAM words/output48.50->9.49 (5.11x).
At90MHz/latency32, cache-OFF underruns7019; ON underruns0/finalFIFO767. In the
adversarial90MHz/latency32 case, cache-ON still underruns536: the cache is not a
universal guarantee for arbitrary32voice formats/rates/short loops.

The DSP arithmetic fallback compiled here is independently checked against
unmodified Intel altmult_add in component-synth/dsp-adders-vendor-proof:
500,000 operand triples over widths16/17/21/23/32, exact. That arithmetic proof
does not establish routed timing. CompactV3 subsequently failed physical
timing, so integrated-v1 is a throughput/correctness result, not a deployable
clock qualification. Optional future mixer sources can be tested via --mixer;
--cases and --q2-only provide a smaller, explicitly recorded capacity matrix.

Earlier reference-v1/v2/v3 directories are fixture revisions using the original
dormant native6 mixer f9cbfe37..., not compact mixerV3. Reference-v3 fixed the
original100ms prefill-masking weakness and froze all sources before simulation.

New selected logical candidate: integrated-v4-fabric, mixerbac9c69b..., same
tail-v2 cacheac018efd.... Full48case matrix again passes PCM equivalence.
All6 Quake2-like busy cases maintain FIFO767-768 with zero underruns; cached
transactions/output32.03->3.60 (~8.9x), words/output48.50->9.49 (~5.1x).
The adverse90MHz/latency32 stereo-short-loop case underruns843; the other
adversarial cases maintain occupancy. This preserves the important capacity
limitation rather than silently reducing voices/formats/rates to pass.
The fabric mixer avoids the DSP-adder helper, so its integrated test does not
depend on a Verilator-only arithmetic substitution. Physical timing remains
separate. See integrated-v4-fabric/{receipt,results,summary}.json.

Rejected registered-DSP scheduling experiment: integrated-v4-capacity,
mixer4cbe23ba.... All12 PCM cases pass, but even Quake2-like cache-ON at90MHz
drainsFIFO700->465 with latency16 and underruns890 with latency32. The extra
FSM wait cycles consume too much capacity. That result motivated the fabric
version; it is not a deployment candidate.
