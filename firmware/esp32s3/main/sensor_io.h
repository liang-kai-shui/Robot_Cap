#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "range_guard.h"
#include "turn_control.h"
#include "test_mode.h"
#ifdef __cplusplus
extern "C" {
#endif
void sensors_start(void);
void sensors_zero_request(void);
void sensors_print(int64_t now);
range_guard_view_t sensors_forward_guard(void);
yaw_sample_t sensors_yaw_read(void);
bool tof_init(void);
// Returns true for a NEW measurement only. status=0 is a valid range.
bool tof_poll(uint16_t *mm,uint8_t *status,bool *bus_error);
#ifdef __cplusplus
}
#endif
