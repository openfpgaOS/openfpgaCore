//
// Fast Behavioral SDRAM AXI4 Slave
//
// Replaces the full io_sdram + sdram_model stack with a simple
// memory array for fast simulation. Reduces SDRAM latency from
// ~50 cycles to ~4 cycles so VexiiRiscv's pipeline doesn't freeze
// on D-cache back-pressure.
//
// Timing:
//   Burst reads:  1 cycle latency + 1 cycle per beat
//   Burst writes: 1 cycle per beat + 1 cycle for B response
//
// 32MB memory (8M x 32-bit words), matching real SDRAM capacity.
//

`default_nettype none

module sdram_fast_model (
    input wire clk,
    input wire reset_n,

    // AXI4 Slave interface — same signal names as axi_sdram_slave
    // AR channel
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    // R channel
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output reg         s_axi_rlast,

    // AW channel
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    // W channel
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    // B channel
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    output wire [1:0]  s_axi_bresp,

    // Backdoor write port (for firmware preload from C++ harness)
    input  wire        bd_we,
    input  wire [23:0] bd_word_addr,
    input  wire [31:0] bd_wdata
);

    assign s_axi_rresp = 2'b00;  // OKAY
    assign s_axi_bresp = 2'b00;  // OKAY

    // 32MB = 8M words of 32 bits
    // Address is byte-addressed on AXI, word index = addr[24:2]
    reg [31:0] mem [0:8388607];  // 8M x 32

    // Read state machine
    localparam R_IDLE = 0, R_DATA = 1;
    reg        r_state;
    reg [22:0] r_addr;    // word address (23 bits for 8M words)
    reg [7:0]  r_cnt;
    reg [7:0]  r_len;

    // Write state machine
    localparam W_IDLE = 0, W_DATA = 1, W_RESP = 2;
    reg [1:0]  w_state;
    reg [22:0] w_addr;
    reg [7:0]  w_cnt;
    reg [7:0]  w_len;

    // Read logic
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            r_state <= R_IDLE;
            s_axi_arready <= 1;
            s_axi_rvalid <= 0;
            s_axi_rdata <= 0;
            s_axi_rlast <= 0;
            r_addr <= 0;
            r_cnt <= 0;
            r_len <= 0;
        end else begin
            case (r_state)
                R_IDLE: begin
                    s_axi_rvalid <= 0;
                    s_axi_rlast <= 0;
                    if (s_axi_arvalid && s_axi_arready) begin
                        // Accept read address — start delivering data next cycle
                        r_addr <= s_axi_araddr[24:2];
                        r_len <= s_axi_arlen;
                        r_cnt <= 0;
                        s_axi_arready <= 0;
                        r_state <= R_DATA;
                    end
                end

                R_DATA: begin
                    // Deliver one beat per cycle with backpressure support.
                    // Hold rvalid + rdata until rready acknowledges the beat.
                    if (!s_axi_rvalid) begin
                        // Present new beat
                        s_axi_rvalid <= 1;
                        s_axi_rdata <= mem[r_addr];
                        s_axi_rlast <= (r_cnt == r_len);
                    end else if (s_axi_rready) begin
                        // Beat accepted — advance
                        if (s_axi_rlast) begin
                            s_axi_rvalid <= 0;
                            s_axi_rlast <= 0;
                            s_axi_arready <= 1;
                            r_state <= R_IDLE;
                        end else begin
                            // Present next beat immediately
                            r_addr <= r_addr + 1;
                            r_cnt <= r_cnt + 1;
                            s_axi_rdata <= mem[r_addr + 1];
                            s_axi_rlast <= ((r_cnt + 1) == r_len);
                        end
                    end
                    // else: hold current beat (rvalid=1, rready=0)
                end
            endcase
        end
    end

    // Write logic + backdoor write (single driver for mem)
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            w_state <= W_IDLE;
            s_axi_awready <= 1;
            s_axi_wready <= 0;
            s_axi_bvalid <= 0;
            w_addr <= 0;
            w_cnt <= 0;
            w_len <= 0;
        end else begin
            case (w_state)
                W_IDLE: begin
                    s_axi_bvalid <= 0;
                    if (s_axi_awvalid && s_axi_awready) begin
                        w_addr <= s_axi_awaddr[24:2];
                        w_len <= s_axi_awlen;
                        w_cnt <= 0;
                        s_axi_awready <= 0;
                        s_axi_wready <= 1;
                        w_state <= W_DATA;
                    end
                end

                W_DATA: begin
                    if (s_axi_wvalid && s_axi_wready) begin
                        // Byte-lane write with strobe
                        if (s_axi_wstrb[0]) mem[w_addr][7:0]   <= s_axi_wdata[7:0];
                        if (s_axi_wstrb[1]) mem[w_addr][15:8]  <= s_axi_wdata[15:8];
                        if (s_axi_wstrb[2]) mem[w_addr][23:16] <= s_axi_wdata[23:16];
                        if (s_axi_wstrb[3]) mem[w_addr][31:24] <= s_axi_wdata[31:24];

                        if (s_axi_wlast) begin
                            s_axi_wready <= 0;
                            s_axi_bvalid <= 1;
                            w_state <= W_RESP;
                        end else begin
                            w_addr <= w_addr + 1;
                            w_cnt <= w_cnt + 1;
                        end
                    end
                end

                W_RESP: begin
                    if (s_axi_bvalid && s_axi_bready) begin
                        s_axi_bvalid <= 0;
                        s_axi_awready <= 1;
                        w_state <= W_IDLE;
                    end
                end

                default: w_state <= W_IDLE;
            endcase
        end

        // Backdoor write (from C++ harness, active during reset or runtime)
        if (bd_we) begin
            mem[bd_word_addr[22:0]] <= bd_wdata;
        end
    end

endmodule
