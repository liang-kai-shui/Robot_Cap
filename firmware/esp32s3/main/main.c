#include <stdbool.h>
#include <stdint.h>
#include "driver/gpio.h"
#include "driver/uart.h"
#include "sdkconfig.h"
#if !CONFIG_ESP_CONSOLE_UART_DEFAULT || CONFIG_ESP_CONSOLE_UART_NUM != 0
#error "Keyboard control requires the existing UART0 console"
#endif
#include "driver/ledc.h"
#include "driver/pulse_cnt.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_rom_gpio.h"
#include "esp_timer.h"
#include "soc/gpio_sig_map.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "speed_pi.h"
#include "sensor_io.h"
#include "heading_control.h"
#include "distance_control.h"
#include "wheel_telemetry.h"
#include "test_mode.h"
#include "command_parser.h"
#include "teleop_control.h"
#include "esp_random.h"

#define MOTOR_STBY GPIO_NUM_15
#define TEST_BUTTON GPIO_NUM_0
#define RUN_US 800000LL
#define CONTROL_US 20000LL
#define CONTROL_GAP_US 60000LL
#define NO_FEEDBACK_US 150000LL
#define STOP_COOLDOWN_US 350000LL
#define KEY_QUIET_US 150000LL
#define TRACE_CAPACITY 320U
#define MOTOR_COUNT 2U

typedef struct { int quadrature, edges; } encoder_sample_t;
typedef struct {
    const char *name, *side;
    int command_sign;
    int forward_sign; // Hand-roll calibration: vehicle forward is A negative, B positive.
    gpio_num_t c1, c2, in1, in2, pwm;
    ledc_channel_t channel;
    pcnt_unit_handle_t q_unit, edge_unit;
    speed_pi_t pi;
    encoder_sample_t start, latest, control_start;
    int64_t last_edge_us;
} motor_t;
static motor_t motors[MOTOR_COUNT] = {
    {.name="A", .side="RIGHT", .forward_sign=-1, .c1=GPIO_NUM_10, .c2=GPIO_NUM_11, .in1=GPIO_NUM_12,
     .in2=GPIO_NUM_13, .pwm=GPIO_NUM_14, .channel=LEDC_CHANNEL_0},
    {.name="B", .side="LEFT", .forward_sign=1, .c1=GPIO_NUM_4, .c2=GPIO_NUM_5, .in1=GPIO_NUM_16,
     .in2=GPIO_NUM_17, .pwm=GPIO_NUM_18, .channel=LEDC_CHANNEL_1},
};
typedef struct {
    long long delta, edges;
    float motion_cps, filtered_cps, percent, target_cps;
} wheel_trace_t;
typedef struct { long long elapsed_us, dt_us; wheel_trace_t wheel[MOTOR_COUNT]; } trace_t;
static trace_t trace[TRACE_CAPACITY];
static unsigned trace_count, overspeed_frames[MOTOR_COUNT];
static bool motor_ready, running, fault_latched, ready_reported;
static unsigned moves_started;
static uint32_t forward_start_sequence;
static turn_control_t turn;
static heading_control_t heading;
static bool turn_report_pending;
static float turn_cutoff_deg;
static int64_t turn_report_us;
static char current_action;
static param_parser_t param_parser;
static teleop_control_t teleop;
static uint32_t boot_id;
static bool range_report_pending;
static float range_cutoff_mm;
static int64_t range_report_us;
static int64_t last_motion_us, last_motion_key_us=-KEY_QUIET_US;
static int64_t run_started_us, control_started_us, last_stop_us;
static struct {
    bool report_pending;
    float cutoff_mm[2],cutoff_yaw_deg,target_mm;
    int64_t report_us;
} distance_move;
static struct {
    bool active,yaw_fault;
    unsigned trial,epoch,fault_events;
    encoder_sample_t start[MOTOR_COUNT];
    float start_yaw,peak_yaw;
    int64_t started_us,printed_us;
} distance_cal;

