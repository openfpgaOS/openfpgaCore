# openfpgaOS Memory Architecture — v2 (simplification)

Target: smallest, safest, most debuggable memory fabric that still
runs Doom-class workloads.  Every avoidable master on every bus is
removed.  Parallelism that doesn't earn its keep is removed.  Shared
memories are time-sliced by explicit software mode switches instead of
relying on concurrent arbitration.

Inspired by PocketQuake's layout (CRAM0 for code, SDRAM for BSS/heap,
single PSRAM slave) but pushed further: CRAM0 becomes a *staging area*
for the bridge, not a runtime execution region.

## 1. Memory chips kept

| Chip  | Size   | Clock      | Role                                                           |
| ----- | ------ | ---------- | -------------------------------------------------------------- |
| BRAM  | 32 KB  | clk_cpu    | Bootloader, trap handler, hot `.fastdata` globals              |
| SDRAM | 64 MB  | clk_cpu    | Everything else: OS text, app text, heap, stack, FB, audio, samples |
| CRAM0 | 16 MB  | **clk_74a** | Bridge scratch only (load / save / readfile staging).  Runs at the APF bridge clock so bridge transfers are zero-friction; CPU accesses go through a CDC. |
| SRAM  | 256 KB | clk_cpu    | GPU-private (Z-buffer, span cache). Not on AXI.                |

### Retired
- **CRAM1** — controller and pin group removed from the bitstream.
- All of CRAM1's former clients (samples, saves, scratch) are moved.

## 2. Address map (CPU-visible)

```
0x00000000 .. 0x00007FFF   BRAM           (32 KB, cached, exec)
0x10000000 .. 0x13FFFFFF   SDRAM cached   (64 MB, cached, exec)
0x30000000 .. 0x30FFFFFF   CRAM0          (16 MB, uncached)         ← bridge staging
0x40000000 .. 0x7FFFFFFF   MMIO           (uncached, non-exec)
0x50000000 .. 0x53FFFFFF   SDRAM uncached alias (64 MB, uncached)   ← FB writes, interact vars
```

CRAM1 aliases (`0x31xxxxxx`, `0x39xxxxxx`) are gone.  SRAM alias
(`0x3Axxxxxx`) is gone — the chip is no longer CPU-addressable.

## 3. Buses

### BRAM bus
- **One master**: CPU.
- No arbiter.

### SDRAM bus
- **Three masters** on `axi_sdram_arbiter`:
  1. GPU   (span/texture reads + framebuffer writes, merged into one AXI master)
  2. CPU   (i + d + p merged through `cpu_target_port`)
  3. Audio (`audio_dma.v`, read-only)
- **Strict priority**: GPU > CPU > Audio.
  - CPU fairness counter (existing): N consecutive GPU grants while CPU
    pending forces a CPU grant.
  - Audio sits at the bottom — the 16 KB / 85 ms ring absorbs scheduling gaps.
- **Single outstanding** transaction (unchanged).

### CRAM0 bus
- Lives entirely on **clk_74a** (APF bridge clock).
- **Two masters**, time-sliced by a single MMIO mode bit (`CRAM0_MODE`):
  - `CRAM0_MODE = BRIDGE` — bridge has exclusive access (load/save transfers).
  - `CRAM0_MODE = CPU`    — CPU has exclusive access (memcpy in/out of SDRAM).
- **Bridge ↔ CRAM0**: native, same clock, zero CDC, zero arbitration.
  The APF `bridge_rd/wr/addr/data/done` signals drop straight into the
  CRAM0 controller.
