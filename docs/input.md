# Unified input pipeline - IRQ-first plan

Status: proposal. This supersedes the earlier keyboard/mouse-only dock
plan in `/home/alberto/Repos/input.md`.

The goal is one input subsystem that accepts all physical input sources:

- Pocket/APF controller slots, including built-in controls and docked
  controllers.
- Docked USB keyboard and mouse, when exposed by APF controller roles.
- Analogizer/SNAC input, including the existing software shifter path.
- Future sources without another public API split.

The target programming model should look like a PC input stack: hardware
raises an interrupt when input data is ready, a short ISR/service routine
captures it into a bounded event queue and current-state cache, and apps
either consume events or read snapshots. Existing `of_input_poll()` and
`OF_BTN_*` APIs remain as compatibility views over the same state cache.

## Current State

- `src/firmware/os/targets/pocket/input.c` reads only `CONT1_*` and
  `CONT2_*`. If SNAC is active it polls the software SNAC driver and
  merges that result into the two-player state according to Analogizer
  assignment bits.
- `src/firmware/api/of_input.h` is poll based. It exposes two static
  controller snapshots and helpers such as `of_btn_pressed()`. It has no
  event queue and no representation for keyboard scan codes, relative
  mouse motion, hotplug, or more than two logical players.
- `src/fpga/targets/pocket/core_top.v` has `cont1_*` through `cont4_*`
  ports, but only `cont1_*` and `cont2_*` are wired into
  `axi_periph_slave`.
- `src/fpga/common/axi_periph_slave.v` exposes only `CONT1_*` and
  `CONT2_*` at `SYSREG_BASE + 0x50..0x64`. It already synchronizes those
  inputs into the CPU clock domain with `synch_3`.
- The machine external IRQ line is a masked OR of UART RX, link RX, and
  vsync. Firmware source bits are currently `UART_RX`, `LINK_RX`, and
  `VSYNC`.
- The old proposed `CONT3/CONT4` register block at `0x68..0x78` is not
  valid anymore: `0x68` is `SYS_GAME_ID`, and `0x70` is
  `SYS_COLOR_MODE`.

## Design Principles

1. Keep physical devices separate from logical players.

   APF slot 0, APF slot 1, dock keyboard, dock mouse, dock gamepad, and
   SNAC port 0 are physical inputs. Player 1 and Player 2 are logical
   mappings. The mapper can project a physical controller onto
   `of_input_state_t`, but keyboard and mouse should not be forced into
   the 16-bit gamepad bitmap unless an app explicitly requests that.

2. Make events the native internal contract.

   The firmware should normalize every source into events, then update
   current-state caches from those events. Snapshot APIs become reads of
   that cache. This avoids separate keyboard, mouse, controller, and SNAC
   code paths that drift over time.

3. Use IRQ first, with bounded polling fallback.

   APF slot changes and dock HID reports should raise an input IRQ. SNAC
   protocols are host-driven serial protocols, so they may still require
   a timer or frame poll unless a hardware SNAC poll engine is added.
   Even then, SNAC results should enter the same event queue and state
   cache.

4. Preserve the existing controller ABI.

   `of_input_poll()`, `of_input_poll_p0()`, `of_btn()`,
   `of_btn_pressed()`, and `of_input_state_t` must keep working. Existing
   apps should not need to know whether the state was updated by IRQ or
   by a fallback poll.

5. Avoid hard-coded dock roles in the low-level pipeline.

   Do not assume `cont3` is always keyboard and `cont4` is always mouse.
   Treat APF controller slots as typed sources. The `core.json` role
   declaration or APF metadata decides whether a slot is a pad, keyboard,
   mouse, or other HID-like source.

## Target Architecture

```
                APF cont1..cont4
                     |
                     v
             RTL input hub/MMIO
          raw slot state + IRQ/data-ready
                     |
                     v
          machine external IRQ source
                     |
                     v
       firmware input service / ISR hook
          source parsers + event queue
                     |
                     v
      canonical state caches and mapper
       |             |              |
       v             v              v
 gamepad API   keyboard API    mouse API
 compatibility event API       device API
```

