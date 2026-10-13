#include <assert.h>
#include <stdio.h>
#include "vector_control.h"
#include "command_parser.h"
static unsigned encoded(unsigned session,int right,int left) {
    return session*100000U+(unsigned)(right+100)*201U+(unsigned)(left+100);
}
int main(void) {
    for(int right=-100;right<=100;++right)for(int left=-100;left<=100;++left) {
        unsigned session;int wheels[2];
        assert(vector_decode(encoded(9999,right,left),&session,wheels));
        assert(session==9999&&wheels[0]==right&&wheels[1]==left);
    }
    unsigned session;int wheels[2];
    assert(!vector_decode(20200,&session,wheels));
    assert(!vector_decode(140401,&session,wheels));
    assert(!vector_decode(1000000200,&session,wheels));
    teleop_control_t p={0};vector_control_t v={0};
    assert(!vector_accept(&p,&v,encoded(1,0,0),1000,true));
    assert(!vector_accept(&p,&v,encoded(1,40,40),1000,false));
    assert(vector_accept(&p,&v,encoded(1,40,40),1000,true));
    v.last_sign[0]=v.last_sign[1]=1;
    assert(vector_accept(&p,&v,encoded(1,20,40),2000,false)&&!vector_reversal(&v));
    assert(vector_accept(&p,&v,encoded(1,-40,40),3000,false)&&vector_reversal(&v));
    v.pending=true;v.paused_us=3000;
    assert(!vector_resume(&v,82999,3000));
    assert(!vector_resume(&v,83000,34000)); // only49ms without new wheel motion
    assert(vector_resume(&v,84000,34000));
    assert(!vector_accept(&p,&v,encoded(2,40,40),4000,false));
    assert(!teleop_accept_pwm(&p,11040,4000,false));
    assert(!vector_accept(&p,&v,encoded(1,40,40),253000,false));
    teleop_close(&p);
    assert(!vector_accept(&p,&v,encoded(1,40,40),254000,true));
    assert(teleop_accept_pwm(&p,22040,254000,true)&&!p.vector_mode);
    const char *wire="@V128280\n@V116220\n@V100200\n";
    for(size_t split=1;split<strlen(wire);++split) {
        param_parser_t parser={0};
        param_packet_t a=param_feed(&parser,(const uint8_t*)wire,split,1000);
        assert(!a.rejected&&a.commands<=1);
        param_packet_t b=param_feed(&parser,(const uint8_t*)wire+split,strlen(wire)-split,2000);
        assert(!b.rejected&&b.commands==1&&b.units==100200);
    }
    param_parser_t parser={0};
    const char *stop="@V128280\nt@V116220\n";
    assert(param_feed(&parser,(const uint8_t*)stop,strlen(stop),1000).stop);
    puts("PASS all signed wheel pairs, lease, stopped reversal, session isolation, fragmentation and STOP priority");
    return 0;
}
