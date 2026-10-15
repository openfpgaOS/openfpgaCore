# openfpgaOS

Bare-metal OS and RISC-V (VexiiRiscv) FPGA core for the Analogue Pocket
(`pocket`) and MiSTer (`mister`). Apps are built with the
[openfpgaOS SDK](https://github.com/ThinkElastic/openfpgaOS-SDK).

## Requirements

- Docker (or Apple `container` on Apple silicon), git, make, bash
- Quartus (not redistributable): drop the Linux installer tarball anywhere
  under `tools/`
  - pocket: Quartus Prime 25.1 Lite or Standard (`Quartus-*-linux.tar`)
  - mister: Quartus 17.0.x (installer tar or a pre-extracted
    `altera-17-quartus.tar*`)
- Verilator 5.x, only for `make test`

The first build creates the toolchain containers (the Quartus image takes
about 10 minutes, once).

## Build

```bash
make full                    # CPU, bootloader, OS and bitstream (default: pocket)
make full TARGET=mister      # MiSTer core
make build VARIANT=os30      # one Pocket variant bitstream (os20, os25, os30)
make firmware                # rebuild bootloader + os.bin into the last bitstream
make sweep VARIANT=os30      # fitter seed search, stored in seeds/<variant>.seed
make test                    # Verilator RTL tests
make package                 # SD-card tree in build/ (pocket) or release zip (mister)
make sdk DEST=<sdk checkout> # copy headers and runtime into an SDK checkout
```

`make use-mister` or `make use-os30` sets a sticky default; `make` lists all
goals.

## Retained Quake2 release

The supported Quake2 I64 release is a frozen, hardware-tested app/core pair,
separate from the general development source tree:

```bash
python3 tools/core_release.py verify
python3 tools/core_release.py export  # new build/releases/os30-quake2/
python3 tools/core_release.py stage   # new build/releases/os30-quake2-sources/
```

The [profile instructions](release/os30-quake2/README.txt) include the exact
sources, binary hashes, licenses and complete 30-seed timing evidence.
Seed 16 estimates **90.69 MHz**, with the clock configured at 100 MHz; this
does **not** establish 100 MHz timing closure. The slower I32 cache cut and
the I64 RAM repack that failed the >90 MHz sweep are excluded.

Use Quake2's approved release targets with this core. Keep SM64's separately
tested core. Generic `make build`, `make package` and `make sdk` operate on
the development tree and do not reproduce these specialized release pairs.
No release-profile command writes to an SD card or publishes a release.

See [os20/os25 applicability](release/OS20-OS25.txt) for inherited improvements,
variant-specific limits and candidates requiring separate qualification.

## Acknowledgments

VexiiRiscv (SpinalHDL, Charles Papon), musl libc, Analogizer and SNAC
reference (RndMnkIII), openFPGA (Analogue), MiSTer framework (MiSTer-devel,
Sorgelig), FatFs (ChaN).
