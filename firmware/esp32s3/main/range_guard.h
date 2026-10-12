#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define FRONT_STOP_MM 250U
#define FRONT_CLEAR_MM 350U
#define FRONT_FRESH_US 200000LL
#define FRONT_CLEAR_SAMPLES 3U
typedef enum { FRONT_CLEAR,FRONT_NOT_READY,FRONT_OBSTACLE,FRONT_BUS_ERROR,
               FRONT_INVALID_RANGE,FRONT_STALE,FRONT_CONFIRM_CLEAR } front_cause_t;
typedef struct {
    bool ready,valid,blocked;
    front_cause_t cause,last_unsafe;
    uint32_t unsafe_sequence;
    unsigned clear_samples;
    uint16_t mm;
    uint8_t status;
    int64_t sample_us;
} range_guard_t;
typedef struct {
    bool blocked,valid;
    front_cause_t cause,last_unsafe;
    uint32_t unsafe_sequence;
    unsigned clear_samples;
    uint16_t mm;
    uint8_t status;
    int64_t age_us;
} range_guard_view_t;
static inline const char *front_cause_name(front_cause_t cause) {
    switch(cause) {
        case FRONT_CLEAR:return "CLEAR";
        case FRONT_OBSTACLE:return "OBSTACLE_WITHIN_250MM";
        case FRONT_BUS_ERROR:return "TOF_I2C_ERROR";
        case FRONT_INVALID_RANGE:return "TOF_INVALID_RANGE";
        case FRONT_STALE:return "TOF_STALE_OVER_200MS";
        case FRONT_CONFIRM_CLEAR:return "WAIT_3_READINGS_AT_350MM";
        default:return "TOF_NOT_READY";
    }
}
static inline void range_guard_reset(range_guard_t *g,bool ready) {
    memset(g,0,sizeof(*g));g->ready=ready;g->blocked=true;
    g->cause=g->last_unsafe=FRONT_NOT_READY;
}
static inline void range_guard_fail(range_guard_t *g,front_cause_t cause) {
    g->blocked=true;g->cause=g->last_unsafe=cause;g->clear_samples=0;
    ++g->unsafe_sequence;
    if(cause!=FRONT_OBSTACLE)g->valid=false;
}
static inline void range_guard_observe(range_guard_t *g,bool fresh,bool bus_error,
                                      uint16_t mm,uint8_t status,int64_t now) {
    if(bus_error) { range_guard_fail(g,FRONT_BUS_ERROR);return; }
    if(!fresh)return; // A repeated task poll must not count as another measurement.
    // Nonmonotonic timestamps also cannot establish new clearance.
    if(now<=0||now<=g->sample_us) {range_guard_fail(g,FRONT_STALE);return;}
    if(g->sample_us && now-g->sample_us>FRONT_FRESH_US)range_guard_fail(g,FRONT_STALE);
    g->sample_us=now;g->mm=mm;g->status=status;
    g->valid=g->ready&&status==0&&mm>0;
    if(!g->valid) {range_guard_fail(g,FRONT_INVALID_RANGE);return;}
    if(mm<=FRONT_STOP_MM) {range_guard_fail(g,FRONT_OBSTACLE);return;}
    if(g->blocked) {
        if(mm<FRONT_CLEAR_MM)g->clear_samples=0;
        else ++g->clear_samples;
        if(g->clear_samples<FRONT_CLEAR_SAMPLES) {g->cause=FRONT_CONFIRM_CLEAR;return;}
        g->blocked=false;
    }
    g->cause=FRONT_CLEAR;
}
static inline range_guard_view_t range_guard_read(range_guard_t *g,int64_t now) {
    if(!g->ready) {g->blocked=true;g->valid=false;g->cause=FRONT_NOT_READY;}
    else if(g->sample_us && (now<g->sample_us||now-g->sample_us>FRONT_FRESH_US)) {
        if(g->cause!=FRONT_STALE)range_guard_fail(g,FRONT_STALE);
    }
    else if(!g->sample_us) {g->blocked=true;g->valid=false;}
    return (range_guard_view_t){.blocked=g->blocked,.valid=g->valid,
        .cause=g->cause,.last_unsafe=g->last_unsafe,.unsafe_sequence=g->unsafe_sequence,
        .clear_samples=g->clear_samples,.mm=g->mm,.status=g->status,
        .age_us=g->sample_us?now-g->sample_us:-1};
}
