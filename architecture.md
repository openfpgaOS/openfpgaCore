# openfpgaOS Architecture Plan

## 1. Split jump table: libc vs OS services

### Problem
The jump table mixes stable libc functions (malloc, memcpy, strlen) with evolving OS services (mixer, video, file I/O). Adding a mixer feature means bumping OF_LIBC_COUNT, which is really an OS services change — not a libc change.

### Design

Two tables at fixed BRAM addresses:

```
0x7C00  of_libc_table     — stable C library (malloc, string, math, stdio, POSIX I/O)
0x7A00  of_services_table  — OS services (video, mixer, input, timer, audio, file)
```

**of_libc_table** (current table, frozen):
- Slots 0-89: current libc + POSIX functions
- Rarely changes — only when musl functions are added
- Version checked by magic + count at load time

**of_services_table** (new):
```c
struct of_services_table {
    uint32_t magic;       // "OSVC"
    uint32_t version;     // bumped when services change
    uint32_t count;

    // Video
    void    (*video_init)(void);
    uint8_t *(*video_get_surface)(void);
    uint8_t *(*video_flip)(void);
    void    (*video_flip_wait)(void);
    void    (*video_vsync)(void);
    void    (*video_set_palette)(uint8_t, uint8_t, uint8_t, uint8_t);
    // ...

    // Mixer
    int     (*mixer_play)(const uint8_t *, uint32_t, uint32_t, int, int);
    void    (*mixer_stop)(int);
    void   *(*mixer_alloc_samples)(uint32_t);
    // ...

    // Input, Timer, File...
};
```

### Migration path
1. Keep current jump table as-is (backward compat)
2. Add services table at 0x7A00
3. New SDK headers use services table; old apps still work via libc table + syscalls
4. OS services currently using ecall (mixer, video) move to direct function pointers — faster, no trap overhead

### Why not just syscalls for everything?
Syscalls work but cost ~50 cycles per ecall (trap entry/exit, CSR save/restore). For hot paths like `of_video_get_surface()` or `of_input_state()`, a function pointer call is 2 cycles. The services table gives syscall-free access to OS functions without apps needing to link kernel code.

---

## 2. Capability descriptors

### Problem
Apps assume hardcoded memory regions: FB at 0x10000000, samples in CRAM1 at 0x39400000, heap in SDRAM. This ties binaries to one specific FPGA target and memory map.

### Design

The OS passes a capability struct to the app at launch (in a register or at a fixed BRAM address):

```c
struct of_capabilities {
    uint32_t magic;           // "CAPS"
    uint32_t version;

    // Memory regions (base + size, 0 = not available)
    uint32_t heap_base;       // App heap (SDRAM)
    uint32_t heap_size;
    uint32_t fb_base;         // Framebuffer (SDRAM)
    uint32_t fb_size;
    uint32_t fb_width;
    uint32_t fb_height;
    uint32_t fb_stride;
    uint32_t sample_base;     // Audio sample pool (CRAM1)
    uint32_t sample_size;

    // Hardware features (bitmask)
    uint32_t hw_features;     // See HW_* flags below
    uint32_t mixer_voices;    // 0 = no mixer, 32 = full
    uint32_t mixer_rate;      // Output sample rate (48000)

    // Platform identity
    uint32_t platform_id;     // PLATFORM_POCKET, PLATFORM_MISTER, etc.
    uint32_t core_variant;    // Bitstream variant (e.g., "full", "lite", "3d")
    uint32_t sdram_size;      // Total SDRAM (64MB Pocket, 32MB/128MB MiSTer)
    uint32_t cram_size;       // Per-bank CRAM/BRAM (16MB Pocket, varies MiSTer)

    // OS info
    uint32_t os_version;
    uint32_t cpu_freq_hz;
    uint32_t libc_table;      // Address of libc jump table
    uint32_t services_table;  // Address of OS services table
};

// Platform IDs
#define PLATFORM_POCKET     1
#define PLATFORM_MISTER     2
#define PLATFORM_DE10NANO   2  // alias
#define PLATFORM_SIM        255

// Hardware feature flags — app checks before using optional hardware
#define HW_MIXER        (1 << 0)   // PCM hardware mixer present
#define HW_OPL3         (1 << 1)   // OPL3 FM synthesis
#define HW_LINK         (1 << 2)   // Link cable / serial port
#define HW_ANALOGIZER   (1 << 3)   // Analog video output
#define HW_GPU_2D       (1 << 4)   // 2D sprite/tile engine (future)
#define HW_GPU_3D       (1 << 5)   // 3D rasterizer (future)
#define HW_MIDI         (1 << 6)   // MIDI I/O
#define HW_WIFI         (1 << 7)   // Network (MiSTer ESP32, etc.)
#define HW_FPU          (1 << 8)   // Hardware FPU (RISC-V F extension)
#define HW_SAVE_SLOTS   (1 << 9)   // Persistent save storage
```

### Where it lives
Fixed BRAM address (e.g., 0x7800). The loader populates it before jumping to the app. Apps read it at startup:

