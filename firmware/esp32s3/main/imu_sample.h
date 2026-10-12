#pragma once
#include "motion_math.h"
// BMI323 SAT_FLAGS: ACC XYZ bits 0..2, GYR XYZ bits 3..5. Tilt and calibration
// use accelerometer data; relative rotation integration uses gyroscope data.
typedef struct {
    motion_sample_t motion;
    uint8_t sat_mask;
    bool accel_saturated;
} imu_frame_t;
static inline imu_frame_t imu_convert(const int16_t acc[3],const int16_t gyro[3],uint8_t sat_mask) {
    imu_frame_t f={.sat_mask=sat_mask,.accel_saturated=(sat_mask&0x07)!=0};
    f.motion=motion_convert(acc,gyro,(sat_mask&0x38)!=0);
    if(f.accel_saturated) {
        f.motion.tilt_valid=false;
        f.motion.roll=f.motion.pitch=NAN;
    }
    return f;
}
static inline motion_event_t imu_update(motion_state_t *m,const imu_frame_t *f,int64_t now) {
    // Never calibrate bias from a clipped accelerometer sample. Once calibrated,
    // clipped acceleration alone does not invalidate independent gyro rotation.
    if(!m->calibrated && f->accel_saturated && !f->motion.saturated) {
        motion_cal_window_reset(m);
        return MOTION_CAL_RESET;
    }
    return motion_update(m,&f->motion,now);
}
