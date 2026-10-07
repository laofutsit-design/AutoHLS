# Vivado 2019.1, run in a deployment directory with a packaged axi_project IP.
set repo [file normalize [file join [file dirname [info script]] ..]]
set ipdir [file normalize axi_project/solution1/impl/ip]
if {![file exists [file join $ipdir component.xml]]} {error "Packaged HLS IP is missing"}
set_param board.repoPaths [list [file join $repo hardware vendor]]
create_project autohls_overlay vivado -part xc7z020clg400-1
set_property board_part tul.com.tw:pynq-z2:part0:1.0 [current_project]
set_property ip_repo_paths [list $ipdir] [current_project]
update_ip_catalog
create_bd_design design_1
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
    -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} [get_bd_cells ps7]
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100.000000}] [get_bd_cells ps7]
create_bd_cell -type ip -vlnv autohls.local:hls:matmul_axi:1.0 matmul_axi_0
foreach name {ctrl data} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 $name
    set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells $name]
}
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins ctrl/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ctrl/M00_AXI] [get_bd_intf_pins matmul_axi_0/s_axi_control]
connect_bd_intf_net [get_bd_intf_pins matmul_axi_0/m_axi_gmem] [get_bd_intf_pins data/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins data/M00_AXI] [get_bd_intf_pins ps7/S_AXI_HP0]
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst
# aux_reset_in is tied low below: it must be active-high, not the IP default.
set_property -dict [list CONFIG.C_EXT_RESET_HIGH {0} CONFIG.C_AUX_RESET_HIGH {1}] [get_bd_cells rst]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero
set_property CONFIG.CONST_VAL {0} [get_bd_cells zero]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins rst/dcm_locked]
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins rst/aux_reset_in] [get_bd_pins rst/mb_debug_sys_rst]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins rst/ext_reset_in]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins ps7/S_AXI_HP0_ACLK] [get_bd_pins matmul_axi_0/ap_clk] [get_bd_pins rst/slowest_sync_clk]
connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins matmul_axi_0/ap_rst_n]
foreach name {ctrl data} {
    connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins $name/ACLK] \
        [get_bd_pins $name/S00_ACLK] [get_bd_pins $name/M00_ACLK]
    connect_bd_net [get_bd_pins rst/interconnect_aresetn] [get_bd_pins $name/ARESETN]
    connect_bd_net [get_bd_pins rst/peripheral_aresetn] [get_bd_pins $name/S00_ARESETN] [get_bd_pins $name/M00_ARESETN]
}
assign_bd_address
set control_seg [get_bd_addr_segs ps7/Data/SEG_matmul_axi_0_Reg]
set_property offset 0x43C00000 $control_seg
set_property range 64K $control_seg
validate_bd_design
if {[get_property CONFIG.C_AUX_RESET_HIGH [get_bd_cells rst]] != 1} {
    error "Unsafe auxiliary reset polarity: aux_reset_in is tied low"
}
save_bd_design
write_bd_tcl overlay_bd.tcl
generate_target all [get_files design_1.bd]
add_files -norecurse [make_wrapper -files [get_files design_1.bd] -top]
set_property top design_1_wrapper [current_fileset]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis did not complete"}
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation did not complete"}
open_run impl_1
report_timing_summary -report_unconstrained -file timing_summary.rpt
report_utilization -file utilization.rpt
report_drc -file drc.rpt
check_timing -verbose -file check_timing.rpt
set setup_paths [get_timing_paths -delay_type max -max_paths 1]
set hold_paths [get_timing_paths -delay_type min -max_paths 1]
if {[llength $setup_paths] != 1 || [llength $hold_paths] != 1} {error "Missing timing paths"}
set setup_slack [get_property SLACK $setup_paths]
set hold_slack [get_property SLACK $hold_paths]
if {$setup_slack < 0 || $hold_slack < 0} {error "Routed timing failed"}
file mkdir bundle
file copy vivado/autohls_overlay.runs/impl_1/design_1_wrapper.bit bundle/autohls_matmul.bit
file copy vivado/autohls_overlay.srcs/sources_1/bd/design_1/hw_handoff/design_1.hwh bundle/autohls_matmul.hwh
puts "AUTOHLS_ROUTED_SETUP_SLACK_NS=$setup_slack"
puts "AUTOHLS_ROUTED_HOLD_SLACK_NS=$hold_slack"
puts "AUTOHLS_PREBOARD_BUILD_COMPLETE; board_verified=false"
exit