```c
// SDK header
#define OF_CAPS ((const struct of_capabilities *)0x7800)

// App init
void *heap = (void *)OF_CAPS->heap_base;
int width = OF_CAPS->fb_width;
```

### Migration path
1. Add the struct, populate from loader — no app changes needed yet
2. SDK convenience functions (`of_video_get_surface()`) already abstract the addresses
3. Future targets (DE10-Nano, MiSTer, etc.) just populate different values
4. Apps that use the SDK API transparently work on any target

### What this replaces
- Hardcoded `FB0_BASE`, `FB1_BASE`, `FB2_BASE` in regs.h (app side)
- Hardcoded `CRAM1_UNCACHED + 0x00400000` sample pool base
- Hardcoded `SDRAM_BASE` heap assumptions
- Feature detection by trial and error

### App-side feature gating
```c
// App gracefully adapts to available hardware
void init_audio(void) {
    if (OF_CAPS->hw_features & HW_OPL3) {
        init_opl3_music();
    } else if (OF_CAPS->hw_features & HW_MIXER) {
        init_pcm_music();   // MOD/WAV fallback
    }
    // else: no audio — game still runs
}

void init_graphics(void) {
    if (OF_CAPS->hw_features & HW_GPU_3D) {
        use_hardware_renderer();
    } else {
        use_software_renderer();
    }
}
```

---

## 4. Multi-target support (Pocket, MiSTer, future)

### Problem
The codebase is Pocket-specific: SDRAM size, CRAM layout, bridge protocol, PLL clocks, I/O pin assignments. Porting to MiSTer (DE10-Nano) or other FPGA boards requires touching code at every level.

### Current structure
```
src/fpga/common/           — portable RTL (mixer, scanout, periph_slave)
src/fpga/targets/pocket/   — Pocket-specific (core_top, PLLs, bridge, PSRAM)
src/firmware/os/hal/       — portable HAL (mixer.c, cache.c)
src/firmware/os/targets/pocket/  — Pocket-specific (video.c, audio.c, file.c)
```

This separation already exists — the `common/` vs `targets/` split is the right foundation.

### What a MiSTer target needs

| Layer | Pocket | MiSTer | Shared |
|-------|--------|--------|--------|
| SDRAM controller | io_sdram.v (custom) | MiSTer SDRAM controller | axi_sdram_slave.v |
| PSRAM (CRAM) | psram.sv (CRAM0/1) | BRAM or HPS DDR3 | None — abstracted |
| Video output | Pocket scaler protocol | MiSTer direct video | video_CRT_scanout.v |
| Audio output | I2S to Pocket DAC | MiSTer HDMI/I2S | audio_mixer.v |
| Bridge/file I/O | APF bridge (clk_74a) | HPS Linux file I/O | syscall interface |
| CPU system | VexiiRiscv (identical) | VexiiRiscv (identical) | Everything |

### New target structure
```
src/fpga/targets/mister/
    core_top.v            — MiSTer top-level, HPS bridge, SDRAM
    mister_constraints.sdc
    pll_mister.v
src/firmware/os/targets/mister/
    video.c               — MiSTer video output
    audio.c               — MiSTer audio path
    file.c                — HPS file access (Linux sockets or shared memory)
```

### Key insight: the CPU system is portable
`cpu_system.v` + `axi_periph_slave.v` + `audio_mixer.v` + `video_CRT_scanout.v` are target-independent. Only the top-level wiring and I/O controllers change. The firmware HAL layer (`hal/*.c`) is already abstracted — each target implements `of_video_init`, `of_file_read`, etc.

Apps don't change at all. They see the same jump table, same syscalls, same capabilities struct. The `platform_id` field tells them where they're running if they care.

---

## 5. Core variants (same target, different hardware)

### Problem
FPGA resources are finite. A "full" bitstream with OPL3 + mixer + link + analogizer may not fit or may fail timing. Users want to choose: "I don't need OPL3, give me more BRAM for samples" or "I want a 3D GPU but can drop the link cable."

### Design

Core variants are different bitstreams for the same target, with different `hw_features`:

```
cores/
    openfpgaOS-full.rbf       — mixer + OPL3 + link + analogizer
    openfpgaOS-lite.rbf       — mixer only (better timing, smaller)
    openfpgaOS-3d.rbf         — mixer + GPU3D (no OPL3, uses those ALMs for rasterizer)
```

Each bitstream populates `of_capabilities.hw_features` and `core_variant` at boot. The OS kernel is the same binary — it probes `hw_features` to know what hardware to initialize:

```c
// hal.c
void of_init(void) {
    of_cache_init();
    of_timer_init();
    of_video_init();
    of_term_init();
    of_input_init();
    if (CAPS->hw_features & HW_MIXER) of_audio_init();
    if (CAPS->hw_features & HW_OPL3)  of_opl_init();
    if (CAPS->hw_features & HW_LINK)  of_link_init();
    of_file_init();
    of_save_init();
    if (CAPS->hw_features & HW_ANALOGIZER) of_analogizer_init();
}
```

