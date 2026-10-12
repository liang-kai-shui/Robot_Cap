#pragma once
#include <stdbool.h>
#include <math.h>

#define SPEED_TARGET_CPS 2200.0f
#define SPEED_START_PERCENT 40.0f
#define SPEED_MAX_PERCENT 60.0f
#define SPEED_KP 0.006f
#define SPEED_KI 0.025f
#define SPEED_FILTER_TAU_S 0.040f
#define SPEED_SLEW_PERCENT_S 100.0f

typedef struct {
    float filtered_cps, integral, percent;
} speed_pi_t;

static inline float speed_clamp(float value, float low, float high)
{
    return fmaxf(low, fminf(high, value));
}

static inline void speed_pi_reset(speed_pi_t *s)
{
    *s = (speed_pi_t){.percent = SPEED_START_PERCENT};
}

static inline bool speed_pi_update_bounded(speed_pi_t *s, float raw_cps, float target_cps, float dt_s, float minimum)
{
    if (!isfinite(raw_cps) || raw_cps < 0 || !isfinite(target_cps) || target_cps < minimum || target_cps > 2600 ||
        !isfinite(dt_s) || dt_s <= 0 || dt_s > 0.060f)
        return false;
    float alpha = dt_s / (SPEED_FILTER_TAU_S + dt_s);
    s->filtered_cps += alpha * (raw_cps - s->filtered_cps);
    float error = target_cps - s->filtered_cps;
    float candidate_i = speed_clamp(s->integral + SPEED_KI*error*dt_s, -20.0f, 20.0f);
    float step = SPEED_SLEW_PERCENT_S * dt_s;
    float low = fmaxf(0.0f, s->percent - step);
    float high = fminf(SPEED_MAX_PERCENT, s->percent + step);
    float request = SPEED_START_PERCENT + SPEED_KP*error + candidate_i;
    float output = speed_clamp(request, low, high);
    // Conditional integration also accounts for the slew limit, so error cannot
    // accumulate while either output limit prevents the requested correction.
    if ((request > output && error > 0) || (request < output && error < 0)) {
        request = SPEED_START_PERCENT + SPEED_KP*error + s->integral;
        output = speed_clamp(request, low, high);
    } else {
        s->integral = candidate_i;
    }
    s->percent = output;
    return true;
}
static inline bool speed_pi_update_target(speed_pi_t *s, float raw_cps, float target_cps, float dt_s)
{
    return speed_pi_update_bounded(s,raw_cps,target_cps,dt_s,1800.0f);
}
static inline bool speed_pi_update(speed_pi_t *s, float raw_cps, float dt_s)
{
    return speed_pi_update_target(s,raw_cps,SPEED_TARGET_CPS,dt_s);
}