Analogizer/SNAC feeds the same firmware input service. In the current
implementation it starts from `snac_poll()` rather than a hardware IRQ,
but it should still generate normalized events before updating logical
player state.

## RTL Plan

### 1. Add an input hub in `axi_periph_slave`

Wire all four APF controller slots into the peripheral slave:

- Add `cont3_key`, `cont3_joy`, `cont3_trig`.
- Add `cont4_key`, `cont4_joy`, `cont4_trig`.
- Synchronize them with the same `synch_3` pattern already used for
  slots 1 and 2.

Current openfpgaOS does not need `core_bridge_cmd.v` for this path.
`core_top.v` receives the APF controller ports directly and passes them
to `axi_periph_slave`.

### 2. Use a new input register page

Do not reuse `SYSREG_BASE + 0x68..0x78`. Use a non-conflicting page under
the system-register aperture, for example `SYSREG_BASE + 0x100`.

Proposed v1 register map:

| Offset | Name | Description |
|--------|------|-------------|
| `0x100` | `INPUT_STATUS` | pending bits, overflow bit, global sequence |
| `0x104` | `INPUT_IRQ_MASK` | enables input sub-sources |
| `0x108` | `INPUT_IRQ_CLEAR` | write-1-to-clear pending bits |
| `0x10C` | `INPUT_SEQ` | increments on every accepted input change |
| `0x110` | `INPUT_SLOT0_INFO` | present/type/role flags for APF slot 0 |
| `0x114` | `INPUT_SLOT0_KEY` | raw key/button word |
| `0x118` | `INPUT_SLOT0_JOY` | raw joy/mouse data word |
| `0x11C` | `INPUT_SLOT0_TRIG` | raw trigger word |
| `0x120` | `INPUT_SLOT1_INFO` | present/type/role flags for APF slot 1 |
| `0x124` | `INPUT_SLOT1_KEY` | raw key/button word |
| `0x128` | `INPUT_SLOT1_JOY` | raw joy/mouse data word |
| `0x12C` | `INPUT_SLOT1_TRIG` | raw trigger word |
| `0x130` | `INPUT_SLOT2_INFO` | present/type/role flags for APF slot 2 |
| `0x134` | `INPUT_SLOT2_KEY` | raw key/button word |
| `0x138` | `INPUT_SLOT2_JOY` | raw joy/mouse data word |
| `0x13C` | `INPUT_SLOT2_TRIG` | raw trigger word |
| `0x140` | `INPUT_SLOT3_INFO` | present/type/role flags for APF slot 3 |
| `0x144` | `INPUT_SLOT3_KEY` | raw key/button word |
| `0x148` | `INPUT_SLOT3_JOY` | raw joy/mouse data word |
| `0x14C` | `INPUT_SLOT3_TRIG` | raw trigger word |

The exact type/role bits depend on the APF contract. If APF does not
provide live role metadata to RTL, firmware can derive slot roles from a
compile-time manifest expectation and keep `INPUT_SLOTn_INFO` minimal:
present, changed, and raw-valid bits.

### 3. Add input IRQ pending

Add a new external IRQ source for input data ready:

- In RTL, add `input_irq_pending` and include it in `ext_irq` when
  enabled.
- Keep existing source bit positions stable. Use a new bit such as
  bit 4:
  - `IRQ_MASK_UART_RX = 1 << 0`
  - `IRQ_MASK_LINK = 1 << 1`
  - bit 2 remains reserved
  - `IRQ_MASK_VSYNC = 1 << 3`
  - `IRQ_MASK_INPUT = 1 << 4`
- In firmware, add matching `IRQ_SRC_INPUT`.

The simplest v1 pending source is "any synchronized raw slot value
changed since the last acknowledged snapshot." That sharply reduces
input latency compared with frame polling and gives the firmware a
single data-ready signal.

