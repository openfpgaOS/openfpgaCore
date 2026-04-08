# MiSTer Support Proposal

## Summary

Supporting MiSTer is feasible, but not as a thin copy of `targets/pocket`.

The repo already has three good foundations:

- The app ABI is mostly target-agnostic: RISC-V ELF apps, Linux-flavored syscalls, and musl-based userspace.
- Large parts of the RTL are reusable: `src/fpga/common/cpu_system.v`, `src/fpga/common/axi_sdram_slave.v`, `src/fpga/common/axi_periph_slave.v`, the mixer, and scanout blocks.
- The capability and services tables already point in the right direction for cross-target support.

The main blockers are below that line:

- boot and storage are built around the Pocket APF bridge and data-slot model
- packaging is OpenFPGA/APF-specific
- tests and linker/build paths still hardcode Pocket assumptions
- the documented multi-target story is ahead of the actual implementation

The recommended plan is:

1. Stabilize the target contract inside this repo.
2. Add a MiSTer target that uses MiSTer-native top-level I/O (`emu`, `HPS_BUS`, `hps_io`, `CONF_STR`).
3. Replace the Pocket data-slot dependency with a MiSTer-native storage model based on mounted images and `ioctl` transfers, not a custom fork of the MiSTer ARM software.

## Repo Review

### What is genuinely portable today

- `src/fpga/common/cpu_system.v`
- `src/fpga/common/axi_sdram_slave.v`
- `src/fpga/common/axi_periph_slave.v`
- `src/firmware/api/`
- `src/firmware/os/kernel/`
- `src/firmware/os/hal/` for the logic that does not assume Pocket registers or APF host behavior

There is also already an explicit cross-target capability surface:

- `src/firmware/api/of_caps.h`
- `src/firmware/os/kernel/caps_table.c`
- `src/firmware/os/kernel/services_table.c`

That is the right place to hang a MiSTer port from, but the current implementation still fills those tables with Pocket constants.

### What is still Pocket-specific in practice

#### 1. The linker and firmware build are not actually target-neutral

The repo advertises `make TARGET=<name>`, but the kernel linker script still pins Pocket objects and memory layout:

- `src/firmware/os/os.ld`

The firmware Makefile also assumes the full Pocket HAL module set exists for every target:

- `src/firmware/os/Makefile`

This means a second target cannot exist cleanly until the linker script and target module list become target-supplied.

#### 2. Boot is APF/data-slot driven

The current boot flow waits for APF readiness and loads `os.bin` through the Pocket bridge:

- `src/firmware/os/targets/pocket/boot/boot.c`

That is not just "board-specific I/O". It is a different host model.

MiSTer has a different host contract:

- the core top-level is `emu`
- HPS services are exposed through `HPS_BUS` and `hps_io`
- ROM/file loading uses `ioctl_*`
- mounted media is exposed as block devices

Trying to preserve the Pocket boot path on MiSTer would produce a fragile shim instead of a clean port.

#### 3. Runtime file I/O is APF bridge semantics all the way down

The file HAL depends on APF command/ack/done behavior, bridge address translation, data slots, shutdown handshakes, and CRAM bounce buffers:

- `src/firmware/os/targets/pocket/file.c`
- `src/fpga/common/axi_periph_slave.v`
- `dist/core/data.json`

This is the single biggest functional blocker for MiSTer support.

MiSTer documentation is clear that cores do not get raw SD-card access. They get image access and HPS-mediated transfers instead. The standard interfaces are `hps_io` block devices and `ioctl` download/upload, not generic open-by-name filesystem RPC.

#### 4. The FPGA top level is APF/OpenFPGA-specific

`src/fpga/targets/pocket/core_top.v` is built around:

- the APF bridge bus
- Pocket controller buses
- Pocket menu/interact register writes
- Analogizer wiring
- Pocket memory peripherals and clocks

MiSTer needs a different top-level shape entirely. Per MiSTer docs, the natural entry point is an `emu` module with `HPS_BUS`, MiSTer video/audio pins, and `hps_io` wiring.

#### 5. Packaging and configuration are APF-only

The current release format depends on:

- `dist/core/*.json`
- `dist/platforms/*.json`
- `src/chip32/pocket/loader.bin`

That works for Analogue OpenFPGA, not MiSTer.

MiSTer wants an `.rbf` plus MiSTer-native OSD/config behavior through `CONF_STR` and status bits. The current `interact.json` options need a MiSTer translation layer instead of direct reuse.

#### 6. Tests are target-coupled

The Verilator test harness still points directly at `../targets/pocket`:

- `src/fpga/test/Makefile`

So even if a MiSTer target were added, the current test path would continue validating Pocket-specific memory/controller choices.

