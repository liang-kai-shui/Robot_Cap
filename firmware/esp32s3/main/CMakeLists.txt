idf_component_register(SRCS "main.c" "sensor_io.c" "tof_port.cpp" "tof/Wire.cpp"
                            "tof/VL53L1X.cpp" "bosch/bmi3.c" "bosch/bmi323.c"
                       INCLUDE_DIRS "." "tof")