static long long magnitude(long long value) { return value<0 ? -value : value; }
static bool angle_key(char action) {return action=='j'||action=='l';}
static bool forward_key(char action) {return action=='w'||action=='f'||action=='r';}
static bool straight_key(char action) {return forward_key(action)||action=='b';}
static bool manual_pwm(void) {return teleop.active&&teleop.direct_pwm;}
static void pwm_set(motor_t *m, uint32_t duty)
{
    if (duty>1023) duty=1023; // Direct manual PWM may use 100%; closed-loop PI keeps its own 60% cap.
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, m->channel, duty));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, m->channel));
}
static void motor_stop(void)
{
    // Shared STBY cuts both bridges before any other operation or UART output.
    if (motor_ready) {
        ESP_ERROR_CHECK(gpio_set_level(MOTOR_STBY, 0));
        for (unsigned i=0; i<MOTOR_COUNT; ++i) {
            pwm_set(&motors[i], 0);
            ESP_ERROR_CHECK(gpio_set_level(motors[i].in1, 0));
            ESP_ERROR_CHECK(gpio_set_level(motors[i].in2, 0));
        }
    }
    if (running) last_stop_us=esp_timer_get_time();
    running=false;
    teleop_close(&teleop);
}
static bool motor_init_guarded(void)
{
    const gpio_num_t pins[]={GPIO_NUM_12, GPIO_NUM_13, GPIO_NUM_14, MOTOR_STBY,
                             GPIO_NUM_16, GPIO_NUM_17, GPIO_NUM_18};
    uint64_t mask=0;
    for (unsigned i=0; i<sizeof(pins)/sizeof(pins[0]); ++i) mask |= 1ULL<<pins[i];
    gpio_config_t config={.pin_bit_mask=mask, .mode=GPIO_MODE_INPUT,
                         .pull_down_en=GPIO_PULLDOWN_ENABLE, .intr_type=GPIO_INTR_DISABLE};
    ESP_ERROR_CHECK(gpio_config(&config));
    vTaskDelay(pdMS_TO_TICKS(30));
    bool high=false;
    for (unsigned repeat=0; repeat<3; ++repeat) {
        for (unsigned i=0; i<sizeof(pins)/sizeof(pins[0]); ++i) {
            if (gpio_get_level(pins[i])) {
                high=true;
                ESP_LOGE("wiring-test", "GPIO%d HIGH before outputs enabled. Check wire/rail with power off.", pins[i]);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (high) return false;
    for (unsigned i=0; i<sizeof(pins)/sizeof(pins[0]); ++i) ESP_ERROR_CHECK(gpio_set_level(pins[i], 0));
    config.mode=GPIO_MODE_INPUT_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&config));
    const ledc_timer_config_t timer={.speed_mode=LEDC_LOW_SPEED_MODE, .timer_num=LEDC_TIMER_0,
        .duty_resolution=LEDC_TIMER_10_BIT, .freq_hz=20000, .clk_cfg=LEDC_AUTO_CLK};
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    for (unsigned i=0; i<MOTOR_COUNT; ++i) {
        motor_t *m=&motors[i];
        const ledc_channel_config_t channel={.gpio_num=m->pwm, .speed_mode=LEDC_LOW_SPEED_MODE,
            .channel=m->channel, .intr_type=LEDC_INTR_DISABLE, .timer_sel=LEDC_TIMER_0,
            .duty=0, .hpoint=0};
        ESP_ERROR_CHECK(ledc_channel_config(&channel));
        // In IDF 5.1.2 enabling pad input resets the route. Restore each LEDC signal.
        ESP_ERROR_CHECK(gpio_set_direction(m->pwm, GPIO_MODE_INPUT_OUTPUT));
        esp_rom_gpio_connect_out_signal(m->pwm, LEDC_LS_SIG_OUT0_IDX+m->channel, false, false);
    }
    motor_ready=true;
    motor_stop();
    return true;
}

static pcnt_unit_handle_t new_counter(void)
{
    const pcnt_unit_config_t config={.low_limit=-30000, .high_limit=30000, .flags.accum_count=true};
    pcnt_unit_handle_t unit=NULL;
    ESP_ERROR_CHECK(pcnt_new_unit(&config, &unit));
    const pcnt_glitch_filter_config_t filter={.max_glitch_ns=1000};
    ESP_ERROR_CHECK(pcnt_unit_set_glitch_filter(unit, &filter));
    ESP_ERROR_CHECK(pcnt_unit_add_watch_point(unit, config.low_limit));
    ESP_ERROR_CHECK(pcnt_unit_add_watch_point(unit, config.high_limit));
    return unit;
}
static void start_counter(pcnt_unit_handle_t unit)
{
    ESP_ERROR_CHECK(pcnt_unit_enable(unit));
    ESP_ERROR_CHECK(pcnt_unit_clear_count(unit));
    ESP_ERROR_CHECK(pcnt_unit_start(unit));
}
static void encoder_init(motor_t *m)
{
    // ESP32-S3 has four PCNT units. Each motor uses two: signed 4x quadrature
    // and the unsigned SUM of C1/C2 edges. A missing channel breaks their agreement.
    m->q_unit=new_counter();
    const pcnt_chan_config_t q1={.edge_gpio_num=m->c1, .level_gpio_num=m->c2};
    const pcnt_chan_config_t q2={.edge_gpio_num=m->c2, .level_gpio_num=m->c1};
    pcnt_channel_handle_t a=NULL, b=NULL;
    ESP_ERROR_CHECK(pcnt_new_channel(m->q_unit, &q1, &a));
    ESP_ERROR_CHECK(pcnt_new_channel(m->q_unit, &q2, &b));
    ESP_ERROR_CHECK(pcnt_channel_set_edge_action(a, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE));
    ESP_ERROR_CHECK(pcnt_channel_set_edge_action(b, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE));
    ESP_ERROR_CHECK(pcnt_channel_set_level_action(a, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE));
    ESP_ERROR_CHECK(pcnt_channel_set_level_action(b, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE));
    start_counter(m->q_unit);
    m->edge_unit=new_counter();
    const gpio_num_t pins[]={m->c1, m->c2};
    for (unsigned i=0; i<2; ++i) {
        const pcnt_chan_config_t edge={.edge_gpio_num=pins[i], .level_gpio_num=-1};
        pcnt_channel_handle_t c=NULL;
        ESP_ERROR_CHECK(pcnt_new_channel(m->edge_unit, &edge, &c));
        ESP_ERROR_CHECK(pcnt_channel_set_edge_action(c, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE));
        ESP_ERROR_CHECK(pcnt_channel_set_level_action(c, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_KEEP));
    }
    start_counter(m->edge_unit);
}
static void encoders_read(encoder_sample_t sample[MOTOR_COUNT])
{
    for (unsigned i=0; i<MOTOR_COUNT; ++i) {
        ESP_ERROR_CHECK(pcnt_unit_get_count(motors[i].q_unit, &sample[i].quadrature));
        ESP_ERROR_CHECK(pcnt_unit_get_count(motors[i].edge_unit, &sample[i].edges));
    }
}
static void telemetry_worker(void *ctx)
{
    (void)ctx;
    encoder_sample_t previous[MOTOR_COUNT];
    encoders_read(previous);
    int64_t previous_us=esp_timer_get_time();
    while(true) {
        vTaskDelay(pdMS_TO_TICKS(250));
        encoder_sample_t sample[MOTOR_COUNT];
        encoders_read(sample);
        int64_t now=esp_timer_get_time(),dt=now-previous_us;
        for(unsigned j=0;j<MOTOR_COUNT;++j) {
            long long delta=((long long)sample[j].quadrature-previous[j].quadrature)*motors[j].forward_sign;
            long long edges=(long long)sample[j].edges-previous[j].edges;
            wheel_telemetry_t v=wheel_telemetry(j,delta,edges,dt);
            ESP_LOGI("telemetry","WHEEL motor=%s side=%s count=%d forward_count=%lld cps=%.1f speed_mm_s=%.1f rpm=%.2f valid=%d dt_ms=%.1f age_ms=0",
                motors[j].name,motors[j].side,sample[j].quadrature,
                (long long)sample[j].quadrature*motors[j].forward_sign,v.cps,v.speed_mm_s,v.rpm,v.valid,dt/1000.0);
            previous[j]=sample[j];
        }
        previous_us=now;
        sensors_attitude_print();
    }
}
static void distance_cal_cancel(const char *cause)
{
    if(!distance_cal.active)return;
    distance_cal.active=false;
    last_stop_us=esp_timer_get_time();
    ESP_LOGI("distance-cal","DISTANCE_CAL_CANCEL trial=%u cause=%s; motors off, sample discarded.",distance_cal.trial,cause);
}
static void distance_cal_begin(int64_t now)
{
    if(distance_cal.active) {
        ESP_LOGI("distance-cal","DISTANCE_CAL_REJECTED cause=ALREADY_ACTIVE; C did not reset counts, E finishes.");return;
    }
    if(running||turn_report_pending||distance_move.report_pending||fault_latched||!motor_ready||!gpio_get_level(TEST_BUTTON)||
       now-last_stop_us<STOP_COOLDOWN_US||now-last_motion_us<KEY_QUIET_US) {
        // In particular, C received during powered motion cannot interrupt it or reset its deadline.
        if(!running){last_stop_us=esp_timer_get_time();ESP_LOGI("distance-cal","DISTANCE_CAL_REJECTED cause=WAIT_IDLE_READY; no measurement started.");}
        return;
    }
    yaw_sample_t yaw=sensors_yaw_read();
    turn_control_t still;
    turn_result_t status=turn_begin(&still,1,yaw,esp_timer_get_time());
    if(status!=TURN_RUN) {
        last_stop_us=esp_timer_get_time();
        ESP_LOGI("distance-cal","DISTANCE_CAL_REJECTED cause=%s; no measurement started.",turn_cause(status));return;
    }
    motor_stop();
    encoders_read(distance_cal.start);
    distance_cal.start_yaw=yaw.angle_deg;distance_cal.peak_yaw=0;distance_cal.yaw_fault=false;
    distance_cal.epoch=yaw.zero_epoch;distance_cal.fault_events=yaw.fault_events;
    distance_cal.started_us=distance_cal.printed_us=esp_timer_get_time();
    distance_cal.active=true;++distance_cal.trial;
    ESP_LOGI("distance-cal","DISTANCE_CAL_BEGIN trial=%u assumed_distance_mm=500; external motor 5V must be DISCONNECTED, USB only. Hand-roll straight 50cm without wheel slip; wait still then E. Motion keys disabled, T cancels.",distance_cal.trial);
}
static void distance_cal_poll(int64_t now)
{
    if(!distance_cal.active)return;
    if(now-distance_cal.started_us>=60000000) {distance_cal_cancel("TIMEOUT_60S");return;}
    yaw_sample_t yaw=sensors_yaw_read();
    if(!turn_yaw_valid(yaw,esp_timer_get_time())||yaw.zero_epoch!=distance_cal.epoch||yaw.fault_events!=distance_cal.fault_events)
        distance_cal.yaw_fault=true; // Retain failures through recovery; do not accept that stroke.
    else distance_cal.peak_yaw=fmaxf(distance_cal.peak_yaw,fabsf(yaw.angle_deg-distance_cal.start_yaw));
    if(now-distance_cal.printed_us>=500000) {
        encoder_sample_t sample[MOTOR_COUNT];encoders_read(sample);
        ESP_LOGI("distance-cal","DISTANCE_CAL_SAMPLE trial=%u right_forward_counts=%lld left_forward_counts=%lld yaw_change_deg=%+.1f yaw_peak_deg=%.1f yaw_fault=%d; E finishes, T cancels.",
            distance_cal.trial,-((long long)sample[0].quadrature-distance_cal.start[0].quadrature),
            (long long)sample[1].quadrature-distance_cal.start[1].quadrature,
            yaw.angle_deg-distance_cal.start_yaw,distance_cal.peak_yaw,distance_cal.yaw_fault);
        distance_cal.printed_us=esp_timer_get_time();
    }
}
static void distance_cal_finish(int64_t now)
{
    if(!distance_cal.active) {if(!running){last_stop_us=esp_timer_get_time();ESP_LOGI("distance-cal","DISTANCE_CAL_REJECTED cause=NOT_ACTIVE; C begins.");}return;}
    yaw_sample_t yaw=sensors_yaw_read();
    if(now-last_motion_us<KEY_QUIET_US||(turn_yaw_valid(yaw,esp_timer_get_time())&&fabsf(yaw.rate_dps)>3)) {
        ESP_LOGI("distance-cal","DISTANCE_CAL_REJECTED cause=WAIT_WHEELS_AND_CHASSIS_STILL; stop hand-push, wait 0.5s then E.");return;
    }
    distance_cal_poll(esp_timer_get_time());
    if(!distance_cal.active)return;
    encoder_sample_t sample[MOTOR_COUNT];encoders_read(sample);
    long long q[2],edges[2];bool counts_ok=true;
    for(unsigned j=0;j<MOTOR_COUNT;j++) {
        q[j]=motors[j].forward_sign*((long long)sample[j].quadrature-distance_cal.start[j].quadrature);
        edges[j]=(long long)sample[j].edges-distance_cal.start[j].edges;
        counts_ok &= q[j]>=50 && edges[j]>=0 && magnitude(q[j]-edges[j])<=8+edges[j]/100;
    }
    bool usable=counts_ok&&!distance_cal.yaw_fault&&distance_cal.peak_yaw<=5.0f;
    const char *cause=!counts_ok?"COUNTS_REVERSE_MISSING_OR_EDGE_MISMATCH":distance_cal.yaw_fault?"BMI_INVALID_RESET_OR_STALE":
                      distance_cal.peak_yaw>5.0f?"NOT_STRAIGHT_OVER_5DEG":"ENCODER_AND_YAW_CHECKS_OK";
    distance_cal.active=false;last_stop_us=esp_timer_get_time();
    ESP_LOGI("distance-cal","DISTANCE_CAL_RESULT trial=%u usable=%d cause=%s assumed_distance_mm=500 right_counts=%lld left_counts=%lld right_edges=%lld left_edges=%lld right_counts_per_mm=%.4f left_counts_per_mm=%.4f mean_counts_per_mm=%.4f yaw_peak_deg=%.2f; assumes measured 50cm and no slip, factors NOT installed.",
        distance_cal.trial,usable,cause,q[0],q[1],edges[0],edges[1],q[0]/500.0,q[1]/500.0,(q[0]+q[1])/1000.0,distance_cal.peak_yaw);
}
static void print_status(void)
{
    ESP_LOGI("vehicle", "VEHICLE_STATUS outputs=%s state=%s fault=%d moves=%u; W/S/A/D motion, F forward30cm, J/L angle turn, T stop, BOOT stop.",
        motor_ready?"READY":"BLOCKED", running?"RUNNING":"IDLE", fault_latched, moves_started);
}
static void dump_trace(void)
{
    for (unsigned i=0; i<trace_count; ++i) {
        for (unsigned j=0; j<MOTOR_COUNT; ++j) {
            const wheel_trace_t *s=&trace[i].wheel[j];
            ESP_LOGI("dual-speed", "DUAL_SAMPLE motor=%s t_ms=%.1f dt_ms=%.1f target=%.1f raw_cps=%.1f motion_cps=%.1f fb_cps=%.1f pwm=%.1f%% dq=%lld edges=%lld",
                     motors[j].name, trace[i].elapsed_us/1000.0, trace[i].dt_us/1000.0,
                     s->target_cps,(float)(s->delta*1000000.0/trace[i].dt_us), s->motion_cps, s->filtered_cps, s->percent, s->delta, s->edges);
        }
    }
}
static void fault_stop(const char *reason, int wheel)
{
    motor_stop();
    fault_latched=true;
    distance_move.report_pending=false;
    distance_cal_cancel("FAULT");
    ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
    ESP_LOGE("dual-test", "STOP motor=%s reason=%s. BOTH stopped. Disconnect 5V and report; no retries.",
             wheel>=0?motors[wheel].name:"BOTH", reason);
    range_report_pending=false;
    turn_report_pending=false;
    dump_trace();
}
static void forward_stop(range_guard_view_t front, bool rejected)
{
    motor_stop();
    ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
    last_stop_us=esp_timer_get_time();
    front_cause_t cause=front.blocked?front.cause:front.last_unsafe;
    ESP_LOGI("vehicle", "%s action=%c source=FORWARD_GUARD cause=%s distance_mm=%u status=%u age_ms=%lld; stopped, fresh key required after clearance.",
        rejected?"VEHICLE_BLOCKED":"VEHICLE_STOP", current_action, front_cause_name(cause),
        front.mm, front.status, (long long)(front.age_us<0?-1:front.age_us/1000));
}
static bool heading_start_checked(char action)
{
    yaw_sample_t yaw=sensors_yaw_read();
    heading_result_t result=heading_begin(&heading,yaw,esp_timer_get_time());
    if(result==HEADING_RUN)return true;
    motor_stop();last_stop_us=esp_timer_get_time();
    ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
    ESP_LOGI("vehicle","VEHICLE_BLOCKED action=%c source=HEADING_HOLD cause=%s; motor not enabled.",action,heading_cause(result));
    return false;
}
static void motor_start(char action,float amount)
{
    if (running || turn_report_pending || range_report_pending || distance_move.report_pending || distance_cal.active ||
        !motor_ready || fault_latched || !gpio_get_level(TEST_BUTTON)) return;
    current_action=action;
    if(action=='f'||action=='b'||action=='r') {
        if(!isfinite(amount)||amount<(action=='r'?100:PARAM_MOVE_MIN_MM)||amount>(action=='r'?1000:PARAM_MOVE_MAX_MM))return;
        distance_move.target_mm=amount;
    }
    if(angle_key(action)) {
        yaw_sample_t yaw=sensors_yaw_read();
        turn_result_t result=turn_begin_target(&turn,action=='j'?1:-1,amount,yaw,esp_timer_get_time());
        if(result!=TURN_RUN) {
            motor_stop();last_stop_us=esp_timer_get_time();
            ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
            ESP_LOGI("vehicle","VEHICLE_BLOCKED action=%c source=ANGLE_TURN cause=%s; motor not enabled.",action,turn_cause(result));
            return;
        }
    }
    if (forward_key(action) && !ROBOT_SUPERVISED_TEST) {
        range_guard_view_t front=sensors_forward_guard();
        if (front.blocked) {forward_stop(front,true);return;}
        forward_start_sequence=front.unsafe_sequence;
    }
    if(straight_key(action)&&!manual_pwm()&&!heading_start_checked(action))return;
    current_action=action;
    // A is RIGHT, B is LEFT. Positive command_sign means vehicle forward.
    motors[0].command_sign=(action=='s' || action=='b' || action=='d' || action=='l')?-1:1;
    motors[1].command_sign=(action=='s' || action=='b' || action=='a' || action=='j')?-1:1;
    trace_count=0;
    ESP_ERROR_CHECK(gpio_set_level(MOTOR_STBY, 0));
    for (unsigned i=0; i<MOTOR_COUNT; ++i) {
        speed_pi_reset(&motors[i].pi);
        overspeed_frames[i]=0;
        ESP_ERROR_CHECK(gpio_set_level(motors[i].in1, motors[i].forward_sign*motors[i].command_sign>0));
        ESP_ERROR_CHECK(gpio_set_level(motors[i].in2, motors[i].forward_sign*motors[i].command_sign<0));
        if(manual_pwm())motors[i].pi.percent=(float)teleop.pwm_percent;
        pwm_set(&motors[i], manual_pwm()?(uint32_t)(teleop.pwm_percent*1023U/100U):410);
    }
    if(manual_pwm())
        ESP_LOGI("vehicle","VEHICLE_START action=%c MANUAL_PWM percent=%d heading_hold=OFF; lease and encoder checks active.",action,teleop.pwm_percent);
    else if(angle_key(action))
        ESP_LOGI("dual-test","VEHICLE_START action=%c ANGLE_TURN v1.1 target_deg=%+.1f pwm=28..40%% max_ms=3000. T stops; rear/sides unmonitored.",action,turn.sign*turn.target_deg);
    else if(action=='w')
        ESP_LOGI("dual-test","VEHICLE_START action=w HEADING_HOLD v1.0 target=current heading base=2200cps correction<=400cps cap=60%% duration=800ms front_guard=%s.",ROBOT_SUPERVISED_TEST?"OFF":"ON");
    else if(action=='r')
        ESP_LOGI("dual-test","VEHICLE_START action=r RANGE_APPROACH target_mm=%.1f max_ms=15000 max_travel_mm=1500; fresh ToF and heading hold required.",distance_move.target_mm);
    else if(action=='f'||action=='b')
        ESP_LOGI("dual-test","VEHICLE_START action=%c DISTANCE_MOVE v1.1 target_mm=%.1f right_counts_per_mm=19.190 left_counts_per_mm=19.294 max_ms=6000 cap=60%% front_guard=%s. Heading hold active. T stops.",action,distance_move.target_mm,ROBOT_SUPERVISED_TEST?"OFF":"ON");
    else
        ESP_LOGI("dual-test", "VEHICLE_START action=%c right_sign=%d left_sign=%d target=2200cps initial=40%% cap=60%% duration=800ms", action, motors[0].command_sign, motors[1].command_sign);
    // Console output can take time. Recheck immediately before enabling bridges;
    // keep any unsafe reading even if subsequent readings have already cleared it.
    if (straight_key(action)&&!manual_pwm()) {
        if(forward_key(action)&&!ROBOT_SUPERVISED_TEST) {
            range_guard_view_t front=sensors_forward_guard();
            if (front.blocked || front.unsafe_sequence!=forward_start_sequence) {
                forward_stop(front,true);return;
            }
        }
        // Use a fresh baseline after logging, immediately before motor enable.
        if(!heading_start_checked(action))return;
    }
    if(angle_key(action)) {
        // Reset the relative baseline after console output, with bridges still off.
        // A queued Z must finish before an angle command can be accepted.
        yaw_sample_t yaw=sensors_yaw_read();
        turn_result_t result=turn_begin_target(&turn,action=='j'?1:-1,amount,yaw,esp_timer_get_time());
        if(result!=TURN_RUN) {
            motor_stop();last_stop_us=esp_timer_get_time();
            ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
            ESP_LOGI("vehicle","VEHICLE_BLOCKED action=%c source=ANGLE_TURN cause=%s; motor not enabled.",action,turn_cause(result));
            return;
        }
    }
    encoder_sample_t sample[MOTOR_COUNT];
    encoders_read(sample);
    if(action=='r') {
        range_guard_view_t front=sensors_forward_guard();
        if(!front.valid||front.age_us<0||front.age_us>FRONT_FRESH_US||front.mm<=amount+20) {
            motor_stop();ESP_LOGW("vehicle","VEHICLE_BLOCKED action=r source=RANGE_TARGET cause=LOST_RANGE_BEFORE_ENABLE");return;
        }
    }
    run_started_us=control_started_us=esp_timer_get_time();
    for (unsigned i=0; i<MOTOR_COUNT; ++i) {
        motors[i].start=motors[i].latest=motors[i].control_start=sample[i];
        motors[i].last_edge_us=run_started_us;
    }
    running=true;
    ++moves_started;
    ESP_ERROR_CHECK(gpio_set_level(MOTOR_STBY, 1));
}
static void print_results(const encoder_sample_t sample[MOTOR_COUNT])
{
    if (trace_count<6) { fault_stop("TOO_FEW_SAMPLES", -1); return; }
    long long elapsed=last_stop_us-run_started_us;
    float tail_cps[MOTOR_COUNT], mae[MOTOR_COUNT], tail_target[MOTOR_COUNT];
    bool near[MOTOR_COUNT]={true, true};
    for (unsigned j=0; j<MOTOR_COUNT; ++j) {
        long long q=(long long)sample[j].quadrature-motors[j].start.quadrature;
        long long edges=(long long)sample[j].edges-motors[j].start.edges;
        long long forward_q=motors[j].forward_sign*motors[j].command_sign*q;
        if (forward_q<=0 || edges<=0 || elapsed<=0 || magnitude(forward_q-edges)>8+edges/100) {
            fault_stop("FINAL_ENCODER_COUNTS_INCONSISTENT", j); return;
        }
        long long tail_delta=0, tail_us=0;
        double error_us=0,target_us=0;
        for (unsigned i=trace_count-6; i<trace_count; ++i) {
            const wheel_trace_t *s=&trace[i].wheel[j];
            tail_delta+=motors[j].forward_sign*motors[j].command_sign*s->delta;
            tail_us+=trace[i].dt_us;
            float error=fabsf(s->motion_cps-s->target_cps);
            error_us+=error/s->target_cps*trace[i].dt_us;
            target_us+=s->target_cps*trace[i].dt_us;
            if (error>s->target_cps*0.15f) near[j]=false;
        }
        tail_cps[j]=(float)(tail_delta*1000000.0/tail_us);
        mae[j]=(float)(error_us/tail_us*100.0);
        tail_target[j]=(float)(target_us/tail_us);
    }
    for (unsigned j=0; j<MOTOR_COUNT; ++j) {
        ESP_LOGI("dual-speed", "VEHICLE_RESULT action=%c motor=%s side=%s vehicle_sign=%d target_tail_mean=%.1f tail_cps=%.1f tail_mae_pct=%.1f last_pwm=%.1f%% tracking_within15pct=%s count_delta=%lld edges=%lld",
                 current_action, motors[j].name, motors[j].side, motors[j].command_sign, tail_target[j],tail_cps[j], mae[j], motors[j].pi.percent, near[j]?"YES":"NO",
                 (long long)sample[j].quadrature-motors[j].start.quadrature,
                 (long long)sample[j].edges-motors[j].start.edges);
    }
    if(current_action=='w')
        ESP_LOGI("vehicle","HEADING_RESULT action=w cutoff_error_deg=%+.2f peak_error_deg=%.2f mean_abs_error_deg=%.2f correction_cps=%+.1f target_right_cps=%.1f target_left_cps=%.1f; measured during drive, forward timed 800ms, distance uncalibrated.",
            heading.error_deg,heading.peak_deg,heading.measured_s>0?(float)(heading.absolute_error_s/heading.measured_s):NAN,
            heading.correction_cps,SPEED_TARGET_CPS+heading.correction_cps,SPEED_TARGET_CPS-heading.correction_cps);
    ESP_LOGI("dual-speed", "VEHICLE_DONE elapsed_ms=%.1f samples=%u both_tracking=%s tail_speed_difference=%.1fcps. IDLE. Release key, wait for READY, then send a fresh key.",
             elapsed/1000.0, trace_count, near[0]&&near[1]?"YES":"NO", fabsf(tail_cps[0]-tail_cps[1]));
    ESP_LOGI("dual-test", "cps is encoder counts/s, not RPM or wheel speed. W holds relative yaw; J/L use relative angle, A/D timed. Equal cps does not guarantee straight travel. No load/stall/current test.");
}
static void turn_complete(void)
{
    turn_cutoff_deg=turn.sign*turn.progress;
    motor_stop();
    ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
    turn_report_pending=true;turn_report_us=last_stop_us+300000;
    ESP_LOGI("vehicle","VEHICLE_STOP action=%c source=ANGLE_TARGET cutoff_deg=%+.1f; drive disabled, no reverse correction.",current_action,turn_cutoff_deg);
}
static void turn_report(void)
{
    yaw_sample_t yaw=sensors_yaw_read();
    bool valid=turn_yaw_valid(yaw,esp_timer_get_time())&&yaw.zero_epoch==turn.epoch&&yaw.fault_events==turn.fault_events;
    float measured=valid?yaw.angle_deg-turn.baseline:NAN;
    float error=valid?measured-turn.sign*turn.target_deg:NAN;
    turn_report_pending=false;
    ESP_LOGI("vehicle","TURN_RESULT action=%c target_deg=%+.1f cutoff_deg=%+.1f after_stop_deg=%+.1f error_deg=%+.1f valid=%d within5deg=%s elapsed_ms=%.1f; after_stop measured 300ms after drive-off.",
        current_action,turn.sign*turn.target_deg,turn_cutoff_deg,measured,error,valid,
        valid&&fabsf(error)<=5.0f?"YES":"NO",(last_stop_us-run_started_us)/1000.0);
    ESP_LOGI("vehicle","VEHICLE_DONE action=%c type=ANGLE_TURN; motors off. Wait for READY, then fresh key.",current_action);
}
static bool distance_read(const encoder_sample_t sample[MOTOR_COUNT],float mm[2])
{
    for(unsigned j=0;j<MOTOR_COUNT;j++) {
        long long q=motors[j].forward_sign*motors[j].command_sign*((long long)sample[j].quadrature-motors[j].start.quadrature);
        long long edges=(long long)sample[j].edges-motors[j].start.edges;
        if(!distance_encoder_valid(q,edges))return false;
        mm[j]=distance_wheel_mm(j,q);
    }
    return true;
}
static void distance_complete(const float mm[2])
{
    // Cut power before logging. No automatic retry or reverse correction.
    motor_stop();
    distance_move.cutoff_mm[0]=mm[0];distance_move.cutoff_mm[1]=mm[1];
    distance_move.cutoff_yaw_deg=heading.error_deg;
    distance_move.report_pending=true;
    distance_move.report_us=last_stop_us+DISTANCE_REPORT_DELAY_US;
    ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
    ESP_LOGI("vehicle","VEHICLE_STOP action=%c source=DISTANCE_TARGET target_mm=%.1f cutoff_mean_mm=%.1f; drive disabled, measuring coast for 300ms.",current_action,distance_move.target_mm,(mm[0]+mm[1])*0.5f);
}
static void distance_report(const encoder_sample_t sample[MOTOR_COUNT])
{
    float mm[2]={NAN,NAN};
    bool encoder_valid=distance_read(sample,mm);
    yaw_sample_t yaw=sensors_yaw_read();
    bool yaw_valid=turn_yaw_valid(yaw,esp_timer_get_time())&&yaw.zero_epoch==heading.epoch&&yaw.fault_events==heading.fault_events;
    distance_move.report_pending=false;
    ESP_LOGI("vehicle","DISTANCE_RESULT action=%c target_mm=%.1f cutoff_right_mm=%.1f cutoff_left_mm=%.1f after_stop_right_mm=%.1f after_stop_left_mm=%.1f after_stop_mean_mm=%.1f estimated_error_mm=%+.1f encoder_valid=%d yaw_valid=%d cutoff_yaw_error_deg=%+.2f after_stop_yaw_error_deg=%+.2f elapsed_ms=%.1f; encoder estimate 300ms after drive-off, verify ground distance with ruler.",
        current_action,distance_move.target_mm,distance_move.cutoff_mm[0],distance_move.cutoff_mm[1],mm[0],mm[1],(mm[0]+mm[1])*0.5f,
        (mm[0]+mm[1])*0.5f-distance_move.target_mm,encoder_valid,yaw_valid,distance_move.cutoff_yaw_deg,
        yaw_valid?yaw.angle_deg-heading.baseline:NAN,(last_stop_us-run_started_us)/1000.0);
    ESP_LOGI("vehicle","VEHICLE_DONE action=%c type=DISTANCE_MOVE; motors off. Wait for READY, then fresh key.",current_action);
}
static void motor_poll(const encoder_sample_t sample[MOTOR_COUNT], int64_t now)
{
    if (now<run_started_us) return; // This iteration preceded START's fresh time/counts.
    yaw_sample_t forward_yaw={0};
    if(teleop.active&&!teleop_live(&teleop,now)) {
        motor_stop();
        ESP_LOGI("vehicle","TELEOP_END cause=LEASE_OR_MAX_HOLD session=%u; drive off, fresh press required.",teleop.closed);
        return;
    }
    if(current_action=='r') {
        range_guard_view_t front=sensors_forward_guard();
        if(!front.valid||front.age_us<0||front.age_us>FRONT_FRESH_US) {fault_stop("RANGE_INVALID_OR_STALE",-1);return;}
        if(front.mm<=distance_move.target_mm+12) {
            range_cutoff_mm=front.mm;motor_stop();range_report_pending=true;range_report_us=last_stop_us+300000;
            ESP_LOGI("vehicle","VEHICLE_STOP action=r source=RANGE_TARGET target_mm=%.1f cutoff_mm=%.1f",distance_move.target_mm,range_cutoff_mm);
            return;
        }
    }
    if(angle_key(current_action)) {
        yaw_sample_t yaw=sensors_yaw_read();
        turn_result_t result=turn_step(&turn,yaw,esp_timer_get_time());
        if(result==TURN_REACHED) {turn_complete();return;}
        if(result!=TURN_RUN) {fault_stop(turn_cause(result),-1);return;}
    }
    if (straight_key(current_action)&&!manual_pwm()) {
        if(forward_key(current_action)&&!ROBOT_SUPERVISED_TEST) {
            range_guard_view_t front=sensors_forward_guard();
            if (front.blocked || front.unsafe_sequence!=forward_start_sequence) {
                forward_stop(front,false);return;
            }
        }
        forward_yaw=sensors_yaw_read();
        heading_result_t result=heading_validate(&heading,forward_yaw,esp_timer_get_time());
        if(result!=HEADING_RUN){fault_stop(heading_cause(result),-1);return;}
    }
    if (gpio_get_level(MOTOR_STBY)!=1) { fault_stop("STBY_PAD_MISMATCH", -1); return; }
    for (unsigned j=0; j<MOTOR_COUNT; ++j) {
        motor_t *m=&motors[j];
        if (sample[j].edges!=m->latest.edges) m->last_edge_us=now;
        m->latest=sample[j];
        if (now-m->last_edge_us>=NO_FEEDBACK_US) { fault_stop("NO_NEW_ENCODER_EDGES", j); return; }
        if (gpio_get_level(m->in1)!=(m->forward_sign*m->command_sign>0) || gpio_get_level(m->in2)!=(m->forward_sign*m->command_sign<0)) {
            fault_stop("DIRECTION_PAD_MISMATCH", j); return;
        }
    }
    long long dt_us=now-control_started_us;
    if (dt_us>CONTROL_GAP_US) { fault_stop("CONTROL_TIMING_GAP_OVER_60MS", -1); return; }
    if(current_action=='f'||current_action=='b'||current_action=='r') {
        // Validate both total counts before using either wheel for the target.
        float mm[2];
        if(!distance_read(sample,mm)) {fault_stop("DISTANCE_ENCODER_COUNTS_INCONSISTENT",-1);return;}
        if(fabsf(mm[0]-mm[1])>DISTANCE_MAX_WHEEL_DIFFERENCE_MM) {fault_stop("DISTANCE_WHEEL_DIFFERENCE_OVER_30MM",-1);return;}
        if(current_action=='r'&&(mm[0]+mm[1])*0.5f>1500) {fault_stop("RANGE_TRAVEL_LIMIT_1500MM",-1);return;}
        if(now-run_started_us>=(current_action=='r'?15000000LL:DISTANCE_MAX_US)) {fault_stop("DISTANCE_TIMEOUT_6S",-1);return;}
        if(current_action!='r'&&(mm[0]+mm[1])*0.5f>=distance_move.target_mm) {distance_complete(mm);return;}
    }
    if (!teleop.active&&!angle_key(current_action)&&current_action!='f'&&current_action!='b'&&current_action!='r'&&now-run_started_us>=RUN_US) {
        motor_stop(); print_results(sample); ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0)); return;
    }
    if (dt_us<CONTROL_US) return;
    if((teleop.active||current_action=='r')&&trace_count>=TRACE_CAPACITY)trace_count=0;
    if (trace_count>=TRACE_CAPACITY) { fault_stop("TRACE_CAPACITY", -1); return; }
    trace_t frame={.elapsed_us=now-run_started_us, .dt_us=dt_us};
    float target[MOTOR_COUNT]={SPEED_TARGET_CPS,SPEED_TARGET_CPS};
    if(straight_key(current_action)&&!manual_pwm()&&!heading_targets(&heading,forward_yaw,dt_us*0.000001f,target)) {
        fault_stop(heading_cause(HEADING_SAMPLE),-1);return;
    }
    if(current_action=='b'){float temp=target[0];target[0]=target[1];target[1]=temp;}
    if(current_action=='r') {
        range_guard_view_t front=sensors_forward_guard();
        float scale=speed_clamp((front.mm-distance_move.target_mm)/200.0f,0.35f,1.0f);
        target[0]*=scale;target[1]*=scale;
    }
    for (unsigned j=0; j<MOTOR_COUNT; ++j) {
        motor_t *m=&motors[j];
        long long q=(long long)sample[j].quadrature-m->control_start.quadrature;
        long long edges=(long long)sample[j].edges-m->control_start.edges;
        long long forward_q=m->forward_sign*m->command_sign*q;
        if (forward_q<-4) { fault_stop("ENCODER_OPPOSES_COMMAND", j); return; }
        if (edges<0 || magnitude(forward_q-edges)>8+edges/100) {
            fault_stop("QUADRATURE_VS_C1_C2_EDGE_SUM_MISMATCH", j); return;
        }
        float raw=fmaxf(0, (float)(forward_q*1000000.0/dt_us));
        if (raw>2*SPEED_TARGET_CPS) ++overspeed_frames[j]; else overspeed_frames[j]=0;
        if (overspeed_frames[j]>=2) { fault_stop("SPEED_OVER_2X_TARGET", j); return; }
        if(manual_pwm()) {
            m->pi.filtered_cps=raw;
            m->pi.percent=(float)teleop.pwm_percent;
        } else if(angle_key(current_action)) {
            // Outer angle feedback sets bounded PWM; encoder checks remain active.
            // No integral speed demand can accelerate the wheels near the target.
            m->pi.filtered_cps=raw;
            float step=SPEED_SLEW_PERCENT_S*dt_us*0.000001f;
            m->pi.percent=speed_clamp(turn.percent,m->pi.percent-step,m->pi.percent+step);
        } else if (!speed_pi_update_bounded(&m->pi, raw, target[j],dt_us*0.000001f,current_action=='r'?600.0f:1800.0f)) { fault_stop("CONTROL_SAMPLE_INVALID", j); return; }
        frame.wheel[j]=(wheel_trace_t){.delta=q, .edges=edges, .motion_cps=raw,
            .filtered_cps=m->pi.filtered_cps, .percent=m->pi.percent,.target_cps=target[j]};
    }
    // Apply neither new command if either encoder failed validation.
    for (unsigned j=0; j<MOTOR_COUNT; ++j) {
        pwm_set(&motors[j], (uint32_t)(motors[j].pi.percent*1024.0f/100.0f+0.5f));
        motors[j].control_start=sample[j];
    }
    trace[trace_count++]=frame;
    control_started_us=now;
}
static void user_stop(const char *source)
{
    param_parser=(param_parser_t){0};
    bool was_running=running;
    bool already_still=!running&&!turn_report_pending&&!distance_move.report_pending&&!range_report_pending&&
        esp_timer_get_time()-last_motion_us>=KEY_QUIET_US;
    motor_stop();
    turn_report_pending=false;
    distance_move.report_pending=false;
    range_report_pending=false;
    distance_cal_cancel(source);
    ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
    // An already confirmed idle STOP still acknowledges T and drains input,
    // but does not repeat a 350ms cooldown. Motion STOP still measures standstill.
    last_stop_us=esp_timer_get_time()-(already_still?STOP_COOLDOWN_US:0);
    ready_reported=false;
    ESP_LOGI("vehicle", "VEHICLE_STOP source=%s was_running=%d; no queued motion.", source, was_running);
}
static bool motion_key(char key) { return forward_key(key)||key=='b'||key=='s'||key=='a'||key=='d'||angle_key(key); }
static void serial_poll(int64_t now)
{
    uint8_t incoming[32];
    int count=uart_read_bytes(UART_NUM_0, incoming, sizeof(incoming), 0);
    if (count<0) { fault_stop("UART_READ_ERROR", -1); return; }
    param_packet_t packet=param_feed(&param_parser,incoming,(size_t)count,now);
    if(packet.stop){user_stop("T");return;}
    if(packet.rejected) {
        motor_stop();turn_report_pending=false;distance_move.report_pending=false;
        distance_cal_cancel("PROTOCOL_REJECTED");
        last_stop_us=esp_timer_get_time();
        ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
        ESP_LOGW("vehicle","VEHICLE_BLOCKED action=? source=PARAM1 cause=FRAME_INVALID_MIXED_OR_TIMEOUT; motors off, no queued motion. T resets parser.");
        return;
    }
    if(packet.waiting&&!packet.commands)return;
    if(packet.commands==1&&(packet.action=='h'||packet.action=='u')) {
        bool was_active=teleop.active;
        bool idle=!running&&!turn_report_pending&&!distance_move.report_pending&&!range_report_pending&&
            !distance_cal.active&&motor_ready&&!fault_latched&&gpio_get_level(TEST_BUTTON)&&
            now-last_stop_us>=STOP_COOLDOWN_US&&now-last_motion_us>=KEY_QUIET_US;
        bool accepted=packet.action=='u'?teleop_accept_pwm(&teleop,(unsigned)packet.units,now,idle):
            teleop_accept(&teleop,(unsigned)packet.units,now,idle);
        if(!accepted) {
            if(running)fault_stop("TELEOP_EXPIRED_OR_WRONG_SESSION",-1);
            return;
        }
        if(!was_active) {
            static const char actions[]={0,'w','s','a','d'};
            motor_start(actions[teleop.direction],0);
            if(!running) {teleop_close(&teleop);return;}
            teleop.began=teleop.renewed=esp_timer_get_time();
            ESP_LOGI("vehicle","TELEOP_START session=%u direction=%d lease_ms=250 max_hold_ms=30000",teleop.session,teleop.direction);
        }
        ESP_LOGI("vehicle","TELEOP_ACK session=%u direction=%d direct_pwm=%d pwm_percent=%d applied_a=%.1f applied_b=%.1f",
            teleop.session,teleop.direction,teleop.direct_pwm,teleop.pwm_percent,motors[0].pi.percent,motors[1].pi.percent);
        return;
    }
    uint8_t *bytes=packet.legacy;count=(int)packet.legacy_len;
    char command=0;
    unsigned movement_keys=0;
    float amount=0;
    if(packet.commands==1) {
        command=packet.action;movement_keys=1;
        amount=(command=='f'||command=='b'||command=='r')?(float)packet.units:packet.units/10.0f;
    }
    // T wins over ALL motion bytes in the same packet, irrespective of order.
    for (int i=0; i<count; ++i) {
        char key=(bytes[i]>='A' && bytes[i]<='Z')?bytes[i]+('a'-'A'):bytes[i];
        if (key=='t') { user_stop("T"); return; }
        if (motion_key(key)) { command=key; ++movement_keys; }
    }
    bool zero=false;unsigned calibration_keys=0;char calibration=0;
    for (int i=0; i<count; ++i) {
        char key=bytes[i]>='A'&&bytes[i]<='Z'?bytes[i]+('a'-'A'):bytes[i];
        if(key=='z')zero=true;
        if(key=='c'||key=='e'){calibration=key;++calibration_keys;}
    }
    if(calibration_keys) {
        if(calibration_keys!=1||movement_keys||zero) {
            if(!running){if(!distance_cal.active)last_stop_us=esp_timer_get_time();ESP_LOGI("distance-cal","DISTANCE_CAL_REJECTED cause=MIXED_KEYS; send one C or E.");}
            return;
        }
        if(calibration=='c')distance_cal_begin(now);else distance_cal_finish(now);
        return;
    }
    if(distance_cal.active) {
        if(movement_keys)ESP_LOGI("vehicle","VEHICLE_BLOCKED action=%c source=DISTANCE_CAL cause=HAND_MEASUREMENT_ACTIVE; motors off. E finishes, T cancels.",command);
        if(zero)ESP_LOGI("distance-cal","DISTANCE_CAL_REJECTED cause=ZERO_DISABLED_DURING_MEASUREMENT; sample unchanged.");
        return;
    }
    if (zero && !running && !turn_report_pending && !range_report_pending && !distance_move.report_pending && !movement_keys) {
        sensors_zero_request();
        ESP_LOGI("sensors", "ANGLE_ZERO_REQUEST: requires calibration and fresh unsaturated BMI data.");
    }
    if (!movement_keys) return;
    bool fresh=now-last_motion_key_us>=KEY_QUIET_US;
    last_motion_key_us=now;
    // No queued action, live reversal or deadline extension. Held-key repeats
    // remain ignored until a quiet interval, even after the motion has stopped.
    if (running || turn_report_pending || range_report_pending || distance_move.report_pending || !fresh || now-last_stop_us<STOP_COOLDOWN_US ||
        now-last_motion_us<KEY_QUIET_US || !gpio_get_level(TEST_BUTTON)) return;
    if (movement_keys!=1) {
        ESP_LOGW("vehicle", "Send ONE W/S/A/D/F/J/L key. Pasted multi-action packets ignored."); return;
    }
    if (!motor_ready || fault_latched) { print_status(); return; }
    if(!packet.commands)amount=command=='f'?DISTANCE_TARGET_MM:TURN_TARGET_DEG;
    if(command=='r') {
        range_guard_view_t front=sensors_forward_guard();
        if(!front.valid||front.age_us<0||front.age_us>FRONT_FRESH_US||front.mm<=amount+20) {
            ESP_LOGW("vehicle","VEHICLE_BLOCKED action=r source=RANGE_TARGET cause=INVALID_OR_ALREADY_TOO_CLOSE");return;
        }
    }
    motor_start(command,amount);
}
void app_main(void)
{
    boot_id=esp_random();
    ESP_LOGI("vehicle", "KEYBOARD_DRIVE v1.9 protocol=PARAM2 build=pwm-stream-v1: @F/@B50..300 (mm), @J/@L150..900 (tenths deg), @R100..1000 front mm, @H session*10+direction lease. Newline terminates frames. T stop, no BOOT arming.");
    ESP_LOGI("vehicle", "A=RIGHT encoder=10/11 IN1=12 IN2=13 PWM=14; B=LEFT encoder=4/5 IN1=16 IN2=17 PWM=18; STBY=15.");
    ESP_LOGI("vehicle", "Keep wiring. VCC and encoder BLACKs=3V3; encoder BLUEs and adapter BLACK=GND; adapter RED=VM only.");
    motor_init_guarded();
    const gpio_config_t button={.pin_bit_mask=1ULL<<TEST_BUTTON, .mode=GPIO_MODE_INPUT,
        .pull_up_en=GPIO_PULLUP_ENABLE, .intr_type=GPIO_INTR_DISABLE};
    ESP_ERROR_CHECK(gpio_config(&button));
    for (unsigned j=0; j<MOTOR_COUNT; ++j) encoder_init(&motors[j]);
    if (!uart_is_driver_installed(UART_NUM_0))
        ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 256, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_flush_input(UART_NUM_0));
    sensors_start();
    if(xTaskCreatePinnedToCore(telemetry_worker,"ui-telemetry",4096,NULL,1,NULL,1)!=pdPASS)
        ESP_LOGE("telemetry","TELEMETRY_UNAVAILABLE: sampling task allocation failed");
    print_status();
    if(ROBOT_SUPERVISED_TEST)
        ESP_LOGI("sensors","SUPERVISED_TEST: TOF acquisition=%s, front_guard=OFF for W/F. User monitors clear space and T stop. BMI and encoder protections remain active.",ROBOT_TOF_ENABLED?"ON":"OFF");
    else
        ESP_LOGI("sensors", "BMI SDA8/SCL9; VL53L1X SDA6/SCL7 fixed facing FRONT. W/F stop at <=250mm, clear after 3 new valid readings >=350mm. Invalid/error/age>200ms blocks W/F. Rear/sides unmonitored.");
    ESP_LOGI("vehicle", "WASD: each fresh key <=800ms, cap 60%%. T or BOOT stops. No automatic run/retry, current or stall test.");
    ESP_LOGI("vehicle", "F: mean wheel distance target 300mm, right=19.190 left=19.294 counts/mm from 500mm hand-roll; <=6s front_guard=%s. Heading hold required. Slip and cable drag affect actual ground distance.",ROBOT_SUPERVISED_TEST?"OFF":"ON");
    ESP_LOGI("vehicle", "W heading hold: requires valid calibrated fixed BMI, level floor, still at start. Right/left targets=2200 +/-400cps, no reverse. BMI stale/reset, error>15deg or yaw rate>90dps stops. Distance not controlled.");
    ESP_LOGI("vehicle", "J/L angle trial: chassis on LEVEL FLOOR, BMI fixed, still for calibration. 90deg relative body yaw, <=3s PWM<=40%%. No yaw progress stops in 400ms; wheels raised cannot verify angle.");
    encoder_sample_t previous[MOTOR_COUNT];
    encoders_read(previous);
    last_motion_us=last_stop_us=esp_timer_get_time();
    bool boot_was_pressed=false;
    int64_t last_sensor_print=0;
    while (true) {
        int64_t now=esp_timer_get_time();
        encoder_sample_t sample[MOTOR_COUNT];
        encoders_read(sample);
        for (unsigned j=0; j<MOTOR_COUNT; ++j)
            if (sample[j].edges!=previous[j].edges) last_motion_us=now;
        if(turn_report_pending&&now>=turn_report_us)turn_report();
        // Report an idle BOOT press too: the PC may be waiting between P steps.
        bool boot_pressed=!gpio_get_level(TEST_BUTTON);
        if (boot_pressed && (!boot_was_pressed||running||distance_cal.active||turn_report_pending||distance_move.report_pending)) user_stop("BOOT");
        boot_was_pressed=boot_pressed;
        serial_poll(now);
        if(distance_move.report_pending&&esp_timer_get_time()>=distance_move.report_us)distance_report(sample);
        if(range_report_pending&&esp_timer_get_time()>=range_report_us) {
            range_guard_view_t front=sensors_forward_guard();
            range_report_pending=false;
            ESP_LOGI("vehicle","RANGE_RESULT action=r target_mm=%.1f cutoff_mm=%.1f after_stop_mm=%u valid=%d elapsed_ms=%.1f",distance_move.target_mm,range_cutoff_mm,front.mm,front.valid&&front.age_us>=0&&front.age_us<=FRONT_FRESH_US,(last_stop_us-run_started_us)/1000.0);
            ESP_LOGI("vehicle","VEHICLE_DONE action=r type=RANGE_APPROACH");
        }
        if(distance_cal.active)distance_cal_poll(esp_timer_get_time());
        if (now-last_stop_us<STOP_COOLDOWN_US||param_parser.receiving||param_parser.discarding) ready_reported=false;
        if (running) { ready_reported=false; motor_poll(sample, now); }
        else if (motor_ready && !fault_latched) {
            bool high=gpio_get_level(MOTOR_STBY);
            for (unsigned j=0; j<MOTOR_COUNT; ++j)
                high |= gpio_get_level(motors[j].in1)||gpio_get_level(motors[j].in2)||gpio_get_level(motors[j].pwm);
            if (high) fault_stop("IDLE_PADS_EXPECTED_LOW", -1);
            else if (!distance_cal.active && !range_report_pending && !distance_move.report_pending && !turn_report_pending && !param_parser.receiving && !param_parser.discarding && !ready_reported && gpio_get_level(TEST_BUTTON) &&
                     now-last_stop_us>=STOP_COOLDOWN_US && now-last_motion_us>=KEY_QUIET_US &&
                     now-last_motion_key_us>=KEY_QUIET_US) {
                ready_reported=true;
                ESP_LOGI("vehicle", "VEHICLE_READY: firmware=1.9 protocol=PARAM2 build=pwm-stream-v1 teleop_pwm=1 boot_id=%08lx teleop_next=%u front_guard=%s tof=%s move_min_mm=50 move_max_mm=300 turn_min_tdeg=150 turn_max_tdeg=900; valid calibrated BMI required. T stops. No BOOT unlock.",(unsigned long)boot_id,teleop.closed+1,ROBOT_SUPERVISED_TEST?"OFF":"ON",ROBOT_TOF_ENABLED?"ON":"OFF");
            }
        }
        if (!running && now-last_sensor_print>=500000) {
            sensors_print(now);
            last_sensor_print=esp_timer_get_time();
        }
        for (unsigned j=0; j<MOTOR_COUNT; ++j) previous[j]=sample[j];
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}