### Documentation drift that should be fixed before or during the port

The docs describe a cleaner multi-target state than the code actually implements.

Examples:

- `README.md` says adding a target is just `targets/<name>` plus `make TARGET=<name>`.
- `docs/building.md` still documents `src/firmware/apps/example` and static-PIE flags that do not match `src/firmware/api/sdk.mk`.
- `src/firmware/api/app.ld` places apps at `0x10400000`, while `src/firmware/os/os.ld` and `src/firmware/os/kernel/main.c` still describe or reference a different fallback load base.

This is not cosmetic. A second target needs a stable build and memory contract.

## Proposal

## Guiding Principle

Treat MiSTer as a new host plus board target, not just a new Quartus project.

The current `TARGET` abstraction collapses three separate concerns:

- FPGA board wiring and clocks
- host services and file transport
- package and frontend integration

For MiSTer to be maintainable, those concerns need to be explicit in the codebase, even if the public build entry point stays `TARGET=mister`.

## Recommended Target Model

Keep the external build UX simple:

- `make TARGET=pocket`
- `make TARGET=mister`

Internally, split the implementation into two layers:

### 1. Board layer

Responsibilities:

- top-level module
- clocks and resets
- physical memory controllers
- video/audio output wiring
- controller/input wiring

Pocket implementation:

- existing `src/fpga/targets/pocket`

MiSTer implementation:

- new `src/fpga/targets/mister`
- MiSTer-native `emu` top-level
- `HPS_BUS` + `hps_io`
- MiSTer video/audio interfaces
- SDRAM-board wiring first, DDR3 later if needed

### 2. Host-services layer

Responsibilities:

- boot image delivery
- app and asset loading
- save persistence
- configuration/options UI
- shutdown/reset coordination
- optional debug transport

Pocket host-services implementation:

- APF bridge
- data slots
- `interact.json`
- Chip32 loader

MiSTer host-services implementation:

- `CONF_STR` + status bits
- `ioctl_download` / `ioctl_upload`
- mounted image block I/O
- MiSTer OSD file selectors

This is the real port boundary.

## MiSTer Storage Model

This is the most important design choice.

### Do not recommend

A custom MiSTer ARM-side file RPC layer that emulates APF data slots.

Why:

- it depends on nonstandard MiSTer software behavior
- it makes distribution and upstreaming harder
- it recreates a host protocol MiSTer already has standard answers for

### Recommend

Use MiSTer-native mounted images plus `ioctl` transfers.

#### Read-only app/assets image

Package `os.bin`, `app.elf`, and asset files into a mounted image or container that the FPGA core can read through MiSTer block-device interfaces.

Two viable formats:

- a small FAT image
- a simple custom read-only packfile with a tiny kernel-side reader

Recommendation: start with a custom packfile or very small FAT reader only if the asset set really needs directory semantics. Otherwise keep it simpler than full FAT.

#### Save path

For saves, use one of:

- a writable mounted image slot
- `ioctl_upload_req` + upload path for NVRAM/save snapshots

Recommendation: start with a single explicit save image or NVRAM blob. Do not try to replicate Pocket's ten APF save slots on day one.

#### Consequence for the OS

The MiSTer file HAL should not try to preserve the APF data-slot register contract.

Instead it should present the same kernel API upward while implementing reads and writes through:

- block reads from mounted media
- optional upload path for saves
- a target-local file table format inside the mounted package

That keeps app-facing `fopen` behavior intact without requiring a custom HPS filesystem service.

## MiSTer Boot Model

Pocket and MiSTer should not share the same boot mechanism.

### Pocket

Keep the current BRAM bootloader plus APF deferload flow.

### MiSTer

Use an FPGA-side loader state machine driven by `ioctl_download` before releasing the VexRiscv reset.

Recommended sequence:

1. MiSTer loads the core.
2. `emu` instantiates `hps_io`.
3. HPS pushes a boot package or kernel image via `ioctl_download`.
4. FPGA-side loader writes it into SDRAM/BRAM.
5. CPU reset is released.
6. Kernel starts with a MiSTer-specific target descriptor already populated.

This avoids forcing the current Pocket bootloader to learn MiSTer transport semantics.

## Memory Strategy for the First MiSTer Target

Start with the lowest-risk memory story, not the most ambitious one.

### Recommended first target

Require the MiSTer SDRAM board for the initial port.

Why:

- it is closer to the current openfpgaOS model
- it reduces latency risk for the soft CPU
- it lets more of the current AXI SDRAM path survive
- it avoids making DDR3 behavior a blocker for first bring-up

### Mapping changes

MiSTer does not have Pocket CRAM0/CRAM1/Analogizer assumptions.

