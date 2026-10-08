#!/usr/bin/env python3
"""Compare generated fetch holds with enabled-edge captures, including stalls."""
import argparse
from pathlib import Path
import re
import subprocess
from check_vexii_fetch_reads import extract_read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", action="store_true",
                        help="Check tag/PLRU RAM reads instead of pipeline payloads")
    args = parser.parse_args()
    source = args.netlist.read_text()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    fields = []
    if args.metadata:
        for suffix in ("ways_0", "ways_1", "plru"):
            stem = "FetchL1Plugin_logic_" + suffix
            name = stem + "_mem_spinal_port1"
            width = int(re.search(rf"^  wire \[(\d+):0\] {name};$", source, re.M)[1]) + 1
            fields.append((name, stem + "_read_cmd_valid",
                           stem + "_mem[" + stem + "_read_cmd_payload]", width))
    else:
        payloads = [(i, "Fetch_WORD_PC", 32) for i in range(1, 4)]
        for i in range(2):
            payload = f"FetchL1Plugin_logic_BANKS_MUXES_{i}"
            name = f"fetch_logic_ctrls_3_up_{payload}"
            width = int(re.search(rf"^  wire \[(31|63):0\] {name};$", source, re.M)[1]) + 1
            payloads.append((3, payload, width))
        for stage, payload, width in payloads:
            fields.append((f"fetch_logic_ctrls_{stage}_up_{payload}",
                           f"fetch_logic_ctrls_{stage-1}_down_isReady",
                           f"fetch_logic_ctrls_{stage-1}_down_{payload}", width))
    tb = "module tb;\nreg clk=0;\n"
    if args.metadata:
        tb += """reg wr;
reg [7:0] wa;
reg [63:0] wd;
reg [63:0] mem[0:255];
always @(posedge clk) if(wr) mem[wa] <= wd;
"""
    drivers, checks = [], []
    for k, (name, enable, data, width) in enumerate(fields):
        tb += f"reg en{k};\nreg [{width-1}:0] ref{k};\n"
        if args.metadata:
            tb += f"reg [7:0] ra{k};\n"
            test_data = f"mem[ra{k}]"
            drivers.append(f"ra{k}=8'($random);")
        else:
            tb += f"reg [{width-1}:0] data{k};\n"
            test_data = f"data{k}"
            drivers.append(f"data{k}={width}'({{$random,$random}});")
        tb += extract_read(source, name, enable, data, width, f"en{k}", test_data)
        tb += f"always @(posedge clk) if(en{k}) ref{k} <= {width}'({test_data});\n"
        drivers.append(f"en{k}=(i==0 || ($random & 7)==0);")
        checks.append(f'if(ref{k} !== {name}) $fatal(1,"Fetch hold mismatch: {name}");')
    tb += "initial begin\n"
    if args.metadata:
        tb += "wr=0;\nfor(integer i=0;i<256;i++) mem[i]={$random,$random};\n"
    tb += "for(integer i=0;i<1000000;i++) begin\n" + "\n".join(drivers) + "\n"
    if args.metadata:
        tb += "wr=1'($random);wa=(i%8==0)?ra0:8'($random);wd={$random,$random};\n"
    tb += "#1;clk=1;#1;\n" + "\n".join(checks) + "\nclk=0;\nend\n"
    tb += f'$display("PASS: {len(fields)} fetch holds, 1000000 cycles with stalls");\n'
    tb += "$finish;\nend\nendmodule\n"
    (out / "tb.v").write_text(tb)
    with (out / "build.log").open("w") as log:
        subprocess.run(["verilator", "--binary", "--timing", "--top-module", "tb",
                        "-j", "4", "-Wno-fatal", "--Mdir", str(out / "obj"),
                        str(out / "tb.v")], stdout=log, stderr=subprocess.STDOUT, check=True)
    with (out / "test.log").open("w") as log:
        subprocess.run([str(out / "obj/Vtb")], stdout=log, stderr=subprocess.STDOUT, check=True)
    print((out / "test.log").read_text(), end="")


if __name__ == "__main__":
    main()
