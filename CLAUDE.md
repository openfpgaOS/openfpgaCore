# Project Instructions

## Build

All builds run from `src/fpga/targets/pocket/`:

```bash
make              # full: cpu → firmware → compile → test → deploy (~9 min)
make flash        # quick: firmware → MIF patch → deploy (~10 sec)
make build        # incremental Quartus compile (~7 min)
make cpu          # regenerate VexiiRiscv from SpinalHDL (~15 sec)
make firmware     # rebuild bootloader + os.bin (~5 sec)
make test         # run all 836 Verilator tests (~30 sec)
make check        # RTL syntax check (~45 sec)
```

## Testing

After making changes to the FPGA design or firmware:

1. `make` from `src/fpga/targets/pocket/` — builds everything and deploys
2. Check the output: `tools/capture_ocr.sh`

## Key Paths

- VexiiRiscv config: `src/fpga/vendor/vexriscv/generate_vexii.sh`
- Firmware: `src/firmware/os/` (bootloader + OS)
- FPGA RTL: `src/fpga/common/` (portable) + `src/fpga/targets/pocket/` (Pocket-specific)
- Verilator tests: `src/fpga/test/`

## SDK App Portability

A single SDK app `.elf` is meant to run unchanged on every openfpgaOS
target. The contract:

- App virtual memory map: `docs/app-virtual-map.md`
- Boot ABI (caps/services via auxv): `src/firmware/api/of_app_abi.h`
- Per-target HAL contract: `src/firmware/os/targets/README.md`
- Audit baseline: `tools/portability_baseline.txt`

To verify the contract still holds after a change:

```bash
cd src/firmware/os && make check-api
```

The check builds both `TARGET=pocket` and `TARGET=sim`, runs three
acceptance greps over `src/firmware/api/`, and inspects the sim
`os.bin` for the divergent `gpu_base` immediate to prove the kernel
is threading per-target addresses through `caps_table.c` at runtime.

Apps that opt into `OF_FASTTEXT` / `OF_FASTDATA` (BRAM hot region)
are NOT portable to targets that don't expose RAM in the v1 BRAM
range -- the loader rejects them with error -8. Apps that don't use
the macros are universally portable.
