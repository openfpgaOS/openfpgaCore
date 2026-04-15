# Workarounds

Documented, intentional workarounds in the tree. Each has a known root
cause — they are load-bearing, not leftovers. Read before "simplifying"
any of them.

---

## RTL

### Same-cycle W capture in AXI slaves

**Where:** `src/fpga/common/axi_cram0_slave.v` (S_IDLE + S_WR_NEXT),
`src/fpga/common/axi_cram1_slave.v`, `src/fpga/common/cpu_target_port.v:402`.

**What:** When AW and W beats arrive together (bundled master), the
slave captures `wdata`/`wstrb` on the *same* cycle `wvalid` is seen —
not one cycle later. `cpu_target_port.v` additionally refuses to
accept a new W beat if one is already pending.

**Why:** An earlier one-cycle-late capture pattern raced with the
master dropping `wvalid` on handshake, corrupting the payload on
bursty writes. The same-cycle pattern is proven across the legacy
`axi_psram_slave` history.

### Burst completion: saw-busy gate

**Where:** `src/fpga/common/cram1_controller.v` burst path.

**What:** After issuing a burst request, the consumer must observe
`burst_busy == 1` first, *then* wait for the `!burst_busy` edge. Polling
`!burst_busy` immediately after the request false-completes because
the controller has not yet raised busy.

**Why:** The request→busy gap is several cycles (CDC sync + FSM advance).
Without the saw-busy gate, a tight polling loop re-issues the same burst
forever.

### `burst_busy` held one cycle past last `q_valid`

**Where:** `src/fpga/common/cram1_controller.v` (ST_DONE).

**What:** `burst_busy` stays HIGH for one cycle after the final
`burst_q_valid` pulse, then drops in ST_DONE.

**Why:** External callers latch the last word on the `q_valid` pulse;
dropping busy in the same cycle lets them miss it if they're sampling
busy first.

### CRAM0 burst hard-tied off at instantiation

**Where:** `src/fpga/targets/pocket/core_top.v` — `cram0_controller`
instantiation ties `burst_rd = 1'b0`.

**What:** The burst FSM inside `cram0_controller.v` is plumbed but
`burst_rd` is pinned to 0 at the top level. `axi_cram0_slave.v` also
routes reads to `S_RD_CMD` (async), never `S_RD_BURST`.

**Why:** Sync-burst on CRAM0 had a hardware timing race that was never
resolved (no SignalTap available). Async page mode (BCR 0x9D1F) is
reliable. The tie-off is belt-and-suspenders: even a stray
`psram_burst_rd` pulse from the slave can't reach the controller.

### CRAM1 stays in POR-default BCR (no BCR write)

**Where:** `src/fpga/common/cram1_controller.v`.

**What:** CRAM1 never receives a BCR write. Burst reads on CRAM1 are
implemented internally as N back-to-back async word reads that match
the sync-burst external contract (one `q_valid` per 32-bit word,
`burst_busy` held throughout).

**Why:** The attempted sync-burst BCR (0x641F, commit bfd1ed0) broke
CRAM1 reads on real hardware — `cram1_clk` runs from a different clock
than the controller (`psram1_a` on `clk_cpu`) and the SDC false-paths
that I/O. Proper sync-burst would need a phase-shifted PLL output for
`cram1_clk` plus matching `input_delay`/`output_delay`. No current
consumer needs the extra bandwidth, so the work isn't justified.

### CRAM clock pin assignments

**Where:** `src/fpga/targets/pocket/core_top.v` — `cram0_clk =
clk_cram`, `cram1_clk = clk_74a`.

**What:** CRAM0 runs on a 100 MHz phase-shifted clock; CRAM1 runs on
the unshifted 74.25 MHz clock.

**Why:** Switching CRAM1 to `clk_cram` (for symmetry) broke ELF loads
on real hardware. The chip doesn't fully ignore `cram_clk` in async
mode, and CRAM0's tuned phase shift is wrong for CRAM1's board
routing. Any future migration requires lab measurement of CRAM1's
actual Tco.

### BCR sanity reset on every boot (CRAM0)

**Where:** `src/fpga/targets/pocket/core_top.v` — `bcr_init_*` FSM at
line ~431.

**What:** On every boot, the FPGA writes `0x9D1F` to both CRAM0 dies
via CRE.

**Why:** BCR state lives on the chip, not in the FPGA — `quartus_pgm`
reload does *not* reset it. If a prior bitstream wrote 0x641F and the
next bitstream expects async, the async controller can't talk to the
sync-mode chip and the CPU's first CRAM0 fetch wedges silently. Only a
true power cycle recovers without this defensive write. ~6 hours of
debugging motivated the fix (2026-04-08).

---

## Firmware

### `nanosleep` truncates to `uint32_t` ms

**Where:** `src/firmware/os/kernel/syscall.c:706,716-725`.

**What:** `sys_clock_nanosleep_time64()` caps the sleep duration at
`uint32_t` milliseconds (~49.7 days).

**Why:** The underlying cycle counter is 32-bit. Honoring longer
sleeps would require 64-bit counter plumbing that no caller needs.

### RISC-V app linker script must define `__global_pointer$`

**Where:** `src/firmware/api/app.ld:71`.

**What:** `PROVIDE(__global_pointer$ = . + 0x800);` in the `.data`
section.

**Why:** musl's `_start` emits `lla gp, __global_pointer$`. Without
the symbol, the linker silently leaves the placeholder relocation as
zero, `gp` becomes a garbage address, and every `gp`-relative access
in libc faults or corrupts memory. App hangs in `__init_libc` before
any user code runs.

### ELF loader must push `AT_PAGESZ` in auxv

**Where:** `src/firmware/os/kernel/loader.c:422-423` — `elf_exec()`
pushes `AT_PAGESZ=4096`.

**Why:** musl's `mallocng` uses `libc.page_size` in size/alignment
math. A zero page size causes a divide-by-zero (silent wraparound on
rv32) and the allocator hangs on the first `malloc`.

### Bridge backend scratch bounce through CRAM1

**Where:** `src/firmware/os/targets/pocket/file.c:43-46`.

**What:** Every bridge-backed file read begins with a 4-byte
`bridge_read_impl(slot=1, ..., CRAM1_SCRATCH, 4)` followed by a D-cache
flush.

**Why:** The APF bridge DMA writes to CRAM1 only. This warm-up read
primes the bridge FSM and ensures the CRAM1 scratch window's D-cache
state is clean before the subsequent full read.

---

## Naming / historical

### `bcr_init_done` is always HIGH on CRAM1

**Where:** `src/fpga/common/cram1_controller.v:167`.

**What:** `bcr_init_done` resets to `1'b1` and never changes.

**Why:** The port is kept as an output for compatibility with the
previous (BCR-writing) version of the controller. `core_top.v`
consumers can remain wired without conditional logic. Remove the port
the next time `core_top.v` is refactored.
