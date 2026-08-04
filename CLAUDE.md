# openfpgaOS — Build & Workflow Guide

Operating system + RISC-V SoC for the **Analogue Pocket** (and MiSTer) FPGA. This file is the
practical build/sweep/deploy reference for agents and contributors. Commands assume the local
toolchain below is on `PATH`.

## Layout

| Path | What |
|---|---|
| `src/fpga/targets/<target>/` | Per-board FPGA project (`pocket`, `mister`) — Quartus `.qsf`, `core_top.v`, `Makefile` |
| `src/fpga/targets/<target>/bld/<job>/` | **Per-job isolated build dir** — generated qsf + own `db/` + `output_files/`; gitignored |
| `src/fpga/common/` | Shared RTL (GPU, SDRAM/CRAM controllers, audio, video scanout, bridges) |
| `src/fpga/vendor/vexriscv/` | VexiiRiscv CPU + `generate_vexii.sh` (SpinalHDL/sbt generator) |
| `src/fpga/test/` | Verilator testbenches (`tb_*.v` + `tb_*_main.cpp`) |
| `src/firmware/os/` | The OS (`os.bin`) + BRAM bootloader (`firmware.mif`); `targets/{pocket,mister,sim}` |
| `src/firmware/api/` | SDK headers (`of_*.h`) + canonical SDK sources |
| `tools/` | `sweep.sh` (container-parallel + host-serial), `quartus-container.sh`, `report.sh`, `reverse_bits`, `docker/Dockerfile` |
| `build/<target>/` | Deploy output (SD-card tree); regenerated, gitignored |

## Toolchain (mostly containerized — `make full` on a fresh clone needs only Docker + git + make)

Both targets run every long-lived toolchain inside Docker by default,
so a fresh checkout needs no host installs of Quartus / sbt / riscv64-elf-gcc
(the same is true of SDK app builds — the canonical `sdk.mk` here delegates
RISC-V compiles to the `openfpgaos-firmware` container by default,
`USE_SDK_CONTAINER=0` to opt out):

- **Docker** — drives all three containers (vexii / firmware / quartus-full),
  built on demand by `vexii-container.sh`, `firmware-container.sh`,
  and `build-quartus-image.sh`.  Runs on Linux x86_64 natively; on Apple
  Silicon the `linux/amd64` containers run under Rosetta via OrbStack /
  Docker Desktop.
- **Quartus Prime 25.1 (Lite or Standard)** — contributor drops the
  Altera `.tar` (~9 GB) anywhere under `tools/`; the first `make full`
  bakes it into `openfpgaos-quartus-full` (one-time, ~10 min).  An
  `ffreep` x87 patch on the bundled SQLite makes Quartus run cleanly
  under Rosetta on Apple Silicon (see `Dockerfile.quartus-full`).
  Bind-mount mode still works for Linux boxes with a host install at
  `$ALTERA_ROOT` (defaults to `/home/alberto/altera_lite`) — see
  `tools/quartus-container.sh`.  EVERY pocket Quartus step runs in this
  container — full compiles, the `make firmware` MIF patch, `make check`,
  and the `report FULL=true` STA re-run (exec mode of
  `quartus-container.sh`); only `make program` (JTAG over USB) uses a
  host install.