Be explicit about the limitation: if APF provides only current state and
not a HID event FIFO, hardware cannot reconstruct very fast
press/release transitions that are overwritten before the FPGA observes
them. For true PC-like keyboard and mouse fidelity, the earliest point
that sees individual reports must queue them. If APF exposes HID events,
prefer a small RTL FIFO or APF-backed event read path over pure current
state diffing.

### 4. Optional hardware event FIFO

The flexible version of the input hub adds a tiny hardware FIFO. This
should be a new input-specific FIFO, not a reused GPU/audio/UART FIFO:

- Do not reuse the GPU ring. It is a command-stream BRAM with GPU-owned
  pointer semantics.
- Do not reuse the audio `dcfifo`. It is a dual-clock megafunction for
  the CPU-to-audio clock crossing and is oversized/wrong-shaped for
  input reports.
- Do not share the UART RX FIFO instance. It is byte-oriented and has
  read-pop behavior specific to the UART MMIO block.
- Reuse the pattern, not the instance: a small parameterized synchronous
  FIFO module is appropriate here and can later replace other ad hoc
  same-clock queues if desired.

The input FIFO should sit at the input-hub boundary and queue raw input
reports or compact "slot changed" records before firmware normalization:

- Event words are produced when an APF slot changes or when APF exposes a
  discrete HID report.
- Firmware drains the FIFO in the input IRQ service.
- Overflow sets `INPUT_STATUS.overflow` and emits or synthesizes an
  overflow event in firmware.

Recommended v1 sizing is 32 entries, with a payload that can be read as
two or four 32-bit MMIO words. Keep the public event queue in firmware as
a separate software ring; the RTL FIFO is only the hardware-to-kernel
handoff.

If APF exposes only current state and no report strobe or HID event
queue, a FIFO cannot recover transitions that were overwritten before
the FPGA observed them. In that case, still keep the IRQ-on-change path,
but treat the RTL FIFO as a latency/decoupling improvement rather than a
full input-history guarantee.

## Firmware Plan

### 1. Add an input core

Create a small target-independent input core under the OS layer. It owns:

- A bounded single-producer/single-consumer event ring.
- Current physical device states.
- Current logical player states.
- The mapper from physical sources to logical players.
- Overflow accounting.

Pocket-specific code becomes a source driver for that core.

Recommended internal event shape:

```c
typedef enum {
    OF_INPUT_EVENT_DEVICE,
    OF_INPUT_EVENT_BUTTON,
    OF_INPUT_EVENT_AXIS,
    OF_INPUT_EVENT_KEY,
    OF_INPUT_EVENT_POINTER,
    OF_INPUT_EVENT_WHEEL,
    OF_INPUT_EVENT_OVERFLOW
} of_input_event_type_t;

typedef enum {
    OF_INPUT_SOURCE_APF_SLOT,
    OF_INPUT_SOURCE_DOCK_HID,
    OF_INPUT_SOURCE_ANALOGIZER_SNAC
} of_input_source_kind_t;

typedef struct {
    uint32_t seq;
    uint32_t timestamp_us;
    uint8_t  type;
    uint8_t  source_kind;
    uint8_t  source_index;
    uint8_t  device_type;
    uint8_t  logical_player;   /* 0xff when not mapped to a player */
    uint8_t  flags;
    uint16_t code;             /* button id, HID usage, axis id, etc. */
    int16_t  value;
    int16_t  value2;
    uint32_t buttons;
} of_input_event_t;
```

Keep this packed and ABI-conscious if it becomes public SDK API. The
internal struct can evolve first; the public event ABI should be added
only once the source/type fields are stable.

### 2. Service input from the external IRQ

Update the IRQ dispatcher:

- Add `IRQ_SRC_INPUT` in `src/firmware/os/kernel/irq.h`.
- In `irq_handler()`, detect `INPUT_STATUS.pending`.
- Call a Pocket input service hook or set `IRQ_SRC_INPUT` for the common
  external callback.
- Clear input pending only after the service has captured the raw slot
  state or drained the input FIFO.

The input service should:

