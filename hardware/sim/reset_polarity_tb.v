`timescale 1ns / 1ps
module reset_polarity_tb;
  reg clk = 0;
  always #5 clk = ~clk;
  reg ext_n = 0;
  reg aux_good = 0;
  wire bad_ic_n, bad_per_n, good_ic_n, good_per_n;

  reset_bad bad(.slowest_sync_clk(clk), .ext_reset_in(ext_n),
    .aux_reset_in(1'b0), .mb_debug_sys_rst(1'b0), .dcm_locked(1'b1),
    .interconnect_aresetn(bad_ic_n), .peripheral_aresetn(bad_per_n));
  reset_good good(.slowest_sync_clk(clk), .ext_reset_in(ext_n),
    .aux_reset_in(aux_good), .mb_debug_sys_rst(1'b0), .dcm_locked(1'b1),
    .interconnect_aresetn(good_ic_n), .peripheral_aresetn(good_per_n));

  initial begin
    #300 ext_n = 1;
    #10000;
    if ({bad_ic_n, bad_per_n} !== 2'b00) $fatal(1, "Old reset fault not reproduced");
    if ({good_ic_n, good_per_n} !== 2'b11) $fatal(1, "Fixed reset did not release");
    $display("RESET_OLD_HELD_FIXED_RELEASED");
    ext_n = 0;
    #500;
    if ({good_ic_n, good_per_n} !== 2'b00) $fatal(1, "External reset not asserted");
    ext_n = 1;
    #10000;
    if ({good_ic_n, good_per_n} !== 2'b11) $fatal(1, "External reset not released");
    aux_good = 1;
    #500;
    if ({good_ic_n, good_per_n} !== 2'b00) $fatal(1, "Auxiliary reset not asserted");
    aux_good = 0;
    #10000;
    if ({good_ic_n, good_per_n} !== 2'b11) $fatal(1, "Auxiliary reset not released");
    if ({bad_ic_n, bad_per_n} !== 2'b00) $fatal(1, "Old configuration unexpectedly released");
    $display("RESET_POLARITY_REGRESSION_PASS");
    $finish;
  end
endmodule
