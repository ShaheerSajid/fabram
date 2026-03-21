// Minimal wrapper that instantiates SRAM_32x4_CM4 — used only to sanity-check
// that the Liberty file parses and all ports resolve correctly under Yosys.
module top (
    input        clk,
    input        cs,
    input        wr,
    input  [4:0] addr,
    input  [3:0] din,
    output [3:0] dout
);
    SRAM_32x4_CM4 u_mem (
        .CLK   (clk),
        .CS    (cs),
        .WRITE (wr),
        .addr  (addr),
        .din   (din),
        .Q     (dout)
    );
endmodule
