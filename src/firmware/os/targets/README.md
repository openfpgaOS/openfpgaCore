# openfpgaOS Targets

Each subdirectory under `targets/` is one openfpgaOS target. A target
is the per-board glue that lets the shared OS run on a specific
piece of hardware: bring-up code, peripheral HAL, memory map, the lot.

## What a target must provide

1. **`target_platform.h`** -- the memory-map contract consumed by
   `os/hal/platform.{h,c}`. Defines `OF_TARGET_*` macros for SDRAM,
   CRAM, framebuffer, sample pool, GPU MMIO base, runtime stack,
   etc. See [`pocket/target_platform.h`](pocket/target_platform.h)
   for the canonical example.

2. **Boot stage** -- `boot/boot.c` and any startup assembly. Brings
   PLLs/SDRAM/peripheral buses up, initializes the runtime caps and
   services tables, and jumps to the OS.

3. **Per-target HAL .c files** -- `video.c`, `audio.c`, `input.c`,
   `save.c`, `file.c`, `terminal.c`, `timer.c`, etc. Implement the
   `of_*_*` functions declared in `os/hal/*.h` against your target's
   actual hardware.

## What a target must honor

### App Virtual Memory Map v1

Every target's RTL/boot must place real RAM at the CPU addresses
defined by [`docs/app-virtual-map.md`](../../../../docs/app-virtual-map.md).
The kernel ELF loader rejects apps whose `PT_LOAD` segments fall
outside the v1 ranges, so a non-conformant target cannot run any
unmodified SDK app.

| Range                          | What                              |
|--------------------------------|-----------------------------------|
| `0x00002000 .. 0x00007800`     | `APP_BRAM` (optional, has fallback) |
| `0x10400000 .. 0x13400000`     | `APP_SDRAM` (mandatory)           |

If your target's bus crossbar can't put RAM at these addresses, you
either fix the crossbar or accept that prebuilt SDK apps need a
per-target rebuild.

### Boot ABI

The kernel ELF loader hands every app two pointers via auxv:

- `AT_OF_CAPS` -- pointer to the populated `of_capabilities`.
- `AT_OF_SVC`  -- pointer to the populated `of_services_table`.

See [`api/of_app_abi.h`](../../api/of_app_abi.h). The boot stage's
job is to make sure these structs are populated before the app is
launched. The kernel does this in `caps_table_init()` /
`services_table_init()` -- see `kernel/main.c` for the call order.

## Adding a new target

1. `cp -R targets/pocket targets/<name>` and rename internals.
2. Edit `target_platform.h` so the memory map matches your board.
3. Rewrite the `boot/` and per-peripheral `.c` files for your
   actual hardware. Stub anything you don't have (`return -1` is
   fine; the relevant `OF_HW_*` capability bit will be cleared at
   runtime and apps will skip the feature).
4. Build with `TARGET=<name>` (see `src/firmware/os/Makefile`).
5. Drop unmodified SDK apps into your release folder. They run.
