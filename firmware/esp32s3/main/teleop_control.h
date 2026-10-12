#pragma once
#include <stdbool.h>
#include <stdint.h>
#define TELEOP_LEASE_US 250000LL
#define TELEOP_MAX_HOLD_US 30000000LL
typedef struct { unsigned closed, session; int direction; int64_t began, renewed; bool active; } teleop_control_t;
static inline bool teleop_live(teleop_control_t *p,int64_t now) {
    return p->active&&now>=p->renewed&&now-p->renewed<TELEOP_LEASE_US&&now-p->began<TELEOP_MAX_HOLD_US;
}
static inline void teleop_close(teleop_control_t *p) {
    if(p->session>p->closed)p->closed=p->session;
    p->active=false;
}
static inline bool teleop_accept(teleop_control_t *p,unsigned encoded,int64_t now,bool idle_ready) {
    unsigned session=encoded/10; int direction=encoded%10;
    if(!session||session>999999||direction<1||direction>4)return false;
    if(p->active) {
        if(!teleop_live(p,now)||session!=p->session||direction!=p->direction)return false;
        p->renewed=now; return true;
    }
    if(!idle_ready||session<=p->closed)return false;
    p->session=session;p->direction=direction;p->began=p->renewed=now;p->active=true;return true;
}