1. Read `INPUT_STATUS` and any changed raw slot registers.
2. Parse each source according to its role/type.
3. Generate normalized events into the ring.
4. Update physical-device states.
5. Run the mapper and update `of_input_states[]`.
6. Acknowledge/clear the hardware pending bits.

For v1, if there is no hardware FIFO, the service can diff each slot's
new snapshot against its previous snapshot. For a pad, this creates
button and axis events. For a keyboard bitmap, this creates key up/down
events. For a mouse report, this creates pointer, wheel, and button
events.

### 3. Keep polling as compatibility and fallback

`of_input_poll()` should remain valid, but it should no longer be the
only way input advances. Its new job:

- Drain or service any pending input work if IRQs are disabled.
- Poll SNAC when the current SNAC backend requires host-driven sampling.
- Return the latest logical player snapshots.

`of_input_poll_p0()` can remain a fast path over the same cache.

### 4. Merge Analogizer/SNAC into the same pipeline

Current SNAC handling in `src/firmware/os/targets/pocket/input.c` is a
special branch that bypasses the normal APF path. Replace that with a
source driver:

- `snac_poll()` samples one or two SNAC controllers.
- The SNAC driver compares new SNAC state to previous SNAC state.
- Changes are pushed as normal button/axis events with
  `source_kind = OF_INPUT_SOURCE_ANALOGIZER_SNAC`.
- The existing Analogizer assignment bits select how SNAC physical ports
  map to logical players.

This keeps the existing behavior but removes the special-case merge from
the public controller API path.

## SDK/API Plan

### 1. Preserve the existing gamepad API

Keep:

- `of_input_state_t`
- `of_input_poll()`
- `of_input_poll_p0()`
- `of_btn()`
- `of_btn_pressed()`
- `of_btn_released()`
- `OF_BTN_*`

These become snapshot views of the unified input core.

### 2. Add a generic event API

Add a new header such as `of_input_events.h` or extend `of_input.h` with
append-only service calls:

```c
int of_input_event_pop(of_input_event_t *out);
int of_input_event_peek(of_input_event_t *out);
uint32_t of_input_event_dropped(void);
```

Avoid blocking waits until the scheduler story is clear. A future
`of_input_wait_event(timeout)` can be added after the IRQ/event path is
stable.

### 3. Add typed convenience views

Keyboard and mouse APIs should be thin views over the unified state, not
separate pipelines:

```c
int of_key_held(uint16_t hid_usage);
int of_key_pressed(uint16_t hid_usage);
int of_key_released(uint16_t hid_usage);

void of_mouse_get_state(of_mouse_state_t *out);
int of_mouse_present(void);
```

For controllers, consider a future `of_input_get_player_state(player,
out)` that supports more than two logical players internally while
leaving the old two-player helpers intact.

## APF/Core Metadata

Verify the APF controller role schema before committing the public API.
The design should support at least these role patterns:

- Slot 0: pad
- Slot 1: pad
- Slot 2: keyboard
- Slot 3: mouse

But the firmware should not depend on that exact assignment. If the APF
manifest can expose multiple dock controllers, a keyboard, and a mouse in
different slots, the input mapper should use the slot role/type metadata
instead of hard-coded slot numbers.

Open question: whether APF provides keyboard and mouse as current-state
registers, report/event queues, or a mixture. That decides whether v1
snapshot diffing is enough or whether the RTL must expose an event FIFO
from the start.

## Concrete Change Set

### RTL

Files:

- `src/fpga/targets/pocket/core_top.v`
- `src/fpga/common/axi_periph_slave.v`
- `src/fpga/test/tb_axi_periph.v`
- `src/fpga/test/tb_axi_periph_main.cpp`

Changes:

- Wire `cont3_*` and `cont4_*` from `core_top.v` into
  `axi_periph_slave`.
- Add synchronized raw state for all four APF slots.
- Add `INPUT_*` MMIO registers at a non-conflicting offset, preferably
  starting at `SYSREG_BASE + 0x100`.
