# SDK Build System & Porting Guide

The openfpgaOS SDK ships a containerized RISC-V toolchain so app
developers — and downstream cores reusing this SDK — don't need to
install `riscv64-unknown-elf-gcc` + musl by hand.  Just Docker, git,
and make.

This doc covers:

1. How SDK app builds use the container.
2. How to port the SDK to a different core (any FPGA target that wants
   the same RISC-V app build flow).

## How SDK app builds use the container

Every SDK app's `Makefile` does `include $(SDK_DIR)/sdk.mk` (or
`$(SDK_DIR)/app.mk`, which itself includes `sdk.mk`).  `sdk.mk`
defines a pattern rule that re-runs make inside the SDK container:

```make
container-%:
    @bash $(SDK_CONTAINER) $(MAKE) -f $(firstword $(MAKEFILE_LIST)) $*
```

So `make container-FOO` runs `make FOO` inside the
`openfpgaos-firmware` image (the same toolchain image that builds the
OS bootloader).  The image carries `riscv64-unknown-elf-gcc` 13.2 +
picolibc headers + bsdmainutils + make + python3 + curl.  musl is
pre-built and mirrored into `$(SDK_DIR)/musl/` by `make sdk DEST=...`,
so the app build picks it up off disk like the host path would.

### Two modes

| Goal                                | Command                       |
| ----------------------------------- | ----------------------------- |
| Build with host toolchain (default) | `make build`                  |
| Build via Docker (no host install)  | `make container-build`        |
| Clean via Docker                    | `make container-sdk-clean`    |
| Run any other target via Docker     | `make container-<target>`     |
| Drop into a container shell         | `bash $(SDK_DIR)/tools/sdk-container.sh bash` |

Container mode amortizes startup cost across the whole build: one
container per `make` invocation, every `$(CC)` inside is in-process.
On macOS / Apple Silicon expect ~1 s container startup; on Linux x86_64
it's instant.

### Wrapper script: `tools/sdk-container.sh`

The wrapper is intentionally generic — it `docker run`s any command
inside `openfpgaos-firmware`, bind-mounting the repo at the same path
so absolute paths in Makefiles resolve identically inside.  The image
auto-builds on first use from `tools/docker/Dockerfile.firmware`.

Overrides:

| Variable           | Default                                  | Purpose                        |
| ------------------ | ---------------------------------------- | ------------------------------ |
| `SDK_IMG`          | `openfpgaos-firmware`                    | Docker image tag               |
| `SDK_DOCKERFILE`   | `$REPO/tools/docker/Dockerfile.firmware` | Build-from-here on first run   |
| `SDK_CONTAINER`    | `$(SDK_DIR)/tools/sdk-container.sh`      | Path to wrapper (set in Makefile) |

## Porting to a different core

The SDK is structured so any FPGA core that wants to use this exact
runtime (RISC-V CPU + musl-static apps + `of_*` capability/services
ABI) can host its own copy of the SDK and rebuild apps for that core
without changes to the SDK build system.

### What gets shipped

`openfpgaCore`'s `make sdk DEST=<other-core>` mirrors into
`<other-core>/` the following:

```
src/sdk/
├── include/             # of_*.h + bundled SDL2 shims
├── of_*.{c,cpp}         # opt-in runtime sources
├── sdk.mk               # build rules (CFLAGS, LDFLAGS, link recipe, container-%)
├── app.ld               # linker script (app .text/.data layout)
├── pc/                  # SDL2 PC backend (for non-FPGA dev/test builds)
└── musl/
    ├── include/         # musl headers
    └── lib/             # libc.a, libm.a, crt1.o, crti.o, crtn.o

tools/
├── sdk-container.sh             # wrapper invoked by `make container-*`
└── docker/Dockerfile.firmware   # toolchain image
```

After the mirror, the downstream core's contributors can build any SDK
app — including new apps written specifically for that core — with
just Docker + git + make.  No openfpgaCore checkout required.

### Steps to port

1. **Generate the SDK tree** from openfpgaCore (one-time):
   ```bash
   # In openfpgaCore:
   cd src/firmware/musl && ./build_musl.sh    # build musl if not already
   cd ../../..
   make sdk DEST=/path/to/your-core-repo
   ```
   This drops the files above into the target repo.

2. **Write your core's runtime** — the of_* services your CPU/HW
   actually implements (audio, video, file I/O, ...) live behind the
   `of_*` headers' SBI vendor-extension ecalls.  See
   `docs/ARCHITECTURE.md` for the contract.

3. **Write/import apps.**  Each app's Makefile is a thin wrapper:
   ```make
   SDK_DIR ?= ../../sdk
   SRCS := main.c
   include $(SDK_DIR)/sdk.mk
   ```
   Then:
   ```bash
   make container-build      # → app.elf via Docker
   ```

4. **Re-sync** by re-running `make sdk DEST=...` whenever you bump the
   openfpgaCore source tree.  rsync `--delete` ensures stale headers
   and removed sources vanish from the downstream tree.

### Same SDK app, different cores

A core that ships the same RISC-V CPU + same of_* services (e.g. a
fork that targets a different FPGA board) gets binary-compatible app
ELFs.  An SDK app `.elf` built for openfpgaOS-Pocket runs unchanged on
that other core.  This is the whole point of the SDK — one app source
tree, one ABI, multiple cores.

If your core diverges on capabilities (e.g. no HW mixer), advertise
that via the `HW_FEATURES` caps register; the SDK helpers in
`of_init.c` + the runtime `of_caps.h` constants let apps branch at
runtime instead of forcing per-core builds.

## Caveats

- musl is built inside the openfpgaOS firmware build flow (see
  `src/firmware/musl/build_musl.sh`).  Downstream cores that don't
  carry that firmware build can either (a) sync a pre-built musl from
  openfpgaCore via `make sdk`, or (b) run `build_musl.sh` themselves
  inside the SDK container (`bash tools/sdk-container.sh bash
  tools/build_musl.sh` after copying the script).
- The SDK container is x86_64 Linux (the riscv64-unknown-elf toolchain
  cross-compiles to RV32IMAFC).  On Apple Silicon it runs under
  Rosetta via OrbStack/Docker Desktop — fast enough for app builds.
- The container image is ~1.5 GB.  Pulled / built once per machine,
  cached after that.  Subsequent app builds reuse it instantly.
- App build outputs (`*.o`, `*.elf`, `*.map`) land in the host with
  host-user ownership (`--user $(id -u):$(id -g)` in the wrapper).
