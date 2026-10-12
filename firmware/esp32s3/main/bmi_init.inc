static uint8_t address;
static esp_err_t last_bus_error;

static BMI3_INTF_RET_TYPE bus_read(uint8_t reg, uint8_t *data, uint32_t length, void *ctx)
{
    if (!ctx || !data || !length || length > BMI3_MAX_LEN) return -1;
    // Bosch already includes the two I2C dummy bytes in length and strips them
    // inside bmi3_get_regs(). Do not strip them again in this adapter.
    last_bus_error = i2c_master_write_read_device(BUS, *(uint8_t *)ctx,
                        &reg, 1, data, length, pdMS_TO_TICKS(50));
    return last_bus_error == ESP_OK ? BMI3_INTF_RET_SUCCESS : -1;
}

static BMI3_INTF_RET_TYPE bus_write(uint8_t reg, const uint8_t *data, uint32_t length, void *ctx)
{
    if (!ctx || !data || !length || length > BMI3_MAX_LEN) return -1;
    uint8_t bytes[BMI3_MAX_LEN + 1];
    bytes[0] = reg;
    memcpy(bytes + 1, data, length);
    last_bus_error = i2c_master_write_to_device(BUS, *(uint8_t *)ctx,
                        bytes, length + 1, pdMS_TO_TICKS(50));
    return last_bus_error == ESP_OK ? BMI3_INTF_RET_SUCCESS : -1;
}

static void bus_delay(uint32_t us, void *ctx)
{
    (void)ctx;
    if (us >= 1000) vTaskDelay(pdMS_TO_TICKS((us + 999) / 1000));
    else esp_rom_delay_us(us);
}

static bool api_ok(const char *operation, int8_t result)
{
    if (result == BMI3_OK) return true;
    (void)0;
    return false;
}

static bool sensor_init(struct bmi3_dev *dev)
{
    bool found = false;
    for (uint8_t a = 0x68; a <= 0x69; ++a) {
        uint8_t raw[4];
        address = a;
        if (bus_read(BMI3_REG_CHIP_ID, raw, sizeof(raw), &address) == BMI3_INTF_RET_SUCCESS &&
            raw[2] == BMI323_CHIP_ID) {
            (void)0;
            found = true;
            break;
        }
    }
    if (!found) {
        (void)0;
        return false;
    }
    *dev = (struct bmi3_dev){
        .intf = BMI3_I2C_INTF, .intf_ptr = &address,
        .read = bus_read, .write = bus_write, .delay_us = bus_delay,
        .read_write_len = 32,
    };
    if (!api_ok("bmi323_init", bmi323_init(dev))) return false;
    struct bmi3_sens_config cfg[2] = {
        {.type = BMI3_ACCEL}, {.type = BMI3_GYRO},
    };
    if (!api_ok("get config", bmi323_get_sensor_config(cfg, 2, dev))) return false;
    cfg[0].cfg.acc = (struct bmi3_accel_config){
        .odr = BMI3_ACC_ODR_100HZ, .range = BMI3_ACC_RANGE_2G,
        .bwp = BMI3_ACC_BW_ODR_QUARTER, .avg_num = BMI3_ACC_AVG4,
        .acc_mode = BMI3_ACC_MODE_NORMAL,
    };
    cfg[1].cfg.gyr = (struct bmi3_gyro_config){
        .odr = BMI3_GYR_ODR_100HZ, .range = BMI3_GYR_RANGE_250DPS,
        .bwp = BMI3_GYR_BW_ODR_QUARTER, .avg_num = BMI3_GYR_AVG4,
        .gyr_mode = BMI3_GYR_MODE_NORMAL,
    };
    if (!api_ok("set config", bmi323_set_sensor_config(cfg, 2, dev))) return false;
    if (!api_ok("config readback", bmi323_get_sensor_config(cfg, 2, dev))) return false;
    if (cfg[0].cfg.acc.odr != BMI3_ACC_ODR_100HZ || cfg[0].cfg.acc.range != BMI3_ACC_RANGE_2G ||
        cfg[0].cfg.acc.bwp != BMI3_ACC_BW_ODR_QUARTER || cfg[0].cfg.acc.avg_num != BMI3_ACC_AVG4 ||
        cfg[0].cfg.acc.acc_mode != BMI3_ACC_MODE_NORMAL ||
        cfg[1].cfg.gyr.odr != BMI3_GYR_ODR_100HZ || cfg[1].cfg.gyr.range != BMI3_GYR_RANGE_250DPS ||
        cfg[1].cfg.gyr.bwp != BMI3_GYR_BW_ODR_QUARTER || cfg[1].cfg.gyr.avg_num != BMI3_GYR_AVG4 ||
        cfg[1].cfg.gyr.gyr_mode != BMI3_GYR_MODE_NORMAL) {
        (void)0;
        return false;
    }
    struct bmi3_map_int map = {.acc_drdy_int = BMI3_INT1, .gyr_drdy_int = BMI3_INT1};
    if (!api_ok("map data-ready", bmi323_map_interrupt(map, dev))) return false;
    // Poll the status register; the module's physical INT1 pin stays unconnected.
    vTaskDelay(pdMS_TO_TICKS(250));
    struct bmi3_err_reg errors = {0};
    if (!api_ok("error status", bmi323_get_error_status(&errors, dev))) return false;
    if (errors.fatal_err || errors.acc_conf_err || errors.gyr_conf_err ||
        errors.feat_eng_ovrld || errors.feat_eng_wd) {
        (void)0;
        return false;
    }
    (void)0;
    return true;
}

