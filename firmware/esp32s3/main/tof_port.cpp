#include "sensor_io.h"
#include "tof/VL53L1X.h"
static VL53L1X ranger;
bool tof_init(void) {
    Wire.clearError();
    ranger.setTimeout(500);
    if(!ranger.init()||Wire.failed()) return false;
    if(!ranger.setDistanceMode(VL53L1X::Long)||Wire.failed()) return false;
    if(!ranger.setMeasurementTimingBudget(50000)||Wire.failed()) return false;
    ranger.startContinuous(60);
    return !Wire.failed();
}
bool tof_poll(uint16_t *mm,uint8_t *status,bool *bus_error) {
    Wire.clearError();
    bool ready=ranger.dataReady();
    if(Wire.failed()) { *bus_error=true; return false; }
    *bus_error=false;
    if(!ready) return false;
    *mm=ranger.read(false);
    *status=static_cast<uint8_t>(ranger.ranging_data.range_status);
    *bus_error=Wire.failed()||ranger.timeoutOccurred();
    return !*bus_error;
}
