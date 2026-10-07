# Vivado 2019.1 behavioral regression using the actual vendor reset IP.
set testdir [file dirname [file normalize [info script]]]
create_project reset_regression reset_project -part xc7z020clg400-1
foreach {name polarity} {reset_bad 0 reset_good 1} {
    create_ip -name proc_sys_reset -vendor xilinx.com -library ip -version 5.0 -module_name $name
    set_property -dict [list CONFIG.C_EXT_RESET_HIGH {0} CONFIG.C_AUX_RESET_HIGH $polarity] [get_ips $name]
    generate_target simulation [get_ips $name]
}
add_files -fileset sim_1 [file join $testdir reset_polarity_tb.v]
set_property top reset_polarity_tb [get_filesets sim_1]
set_property xsim.simulate.runtime {0ns} [get_filesets sim_1]
launch_simulation
run all
close_sim
exit
