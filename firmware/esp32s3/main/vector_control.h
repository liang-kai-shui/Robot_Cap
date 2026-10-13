#pragma once
#include <stdbool.h>
#include <stdlib.h>
#include "teleop_control.h"
#define MANUAL_COOLDOWN_US 80000LL
#define MANUAL_QUIET_US 50000LL
#define VECTOR_SLEW_PERCENT_S 500.0f
typedef struct { int desired[2], last_sign[2]; bool pending; int64_t paused_us; } vector_control_t;
static inline int vector_sign(int value) { return (value>0)-(value<0); }
static inline bool vector_decode(unsigned encoded,unsigned *session,int wheel[2]) {
    *session=encoded/100000;unsigned payload=encoded%100000;
    if(!*session||*session>9999||payload>40400)return false;
    wheel[0]=(int)(payload/201)-100;wheel[1]=(int)(payload%201)-100;
    return true;
}
static inline bool vector_accept(teleop_control_t *p,vector_control_t *v,unsigned encoded,int64_t now,bool idle) {
    unsigned session;int wheel[2];
    if(!vector_decode(encoded,&session,wheel))return false;
    if(p->active) {
        if(!p->vector_mode||!teleop_live(p,now)||session!=p->session)return false;
        p->renewed=now;
    } else {
        if(!idle||session<=p->closed||(!wheel[0]&&!wheel[1]))return false;
        *v=(vector_control_t){0};
        p->session=session;p->direction=0;p->began=p->renewed=now;
        p->active=p->direct_pwm=p->vector_mode=true;
    }
    for(unsigned i=0;i<2;++i)v->desired[i]=wheel[i];
    p->pwm_percent=abs(wheel[0])>abs(wheel[1])?abs(wheel[0]):abs(wheel[1]);
    return true;
}
static inline bool vector_reversal(const vector_control_t *v) {
    for(unsigned i=0;i<2;++i) {
        int sign=vector_sign(v->desired[i]);
        if(sign&&v->last_sign[i]&&sign!=v->last_sign[i])return true;
    }
    return false;
}
static inline bool vector_resume(const vector_control_t *v,int64_t now,int64_t last_motion) {
    return v->pending&&now>=v->paused_us&&now>=last_motion&&
           now-v->paused_us>=MANUAL_COOLDOWN_US&&now-last_motion>=MANUAL_QUIET_US;
}
