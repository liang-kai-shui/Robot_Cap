#include "Arduino.h"
#include "Wire.h"
extern "C" {
#include "driver/i2c.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
}
TwoWire Wire;
uint8_t TwoWire::endTransmission() {
    if(error||!used) { error=true; return 4; }
    esp_err_t result=i2c_master_write_to_device(I2C_NUM_1,addr,tx,used,pdMS_TO_TICKS(20));
    if(result!=ESP_OK) { error=true; return 4; }
    return 0;
}
uint8_t TwoWire::requestFrom(uint8_t address,uint8_t count) {
    pos=received=0;
    if(error||!count||count>sizeof(rx)||address!=addr) { error=true; return 0; }
    if(i2c_master_read_from_device(I2C_NUM_1,address,rx,count,pdMS_TO_TICKS(20))!=ESP_OK) {
        error=true; return 0;
    }
    received=count; return count;
}
unsigned long millis() { return esp_timer_get_time()/1000; }
void delay(unsigned long ms) { if(ms) vTaskDelay(pdMS_TO_TICKS(ms+1)); }
void delayMicroseconds(unsigned int us) { esp_rom_delay_us(us); }
