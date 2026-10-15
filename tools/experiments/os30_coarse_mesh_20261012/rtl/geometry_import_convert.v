// Direct LOAD-result import: binary32 power-of-two scale and signed RNE.
// This is the existing app's FE_TONEAREST contract, not a general FPU.
// Unsupported nonfinite and nonzero subnormal inputs fail before cache writes.
module geometry_import_convert (
    input wire [31:0] value,
    input wire depth_mode,
    output reg [31:0] result,
    output reg error
);
    reg [23:0] mantissa;
    reg [32:0] magnitude, remainder, half;
    integer shift_count, right_count;
    always @* begin
        result = 0;
        error = 0;
        mantissa = {1'b1, value[22:0]};
        magnitude = 0;
        remainder = 0;
        half = 0;
        shift_count = 0;
        right_count = 0;
        if (value[30:23] == 8'hff ||
            (value[30:23] == 0 && value[22:0] != 0)) begin
            error = 1;
        end else if (depth_mode && (value[31] || value[30:0] <= 31'h30800000)) begin
            result = 32'd1;
        end else if (depth_mode && value[30:0] >= 31'h3fffffff) begin
            result = 32'h7fffff80;
        end else if (value[30:23] != 0) begin
            // value = mantissa * 2^(exponent - 150).
            shift_count = {24'd0, value[30:23]} + (depth_mode ? 30 : 8) - 150;
            if (shift_count >= 0) begin
                if (shift_count > 8) error = 1;
                else magnitude = {9'd0, mantissa} << shift_count;
            end else begin
                right_count = -shift_count;
                if (right_count <= 24) begin
                    magnitude = {9'd0, mantissa} >> right_count;
                    remainder = {9'd0, mantissa} & ((33'd1 << right_count) - 1'b1);
                    half = 33'd1 << (right_count - 1);
                    if (remainder > half || (remainder == half && magnitude[0]))
                        magnitude = magnitude + 1'b1;
                end
            end
            if ((!value[31] && magnitude > 33'h07fffffff) ||
                (value[31] && magnitude > 33'h080000000)) error = 1;
            result = value[31] ? -magnitude[31:0] : magnitude[31:0];
        end
    end
endmodule
