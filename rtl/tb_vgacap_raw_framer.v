// SPDX-License-Identifier: Apache-2.0
// Icarus testbench for the generated vgacap_raw_framer.v: drives +count
// samples from +samples (hex, one per line), one every other clock (the
// header overhead needs headroom; see the framer's docstring), with the sink
// always ready, and writes every output byte to +out (hex, one per line).
`timescale 1ns/1ps
module tb;
    reg sys_clk = 0;
    reg sys_rst = 1;
    reg [7:0] sample = 0;
    reg sample_valid = 0;
    reg out_ready = 1;
    wire [7:0] out_data;
    wire out_valid;
    wire [31:0] dropped;

    vgacap_raw_framer dut (
        .sys_clk(sys_clk), .sys_rst(sys_rst),
        .sample(sample), .sample_valid(sample_valid),
        .out_data(out_data), .out_valid(out_valid), .out_ready(out_ready),
        .dropped(dropped)
    );

    always #5 sys_clk = ~sys_clk;

    reg [7:0] mem [0:1048575];
    integer count, i, fd;
    reg [8*256-1:0] samples_path, out_path;

    always @(posedge sys_clk)
        if (out_valid && out_ready)
            $fwrite(fd, "%02x\n", out_data);

    initial begin
        if (!$value$plusargs("samples=%s", samples_path) || !$value$plusargs("count=%d", count) ||
            !$value$plusargs("out=%s", out_path)) begin
            $display("usage: +samples=FILE +count=N +out=FILE");
            $finish;
        end
        $readmemh(samples_path, mem);
        fd = $fopen(out_path, "w");
        repeat (4) @(posedge sys_clk);
        sys_rst <= 0;
        @(posedge sys_clk);
        for (i = 0; i < count; i = i + 1) begin
            sample <= mem[i];
            sample_valid <= 1;
            @(posedge sys_clk);
            sample_valid <= 0;
            @(posedge sys_clk);
        end
        sample_valid <= 0;
        repeat (20000) @(posedge sys_clk);
        $fclose(fd);
        $finish;
    end
endmodule
