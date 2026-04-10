# ofOS Architecture

## Overview

ofOS is a minimal operating system for the Analogue Pocket FPGA handheld. It runs on a VexiiRiscv RISC-V soft CPU (rv32imafc @ 100 MHz) synthesized on the Cyclone V 5CEBA4F23C8 FPGA. The system uses a two-stage architecture: a BRAM-resident bootloader/trap handler and an SDRAM-resident OS kernel that loads and executes application ELF binaries.

## CPU

- **Core:** VexiiRiscv (SpinalHDL)
- **ISA:** rv32imafc (integer, multiply/divide, atomics, single-precision FPU, compressed instructions)
- **Clock:** 100 MHz
- **Bus:** AXI4, 3-bus architecture (FetchL1, LsuL1, LsuIO)
- **I-Cache:** 8KB, direct-mapped (128 sets × 1 way × 64B)
- **D-Cache:** 32KB, direct-mapped (512 sets × 1 way × 64B, write-back)
- **MMU:** None (bare-metal, M-mode only)
- **Privilege level:** M-mode only (all code runs in machine mode)

## Memory Map

| Address Range             | Size   | Description                              |
|---------------------------|--------|------------------------------------------|
| `0x00000000 - 0x00007FFF` | 32 KB  | BRAM -- bootloader, trap handler, hot code |
| `0x10000000 - 0x100FFFFF` | 1 MB   | Framebuffer 0 (320x240, 8-bit indexed)   |
| `0x10100000 - 0x101FFFFF` | 1 MB   | Framebuffer 1 (triple buffer)            |
| `0x10200000 - 0x102FFFFF` | 1 MB   | Framebuffer 2 (triple buffer)            |
| `0x10280000 - 0x102FFFFF` | 512 KB | DMA bounce buffer                        |
| `0x10300000 - 0x103FFFFF` | 1 MB   | OS kernel (code + data + BSS)            |
| `0x10400000+`             | ~48 MB | Application load area + heap             |
| `0x39000000 - 0x3927FFFF` | 2.5 MB | Save region (10 × 256 KB slots, CRAM1 uncached) |
| `0x13F80000`              |        | Application stack top                    |
| `0x14000000`              |        | Runtime stack top                        |
| `0x20000000`              | 1.2 KB | Terminal VRAM (40x30 characters)         |
| `0x30000000 - 0x30FFFFFF` | 16 MB  | PSRAM                                    |
| `0x40000000`              | 256 B  | System registers                         |
| `0x4C000000`              | 8 B    | Audio FIFO                               |
| `0x4D000000`              | 256 B  | Link cable MMIO                          |
| `0x4E000000`              | 16 B   | OPL3 registers (YMF262, 2 banks)         |
| `0x50000000 - 0x53FFFFFF` | 64 MB  | SDRAM uncached alias (D-cache bypass)    |
| `0xF7000000`              |        | Analogizer bridge registers              |

## Boot Flow

```
Power On
  |
  v
1. FPGA configures from bitstream
  |
  v
2. BRAM bootloader (_start in start.S)
   - Set up stack at top of BRAM (0x10000)
   - Enable FPU (mstatus.FS = Dirty)
   - Set mtvec to _start (trap handler)
   - Initialize thread pointer (tp) for TLS
   - Clear BRAM BSS
   - Call main() (boot.c)
  |
  v
3. boot.c: main()
   - Wait for APF bridge allcomplete signal
   - Load os.bin from data slot 1 via deferload DMA
   - fence + fence.i (flush D-cache, invalidate I-cache)
   - Clear OS BSS in SDRAM
   - Jump to os_main() at 0x10200000
  |
  v
4. kernel/main.c: os_main()
   - hal_init() -- initialize all hardware subsystems
   - Display boot banner on terminal
   - Initialize syscall subsystem (fd table, brk)
   - Load application ELF from data slot 2
   - Process ELF headers, PT_LOAD segments, relocations
   - Switch to framebuffer mode
   - elf_exec() -- set up stack, jump to app entry
  |
  v
5. Application runs
   - Accesses hardware via ecall syscalls
   - OS trap handler dispatches to HAL
```

