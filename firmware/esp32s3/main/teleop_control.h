#pragma once
#include <stdbool.h>
#include <stdint.h>
#define TELEOP_LEASE_US 250000LL
#define TELEOP_NO_FEEDBACK_US 150000LL
// A recovery category, not a measured motor starting threshold. Never boost PWM.
#define TELEOP_LOW_PWM_BOUNDARY 40
typedef enum { TELEOP_FEEDBACK_OK, TELEOP_FEEDBACK_LOW_STOP, TELEOP_FEEDBACK_FAULT } teleop_feedback_t;
static inline teleop_feedback_t teleop_feedback(bool direct_pwm,int percent,int64_t silence_us) {
    if(silence_us<TELEOP_NO_FEEDBACK_US)return TELEOP_FEEDBACK_OK;
    if(direct_pwm&&percent>0&&percent<TELEOP_LOW_PWM_BOUNDARY)return TELEOP_FEEDBACK_LOW_STOP;
    return TELEOP_FEEDBACK_FAULT;
}
#define TELEOP_MAX_HOLD_US 30000000LL
typedef struct { unsigned closed, session; int direction, pwm_percent; int64_t began, renewed; bool active, direct_pwm; } teleop_control_t;
static inline bool teleop_live(teleop_control_t *p,int64_t now) {
    return p->active&&now>=p->renewed&&now-p->renewed<TELEOP_LEASE_US&&now-p->began<TELEOP_MAX_HOLD_US;
}
static inline void teleop_close(teleop_control_t *p) {
    if(p->session>p->closed)p->closed=p->session;
    p->active=false;
}
static inline bool teleop_accept_mode(teleop_control_t *p,unsigned session,int direction,
                                     bool direct_pwm,int percent,int64_t now,bool idle_ready) {
    if(!session||session>(direct_pwm?99999U:999999U)||direction<1||direction>4||
       (direct_pwm&&(percent<1||percent>100)))return false;
    if(p->active) {
        if(!teleop_live(p,now)||session!=p->session||direction!=p->direction||direct_pwm!=p->direct_pwm)return false;
        p->renewed=now;p->pwm_percent=percent; return true;
    }
    if(!idle_ready||session<=p->closed)return false;
    p->session=session;p->direction=direction;p->began=p->renewed=now;p->active=true;
    p->direct_pwm=direct_pwm;p->pwm_percent=percent;return true;
}
static inline bool teleop_accept(teleop_control_t *p,unsigned encoded,int64_t now,bool idle_ready) {
    return teleop_accept_mode(p,encoded/10,encoded%10,false,0,now,idle_ready);
}
static inline bool teleop_accept_pwm(teleop_control_t *p,unsigned encoded,int64_t now,bool idle_ready) {
    return teleop_accept_mode(p,encoded/10000,encoded/1000%10,true,encoded%1000,now,idle_ready);
}
