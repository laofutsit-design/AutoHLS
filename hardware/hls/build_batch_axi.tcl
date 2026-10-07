# Independent work directory with the frozen optimized32 kernel.cpp.
set repo [file normalize [file join [file dirname [info script]] ../..]]
open_project axi_project
set_top matmul_axi
add_files kernel.cpp
add_files [file join $repo hardware hls matmul_batch_axi.cpp]
add_files -tb [file join $repo hardware hls matmul_batch_axi_tb.cpp]
open_solution -reset solution1
set_part {xc7z020clg400-1}
create_clock -period 10.0 -name default
csim_design
csynth_design
cosim_design -rtl verilog
export_design -format ip_catalog -rtl verilog -vendor autohls.local -library hls -version 1.0
exit
