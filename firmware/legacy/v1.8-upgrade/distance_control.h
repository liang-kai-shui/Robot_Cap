#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>

// 2026-10-11 hand-roll trial: measured 500 mm, RIGHT=9595 LEFT=9647.
// Initial estimates depend on accurate marking and rolling without slip.
#define DISTANCE_TARGET_MM 300.0f
#define DISTANCE_MAX_US 6000000LL
#define DISTANCE_REPORT_DELAY_US 300000LL
#define DISTANCE_MAX_WHEEL_DIFFERENCE_MM 30.0f
static const float distance_counts_per_mm[2]={19.190f,19.294f};

static inline float distance_wheel_mm(unsigned wheel,long long forward_counts)
{
    return (float)(forward_counts/distance_counts_per_mm[wheel]);
}
static inline bool distance_encoder_valid(long long forward_counts,long long edges)
{
    long long difference=forward_counts-edges;
    if(difference<0)difference=-difference;
    return forward_counts>=-4 && edges>=0 && difference<=8+edges/100;
}
