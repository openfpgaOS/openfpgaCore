Local VexiiRiscv generator fixes are applied by `generate_vexii.sh` before
generation. Applying them twice is harmless; an unexpected upstream source
change stops generation for review. The submodule will show these source edits.

`0001-pipeline-fpu-conversion-range-checks.patch` captures the overflow and
underflow flags during the float-to-integer unit's existing half-rate pause,
alongside its already registered integer result. Instruction latency is
unchanged. With half-rate mode disabled, the flags remain combinational.

This cuts the range-check path into integer writeback and forwarding. MiSTer
validation covers firmware boot, integer/FPU dependencies, cache traffic,
atomics, traps, and 1,960 signed/unsigned conversion cases checking both results
and exception flags across all five rounding modes. The configured core ignores
subnormals, so the conversion vectors cover normal values, zeros, infinities,
NaNs and saturation boundaries.

The MiSTer configuration also enables `FETCH_READ_HOLD`. After generation,
`../retime_fetch_reads.pl` moves the two instruction-cache data-bank read
enables and the prefetch PC-buffer enable into preserved selector registers.
Unconditional raw reads and held values reproduce the original synchronous
read latency and stall behavior, including writes while stalled. The helper
checks every expected generated block before writing the netlist; applying it
twice is harmless. Other configurations leave this transformation disabled.

Run `python3 tools/check_vexii_fetch_reads.py` from the repository after generating the
MiSTer CPU. It checks one million independently enabled read cycles against the
last enabled memory/PC value. The whole-CPU stress suite additionally exercises
the transformed CPU with concurrent SDRAM scanout.
`python3 tools/check_vexii_interrupts.py` repeats that workload with timer
interrupts, checking interrupt acknowledgement and return under memory stalls.

`0002-relaxed-learn-registered-predictor-update.patch` adds `--relaxed-learn`
(`RELAXED_LEARN=1` in a config): the branch-predictor learn stream is
registered even with a single learn source, taking the late trap/cancel cone
off the BTB and GShare RAM write enables.  Training lands one cycle later;
on os30 it measured +0.5% on the RTL display-list kernel, so no config
enables it yet.

`0003-pipelined-int-to-float-conversion.patch` adds `--fpu-i2f-pipelined`
(`FPU_I2F_PIPELINED=1`): `fcvt.s.w[u]` computes the unpacker's leading-zero
count and shift in the lane's own stages and reaches the packer three stages
later from registers, instead of freezing the lane for the shared side
pipeline.  Values are the same expressions; `src/fpga/test/cpu_i2f` checks
30,000 conversions and hazard sequences.  The new code sits below the
original so SpinalHDL's line-numbered signal names, and therefore every
config without the option, stay byte-identical.


`LSU_WRITE_REG=1` runs `../retime_dcache_writes.pl` on the generated netlist.
Each data-cache bank's write port (per-byte enables, address, data) is
registered, so the store hit/redo/trap cone ends at a flop instead of the
depth-decoded write enables of every bank M10K (os30's worst CPU cluster,
about 110 endpoints).  A read captured on the edge that commits the pending
write overlays the pending bytes on the RAM's OLD_DATA mixed-port result,
so every read, including writeback victim reads, returns the value the
original memory returns.  The whole-CPU stress, scanout, IRQ and `cpu_i2f`
runs are cycle-identical, and a shadow copy of each bank with the original
write timing matched all ~4.9M read results in those runs; with the bypass
disabled the shadow reports mismatches, so the bypass is exercised.

`../os20-lsu-dispatch.patch` is not in this folder because it is applied only
for os20 (`OS20_LSU_DISPATCH=1`), to a private copy of the generator source,
so every other variant generates from unchanged Scala. It replaces the
store-buffer full/occupancy subtraction with a registered occupancy counter,
and decodes the dispatch bypass flags in the decode stage instead of the
execute lanes. Cycle counts in the CPU stress and kernel bench are unchanged.
os20 also sets `RELAXED_SHIFT=0`, which omits `--relaxed-shift` so shifts
complete in one execute stage (bench: 972,199 -> 868,749 cycles).
