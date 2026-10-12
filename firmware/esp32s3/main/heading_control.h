#pragma once
#include "turn_control.h"
#include "speed_pi.h"

#define HEADING_KP_CPS_DEG 40.0f
#define HEADING_KD_CPS_DPS 4.0f
#define HEADING_CORRECTION_MAX_CPS 400.0f
#define HEADING_SLEW_CPS_S 1200.0f
#define HEADING_MAX_ERROR_DEG 15.0f
#define HEADING_MAX_RATE_DPS 90.0f

typedef enum { HEADING_RUN,HEADING_INVALID,HEADING_LEVEL,HEADING_STILL,
               HEADING_RESET,HEADING_DEVIATION,HEADING_RATE,HEADING_SAMPLE } heading_result_t;
typedef struct {
    float baseline,error_deg,peak_deg,correction_cps;
    double absolute_error_s,measured_s;
    unsigned epoch,fault_events;
} heading_control_t;
static inline const char *heading_cause(heading_result_t r) {
    switch(r) {
        case HEADING_LEVEL:return "HEADING_BMI_NOT_LEVEL_OR_POSE_CHANGED";
        case HEADING_STILL:return "HEADING_CHASSIS_NOT_STILL";
        case HEADING_RESET:return "HEADING_ANGLE_RESET_OR_FAULT_EVENT";
        case HEADING_DEVIATION:return "HEADING_ERROR_OVER_15DEG";
        case HEADING_RATE:return "HEADING_YAW_RATE_OVER_90DPS";
        case HEADING_SAMPLE:return "HEADING_CONTROL_SAMPLE_INVALID";
        default:return "HEADING_BMI_INVALID_OR_STALE";
    }
}
static inline heading_result_t heading_begin(heading_control_t *h,yaw_sample_t v,int64_t now) {
    if(!turn_yaw_valid(v,now))return HEADING_INVALID;
    if(!v.level)return HEADING_LEVEL;
    if(fabsf(v.rate_dps)>3.0f)return HEADING_STILL;
    *h=(heading_control_t){.baseline=v.angle_deg,.error_deg=0,.peak_deg=0,.correction_cps=0,
        .absolute_error_s=0,.measured_s=0,.epoch=v.zero_epoch,.fault_events=v.fault_events};
    return HEADING_RUN;
}
static inline heading_result_t heading_validate(heading_control_t *h,yaw_sample_t v,int64_t now) {
    if(!turn_yaw_valid(v,now))return HEADING_INVALID;
    if(v.zero_epoch!=h->epoch||v.fault_events!=h->fault_events)return HEADING_RESET;
    h->error_deg=v.angle_deg-h->baseline;
    h->peak_deg=fmaxf(h->peak_deg,fabsf(h->error_deg));
    if(fabsf(h->error_deg)>HEADING_MAX_ERROR_DEG)return HEADING_DEVIATION;
    if(fabsf(v.rate_dps)>HEADING_MAX_RATE_DPS)return HEADING_RATE;
    return HEADING_RUN;
}
static inline bool heading_targets(heading_control_t *h,yaw_sample_t v,float dt_s,float target[2]) {
    if(!isfinite(dt_s)||dt_s<=0||dt_s>0.060f||!isfinite(v.rate_dps)||!isfinite(h->error_deg))return false;
    // Positive yaw is left: reduce RIGHT speed and increase LEFT speed to steer right.
    // No change of motor polarity. Inner encoder PI tracks these bounded demands.
    float request=speed_clamp(-HEADING_KP_CPS_DEG*h->error_deg-HEADING_KD_CPS_DPS*v.rate_dps,
                             -HEADING_CORRECTION_MAX_CPS,HEADING_CORRECTION_MAX_CPS);
    float step=HEADING_SLEW_CPS_S*dt_s;
    h->correction_cps=speed_clamp(request,h->correction_cps-step,h->correction_cps+step);
    target[0]=SPEED_TARGET_CPS+h->correction_cps;
    target[1]=SPEED_TARGET_CPS-h->correction_cps;
    h->absolute_error_s+=fabsf(h->error_deg)*dt_s;h->measured_s+=dt_s;
    return true;
}