- Add input pending/mask/clear logic.
- Add `IRQ_MASK_INPUT` to the external IRQ OR.
- Add Verilator tests that:
  - confirm legacy `CONT1_*`, `CONT2_*`, `SYS_GAME_ID`, and
    `SYS_COLOR_MODE` addresses do not move;
  - confirm raw slot 2/3 data reads from the new input page;
  - confirm changing any enabled slot asserts input pending and
    `ext_irq`;
  - confirm write-1-to-clear behavior clears pending without disturbing
    UART/link/vsync masks.

### Firmware/OS

Files:

- `src/firmware/os/hal/regs.h`
- `src/firmware/os/kernel/irq.h`
- `src/firmware/os/kernel/irq.c`
- `src/firmware/os/hal/input.h`
- `src/firmware/os/targets/pocket/input.c`
- `src/firmware/os/targets/sim/input.c`
- existing Analogizer/SNAC target files as needed

Changes:

- Add input-hub register definitions and `IRQ_MASK_INPUT`.
- Add `IRQ_SRC_INPUT`.
- Add an input IRQ service path.
- Refactor Pocket input into source parsers feeding one event/state core.
- Keep SNAC polling as a source driver until there is a hardware SNAC
  event producer.
- Keep `of_input_poll()` and `of_input_poll_p0()` ABI-compatible.

### SDK

Files:

- `src/firmware/api/of_input.h`
- `src/firmware/api/of_input_types.h`
- `src/firmware/api/of_services.h`
- `src/firmware/os/kernel/services_table.c`
- SDK mirror headers, if this repo is synced into a separate SDK tree

Changes:

- Add append-only event service calls.
- Add keyboard and mouse convenience state/accessor APIs.
- Preserve old service-table entries and null-check any new ones for
  compatibility with older firmware.

## Phasing

### Phase 0 - APF contract verification

Confirm:

- exact `core.json` controller role schema;
- keyboard data format;
- mouse data format;
- whether APF queues HID reports or only exposes current state;
- whether APF exposes present/hotplug metadata per slot.

This gates the final register fields and event parser.

### Phase 1 - Raw input hub and IRQ

Add all four APF slots to the input hub and expose IRQ-on-change. Keep
existing controller polling behavior intact while the new path is tested.

### Phase 2 - Firmware event core

Add the event ring, physical source states, mapper, and IRQ service. Wire
APF pad slots through it first. Existing gamepad apps should behave
identically.

### Phase 3 - Analogizer/SNAC source integration

Move the existing SNAC merge logic into the event core. Preserve current
Analogizer assignment behavior.

### Phase 4 - Dock keyboard/mouse/controller roles

Enable APF role metadata and parse keyboard/mouse reports. Add typed SDK
views. Validate on actual dock hardware.

### Phase 5 - Optional hardware event FIFO

Add the RTL event FIFO if APF/HID behavior shows that current-state
snapshot diffing can lose important keyboard or mouse transitions.

## Validation

Run at minimum:

- `tb_axi_periph` Verilator tests for the input page and IRQ behavior.
- Firmware build for Pocket and sim targets.
- A small input demo that prints:
  - physical source attach/present state;
  - raw APF slot states;
  - logical player mapping;
  - event stream;
  - keyboard held/pressed/released;
  - mouse dx/dy/buttons/wheel;
  - SNAC controller state.

Hardware checks:

- Undocked Pocket controls still drive P1/P2.
- Dock gamepad can map to a logical player.
- Dock keyboard produces key down/up events.
- Dock mouse produces motion/button/wheel events without frame-rate
  polling.
- Analogizer/SNAC assignments still produce the same P1/P2 snapshots as
  the current code.
- Rapid key taps and high-rate mouse motion do not overflow silently.

## Recommendation

Implement the unified input core before adding public keyboard and mouse
APIs. The public keyboard/mouse helpers should sit on top of the unified
event/state cache. The first hardware step should be an input hub with
all four APF slots, a non-conflicting register page, and a new input IRQ
source. Add a hardware event FIFO only if APF does not already preserve
individual keyboard/mouse reports.