## OS / Application Separation

The OS and applications are separate binaries with a clean syscall boundary:

```
+-----------------------------------------------------+
|  Application (.elf)                                  |
|  - Compiled as static-PIE (position-independent)     |
|  - Accesses hardware via ecall (pocket.h wrappers)   |
|  - Loaded at 0x10400000 by OS ELF loader             |
+-----------------------------------------------------+
                    | ecall (trap)
                    v
+-----------------------------------------------------+
|  ofOS Kernel (os.bin at 0x10200000)                  |
|  - Trap handler dispatches syscalls                  |
|  - HAL implements hardware access                    |
|  - Linux-compatible syscall ABI for musl             |
|  - Custom 0x1000+ range for Pocket HAL               |
+-----------------------------------------------------+
                    |
                    v
+-----------------------------------------------------+
|  FPGA Hardware                                       |
|  - MMIO registers at 0x40000000+                     |
|  - DMA via APF bridge                                |
|  - Framebuffer, audio FIFO, OPL3, link cable         |
+-----------------------------------------------------+
```

### Syscall Mechanism

Applications invoke hardware functionality via the RISC-V `ecall` instruction:

1. App places syscall number in `a7`, arguments in `a0`-`a5`
2. `ecall` triggers trap (mcause = 11, environment call from M-mode)
3. Trap handler in `start.S` saves full register context (32 int + 32 float + CSRs)
4. Dispatches to `syscall_dispatch()` in C
5. Return value placed in `a0`, `mepc` advanced by 4
6. Context restored, `mret` returns to app

### ELF Loading

The OS loads applications as ELF32 static-PIE binaries:

1. Read ELF header from data slot 2 via DMA
2. Validate magic, machine (EM_RISCV), type (ET_DYN for PIE)
3. Process PT_LOAD segments: DMA file contents to SDRAM, zero BSS
4. Parse PT_DYNAMIC section for relocation tables
5. Process R_RISCV_RELATIVE relocations: `*(base + offset) = base + addend`
6. Flush D-cache and invalidate I-cache
7. Set up initial stack (argc, argv, envp, auxv) and jump to entry

## Cache Coherency

The system has no hardware cache coherency mechanism. Software manages coherency:

- **D-cache flush:** Read 32KB from the top of SDRAM to evict all dirty lines from the 32KB direct-mapped D-cache
- **I-cache invalidate:** `fence.i` instruction
- **Uncached alias:** SDRAM addresses at 0x50000000 bypass D-cache entirely
- **DMA coherency:** Fence before DMA operations; use uncached alias to read DMA results
- **Save coherency:** Save reads/writes use uncached alias so the APF bridge sees consistent data
- **Framebuffer swap:** D-cache flushed before requesting vsync swap so video scanout sees latest pixels

## HAL Subsystems

The HAL is organized into 11 independent modules:

| Module | Header | Description |
|--------|--------|-------------|
| `fb` | `hal/fb.h` | Framebuffer (320x240, 8-bit indexed, triple-buffered) |
| `audio` | `hal/audio.h` | PCM audio FIFO + OPL3 FM synthesis |
| `input` | `hal/input.h` | Controller polling with edge detection (2 players) |
| `save` | `hal/save.h` | Nonvolatile save slots (10 × 256 KB, CRAM1 PSRAM) |
| `dataslot` | `hal/dataslot.h` | APF bridge file I/O (DMA read/write) |
| `analogizer` | `hal/analogizer.h` | Analogizer state query (video mode, SNAC type) |
| `terminal` | `hal/terminal.h` | Text terminal (40x30, printf support) |
| `timer` | `hal/timer.h` | Cycle counter, microsecond/millisecond timing |
| `cache` | `hal/cache.h` | D-cache flush, I-cache invalidate |
| `link` | `hal/link.h` | Link cable serial I/O |
| `regs` | `hal/regs.h` | Hardware register definitions and memory map constants |

See [HAL API Reference](hal-api.md) for the complete API.
