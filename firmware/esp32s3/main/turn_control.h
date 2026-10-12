#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <string.h>
#include "command_parser.h"
#define TURN_TARGET_DEG 90.0f
#define TURN_MAX_US 3000000LL
#define TURN_PROGRESS_US 400000LL
#define TURN_MIN_PERCENT 28.0f
#define TURN_MAX_PERCENT 40.0f
typedef struct {
    bool valid,level,zero_pending;
    float angle_deg,rate_dps,tilt_deg;
    int64_t sample_us;
    unsigned zero_epoch,fault_events;
} yaw_sample_t;
typedef enum { TURN_RUN,TURN_REACHED,TURN_IMU_INVALID,TURN_NOT_LEVEL,TURN_NOT_STILL,
               TURN_RESET,TURN_WRONG_DIRECTION,TURN_NO_PROGRESS,TURN_TIMEOUT,
               TURN_RATE_HIGH,TURN_OVERSHOOT,TURN_TARGET_INVALID } turn_result_t;
typedef struct {
    int sign;
    float baseline,progress,progress_mark,percent,target_deg;
    unsigned epoch,fault_events;
    int64_t start_us,progress_us;
} turn_control_t;
static inline const char *turn_cause(turn_result_t r) {
    switch(r) {
        case TURN_REACHED:return "TARGET_REACHED";
        case TURN_NOT_LEVEL:return "BMI_NOT_LEVEL_OR_POSE_CHANGED";
        case TURN_NOT_STILL:return "CHASSIS_NOT_STILL";
        case TURN_RESET:return "ANGLE_RESET_DURING_TURN";
        case TURN_WRONG_DIRECTION:return "GYRO_OPPOSES_TURN";
        case TURN_NO_PROGRESS:return "NO_CHASSIS_YAW_PROGRESS";
        case TURN_TIMEOUT:return "TURN_TIMEOUT_3S";
        case TURN_RATE_HIGH:return "YAW_RATE_OVER_180DPS";
        case TURN_OVERSHOOT:return "ANGLE_OVERSHOOT_OVER_TARGET_PLUS_30DEG";
        case TURN_TARGET_INVALID:return "TURN_TARGET_OUT_OF_RANGE";
        default:return "BMI_ANGLE_INVALID_OR_STALE";
    }
}
// On a level floor, the static accelerometer direction identifies body UP even
// when the IMU is tilted or mounted upside down. Project all three gyro axes
// onto that fixed direction; do not use moving acceleration to change this axis.
static inline bool turn_project(const float reference[3],const float acc[3],
                                const float angle[3],const float w[3],yaw_sample_t *v) {
    float rn=0,an=0,dot=0,projected=0,rate=0;
    for(int i=0;i<3;i++) {
        if(!isfinite(reference[i])||!isfinite(acc[i])||!isfinite(angle[i])||!isfinite(w[i]))return false;
        rn+=reference[i]*reference[i];an+=acc[i]*acc[i];dot+=reference[i]*acc[i];
        projected+=reference[i]*angle[i];rate+=reference[i]*w[i];
    }
    rn=sqrtf(rn);an=sqrtf(an);
    if(rn<0.85f||rn>1.15f||an<0.001f)return false;
    v->angle_deg=projected/rn;v->rate_dps=rate/rn;
    v->tilt_deg=acosf(fminf(1.0f,fabsf(acc[2])/an))*57.2957795131f;
    v->level=an>=0.85f&&an<=1.15f&&v->tilt_deg<=15.0f&&dot/(rn*an)>=0.984807753f;
    return true;
}
static inline bool turn_yaw_valid(yaw_sample_t v,int64_t now) {
    return v.valid&&!v.zero_pending&&v.sample_us>0&&now>=v.sample_us&&
           now-v.sample_us<=50000&&isfinite(v.angle_deg)&&isfinite(v.rate_dps);
}
static inline turn_result_t turn_begin_target(turn_control_t *t,int sign,float target,yaw_sample_t v,int64_t now) {
    if((sign!=1&&sign!=-1)||!isfinite(target)||target<PARAM_TURN_MIN_TDEG/10.0f||target>PARAM_TURN_MAX_TDEG/10.0f)return TURN_TARGET_INVALID;
    if(!turn_yaw_valid(v,now))return TURN_IMU_INVALID;
    if(!v.level)return TURN_NOT_LEVEL;
    if(fabsf(v.rate_dps)>3.0f)return TURN_NOT_STILL;
    *t=(turn_control_t){.sign=sign,.baseline=v.angle_deg,.progress=0,.progress_mark=0,.percent=TURN_MAX_PERCENT,.target_deg=target,
        .epoch=v.zero_epoch,.fault_events=v.fault_events,.start_us=now,.progress_us=now};
    return TURN_RUN;
}
static inline turn_result_t turn_begin(turn_control_t *t,int sign,yaw_sample_t v,int64_t now) {
    return turn_begin_target(t,sign,TURN_TARGET_DEG,v,now);
}
static inline turn_result_t turn_step(turn_control_t *t,yaw_sample_t v,int64_t now) {
    if(!turn_yaw_valid(v,now))return TURN_IMU_INVALID;
    if(v.zero_epoch!=t->epoch||v.fault_events!=t->fault_events)return TURN_RESET;
    if(now<t->start_us||now-t->start_us>=TURN_MAX_US)return TURN_TIMEOUT;
    if(fabsf(v.rate_dps)>180.0f)return TURN_RATE_HIGH;
    t->progress=t->sign*(v.angle_deg-t->baseline);
    if(t->progress < -3.0f)return TURN_WRONG_DIRECTION;
    if(t->progress > t->target_deg+30.0f)return TURN_OVERSHOOT;
    if(t->progress >= t->target_deg-2.0f)return TURN_REACHED;
    if(t->progress>=t->progress_mark+1.0f) {
        t->progress_mark=t->progress;t->progress_us=now;
    }
    if(now-t->progress_us>=TURN_PROGRESS_US)return TURN_NO_PROGRESS;
    float remaining=t->target_deg-t->progress;
    t->percent=TURN_MIN_PERCENT+(TURN_MAX_PERCENT-TURN_MIN_PERCENT)*fminf(1.0f,remaining/40.0f);
    return TURN_RUN;
}