- **mister target** — **Quartus 17.0.x in a container by default**
  (`openfpgaos-quartus17` — the sys/ framework is a hard Q17 requirement).
  The image is baked (`build-quartus17-image.sh`, one-time) from either a
  **pre-extracted** Q17 tree (`altera-17-quartus.tar*`) or the **Altera
  installer tar** (`Quartus-*17.0*-linux.tar`) dropped under `tools/`.  From
  the installer the bake installs Q17 in a container first: native on x86_64
  Linux, or via **QEMU** on ARM.  **On Apple Silicon this install step
  requires Docker/OrbStack** — Apple `container`'s only x86 engine is Rosetta,
  which overflows on Q17's Bitrock installer (`bss_size overflow`), and QEMU
  needs `binfmt_misc`/`--privileged` that Apple container doesn't grant
  (apple/container#206).  `build-quartus17-image.sh` auto-borrows Docker just
  for this bake when it's up, then caches `tools/altera-17-quartus.tar.gz` so
  later bakes (and other machines) skip the install.  A pre-extracted tree
  needs no Docker/QEMU at all.  Build, sweep, MIF patch and report all run
  through `quartus17-container.sh`; `USE_QUARTUS_CONTAINER=0` falls back to a
  host install at `/home/alberto/intelFPGA_lite/17.0/quartus`.
  - **Apple Silicon + Docker Desktop — Rosetta setting is opposite for install
    vs. run:** the one-time *installer* bake needs Rosetta **OFF** (Settings →
    "Use Rosetta for x86/amd64 emulation" unchecked) so x86 runs under QEMU —
    Rosetta overflows the Bitrock installer, and QEMU's default CPU is fine for
    it.  *Running* Quartus (every build/sweep/report) needs Rosetta **ON** —
    QEMU's emulated CPU fails Quartus's SSE/CPU-model check ("will not function
    properly on this processor model"), whereas Rosetta passes it (the image's
    ffreep x87 patch makes Quartus run cleanly under Rosetta).  Because the
    extracted tree is cached after the first install, you normally keep Rosetta
    **ON** — you only flip it OFF for a rare from-installer re-bake.  The
    install container is always amd64 (never arm64+binfmt: the dynamically
    linked Bitrock installer needs the x86 loader, absent in an arm64 userland
    → exit 127).
- **Verilator 5.x** — host-installed (or via your distro) for the RTL
  test suite (`make test`).  Not part of the default build.
  (Deliberate host exception: tests produce no shipped artifacts.  The
  chip32 `bass` assembler is the other documented host tool.)

Both targets build firmware and the CPU netlist in containers by
default.  Setting `USE_VEXII_CONTAINER=0` / `USE_FIRMWARE_CONTAINER=0` /
`USE_QUARTUS_CONTAINER=0` falls back to host `sbt` /
`riscv64-(unknown-)elf-gcc` / Quartus if you'd rather not use Docker for
those steps — but shipped artifacts should always come from the
containers so every machine (and every target) compiles with the SAME
toolchains.

## Targets & variants

- **Target** = board. Selected by `.target` (gitignored), default `pocket`. Override per-command with `TARGET=mister`, or set sticky with `make use-mister` / `make use-pocket`.
- **VARIANT** = feature profile of a target, **auto-discovered** from `src/fpga/targets/<t>/variants/<name>.mk`. Resolution: command line > environment > `.variant` (root file written by `make use-os30` etc., gitignored) > target default (pocket→`os25`, mister→`mister`). The sticky `.variant` is SOFT (applies only when the selected target has that variant); an explicit `VARIANT=<unknown>` errors. `make use-<name>` at the root auto-detects whether `<name>` is a target or a variant; `make use-default` clears both.
- **Adding a variant = 3 file drops, zero edits**: `variants/<name>.mk` (its `DEFS` module list), `src/fpga/vendor/vexriscv/configs/<name>.cfg` (its CPU config — cache geometry, extra Generate flags, maxfan hint), `seeds/<name>.seed`. Then `build`/`sweep`/`build-all` just work, shipping `<name>.rbf_r`.
- Pocket variants, default `os25`:
  - **os20** — 2D games (Diablo/DevilutionX, ScummVM point-and-click) with a **DUAL-ISSUE CPU** (the mister `--decoders=2 --lanes=2` config on the Pocket A4/C8, + fma-reduced, 64 KB D$). Keeps Analogizer, HW mixer, translucency, palette lane, 4-player/mouse; cuts the three 2.5D span input forms (compact 0x48, column 0x4C, Q29 perspective — caps 23/21/18 clear, SDK emitters self-gate). 16,397 ALM (89%), 246/308 M10K. Games stay pinned to os25 until os20 is HW-validated.
  - **os25** — 2.5D games (Doom/Duke3D/ScummVM/Wolf + Quake1 SW). Full HW audio mixer, Analogizer/SNAC, translucency, column lists, compact span groups. No HW triangle path, no fast-texture. **At 308/308 M10K (100%).**
  - **os30** — Quake2 / SM64 (3D triangles). HW vertex-triangle + truecolor; cuts Analogizer/SNAC, HW mixer (→ **CPU software mixer**), translucency, column/compact.
  - **One caps-driven `os.bin` runs on both** — the bitstream sets `HW_FEATURES`; firmware auto-selects features at boot. There is no per-variant firmware flag.
  - Output bitstreams are variant-named: `os25.rbf_r` / `os30.rbf_r` (≤15-char Pocket filename cap), so both coexist on one SD card.

## Build dirs & jobs (isolation model)

Every Quartus compile runs in its **own** dir, `bld/<JOB>/`, from a **self-contained generated qsf**
(absolute source paths + the variant's `VERILOG_MACRO`s + `SEED` baked in) — so its `db/`,
`incremental_db/`, and `output_files/` never collide with another build.

- `JOB` defaults to `VARIANT` → `make build VARIANT=os30` compiles in `bld/os30/`.
- A parallel seed search uses distinct jobs (`bld/os30-s7/`, …) so **many builds of the same variant**
  coexist. Override `SEED` per job on the command line.

## Build commands (run from `src/fpga/targets/pocket/`)

Builds run in an isolated Docker **container by default** (see *Container builds* below) — that is what lets
`build-all` and `sweep` fan out without the concurrent-Quartus segfault.

```bash
make build VARIANT=os25        # container compile in bld/os25/ → os25.rbf_r              (~7 min)
make build VARIANT=os30        # container compile in bld/os30/ → os30.rbf_r
make build-all                 # ALL $(VARIANTS) in parallel containers (MAXJOBS-wide)     (~7 min)
make sweep-all                 # seed-sweep every variant in turn (each sweep MAXJOBS-wide)
make full  VARIANT=os25        # cpu → bootloader → os → build

make firmware VARIANT=os25     # bootloader + os.bin, then MIF-patch the .sof in place    (~10 s)
                               #   ships a new boot ROM / os.bin WITHOUT re-running Quartus
make os                        # build os.bin only                                        (~5 s)
make cpu VARIANT=os30          # regenerate THIS variant's netlist (VexiiRiscv_os30.v); only if missing
```

`build` generates `bld/<job>/ap_core.qsf` (source paths absolute, VERILOG_MACROs + SEED baked in — no
`--verilog_macro`/`--seed` flags), then runs `quartus_map → fit → asm → sta` in a container with `bld/<job>/`
mounted, and reverses the `.rbf` into `build/pocket/Cores/ThinkElastic.openfpgaOS/<variant>.rbf_r`.

> **Deploy = the Makefile**, not a top-level `deploy.sh`. Device push lives in the SDK (`copy.sh`), invoked via Makefile targets. On **pocket**, `make deploy`/`make package` refresh the `build/pocket/` SD tree. On **mister** the model is **per-game / update-safe**: `make copy TARGET=mister` (alias `make copy-app`) does a per-game engine update — atomically scp's the built ELF to the loose F-loaded engine `games/OpenfpgaOS/<Game>/<GameElf>` (e.g. `doom.elf`), leaving the user's `boot.vhd` wads and `<Game>.vhd` saves untouched. The game-agnostic **core** ships separately: `make package TARGET=mister` → `releases/mister/openfpgaos-core-v<ver>.zip` (`_Computer/OpenfpgaOS.rbf` + `games/OpenfpgaOS/boot.rom` + `INSTALL.txt`) plus a Downloader DB `openfpgaos.json.zip`; `make release TARGET=mister` drafts the GitHub release. `make sdk` stages `os.bin` into game repos but no longer vendors the `.rbf`. Full flow: `src/sdk/platforms/mister/PACKAGING.md`.

## Container builds & parallel jobs

`make build` runs Quartus in Docker by default. Concurrent Quartus on one host **segfaults** (`Fatal Error:
Segment Violation`) — not RAM, not the dir isolation, but the shared per-user state (`~/.altera.quartus`) and
shared `/tmp` scratch. Each container gets a **private `$HOME` + `/tmp`**, so any number run at once — that is
what makes `build-all` (all `$(VARIANTS)`) and `sweep` (MAXJOBS-wide) parallel.

```bash
# one-time: build the Quartus image (Quartus is bind-mounted from the host, not baked in)
docker build -t openfpgaos-quartus tools/docker/
# CPU-netlist image (JDK+sbt baked in) — auto-builds on first `make cpu`, or prebuild:
docker build -t openfpgaos-vexii -f tools/docker/Dockerfile.vexii tools/docker/   # (or: make vexii-image)
```

`tools/quartus-container.sh <abs-bld-dir>` runs one full Quartus compile in a container; `make build` /
`build-all` / `sweep` call it for you. `tools/vexii-container.sh <variant>` does the same for the CPU netlist
(see *CPU netlist*). Outputs stay host-owned (`--user $(id -u):$(id -g)`). With both images, a fresh checkout
builds end-to-end on **Docker + internet alone** — no host Quartus, JDK, or sbt.

## Seeds & timing sweep

- Fitter seed is **committed per variant** in `seeds/<variant>.seed`. `make build` reads it; the design is seed/placement-sensitive at the timing wall, so the seed matters.
- `make sweep` is a **parallel container** seed search: one job (`bld/<variant>-s<seed>/`) per seed, shared netlist+firmware built once, `MAXJOBS` container fits at a time, ranked by setup **WNS** on the 100 MHz `mp_ram` clock; the winner is written back to `seeds/<variant>.seed`.

```bash
make sweep VARIANT=os30 SWEEP_MIN=1 SWEEP_MAX=12 MAXJOBS=4
```

> MiSTer's `make sweep` runs the **same** `tools/sweep.sh` in in-place serial mode (`USE_CONTAINER=0 MAXJOBS=1` — no `bld/<job>/` isolation on the Q17 flow), but every quartus invocation goes through the **Q17 container** (`QRUN=quartus17-container.sh`) and now carries the variant's `INCLUDE_*` macros (`VARIANT_DEFS`) — an unmacro'd sweep ranks a featureless design and its seed is meaningless. `sweep` depends on `firmware` so the baked `.mif` is fresh (stale-mif black-boot guard).

Always **include the current stored seed in the range** so the sweep can't replace it with a worse one. The "failed" in the Fmax column is a cosmetic parse quirk — ranking is by WNS, which is correct.

```bash
make timing      # Fmax + setup slack per clock, from the last build
make report      # resource summary  (FULL=true → top ALM entities + 25 worst paths)
```

## CPU netlist (VexiiRiscv)

- Generated by `src/fpga/vendor/vexriscv/generate_vexii.sh` (sbt). Output is **gitignored**; `make cpu VARIANT=<v>` regenerates **only if missing** — to force: `rm src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_<v>.v && make cpu VARIANT=<v>`.
- **Runs in a Docker container by default** (`tools/vexii-container.sh`, image `openfpgaos-vexii` = JDK21 + sbt, auto-built on first use) — no host JDK/sbt needed. Scala/SpinalHDL deps download once into `tools/.vexii-home` (gitignored, ~250 MB) then reuse offline; the `Generate` itself is ~3 s. Output is **byte-identical** to host sbt (the netlist header carries only the SpinalHDL + VexiiRiscv git hashes, no timestamp → deterministic). Use host sbt instead with `make cpu USE_VEXII_CONTAINER=0`.
- **One netlist per variant**, generated separately: `VexiiRiscv_os25.v`, `VexiiRiscv_os30.v`, `VexiiRiscv_mister.v`. Each per-job `bld/<job>/ap_core.qsf` names its variant's netlist directly via `VERILOG_FILE` (injected by the pocket Makefile's qsf-gen rule); mister's qsf does the same. `grep VERILOG_FILE bld/<job>/ap_core.qsf` shows exactly which CPU is being fit.
- **Per-variant CPU config lives in `src/fpga/vendor/vexriscv/configs/<variant>.cfg`** (sourced by `generate_vexii.sh`): `ICACHE_SETS`/`DCACHE_SETS`, `EXTRA_FLAGS` (os30 = `--fma-reduced-accuracy`, mister = `--decoders=2 --lanes=2`), and `MAXFAN_HINT` (the pocket-tuned `execute_freeze_valid` annotation; 0 on mister). All variants currently run **32 KB I$ / 128 KB D$** (256 KB D$ overflows the mister A6's M10K *block* count — see `configs/mister.cfg`). Adding a variant's CPU = drop a `.cfg`; unknown variants error listing the available configs.
- Shared base config (RV32IMAFC + Zicbom): `--allow-bypass-from=0` (mandatory for correctness), `--lsu-hardware-prefetch=none`. Changing a `.cfg` needs an sbt regen (`rm` the netlist, `make cpu VARIANT=<v>`).

## Firmware / OS

```bash
cd src/firmware/os
make os.bin            # the OS image (loaded from SD into SDRAM by the bootloader)
make install           # build + stage firmware.mif (the BRAM boot ROM baked into the bitstream)
make check-api         # verify the cross-target app .elf contract
```
The OS is selected by the same `.target` mechanism. `boot.c` (`.text.boot` → BRAM → `firmware.mif`) is baked into the bitstream — changing it needs a `make firmware` MIF-patch (or a full refit), **not** just a new `os.bin`.

## Hardware/OS contract (fw ↔ os.bin pairing)

The bitstream and os.bin share a contract — register map, memory boundaries
(`target_platform.h`, both `app.ld` copies), caps ABI. A mismatched pair used to
boot into a blank screen / trap cascade. Three guards now cover it:

- **Boot handshake** — `HW_CONTRACT_REV` (`axi_periph_slave.v`, sysreg 0x8C) vs
  `OS_EXPECTED_HW_CONTRACT` (`hal/regs.h`). Nonzero mismatch = halt with a
  readable banner; pre-versioning cores (read 0) warn and continue. **Bump BOTH
  in the same commit** for any breaking register/memory-map change.
- **Boot memtest** (`hal/memtest.c`) — catches the physical layer no version
  number can: controller interleave changes, DDIO DQ capture regressions,
  timing-marginal cores. Prints region/addr/wrote/read on failure and halts.
- **Contract tripwire** (`tools/contract-check.sh`, run by every os.bin build) —
  fails the build when a contract-surface file changes without an explicit
  decision. Breaking → bump both revs, then `tools/contract-check.sh --bless`;
  additive → just re-bless. Commit `.contract.md5` with the change.

**Policy: prefer additive changes.** New behavior lands at new addresses behind a
new `HW_FEATURES`/caps bit (the `CLK_HZ` fallback idiom); existing registers keep
their meaning. The rev should move rarely. Semantics-only changes (same fields,
new meaning) evade the tripwire — those still rely on the manual bump rule.

`make sdk` writes `runtime/MANIFEST` (md5s of the atomically published set);
consumer `image.sh` refuses to assemble a hand-mixed runtime. Ship rbf + os.bin
from ONE tree — `make full VARIANT=<v>`, then `make sdk DEST=…`.

## Tests (Verilator)

```bash
make test                              # full suite (root or pocket)
cd src/fpga/test && make <name>        # one test, e.g. gpu / gpu-acceptance / sdram-all /
                                       #   axi-periph / audio-mixer / scanout / cram1-tex-chain / skid
```
Build success = elaboration-clean; each prints a `=== Results: N passed, M failed ===`.

## Notes for agents

- **Concurrency:** `make build` runs in a **container by default** (private `$HOME`+`/tmp`), so fits parallelize freely — `build-all` runs every `$(VARIANTS)` at once (MAXJOBS-capped) and `sweep` is MAXJOBS-wide. (On the *host*, two Quartus fits at the same instant segfault on shared `~/.altera.quartus` + `/tmp`; the container is what isolates that state. Don't add a host-side build path back without that isolation.)
- **Apple Silicon (Rosetta) — single-threaded by default.** Rosetta's thread allocation / locking overhead makes Quartus die with `Error (112002): Can not read any output from quartus_map` when too many threads are spawned (per-fit AND across parallel fits). The pocket Makefile auto-detects `Darwin/arm64` and drops `QPROCS` and `MAXJOBS` from 4 to 1. Override with `QPROCS=N MAXJOBS=N make build` if you want to experiment. On native x86_64 Linux the wide defaults (4 × 4) stay.
- **Builds/sweeps are long** (~18 min/fit). Launch with `run_in_background` (or `&`) and chain with `&&` — never busy-poll with `until`/`sleep` loops.
- **Don't start a host build while another is mid-`sbt`** — the generator emits a transient `VexiiRiscv.v` before renaming, so two concurrent `make cpu` collide. Generate netlists first (or let the build do it), then parallelize the fits via containers.
- **os25 is M10K-bound (308/308); os30 ~88% ALM.** The 100 MHz `clk_ram`/CPU clock is the timing wall (WNS negative; ships passing at nominal). The critical path is CPU-internal (decode→BTB / FP-operand cluster) — not the fabric.
- **Do not commit or push without being asked.** Reverts must be forward edits, never `git checkout`/`restore`/`stash` of modified files.
- Build artifacts (`bld/`, `db/`, `output_files/`, `obj_dir*/`, `*.rbf`, generated `VexiiRiscv_*.v`) are gitignored — don't add them.
```
