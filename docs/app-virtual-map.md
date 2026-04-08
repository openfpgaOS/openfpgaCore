# openfpgaOS App Virtual Memory Map v1

This is the binary contract every openfpgaOS target must honor for an
unmodified SDK app `.elf` to run on it. The map is a tuple of CPU
virtual address ranges that an app's `PT_LOAD` segments live in. The
*contents* of each range are abstracted (the app gets actual addresses
for the heap, framebuffer, sample pool, etc. via `of_capabilities`),
but the ranges themselves are fixed so that the same compiled `.elf`
addresses the same memory on every device.

A target whose RTL or boot stage cannot place real RAM at these CPU
addresses cannot run unmodified SDK apps. It can still implement the
OS API, but app authors will need to rebuild against a per-target
linker script. **The whole point of v1 is that this should never
happen.**

## The map

| Range                          | Purpose      | Notes                                                |
|--------------------------------|--------------|------------------------------------------------------|
| `0x00002000 .. 0x00007800`     | `APP_BRAM`   | 22 KB hot code/data section. Optional per-target.    |
| `0x10400000 .. 0x13400000`     | `APP_SDRAM`  | 48 MB app text, data, bss, heap. Mandatory.          |

See [`src/firmware/api/app.ld`](../src/firmware/api/app.ld) for the
linker script that emits these ranges.

### `APP_SDRAM` — mandatory

Every SDK app's text, rodata, data, bss, and heap live here. The
kernel ELF loader rejects any app whose `PT_LOAD` segments fall
outside this range (or `APP_BRAM`). The lower bound `0x10400000`
deliberately leaves the first 4 MB of the SDRAM-mapped window free
for kernel/HAL use; app data starts above it.

A target's bus crossbar must route CPU loads/stores in this range to
some real RAM. The actual physical backing can be any technology
(SDRAM, DDR, on-chip RAM, ...) as long as it is at least 48 MB and
honors normal cached read/write semantics.

### `APP_BRAM` — optional, but binding when used

Apps that opt in via `OF_FASTTEXT` / `OF_FASTDATA` (see
[`of_fastram.h`](../src/firmware/api/of_fastram.h)) get a `PT_LOAD`
segment in this range, intended to be backed by tightly-coupled BRAM
for zero-wait-state hot-path execution.

**The opt-in is binding.** Once an app links a single function or
constant into the FASTTEXT/FASTDATA section, all references to those
symbols use VMA addresses inside `0x00002000-0x00007800`. There is no
runtime relocation pass; the addresses are baked into every call
site at link time. So if a target cannot expose RAM at the v1 BRAM
range, the kernel ELF loader has nowhere to put the segment that
won't immediately crash on the first FASTTEXT call.

The honest behavior:

- **Apps that use `OF_FASTTEXT` / `OF_FASTDATA`** require a target
  that backs the v1 `APP_BRAM` range. The loader rejects them on
  non-conformant targets with error -8.
- **Apps that don't use either macro** have no `APP_BRAM` PT_LOAD,
  so the BRAM region is irrelevant -- the same `.elf` runs on any
  target that honors `APP_SDRAM`.

In practice this means: if you're writing an app you intend to ship
across multiple targets, don't use `OF_FASTTEXT`. Reserve it for
performance-critical code on Pocket where the BRAM window is part of
the contract.

A target signals "no BRAM" by setting `OF_TARGET_APP_BRAM_BASE` and
`OF_TARGET_APP_BRAM_END` to zero (or any range that does not cover
the v1 window) in its `target_platform.h`.

## What this contract does NOT specify

- **Where the OS itself lives.** The OS (`os.bin`) is per-target and
  can sit at whatever CPU address the target's bring-up code places
  it; apps never see those addresses.
- **Where peripherals live.** Apps reach hardware only via the
  services table (function pointers populated by the kernel) or via
  `of_capabilities` fields like `gpu_base`, `sdram_uncached_base`,
  `fb_base`. Peripheral CPU addresses are per-target and never
  compiled into apps.
- **What's at any given offset within `APP_SDRAM`.** The app linker
  script puts text first then data/bss/heap, but apps must read every
  layout-relevant address from `of_capabilities` -- never compute one
  from the v1 base.

## How to add a new target

1. Write `src/firmware/os/targets/<name>/target_platform.h` with
   memory bases that map *something* into the v1 ranges. Reuse the
   Pocket file as a template.
2. Implement the per-target HAL bring-up (boot, video, audio, input,
   ...). Populate the runtime caps descriptor and services table from
   your target's actual hardware.
3. Build `os.bin` for `TARGET=<name>`.
4. Drop unmodified SDK apps into your release folder. They run.

If step 4 fails because the loader rejects a PT_LOAD outside v1, your
target is non-conformant. Either fix the bus map, or accept that
prebuilt apps need a per-target rebuild.

## Versioning

This is **v1**. Any future change that moves a range or adds a new
mandatory range bumps the version. The kernel loader can refuse apps
linked against a future version it doesn't understand. Apps don't
encode the version they were built for in the ELF, so the contract is
"stable until further notice"; the version exists so a future change
has a clear name and a migration story.
