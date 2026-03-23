module rgb2YPbPr
(
    input wire         clk,
    input wire         ypbpr_en,

    input wire         hsync,
    input wire         vsync,
    input wire         csync,
    input wire         de,

    input  wire [23:0] din,
    output wire [23:0] dout,

    output reg         hsync_o,
    output reg         vsync_o,
    output reg         csync_o,
    output reg         de_o
);

    // Separación de canales RGB
    wire [7:0] red   = din[23:16];
    wire [7:0] green = din[15:8];
    wire [7:0] blue  = din[7:0];

    // --- Registros del Pipeline ---
    
    // Etapa 1: Multiplicadores (Shift and Add)
    reg [15:0] y_r, y_g, y_b;
    reg [15:0] pb_r, pb_g, pb_b;
    reg [15:0] pr_r, pr_g, pr_b;
    
    // Etapa 2: Sumas y Restas
    reg [16:0] y_sum, pb_sum, pr_sum;
    
    // Etapa 3: Resultados finales (Y, Pb, Pr)
    reg [7:0] y_f, pb_f, pr_f;

    // Registros para sincronía y bypass (Latencia de 3 ciclos)
    reg hsync1, vsync1, csync1, de1;
    reg hsync2, vsync2, csync2, de2;
    reg [23:0] din_d1, din_d2, din_d3;

    // --- Lógica Secuencial ---

    always @(posedge clk) begin
        
        // --- ETAPA 1: Coeficientes BT.601 (SDTV) ---
        // Y  = 0.299R + 0.587G + 0.114B  -> (77R + 150G + 29B) / 256
        y_r  <= (red   << 6) + (red   << 3) + (red   << 2) + red;   
        y_g  <= (green << 7) + (green << 4) + (green << 2) + green; 
        y_b  <= (blue  << 4) + (blue  << 3) + (blue  << 2) + blue;  

        // Pb = -0.169R - 0.331G + 0.500B -> (-43R - 85G + 128B) / 256
        pb_r <= (red   << 5) + (red   << 3) + (red   << 1) + red;  
        pb_g <= (green << 6) + (green << 4) + (green << 2) + green; 
        pb_b <= (blue  << 7);                                      

        // Pr = 0.500R - 0.419G - 0.081B  -> (128R - 107G - 21B) / 256
        pr_r <= (red   << 7);                                      
        pr_g <= (green << 6) + (green << 5) + (green << 3) + (green << 1) + green; 
        pr_b <= (blue  << 4) + (blue  << 2) + blue;                

        // Delay Line 1
        hsync1  <= hsync; vsync1 <= vsync; csync1 <= csync; de1 <= de;
        din_d1  <= din;

        // --- ETAPA 2: Combinación y Offset (128 << 8 = 32768) ---
        y_sum  <= y_r + y_g + y_b;
        pb_sum <= 17'd32768 - pb_r - pb_g + pb_b;
        pr_sum <= 17'd32768 + pr_r - pr_g - pr_b;

        // Delay Line 2
        hsync2  <= hsync1; vsync2 <= vsync1; csync2 <= csync1; de2 <= de1;
        din_d2  <= din_d1;

        // --- ETAPA 3: Blanking, Clipping y Sincronía Final ---
        if (de2) begin
            // Saturación de Y (0-255)
            y_f  <= (y_sum[16]) ? 8'hFF : y_sum[15:8];
            
            // Saturación de Pb (0-255)
            if (pb_sum[16]) // Underflow o valor negativo
                pb_f <= 8'h00;
            else if (pb_sum[15:8] > 8'hFF) // Overflow
                pb_f <= 8'hFF;
            else
                pb_f <= pb_sum[15:8];

            // Saturación de Pr (0-255)
            if (pr_sum[16]) 
                pr_f <= 8'h00;
            else if (pr_sum[15:8] > 8'hFF)
                pr_f <= 8'hFF;
            else
                pr_f <= pr_sum[15:8];
        end else begin
            // NIVELES DE BLANKING: Y=0, Pb=128, Pr=128
            y_f  <= 8'd0;
            pb_f <= 8'd128;
            pr_f <= 8'd128;
        end

        // Salida de señales de control alineadas
        hsync_o <= hsync2;
        vsync_o <= vsync2;
        csync_o <= csync2;
        de_o    <= de2;
        
        // Delay Line 3 para bypass
        din_d3  <= din_d2;
    end

    // --- Asignación de Salida ---
    // Si ypbpr_en es 1, saca YPbPr. Si es 0, saca el RGB original (con el delay compensado)
    assign dout = ypbpr_en ? {pr_f, y_f, pb_f} : din_d3;

endmodule