#!/usr/bin/env python3
"""Exhaustively compare original and factored single/dual-issue fetch readiness."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TRANSFORM = ROOT / "src/fpga/vendor/vexriscv/factor_fetch_ready.pl"


def fixture(width):
    text = f"""module original_{width} (
    input [{2 * width + 5}:0] inputs,
    output fetch_logic_ctrls_3_down_ready
);
  reg [{width - 1}:0] AlignerPlugin_logic_buffer_mask;
  wire [{2 * width - 1}:0] AlignerPlugin_logic_buffer_usedMask;
  wire AlignerPlugin_logic_buffer_downFire, AlignerPlugin_logic_buffer_haltUp;
  wire decode_ctrls_0_up_ready, decode_ctrls_1_up_ready, decode_ctrls_2_up_ready;
  wire decode_ctrls_0_down_isReady, decode_ctrls_1_down_isReady, decode_ctrls_2_down_isReady;
  reg decode_ctrls_0_down_ready, decode_ctrls_1_down_ready;
  wire decode_ctrls_0_up_isReady, when_StageLink_l71_2, when_StageLink_l71_3;
  wire AlignerPlugin_api_haltIt, fetch_logic_ctrls_3_down_valid;
  wire decode_ctrls_1_up_isValid, decode_ctrls_2_up_isValid;
  wire decode_ctrls_0_up_isCancel, decode_ctrls_2_down_ready;
  assign AlignerPlugin_logic_buffer_mask = inputs[{width - 1}:0];
  assign AlignerPlugin_logic_buffer_usedMask = {{{width}'b0, inputs[{2 * width - 1}:{width}]}};
  assign {{AlignerPlugin_api_haltIt, fetch_logic_ctrls_3_down_valid,
           decode_ctrls_1_up_isValid, decode_ctrls_2_up_isValid,
           decode_ctrls_0_up_isCancel, decode_ctrls_2_down_ready}} = inputs[{2 * width + 5}:{2 * width}];
"""
    for stage in range(3):
        text += f"  assign decode_ctrls_{stage}_up_ready = decode_ctrls_{stage}_down_isReady;\n"
        text += f"  assign decode_ctrls_{stage}_down_isReady = decode_ctrls_{stage}_down_ready;\n"
    text += """  assign decode_ctrls_0_up_isReady = decode_ctrls_0_up_ready;
  assign when_StageLink_l71_2 = (! decode_ctrls_1_up_isValid);
  assign when_StageLink_l71_3 = (! decode_ctrls_2_up_isValid);
  assign AlignerPlugin_logic_buffer_downFire = (decode_ctrls_0_up_isReady || decode_ctrls_0_up_isCancel);
"""
    text += f"  assign AlignerPlugin_logic_buffer_haltUp = ((|(AlignerPlugin_logic_buffer_mask & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[{width - 1} : 0] : {width}'b{'0' * width})))) || AlignerPlugin_api_haltIt);\n"
    for stage in range(2):
        text += f"""  always @(*) begin
    decode_ctrls_{stage}_down_ready = decode_ctrls_{stage + 1}_up_ready;
    if(when_StageLink_l71_{stage + 2}) begin
      decode_ctrls_{stage}_down_ready = 1'b1;
    end
  end
"""
    return text + """  assign fetch_logic_ctrls_3_down_ready = ((! fetch_logic_ctrls_3_down_valid) || (! AlignerPlugin_logic_buffer_haltUp));
endmodule
"""


with tempfile.TemporaryDirectory() as name:
    out = Path(name)
    modules = []
    for width in (2, 4):
        original = fixture(width)
        cpu = out / f"cpu{width}.v"
        cpu.write_text(original)
        subprocess.run(["perl", str(TRANSFORM), str(cpu)], check=True)
        factored = cpu.read_text()
        subprocess.run(["perl", str(TRANSFORM), str(cpu)], check=True)
        assert cpu.read_text() == factored, "transform must be idempotent"
        assert f"usedMask[{width - 1}:0]" in factored
        modules += [original, factored.replace(f"module original_{width}", f"module factored_{width}")]
        for malformed in (
            original.replace("(! decode_ctrls_1_up_isValid)", "decode_ctrls_1_up_isValid"),
            original.replace(f"reg [{width - 1}:0]", "reg [7:0]"),
            original.replace("buffer_downFire ?", "buffer_haltUp ?"),
            original.replace("endmodule", "  assign fetch_logic_ctrls_3_down_ready = 1'b0;\nendmodule"),
        ):
            cpu.write_text(malformed)
            result = subprocess.run(["perl", str(TRANSFORM), str(cpu)], capture_output=True)
            assert result.returncode != 0 and cpu.read_text() == malformed
    modules.append("""module tb;
  reg [13:0] inputs;
  wire old2, new2, old4, new4;
  original_2 o2(inputs[9:0], old2);
  factored_2 f2(inputs[9:0], new2);
  original_4 o4(inputs, old4);
  factored_4 f4(inputs, new4);
  initial begin
    for (integer i = 0; i < 16384; i = i + 1) begin
      inputs = 14'(i);
      #1;
      if (old2 !== new2 || old4 !== new4) $fatal(1, "Mismatch at %0d", i);
    end
    $display("PASS: exhaustive fetch readiness for 2- and 4-slice buffers");
    $finish;
  end
endmodule
""")
    (out / "tb.v").write_text("\n".join(modules))
    result = subprocess.run(["verilator", "--binary", "--timing", "-j", "4",
                             "-Wno-fatal", "--top-module", "tb", "--Mdir", str(out / "obj"),
                             str(out / "tb.v")], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    subprocess.run([str(out / "obj/Vtb")], check=True)
print("PASS: idempotence and rejected dependency/width/writer changes")
