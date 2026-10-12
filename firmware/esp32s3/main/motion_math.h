#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <string.h>

#define CAL_SAMPLES 250U
#define DEG_PER_RAD 57.2957795131f

typedef struct {
    float a[3];
    float w[3];
    float norm;
    float roll;
    float pitch;
    bool saturated;
    bool tilt_valid;
} motion_sample_t;

typedef struct {
    unsigned n;
    double sum_w[3], sum_w2[3], sum_a[3], sum_a2[3];
    float bias[3], angle[3], previous_w[3];
    int64_t previous_us;
    bool calibrated, angle_valid, have_previous;
} motion_state_t;

typedef enum { MOTION_SAMPLE, MOTION_CAL_RESET, MOTION_CAL_DONE, MOTION_TIME_GAP,
               MOTION_SATURATION } motion_event_t;

static inline motion_sample_t motion_convert(const int16_t a[3], const int16_t w[3], bool saturated)
{
    motion_sample_t s = {.saturated = saturated};
    for (int i = 0; i < 3; ++i) {
        s.a[i] = (float)a[i] / 16384.0f; // +/-2g, 16-bit
        s.w[i] = (float)w[i] / 131.072f; // +/-250 degrees/s, 16-bit
    }
    s.norm = sqrtf(s.a[0]*s.a[0] + s.a[1]*s.a[1] + s.a[2]*s.a[2]);
    s.tilt_valid = !saturated && s.norm >= 0.75f && s.norm <= 1.25f;
    s.roll = s.tilt_valid ? atan2f(s.a[1], s.a[2]) * DEG_PER_RAD : NAN;
    s.pitch = s.tilt_valid ? atan2f(-s.a[0], hypotf(s.a[1], s.a[2])) * DEG_PER_RAD : NAN;
    return s;
}

static inline void motion_cal_window_reset(motion_state_t *m)
{
    m->n = 0;
    memset(m->sum_w, 0, sizeof(m->sum_w));
    memset(m->sum_w2, 0, sizeof(m->sum_w2));
    memset(m->sum_a, 0, sizeof(m->sum_a));
    memset(m->sum_a2, 0, sizeof(m->sum_a2));
}

static inline void motion_break_timing(motion_state_t *m)
{
    m->have_previous = false;
    if (m->calibrated) m->angle_valid = false;
    else motion_cal_window_reset(m);
}

static inline void motion_zero(motion_state_t *m)
{
    memset(m->angle, 0, sizeof(m->angle));
    m->have_previous = false;
    m->angle_valid = m->calibrated;
}

static inline motion_event_t motion_update(motion_state_t *m, const motion_sample_t *s, int64_t now)
{
    if (s->saturated) {
        motion_break_timing(m);
        return MOTION_SATURATION;
    }
    if (!m->calibrated) {
        // This is only a motion screen. The user must keep the board stationary:
        // constant slow rotation about gravity cannot be distinguished from bias.
        bool quiet = s->norm >= 0.85f && s->norm <= 1.15f;
        for (int i = 0; i < 3; ++i) quiet = quiet && fabsf(s->w[i]) <= 3.0f;
        if (!quiet) {
            motion_cal_window_reset(m);
            return MOTION_CAL_RESET;
        }
        for (int i = 0; i < 3; ++i) {
            m->sum_w[i] += s->w[i]; m->sum_w2[i] += s->w[i]*s->w[i];
            m->sum_a[i] += s->a[i]; m->sum_a2[i] += s->a[i]*s->a[i];
        }
        if (++m->n < CAL_SAMPLES) return MOTION_SAMPLE;
        for (int i = 0; i < 3; ++i) {
            double mw = m->sum_w[i] / m->n, ma = m->sum_a[i] / m->n;
            double vw = m->sum_w2[i] / m->n - mw*mw;
            double va = m->sum_a2[i] / m->n - ma*ma;
            if (vw > 0.09 || va > 0.0004) {
                motion_cal_window_reset(m);
                return MOTION_CAL_RESET;
            }
        }
        for (int i = 0; i < 3; ++i) m->bias[i] = (float)(m->sum_w[i]/m->n);
        m->calibrated = true;
        motion_zero(m);
        return MOTION_CAL_DONE;
    }
    float corrected[3];
    for (int i = 0; i < 3; ++i) corrected[i] = s->w[i] - m->bias[i];
    if (m->have_previous) {
        int64_t delta_us = now - m->previous_us;
        if (delta_us <= 0 || delta_us > 50000) {
            motion_break_timing(m);
            return MOTION_TIME_GAP;
        }
        if (m->angle_valid) {
            float dt = (float)delta_us * 0.000001f;
            for (int i = 0; i < 3; ++i)
                m->angle[i] += 0.5f * (m->previous_w[i] + corrected[i]) * dt;
        }
    }
    memcpy(m->previous_w, corrected, sizeof(corrected));
    m->previous_us = now;
    m->have_previous = true;
    return MOTION_SAMPLE;
}