- **CPU ↔ CRAM0**: through a small CDC block (`cram0_cdc.v`):
  - CPU side: AXI4 slave on `clk_cpu` (serves CRAM0 reads/writes from
    the CPU's `p_axi` peripheral master).
  - Bridge side: req/ack handshake converted to the same word-level
    interface the bridge uses.
  - An async FIFO is unnecessary — CPU transfers are small (single
    words) and bursty (memcpy in tight loops).  A 4-stage request /
    response synchronizer is enough.
- No arbitration RTL inside the controller — the multiplexer at the
  controller input simply selects whichever side `CRAM0_MODE` names;
  the other side's signals are masked to idle.
- Firmware guarantees a quiescent period between mode flips: drain any
  pending bridge completion before switching to CPU, and the CDC's
  last response before switching to BRIDGE.  A 4-cycle settle at the
  destination clock after the mode bit change is enough to absorb any
  in-flight synchronizer hops.

### SRAM bus
- GPU-private, no AXI wrapper.
- GPU core drives the chip directly (same port shape the Z-buffer uses today).

## 4. Load / unload protocol (bridge ↔ SDRAM via CRAM0)

### Loading a file (OS boot, app ELF, asset, SF2, savegame)

```
CPU: CRAM0_MODE = BRIDGE
CPU: issue bridge read(slot, offset, length, cram0_dest)
CPU: wait for bridge_done
CPU: CRAM0_MODE = CPU
CPU: memcpy(sdram_dst, CRAM0 + cram0_dest, length)
CPU: (optional) CRAM0_MODE = BRIDGE    — leave bridge owning by default
```

### Saving a file

```
CPU: (app writes save payload into SDRAM buffer)
CPU: CRAM0_MODE = CPU
CPU: memcpy(CRAM0 + cram0_src, sdram_buf, length)
CPU: CRAM0_MODE = BRIDGE
CPU: issue bridge write(slot, offset, length, cram0_src)
CPU: wait for bridge_done
```

Under this protocol there is **no time** at which the bridge and CPU
are both driving CRAM0.  No corruption, no arbitration RTL, and the
whole flow is visible and debuggable in C.

## 5. Where each payload lives

| Payload                 | Location                               | Notes                                          |
| ----------------------- | -------------------------------------- | ---------------------------------------------- |
| Bootloader              | BRAM                                   | MIF-initialised with the bitstream             |
| Trap handler            | BRAM (`.text.boot`)                    | Kept in BRAM so traps don't need SDRAM         |
| OS text + rodata        | SDRAM 0x10000000                       | Loaded at boot from CRAM0 (slot 1)             |
| OS .osdata + .bss       | SDRAM                                  | Contiguous with OS text                        |
| OS `.fastdata` hot globals | BRAM                                 | Timer ISR state, voice state, etc.             |
| App text + rodata       | SDRAM (above OS)                       | Loaded via CRAM0 from slot 2                   |
| App heap / mmap         | SDRAM                                  | brk grows up, mmap grows down (unchanged)      |
| App stack               | SDRAM 0x13F00000..0x13F80000 (512 KB)  |                                                |
| OS runtime stack        | SDRAM 0x13F80000..0x14000000 (512 KB)  |                                                |
| Framebuffers (×3)       | SDRAM 0x10000000..0x10300000 (uncached alias for writes) | CPU writes via 0x50xxxxxx; scanout reads from SDRAM |
| Audio DMA ring          | SDRAM, in `.osdata` via static array   | 16 KB, cached writes + cbo.clean, DMA reads    |
| SF2 / sample pool       | SDRAM, loaded once at boot via CRAM0   | ~11 MB; cached reads via L1 D$ burst line fill |
| Save slots              | CRAM0 (bridge scratch)                 | Written/read only during explicit save/load    |
| Z-buffer, span cache    | GPU SRAM                               | Not CPU-addressable                            |

## 6. Boot sequence

1. FPGA loads bitstream — BRAM MIF contains bootloader + trap handler.
2. Bootloader sets `CRAM0_MODE = BRIDGE`.
3. Bootloader waits for APF to push `os.bin` into CRAM0 slot 1.
4. Bootloader flips `CRAM0_MODE = CPU`, `memcpy(SDRAM_OS_BASE, CRAM0 + 0, os_size)`.
5. Bootloader jumps to SDRAM OS entry point.
6. OS runs out of the cached SDRAM alias (L1 I$ + D$).

App load is the same pattern, targeted at slot 2 and the app load
base.

## 7. Simplifications vs today

- **No CRAM1 chip / controller / PHY / BCR init FSM.**
- **No CRAM1 arbiter (bridge + CPU + burst).**
- **No cross-CPU-master arbitration on CRAM0** (single-master at a time
  via mode bit).
- **No cram1_burst_mmio**, no saw-busy gate, no MMIO spin-waits on a
  controller — L1 D$ line fills are the only CRAM1-like burst path and
  they're already gone.
- **Audio DMA is the only non-CPU/GPU master on SDRAM** — bridge no
  longer touches SDRAM directly.
- **SRAM off the fabric entirely** — no axi_sram_slave, no SRAM target
  port.
- **Five AXI masters on SDRAM drop to three**; CRAM1 slaves drop to
  zero.  `cpu_system.v` sheds its CRAM1 and SRAM target ports.
- **Bridge runs CRAM0 at its native clock** — the existing
  `bridge_to_cram1.v`-style CDC (clk_74a → clk_cpu, skid buffer,
  response dcfifo) is retired for the bridge side.  CDC now lives on
  the CPU side, which is the less-frequent path, and the CPU AXI
  traffic to CRAM0 is simple enough that the CDC is 3–4 flops per
  direction instead of a dual-clock FIFO.

## 8. What stays the same

- VexiiRiscv config (caches, Zicbom, branch predictor, bypass).
- SDRAM controller + axi_sdram_slave (proven burst-capable).
- Audio DMA + audio_output dcfifo + I2S serialiser.
- GPU core + its private SRAM Z-buffer port.
- APF bridge protocol and phdp debug pipe.

## 9. Risks / open items

- **SF2 sample pool (11 MB) now lives in SDRAM**, competing with Doom
  for cache and bandwidth.  Expected: sample accesses are small and
  L1-friendly (one cache line covers 16 samples); bandwidth ≈ 0.5 MB/s
  (same as today's CRAM1 burst rate).
- **Mode-switch atomicity**: firmware must make sure the bridge has
  ACK'd its last transfer before flipping `CRAM0_MODE = CPU`, and
  vice versa.  A settle delay measured in clk_74a cycles (not clk_cpu)
  removes any doubt.  The CPU-side CDC adds 2–3 `clk_74a` cycles to
  each CPU access — acceptable because CPU↔CRAM0 is cold-path only.
- **OS in SDRAM means a trap during SDRAM-busy windows can't fetch the
  trap handler from SDRAM** — which is exactly why the trap handler
  stays in BRAM.  Kept as-is.
- **Bootloader must know where in CRAM0 the APF put `os.bin`** —
  either a fixed offset, or a small metadata block at `CRAM0 + 0`
  written by APF before the core is released from reset.
