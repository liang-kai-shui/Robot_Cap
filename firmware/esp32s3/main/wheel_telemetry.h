#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <stdlib.h>
#include "distance_control.h"

typedef struct { bool valid; float cps,speed_mm_s,rpm; } wheel_telemetry_t;
static inline wheel_telemetry_t wheel_telemetry(unsigned wheel,long long delta,
                                               long long edges,int64_t dt_us) {
    wheel_telemetry_t v={0};
    if(wheel>=2||dt_us<50000||dt_us>1000000||edges<0||llabs(delta)>edges+8) return v;
    v.cps=(float)(delta*1000000.0/dt_us);
    v.speed_mm_s=v.cps/distance_counts_per_mm[wheel];
    v.rpm=v.speed_mm_s*60.0f/(3.14159265358979323846f*43.0f);
    v.valid=isfinite(v.speed_mm_s)&&isfinite(v.rpm);
    return v;
}
