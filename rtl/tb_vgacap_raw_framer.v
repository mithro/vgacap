// SPDX-License-Identifier: Apache-2.0
// Icarus testbench for the generated vgacap_raw_framer.v: drives +count
// samples from +samples (hex, one per line) with +gap idle clocks between
// them (default 1: the header overhead needs headroom; see the framer's
// docstring), and writes every output byte to +out (hex, one per line).
//
// The sink is ready on each clock with probability +ready_pct percent
// (default 100), drawn from $random seeded by +seed, so backpressure and
// overrun drops can be exercised. Every clock it checks the valid/ready hold
// rule -- a byte offered and not taken is offered again, unchanged -- and
// stops with $fatal on a violation. At the end it prints "dropped=N", the
// framer's `dropped` output.
`timescale 1ns/1ps
module tb;
    reg sys_clk = 0;
    reg sys_rst = 1;
    reg [7:0] sample = 0;
    reg sample_valid = 0;
    reg out_ready = 0;
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
    integer count, gap, ready_pct, seed, i, j, fd;
    reg [8*256-1:0] samples_path, out_path;
    reg held = 0;
    reg [7:0] held_data = 0;

    // Everything here samples the pre-edge values (all drivers are
    // nonblocking), i.e. exactly what the DUT sees on this edge.
    always @(posedge sys_clk) begin
        if (!sys_rst) begin
            if (held && !(out_valid && out_data == held_data))
                $fatal(1, "hold violation at %0t: offered %02x, now valid=%b data=%02x",
                       $time, held_data, out_valid, out_data);
            if (out_valid && out_ready)
                $fwrite(fd, "%02x\n", out_data);
            held <= out_valid && !out_ready;
            held_data <= out_data;
        end
        out_ready <= ($unsigned($random(seed)) % 100) < ready_pct;
    end

    initial begin
        if (!$value$plusargs("samples=%s", samples_path) || !$value$plusargs("count=%d", count) ||
            !$value$plusargs("out=%s", out_path)) begin
            $display("usage: +samples=FILE +count=N +out=FILE [+gap=N] [+ready_pct=P] [+seed=S]");
            $finish;
        end
        if (!$value$plusargs("gap=%d", gap)) gap = 1;
        if (!$value$plusargs("ready_pct=%d", ready_pct)) ready_pct = 100;
        if (!$value$plusargs("seed=%d", seed)) seed = 1;
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
            for (j = 0; j < gap; j = j + 1)
                @(posedge sys_clk);
        end
        sample_valid <= 0;
        repeat (20000) @(posedge sys_clk);
        $display("dropped=%0d", dropped);
        $fclose(fd);
        $finish;
    end
endmodule
