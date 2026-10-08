#!/usr/bin/env python3
"""Run CPU stress with real timer interrupts and concurrent SDRAM scanout.

Uses an isolated fixture: the shipping CPU, peripheral timer and SDRAM
controller remain unchanged. Requires Verilator and the firmware container.
"""
import argparse
from pathlib import Path
import re
import subprocess
from check_pocket_memory_contention import pocket_sdram_twin


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"stress fixture changed: {old!r}")
    return text.replace(old, new, 1)


def run(command, logfile, cwd=None):
    with logfile.open("w") as log:
        subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                       check=True)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, default=root /
                        "src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_mister.v")
    parser.add_argument("--output", type=Path, default=root / "build/vexii-interrupts")
    parser.add_argument("--profile", choices=("mister", "pocket"), default="mister")
    parser.add_argument("--bank-row-track", type=int, choices=(0, 1), default=1,
                        help="Pocket SDRAM bank tracking (os20 uses 0)")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    test = root / "src/fpga/test"
    original = test / "cpu_stress"
    sources = out / "sources"
    firmware = out / "firmware"
    sources.mkdir(exist_ok=True)
    firmware.mkdir(exist_ok=True)
    subprocess.run(["python3", str(original / "generate.py"), str(firmware)], check=True)
    (sources / "link.ld").write_bytes((original / "link.ld").read_bytes())

    start = replace_once((original / "start.S").read_text(),
                         "  bne t0, t1, 3b", """  beq t0, t1, sync_trap
  li t1, 0x80000007
  bne t0, t1, 3b
  la t0, irq_count
  lw t1, 0(t0)
  addi t1, t1, 1
  sw t1, 0(t0)
  li t0, 0x400000b8
  li t1, 3
  sw t1, 0(t0)
  j trap_return
sync_trap:""")
    start = replace_once(start, "  lw t0, 0(sp)", "trap_return:\n  lw t0, 0(sp)")
    (sources / "start.S").write_text(start)
    main_c = replace_once((original / "main.c").read_text(),
                          "volatile unsigned traps;",
                          "volatile unsigned traps;\nvolatile unsigned irq_count;")
    main_c = replace_once(main_c, "    f2i_ranges();", """    *(volatile unsigned *)0x400000b4u = 4001;
    *(volatile unsigned *)0x400000b8u = 3;
    __asm__ volatile("li t0,128; csrs mie,t0; csrsi mstatus,8" ::: "t0", "memory");
    f2i_ranges();""")
    main_c = replace_once(main_c, '    puts_uart("CPU stress PASS:', r'''    __asm__ volatile("csrci mstatus,8" ::: "memory");
    *(volatile unsigned *)0x400000b8u = 2;
    if (irq_count < 200) { puts_uart("IRQ COUNT FAIL\n"); hex(irq_count); fail(); }
    puts_uart("IRQ count: "); hex(irq_count); puts_uart("\n");
    puts_uart("CPU stress PASS:''')
    (sources / "main.c").write_text(main_c)
    run(["bash", str(root / "tools/firmware-container.sh"), "-f",
         str(original / "Makefile"), "firmware", f"SOURCE_DIR={sources}/",
         f"BUILD_DIR={firmware}", f"REPO_ROOT={root}"], out / "firmware-build.log")

    tb = replace_once((test / "tb_system_sdram.v").read_text(),
                      "cpu_system cpu_sys (", "wire test_timer_irq;\ncpu_system cpu_sys (")
    tb = replace_once(tb, ".int_m_timer   (1'b0)", ".int_m_timer   (test_timer_irq)")
    tb = replace_once(tb, ".timer_irq  ()", ".timer_irq  (test_timer_irq)")
    if args.profile == "pocket":
        tb, count = re.subn(r'io_sdram\s+(\w+)\s*\(',
                           rf"assign phy_ncs = 1'b0;\nio_sdram #(.BANK_ROW_TRACK({args.bank_row_track})) \1 (", tb)
        if count != 1:
            raise RuntimeError("SDRAM controller instance changed")
        tb = replace_once(tb, "    .phy_ncs(phy_ncs),", "")
    fixture = out / "tb_system_irq.v"
    fixture.write_text(tb)
    makefile = out / "test.mk"
    makefile.write_text(f"include {test}/Makefile\n"
                        f"SYSTEM_SDRAM_SRCS := $(subst tb_system_sdram.v,{fixture},$(SYSTEM_SDRAM_SRCS))\n")
    if args.profile == "pocket":
        twin = pocket_sdram_twin(root, out)
        with makefile.open("a") as f:
            f.write(f"SYSTEM_SDRAM_SRCS := $(subst io_sdram_mister_test.v,{twin},$(SYSTEM_SDRAM_SRCS))\n")
    bench = out / "bench"
    run(["make", "-f", str(makefile), str(bench / "Vtb_system"),
         f"SYSTEM_SDRAM_DIR={bench}", f"VEXII_MISTER={args.netlist.resolve()}",
         "VERILATOR=verilator -j 4"], out / "bench-build.log", cwd=test)
    run(["env", "SCAN_PERIOD=700", "SCAN_LEN=128", "SCAN_PHASE=317",
         str(bench / "Vtb_system"), "stress.bin", "4000000"],
        out / "test.log", cwd=firmware)
    log = (out / "test.log").read_text()
    count = re.search(r"IRQ count: ([0-9a-f]{8})", log)
    if count is None or int(count[1], 16) < 200 or "CPU stress PASS:" not in log:
        raise RuntimeError(f"interrupt stress failed; see {out / 'test.log'}")
    print(f"PASS: {int(count[1], 16)} timer interrupts during integer/FPU/atomic/trap/cache stress")
    print(log[log.index("=== SUMMARY ==="):], end="")


if __name__ == "__main__":
    main()
