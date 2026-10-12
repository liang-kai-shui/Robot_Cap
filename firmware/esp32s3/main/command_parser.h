#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <limits.h>
#include <stdio.h>

#define PARAM_MOVE_MIN_MM 50
#define PARAM_MOVE_MAX_MM 300
#define PARAM_TURN_MIN_TDEG 150
#define PARAM_TURN_MAX_TDEG 900
#define PARAM_FRAME_TIMEOUT_US 250000LL

// @F200\n = forward 200mm; @J450\n/@L450\n = left/right 45.0deg.
// T always wins, including inside a fragmented or malformed frame.
typedef struct {
    char text[16]; unsigned used;
    bool receiving,discarding;
    int64_t started_us;
} param_parser_t;
typedef struct {
    bool stop,rejected,waiting;
    unsigned commands,legacy_len;
    char action; int units;
    uint8_t legacy[32];
} param_packet_t;

static inline bool param_decode(const char *text,unsigned length,char *action,int *units) {
    if(length<2 || !strchr("FBJLRHU",text[0]))return false;
    int n=0;
    for(unsigned i=1;i<length;i++) {
        if(text[i]<'0'||text[i]>'9')return false;
        int digit=text[i]-'0';
        if(n>(INT_MAX-digit)/10)return false;
        n=n*10+digit;
    }
    int minimum=(text[0]=='F'||text[0]=='B')?PARAM_MOVE_MIN_MM:text[0]=='R'?100:text[0]=='H'?11:text[0]=='U'?11001:PARAM_TURN_MIN_TDEG;
    int maximum=(text[0]=='F'||text[0]=='B')?PARAM_MOVE_MAX_MM:text[0]=='R'?1000:text[0]=='H'?9999994:text[0]=='U'?999994100:PARAM_TURN_MAX_TDEG;
    if(n<minimum||n>maximum)return false;
    if(text[0]=='H'&&(n%10<1||n%10>4))return false;
    if(text[0]=='U'&&(n/10000<1||n/10000>99999||n/1000%10<1||n/1000%10>4||n%1000<1||n%1000>100))return false;
    *action=(char)(text[0]+('a'-'A')); *units=n;
    return true;
}
// UART is a byte stream: repeated renewals may be coalesced into one read.
// Only the SAME session, direction and mode can collapse; finite motions never queue.
static inline bool param_same_renewal(char action,int previous,int next) {
    return action=='h'?previous==next:action=='u'&&previous/1000==next/1000;
}
static inline bool param_pending_renewal(const param_parser_t *p,char action,int units) {
    if(p->discarding)return false;
    char expected[16];
    int length=snprintf(expected,sizeof(expected),"%c%d",action-('a'-'A'),units);
    if(length<1||p->used>(unsigned)length)return false;
    unsigned identity=(unsigned)length-(action=='u'?3U:0U);
    unsigned compare=p->used<identity?p->used:identity;
    return memcmp(p->text,expected,compare)==0;
}
static inline param_packet_t param_feed(param_parser_t *p,const uint8_t *bytes,size_t length,int64_t now) {
    param_packet_t out;memset(&out,0,sizeof(out));
    for(size_t i=0;i<length;i++)if(bytes[i]=='t'||bytes[i]=='T') {
        memset(p,0,sizeof(*p));out.stop=true;return out;
    }
    if(p->receiving&&(now<p->started_us||now-p->started_us>=PARAM_FRAME_TIMEOUT_US)) {
        p->receiving=false;p->discarding=true;out.rejected=true;
    }
    for(size_t i=0;i<length;i++) {
        uint8_t b=bytes[i];
        if(p->discarding) {if(b=='\n')p->discarding=false;continue;}
        if(p->receiving) {
            if(b=='\n') {
                p->receiving=false;
                char action=0;int units=0;
                if(param_decode(p->text,p->used,&action,&units)) {
                    if(out.commands==1&&out.action==action&&param_same_renewal(action,out.units,units)) {
                        out.units=units; // Latest PWM in this read, same live press.
                    } else {++out.commands;out.action=action;out.units=units;}
                } else out.rejected=true;
                p->used=0;
            } else if(b!='\r') {
                if(p->used>=sizeof(p->text)) {
                    p->receiving=false;p->discarding=true;out.rejected=true;
                } else p->text[p->used++]=(char)b;
            }
        } else if(b=='@') {
            p->receiving=true;p->used=0;p->started_us=now;
        } else if(b!='\n'&&b!='\r'&&b!=' '&&b!='\t') {
            if(out.legacy_len>=sizeof(out.legacy))out.rejected=true;
            else out.legacy[out.legacy_len++]=b;
        }
    }
    out.waiting=p->receiving||p->discarding;
    bool renewal_tail=out.commands==1&&(out.action=='h'||out.action=='u')&&
        !out.legacy_len&&param_pending_renewal(p,out.action,out.units);
    if(out.commands>1||(out.commands&&(out.legacy_len||(out.waiting&&!renewal_tail)))||
       (out.waiting&&out.legacy_len))out.rejected=true;
    return out;
}