### How the FPGA reports its variant
A read-only register in `axi_periph_slave.v` populated by a localparam:

```verilog
localparam CORE_HW_FEATURES = 32'h0000_030F;  // set per variant at synthesis
// Read at offset 0x98:
6'b100110: sysreg_rdata = CORE_HW_FEATURES;
```

The bootloader reads this register and populates `of_capabilities.hw_features`. No firmware rebuild needed per variant — the same firmware binary reads the hardware's self-description.

### Build system
```makefile
# Makefile
variant-full:  FEATURES="-DHW_FEATURES=0x30F"
variant-lite:  FEATURES="-DHW_FEATURES=0x001"
variant-3d:    FEATURES="-DHW_FEATURES=0x031"
```

Each variant `ifdef`s the optional RTL modules. Quartus synthesizes only what's included.

### What this enables
- Users pick the variant that fits their needs
- Developers test on lite (fast compile, good timing) and ship on full
- 3D GPU experiments don't risk breaking audio/link
- Same app binary runs on all variants — degrades gracefully via `hw_features`

---

## Implementation priority (updated)

| Feature | Effort | Impact | Dependencies |
|---------|--------|--------|--------------|
| **Capability struct** | Small | High | None |
| **HW_FEATURES register** | Tiny | High | Capability struct |
| **Vsync callback** | Small | Medium | IRQ mask (done) |
| **Mixer voice-end callback** | Small | Medium | IRQ mask (done) |
| **Services table** | Medium | High | Capability struct |
| **MiSTer target skeleton** | Medium | High | Capability struct, target split |
| **Core variants build** | Medium | Medium | HW_FEATURES register |
| **Async file read** | Medium | High | Kernel state machine |

Start with capability struct + HW_FEATURES register. Everything else builds on them.

### Problem
Apps currently block on file I/O (`fread` → ecall → bridge DMA → spin-wait) and vsync (`of_video_wait_flip` → spin on register). While waiting, the CPU does nothing useful. Adding threads would solve this but explode complexity.

### Design

Event-driven callbacks, extending the existing timer callback pattern:

```c
// Already exists
void of_timer_set_callback(void (*cb)(void), int hz);

// New: vsync callback — called once per vblank from kernel context
void of_video_set_vsync_callback(void (*cb)(void));

// New: voice-end callback — called when any mixer voice finishes
// The IRQ mask register and irq.c dispatcher are already wired for this
void of_mixer_set_end_callback(void (*cb)(uint32_t ended_mask));

// New: async file read — DMA runs in background, callback on completion
// Returns a token; callback receives the token + byte count
int of_file_read_async(int fd, void *buf, uint32_t count,
                       void (*cb)(int token, int bytes_read));
```

### Implementation

**Vsync callback** (easy — infrastructure exists):
- `axi_periph_slave.v` already generates `vsync_rising`
- The periph slave's vsync detection runs in clk_cpu — just fire the callback from the idle hook in `file_wait_complete`, or from a new vsync IRQ source added to the IRQ mask register
- Apps register a callback; kernel calls it at each vblank

**Mixer voice-end callback** (easy — hardware ready):
- `IRQ_MASK` bit 2 enables `mix_voice_end_irq`
- `irq.c` already dispatches mcause 11 to `external_cb`
- The callback receives the pending bitmask (which voices ended)
- App drains ended voices, queues new ones — zero polling

**Async file read** (moderate — needs kernel state machine):
- Current flow: ecall → `sys_read` → `io_cache_fill` → `file_wait_complete` (spin)
- Async flow: ecall → `sys_read_async` → set up DMA → return immediately
- Kernel checks DMA completion in the idle hook (already called during other waits)
- When done: kernel calls the app's callback via saved function pointer
- Only one async read in flight at a time (bridge is single-command)

### What this enables
- **Game loop without vsync spin**: register vsync callback, do game logic, callback flips the buffer
- **Streaming audio**: mixer voice-end callback triggers loading the next chunk
- **Background loading**: start a file read, render a loading animation, callback signals completion

### What this does NOT add
- No scheduler, no threads, no preemption
- Callbacks run in kernel context (like timer ISR) — must be short
- One async file op at a time (hardware limitation, not a software choice)
- No cancellation — once a DMA starts, it finishes

### Migration path
1. **Phase 1**: Vsync callback + mixer voice-end callback (both trivial, hardware ready)
2. **Phase 2**: Async file read (needs kernel state machine, more work)
3. Existing blocking APIs stay — apps opt in to async when they need it

---

## Implementation priority

| Feature | Effort | Impact | Dependencies |
|---------|--------|--------|--------------|
| Capability struct | Small | High | None — just a struct in BRAM |
| Vsync callback | Small | Medium | IRQ mask register (done) |
| Mixer voice-end callback | Small | Medium | IRQ mask register (done) |
| Services table | Medium | High | Capability struct (for table address) |
| Async file read | Medium | High | Kernel state machine refactor |

Start with the capability struct — it's the foundation for everything else and costs almost nothing.
