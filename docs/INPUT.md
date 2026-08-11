# openfpgaOS — Unified Mouse (design + contract)

**Goal.** One pointer model for every game (ScummVM, Doom/DevilutionX, Quake) on **both targets**: a
physical mouse where the platform has one (Pocket dock, MiSTer USB), the right-stick synthesis as the
fallback everywhere else, and **zero app-visible API change** — games consume the mouse through the SDL2
shim, and the shim consumes it through the pre-existing `of_input_mouse_state()`.

The unification points are the **app ABI** and the **input-hub slot-3 register contract**. The transport
below slot 3 is per-target by design (APF 1-wire dock bus vs `hps_io` from Main), matching the existing
`targets/<t>/input.c` split.

---

## Layering

| Layer | File(s) | Shared? |
|---|---|---|
| Game | external repos | consumes SDL2 mouse events + state getters |
| SDL2 shim | `src/firmware/api/of_sdl2.c` (+ `api/pc/` mirror) | yes — one pointer model |
| App ABI | `of_input_mouse_state()` / `of_mouse_state_t` (`of_input.h`, FID 5) | yes — **frozen** |
| Decoder | `src/firmware/os/hal/hid_mouse.{h,c}` | yes — parses slot 3, both X/Y modes |
| Driver | `targets/pocket/input.c` / `targets/mister/input.c` | per-target |
| Registers | input-hub slot 3, `INPUT_SLOT_*(3)` (`hal/regs.h`) | yes — same periph both targets |
| Transport | APF dock (Player 4) / `hps_mouse.v` encoder | per-target |

## Slot-3 register contract (both targets)

| Field | Contents |
|---|---|
| `KEY[31:28]` | `5` (`OF_INPUT_TYPE_MOUSE`) once a mouse has been seen, else `0` (= absent) |
| `KEY[15:0]` | report counter, +1 per hardware report |
| `JOY[31:16]` | buttons: bit0=L, bit1=R, bit2=M, bits3+ = extra buttons |
| `JOY[15:0]` | X field, signed 16-bit, positive = right |
| `TRIG[15:0]` | Y field, signed 16-bit, positive = **down** (HID convention) |

**X/Y semantics differ per target — the one deliberate asymmetry:**

- **Pocket** (defined by the Analogue dock, unchangeable): per-report **deltas**. Polling alone would
  drop reports that land between polls (USB mice report at 125 Hz+), so the driver decodes **from the
  input-hub change-FIFO IRQ**: `of_input_irq_service` re-decodes slot 3 on every slot-3 change event,
  making per-report deltas effectively lossless (µs IRQ latency at 100 MHz). Poll-path decode stays as
  an idempotent fallback (counter dedup).
- **MiSTer** (`hps_mouse.v`): free-running wrapping **accumulators**; firmware computes
  `delta = (int16_t)(now - prev)`. Lossless under pure polling, needs no hub IRQ, and self-heals a
  torn CDC read (an accumulator sample is absolute). Do **not** saturate them — the differencing
  requires 16-bit wraparound. `of_input_read_mouse_state()` refreshes the slot itself so pure
  state-getter apps never see frozen deltas.

Presence is runtime (`of_mouse_state_t.present`, from the type nibble) — hot-pluggable, like the
keyboard. **No `HW_FEATURES` caps bit.** A bitstream without the transport reads nibble 0 and the mouse
is simply absent.

## Consuming-read semantics

`of_input_read_mouse_state()` copies then clears `dx/dy` **and** the `buttons_pressed/released` edge
masks; edges **accumulate** between reads in the decoder (`hid_mouse.c`), so a press+release pair that
both land between two app reads still surfaces both edges. `buttons` stays level-based. Disconnect
latches release edges for whatever was held into the final `present=0` read. Because the read consumes,
`mouse_refresh()` in the SDL2 shim must remain the **only** caller of `of_input_mouse_state()`.

## SDL2 shim pointer model (`of_sdl2.c`)

- `mouse_refresh()` — single consume point, **state only**: cursor (clamped to the window/framebuffer
  dims), SDL button mask, relative accumulator, and *pending* event masks. Getters
  (`SDL_GetMouseState`, `SDL_GetRelativeMouseState`) refresh so pure pollers see live deltas, but never
  push events — a getter growing the queue would defeat `SDL_PollEvent`'s empty-queue pump gate and
  starve pad/keyboard synthesis.
- `mouse_flush_events()` — pump-only: emits `SDL_MOUSEMOTION` (coalesced) + `SDL_MOUSEBUTTONDOWN/UP`.
  When both edges of a button landed in one interval, order follows the final level (ends held →
  UP,DOWN; ends up → DOWN,UP). Hot-unplug emits the pending BUTTONUPs and clears the mask.
- `SDL_GetRelativeMouseState` = right-stick synthesis (unchanged) **+** real deltas, summed — stick-look
  and mouse-look coexist. Button mapping note: OF order is L,R,M; SDL numbers MIDDLE=2, RIGHT=3.
- **No mouse ⇒ byte-for-byte the old behavior** (warp-echo `GetMouseState`, stick-only relative state).
- The `api/pc/` mirror implements `of_input_mouse_state()` over real SDL (complementary, not a copy):
  present once the window exists; `report_counter` advances per read-that-observed-change.

## MiSTer transport (`hps_mouse.v`, clk_cpu domain)

`hps_io.ps2_mouse[24]` toggles per packet; flags byte carries buttons + 9-bit delta signs + overflow
flags. The encoder sign-extends (overflow clamps to ±255), **negates Y** (PS/2 is positive-up), adds
into `acc_x/acc_y`, increments the counter, and latches `seen` → type nibble 5. Extra buttons ride
`ps2_mouse_ext[11:8]` → buttons bits 3-6; the wheel byte is ignored (frozen ABI). No reset on purpose:
`hps_io` isn't reset by warm resets either, and clearing `stb_prev` would desync the toggle tracker.
Feeds the periph's `cont4_*` port (2FF-synced inside `axi_periph_slave` like every cont bus).

## Gating & scope

- **Pocket**: needs `INCLUDE_4PLAYER` (wires cont3/cont4 through, `core_top.v`) + the input hub.
  os20/os25 have it; **os30 does not, by decision** — slot 3 reads 0 there and the mouse is absent.
- **Keyboard**: Pocket dock keyboard (slot 2, type 4) and the MiSTer USB keyboard both work — MiSTer
  consumes hps_io's `ps2_key` stream via `targets/mister/hps_keyboard.v` (see `tb_hps_keyboard`).
- **Wheel / >5 buttons**: deferred — `of_mouse_state_t` is a fixed-size syscall copy, so extending it
  breaks shipped ELFs. Needs a size-carrying v2 FID when wanted (MiSTer already has the data;
  whether the Pocket dock forwards wheel needs a HW probe).

## Tests

- `src/fpga/test: make hps-mouse` — 17-check cosim of the encoder against a software model (signs,
  Y inversion, overflow clamp, wraparound, counter, buttons, reset), incl. a 70 000-packet random run.
- `make system` — full OS boot in sim (needs TARGET=sim firmware staged, see test/Makefile).
- Firmware: all three targets build in the container; `make check-api` portability greps add no new hits.

## HW validation still pending

1. Pocket dock: confirm the APF mouse Y sign convention (assumed HID positive-down) and whether the
   dock coalesces reports between APF polls.
2. MiSTer: end-to-end USB mouse → cursor in a game; verify `ps2_mouse_ext` extra-button numbering.
3. Games pick the mouse up only after a rebuild against the updated SDK (`of_sdl2.c` ships with apps,
   not with os.bin).