So the following need to become target data, not shared constants:

- framebuffer base and count
- sample pool base/size
- save scratch region
- uncached alias assumptions
- total RAM sizes

The existing capability table is the correct outward-facing place for this, but the population logic in `caps_table.c` must move behind target-provided values.

## Concrete Refactor Plan

### Phase 0: Make the repo honestly multi-target

Goal: make the existing `TARGET=` switch real.

Changes:

- move linker layout to a target-supplied linker script or linker fragments
- stop hardcoding Pocket objects in `src/firmware/os/os.ld`
- make the target HAL source list declarative instead of fixed in `src/firmware/os/Makefile`
- split APF packaging under `dist/pocket/`
- make tests parameterized by target
- fix the documented app build and memory contract

Deliverable:

- `TARGET=pocket` still works
- the codebase can compile an empty `TARGET=mister` skeleton without editing shared files

### Phase 1: Introduce a real target descriptor

Goal: move platform constants out of shared kernel code.

Changes:

- add a target descriptor struct for:
  - platform id
  - memory map
  - framebuffer layout
  - sample pool layout
  - hardware feature bits
  - CPU frequency
  - boot transport type
- feed `caps_table.c` from that descriptor instead of Pocket constants
- keep `of_caps.h` and `of_services.h` as the stable app contract

Deliverable:

- the kernel no longer assumes Pocket memory windows

### Phase 2: Add MiSTer FPGA skeleton

Goal: bring up a minimal MiSTer target with CPU, framebuffer, input, and OSD status.

Changes:

- add `src/fpga/targets/mister/emu.sv`
- instantiate `hps_io`
- expose MiSTer joystick/status/video/audio wiring
- adapt `cpu_system` and `axi_periph_slave` into the MiSTer top-level
- disable unsupported optional blocks at first:
  - Analogizer
  - link cable
  - Pocket-specific bridge command handlers

Deliverable:

- kernel boots far enough to show a framebuffer or terminal under MiSTer

### Phase 3: Replace APF data-slot file transport

Goal: get `os.bin`, `app.elf`, asset reads, and saves working on MiSTer.

Changes:

- implement MiSTer-side boot image download via `ioctl_download`
- implement mounted-media block reads in `src/firmware/os/targets/mister/file.c`
- define a simple package format for app plus assets
- implement save upload or writable image flushing

Deliverable:

- a basic app loads and can read assets and write saves on MiSTer

### Phase 4: Performance and feature parity

Goal: move from "it runs" to "it is a credible target".

Changes:

- evaluate whether SDRAM-board-only is sufficient or whether DDR3 should be used for bulk assets
- tune cache policy and sample pool placement
- decide whether OPL3 stays enabled by default on MiSTer
- add variant feature gating through `hw_features`
- optionally add MiSTer-native niceties such as RTC, more controllers, or network-backed features

Deliverable:

- a maintainable MiSTer target with explicit feature bits

## Suggested Repo Layout

This is one pragmatic shape, not the only one:

```text
dist/
  pocket/
    core/
    platforms/
  mister/
    conf_str.txt
    package.mk

src/fpga/targets/
  pocket/
  mister/
    emu.sv
    Makefile
    sys/

src/firmware/os/targets/
  pocket/
  mister/
    boot/
    file.c
    input.c
    video.c
    audio.c
    platform.c
    target.ld
```

If desired, the host-services split can later become a separate directory. It does not need to be the first refactor as long as the code is written with that split in mind.

## Recommendation on Scope

For the first MiSTer target, aim for:

- VexRiscv soft CPU
- framebuffer video
- controller input
- basic PCM audio
- app loading
- read-only assets
- one save mechanism

Delay these until after first bring-up:

- Analogizer equivalents
- link cable behavior
- Pocket-style multi-slot save UX
- perfect feature parity with APF packaging
- GPU/variant work beyond what already compiles

## Bottom Line

This repo should support MiSTer, but only after one architectural correction:

the codebase needs to separate "portable openfpgaOS runtime" from "Pocket APF host model".

Once that is explicit, MiSTer becomes a reasonable target:

- the soft CPU stays
- the app ABI stays
- most shared RTL stays
- Pocket-specific boot, packaging, and host transport stop leaking into every layer

That is the path that gives you a real second platform instead of a permanent fork.

## References

- Existing repo architecture plan: `architecture.md`
- MiSTer porting guide: https://mister-devel.github.io/MkDocs_MiSTer/developer/porting/
- MiSTer `hps_io` reference: https://mister-devel.github.io/MkDocs_MiSTer/developer/hps_io/
- MiSTer core configuration string reference: https://mister-devel.github.io/MkDocs_MiSTer/developer/conf_str/
