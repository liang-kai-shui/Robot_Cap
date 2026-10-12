#include <assert.h>
#include <stdio.h>
#include <math.h>
#include "command_parser.h"
#include "turn_control.h"
#include "wheel_telemetry.h"
#include "teleop_control.h"
#include "speed_pi.h"

static param_packet_t feed(param_parser_t *p,const char *s,int64_t now) {
    return param_feed(p,(const uint8_t *)s,strlen(s),now);
}
int main(void) {
    teleop_control_t hold={0};
    assert(!teleop_accept(&hold,11,1000,false));
    assert(teleop_accept(&hold,11,1000,true));
    assert(teleop_live(&hold,250999));
    assert(!teleop_live(&hold,251000));
    assert(!teleop_accept(&hold,11,251000,true)); // expired heartbeat cannot restart
    teleop_close(&hold);
    assert(!teleop_accept(&hold,11,300000,true)); // old session cannot restart
    assert(teleop_accept(&hold,22,300000,true));
    assert(!teleop_accept(&hold,23,310000,true)); // no live reversal
    assert(teleop_accept(&hold,22,310000,false)); // renew live same direction
    hold.began=0;hold.renewed=30000000;
    assert(!teleop_live(&hold,30000000)); // maximum hold even with heartbeat
    speed_pi_t slow={0};speed_pi_reset(&slow);
    assert(speed_pi_update_bounded(&slow,500,630,.02f,600));
    assert(!speed_pi_update_bounded(&slow,500,599,.02f,600));
    wheel_telemetry_t speed=wheel_telemetry(0,1919,1919,1000000);
    assert(speed.valid&&fabsf(speed.speed_mm_s-100.0f)<.01f);
    assert(fabsf(speed.rpm-44.415f)<.01f);
    speed=wheel_telemetry(1,-1929,1929,1000000);
    assert(speed.valid&&speed.speed_mm_s<0);
    assert(!wheel_telemetry(0,100,0,250000).valid);
    assert(!wheel_telemetry(0,0,0,0).valid);
    assert(!wheel_telemetry(2,0,0,250000).valid);
    const char *valid[]={"@F50\n","@F200\n","@F300\n","@J150\n","@J450\n","@L900\r\n","@B50\n","@B300\n","@R100\n","@R1000\n","@H11\n","@H9999994\n"};
    for(unsigned n=0;n<sizeof(valid)/sizeof(*valid);n++) {
        size_t length=strlen(valid[n]);
        for(size_t split=1;split<length;split++) {
            param_parser_t p={0};
            param_packet_t a=param_feed(&p,(const uint8_t *)valid[n],split,1000);
            assert(!a.stop&&!a.rejected&&a.commands==0&&a.waiting);
            param_packet_t b=param_feed(&p,(const uint8_t *)valid[n]+split,length-split,2000);
            assert(!b.rejected&&b.commands==1&&!b.waiting&&b.legacy_len==0);
        }
    }
    const char *invalid[]={"@F49\n","@F301\n","@F-200\n","@F200.0\n","@J149\n","@L901\n",
        "@Jnan\n","@F9999999999999999999999999999\n","@F200\n@J450\n","@F200\nf","@F200\n@J45",
        "f@F20","@X200\n","@F200e\n","@B49\n","@B301\n","@R99\n","@R1001\n","@H10\n","@H15\n","@H10000001\n"};
    for(unsigned n=0;n<sizeof(invalid)/sizeof(*invalid);n++) {
        param_parser_t p={0};assert(feed(&p,invalid[n],1000).rejected);
    }
    const char *stop[]={"@F200\nt","t@J450\n","@FT200\n"};
    for(unsigned n=0;n<sizeof(stop)/sizeof(*stop);n++) {
        param_parser_t p={0};param_packet_t a=feed(&p,stop[n],1000);
        assert(a.stop&&a.commands==0&&!p.receiving&&!p.discarding);
    }
    param_parser_t p={0};
    assert(feed(&p,"@F2",1000).waiting);
    assert(feed(&p,"",251000).rejected);
    assert(feed(&p,"00\n",252000).commands==0);
    assert(feed(&p,"t",253000).stop);
    assert(feed(&p,"@F200\n",254000).commands==1);

    for(int sign=-1;sign<=1;sign+=2)for(int target=15;target<=90;target+=15) {
        yaw_sample_t yaw={.valid=true,.level=true,.sample_us=1000000};
        turn_control_t turn;
        assert(turn_begin_target(&turn,sign,(float)target,yaw,1000000)==TURN_RUN);
        yaw.angle_deg=sign*(target-2.1f);yaw.sample_us=1010000;
        assert(turn_step(&turn,yaw,1010000)==TURN_RUN);
        yaw.angle_deg=sign*(target-2.0f);yaw.sample_us=1020000;
        assert(turn_step(&turn,yaw,1020000)==TURN_REACHED);
        assert(turn_begin_target(&turn,sign,14.9f,yaw,1020000)==TURN_TARGET_INVALID);
        assert(turn_begin_target(&turn,sign,90.1f,yaw,1020000)==TURN_TARGET_INVALID);
        assert(turn_begin_target(&turn,sign,NAN,yaw,1020000)==TURN_TARGET_INVALID);
    }
    puts("PASS fragmented parameter frames, STOP priority, mixed commands, bounds, timeout, variable turn targets");
    return 0;
}
