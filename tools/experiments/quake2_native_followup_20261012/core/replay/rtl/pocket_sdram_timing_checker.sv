// Pin-command timing monitor for AS4C32M16MSA-6BIN (CL3, BL2).
// Complements the data model; checks tRC even after a bank was precharged.
module pocket_sdram_timing_checker #(parameter MHZ=166) (
    input wire clk, cke,
    input wire [2:0] cmd,
    input wire [1:0] bank,
    input wire [12:0] address,
    output integer errors = 0
);
    localparam RCD=(18*MHZ+999)/1000, RP=(18*MHZ+999)/1000;
    localparam RAS=(48*MHZ+999)/1000, RC=(60*MHZ+999)/1000;
    localparam RFC=(80*MHZ+999)/1000, RRD=(12*MHZ+999)/1000;
    localparam WR=(15*MHZ+999)/1000, REFI=(78125*MHZ)/10000;
    integer cycle=0, last_act=-100000, last_refresh=-100000, last_mode=-100000;
    integer act[0:3], pre[0:3], wr[0:3];
    reg [3:0] active=0;
    reg write_tail=0;
    reg [1:0] write_bank;
    integer first_refresh=-1, refreshes=0;
    integer b;
    initial for(integer i=0;i<4;i=i+1) begin
        act[i]=-100000; pre[i]=-100000; wr[i]=-100000;
    end
    task check(input bit condition, input string name);
        if (!condition) begin
            errors=errors+1;
            $error("SDRAM %s violation at chip cycle %0d, bank %0d",name,cycle,bank);
        end
    endtask
    always @(posedge clk) begin
        cycle=cycle+1;
        if (cke) begin
            if (write_tail) wr[write_bank]=cycle;
            write_tail=0;
            if (cmd!=3'b111) begin
                check(cycle-last_refresh>=RFC,"tRFC");
                check(cycle-last_mode>=2,"tMRD");
            end
            case(cmd)
            3'b011: begin
                check(!active[bank],"ACT with open row");
                check(cycle-act[bank]>=RC,"tRC");
                check(cycle-pre[bank]>=RP,"tRP");
                check(cycle-last_act>=RRD,"tRRD");
                active[bank]=1; act[bank]=cycle; last_act=cycle;
            end
            3'b101,3'b100: begin
                check(active[bank],"READ/WRITE without ACT");
                check(cycle-act[bank]>=RCD,"tRCD");
                if (cmd==3'b100) begin
                    wr[bank]=cycle; write_tail=1; write_bank=bank;
                end
            end
            3'b010: for(b=0;b<4;b=b+1) if(address[10] || bank==b) begin
                if(active[b]) begin
                    check(cycle-act[b]>=RAS,"tRAS");
                    check(cycle-wr[b]>=WR,"tWR");
                end
                active[b]=0; pre[b]=cycle;
            end
            3'b001: begin
                check(active==0,"refresh with open bank");
                for(b=0;b<4;b=b+1) check(cycle-pre[b]>=RP,"refresh tRP");
                if(first_refresh<0) begin
                    first_refresh=cycle;
                    check(cycle>=(200*MHZ),"200us initialization");
                end
                refreshes=refreshes+1;
                last_refresh=cycle;
            end
            3'b000: begin
                check(active==0,"mode set with open bank");
                last_mode=cycle;
            end
            default: begin end
            endcase
            // Allow one maximum-length scanout burst to delay refresh;
            // sustained insufficient refresh cannot accumulate unnoticed.
            if(first_refresh>=0)
                check(cycle-first_refresh <= refreshes*REFI+4096+REFI,"refresh debt");
        end
    end
endmodule
