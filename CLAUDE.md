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
