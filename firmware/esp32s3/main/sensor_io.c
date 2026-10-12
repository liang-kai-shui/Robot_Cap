#include <string.h>
#include "sensor_io.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "bosch/bmi323.h"
#include "motion_math.h"
#include "imu_sample.h"
#define BUS I2C_NUM_0
#define TAG "sensors"
#include "bmi_init.inc"

typedef struct {
    bool bmi_ready,tof_ready,calibrated,angle_valid,tilt_valid,saturated;
    int bmi_error,tof_error,zero_result;
    unsigned cal_samples,samples,zero_epoch;
    bool zero_pending;
    int64_t bmi_us,tof_us;
    unsigned angle_fault_count;
    int angle_fault_cause,angle_fault_api;
    esp_err_t angle_fault_bus;
    int64_t angle_fault_us,angle_fault_gap_us;
    uint8_t angle_fault_sat_mask,current_sat_mask,last_sat_mask;
    unsigned sat_samples;
    int64_t last_sat_us;
    float last_sat_acc[3],last_sat_gyro[3];
    uint16_t mm;
    uint8_t range_status;
    float norm,roll,pitch,w[3],angle[3],bias[3],a[3],gravity[3];
} sensor_snapshot_t;
static sensor_snapshot_t snapshot;
static portMUX_TYPE snapshot_lock=portMUX_INITIALIZER_UNLOCKED;
static bool zero_requested;
static range_guard_t forward_guard;
enum { ANGLE_OK, ANGLE_SAMPLE_GAP, ANGLE_I2C_ERROR, ANGLE_SATURATION };

static bool init_bus(i2c_port_t port,int sda,int scl) {
    i2c_config_t cfg={.mode=I2C_MODE_MASTER,.sda_io_num=sda,.scl_io_num=scl,
        .sda_pullup_en=GPIO_PULLUP_ENABLE,.scl_pullup_en=GPIO_PULLUP_ENABLE,
        .master.clk_speed=100000};
    return i2c_param_config(port,&cfg)==ESP_OK && i2c_driver_install(port,cfg.mode,0,0,0)==ESP_OK;
}
static void bmi_worker(void *ctx) {
    (void)ctx;
    struct bmi3_dev dev;
    bool ready=init_bus(BUS,8,9)&&sensor_init(&dev);
    portENTER_CRITICAL(&snapshot_lock);
    snapshot.bmi_ready=ready; snapshot.bmi_error=ready?0:-1;
    portEXIT_CRITICAL(&snapshot_lock);
    if(!ready) { vTaskDelete(NULL); return; }
    motion_state_t motion={0};
    motion_sample_t latest={0};
    uint8_t sat_mask=0;
    struct bmi3_sensor_data data[2]={{.type=BMI3_ACCEL},{.type=BMI3_GYRO}};
    unsigned samples=0;
    uint16_t pending=0;
    int64_t last_fresh=0;
    while(true) {
        bool previously_valid=motion.angle_valid;
        int fault_cause=ANGLE_OK;
        int64_t fault_gap_us=0;
        uint16_t status=0;
        int8_t result=bmi323_get_int1_status(&status,&dev);
        if(result==BMI3_OK) {
            pending|=status&(BMI3_INT_STATUS_ACC_DRDY|BMI3_INT_STATUS_GYR_DRDY);
            if(pending==(BMI3_INT_STATUS_ACC_DRDY|BMI3_INT_STATUS_GYR_DRDY)) {
                result=bmi323_get_sensor_data(data,2,&dev);
                pending=0;
                if(result==BMI3_OK) {
                    int64_t now=esp_timer_get_time();
                    const struct bmi3_sens_axes_data *a=&data[0].sens_data.acc,*w=&data[1].sens_data.gyr;
                    const int16_t acc[3]={a->x,a->y,a->z},gyro[3]={w->x,w->y,w->z};
                    sat_mask=(a->sat_x?1:0)|(a->sat_y?2:0)|(a->sat_z?4:0)|
                             (w->sat_x?8:0)|(w->sat_y?16:0)|(w->sat_z?32:0);
                    imu_frame_t frame=imu_convert(acc,gyro,sat_mask);
                    latest=frame.motion;
                    if(last_fresh && now-last_fresh>50000) {
                        fault_cause=ANGLE_SAMPLE_GAP;
                        fault_gap_us=now-last_fresh;
                        motion_break_timing(&motion);
                    }
                    motion_event_t event=imu_update(&motion,&frame,now);
                    if(event==MOTION_SATURATION && fault_cause==ANGLE_OK) fault_cause=ANGLE_SATURATION;
                    if(event==MOTION_TIME_GAP && fault_cause==ANGLE_OK) {
                        fault_cause=ANGLE_SAMPLE_GAP;
                        fault_gap_us=now-last_fresh;
                    }
                    last_fresh=now; ++samples;
                    if(sat_mask) {
                        portENTER_CRITICAL(&snapshot_lock);
                        ++snapshot.sat_samples; snapshot.last_sat_us=now; snapshot.last_sat_mask=sat_mask;
                        for(unsigned i=0;i<3;i++) {
                            snapshot.last_sat_acc[i]=latest.a[i];
                            snapshot.last_sat_gyro[i]=latest.w[i];
                        }
                        portEXIT_CRITICAL(&snapshot_lock);
                    }
                }
            }
        }
        int64_t now=esp_timer_get_time();
        if(result!=BMI3_OK||!last_fresh||now-last_fresh>50000) {
            if(result!=BMI3_OK) fault_cause=ANGLE_I2C_ERROR;
            else if(last_fresh) { fault_cause=ANGLE_SAMPLE_GAP; fault_gap_us=now-last_fresh; }
            motion_break_timing(&motion); pending=0;
        }
        // Retain the event that invalidated an angle, even if communication has
        // recovered before the next 500ms console snapshot. No worker logging.
        if(previously_valid && !motion.angle_valid) {
            portENTER_CRITICAL(&snapshot_lock);
            ++snapshot.angle_fault_count;
            snapshot.angle_fault_cause=fault_cause;
            snapshot.angle_fault_api=result;
            snapshot.angle_fault_bus=fault_cause==ANGLE_I2C_ERROR?last_bus_error:ESP_OK;
            snapshot.angle_fault_us=now; snapshot.angle_fault_gap_us=fault_gap_us;
            snapshot.angle_fault_sat_mask=sat_mask;
            portEXIT_CRITICAL(&snapshot_lock);
        }
        bool zero;
        portENTER_CRITICAL(&snapshot_lock);
        zero=zero_requested; zero_requested=false;
        portEXIT_CRITICAL(&snapshot_lock);
        int zero_result=0;
        if(zero) {
            if(motion.calibrated && last_fresh && now-last_fresh<=50000 && result==BMI3_OK && !latest.saturated) {
                motion_zero(&motion); zero_result=1;
            } else zero_result=-1;
        }
        portENTER_CRITICAL(&snapshot_lock);
        snapshot.bmi_error=result;
        snapshot.bmi_us=last_fresh; snapshot.samples=samples;
        snapshot.calibrated=motion.calibrated; snapshot.cal_samples=motion.n;
        snapshot.angle_valid=motion.angle_valid; snapshot.tilt_valid=latest.tilt_valid;
        snapshot.saturated=latest.saturated;
        snapshot.current_sat_mask=sat_mask;
        snapshot.norm=latest.norm; snapshot.roll=latest.roll; snapshot.pitch=latest.pitch;
        for(unsigned i=0;i<3;i++) {
            snapshot.a[i]=latest.a[i];
            if(motion.calibrated) snapshot.gravity[i]=(float)(motion.sum_a[i]/CAL_SAMPLES);
            snapshot.w[i]=latest.w[i]-motion.bias[i];
            snapshot.bias[i]=motion.bias[i]; snapshot.angle[i]=motion.angle[i];
        }
        if(zero_result) snapshot.zero_result=zero_result;
        if(zero_result) snapshot.zero_pending=zero_requested;
        if(zero_result>0) {snapshot.angle_fault_cause=ANGLE_OK;++snapshot.zero_epoch;}
        portEXIT_CRITICAL(&snapshot_lock);
        vTaskDelay(pdMS_TO_TICKS(2));
    }
}
static void tof_worker(void *ctx) {
    (void)ctx;
    bool ready=init_bus(I2C_NUM_1,6,7)&&tof_init();
    portENTER_CRITICAL(&snapshot_lock);
    snapshot.tof_ready=ready; snapshot.tof_error=ready?0:-1;
    range_guard_reset(&forward_guard,ready);
    portEXIT_CRITICAL(&snapshot_lock);
    if(!ready) { vTaskDelete(NULL); return; }
    while(true) {
        uint16_t mm=0; uint8_t status=255; bool error=false;
        bool fresh=tof_poll(&mm,&status,&error);
        int64_t now=esp_timer_get_time();
        portENTER_CRITICAL(&snapshot_lock);
        snapshot.tof_error=error?-2:0;
        range_guard_observe(&forward_guard,fresh,error,mm,status,now);
        if(fresh) { snapshot.mm=mm; snapshot.range_status=status; snapshot.tof_us=now; }
        portEXIT_CRITICAL(&snapshot_lock);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
void sensors_start(void) {
    // Separate tasks and separate controllers: a broken ToF wire cannot stall IMU
    // acquisition or the motor loop. No I2C calls or worker UART logs in motor task.
    range_guard_reset(&forward_guard,false);
    if(xTaskCreatePinnedToCore(bmi_worker,"bmi-sample",8192,NULL,3,NULL,1)!=pdPASS) snapshot.bmi_error=-3;
    if(!ROBOT_SUPERVISED_TEST && xTaskCreatePinnedToCore(tof_worker,"tof-sample",4096,NULL,2,NULL,1)!=pdPASS) snapshot.tof_error=-3;
}
void sensors_zero_request(void) {
    portENTER_CRITICAL(&snapshot_lock);
    zero_requested=true;
    snapshot.zero_pending=true;
    if(!snapshot.bmi_ready) snapshot.zero_result=-1;
    portEXIT_CRITICAL(&snapshot_lock);
}
yaw_sample_t sensors_yaw_read(void) {
    float reference[3],acc[3],angle[3],w[3];
    portENTER_CRITICAL(&snapshot_lock);
    int64_t now=esp_timer_get_time();
    yaw_sample_t v={.sample_us=snapshot.bmi_us,.zero_epoch=snapshot.zero_epoch,
        .fault_events=snapshot.angle_fault_count,.zero_pending=snapshot.zero_pending};
    v.valid=snapshot.bmi_ready&&snapshot.calibrated&&snapshot.angle_valid&&
        snapshot.bmi_error==0&&!snapshot.saturated&&!snapshot.zero_pending&&
        snapshot.bmi_us>0&&now>=snapshot.bmi_us&&now-snapshot.bmi_us<=50000;
    memcpy(reference,snapshot.gravity,sizeof(reference));memcpy(acc,snapshot.a,sizeof(acc));
    memcpy(angle,snapshot.angle,sizeof(angle));memcpy(w,snapshot.w,sizeof(w));
    portEXIT_CRITICAL(&snapshot_lock);
    bool projected=turn_project(reference,acc,angle,w,&v);
    v.valid=v.valid&&projected;
    return v;
}
range_guard_view_t sensors_forward_guard(void) {
    portENTER_CRITICAL(&snapshot_lock);
    range_guard_view_t view=range_guard_read(&forward_guard,esp_timer_get_time());
    portEXIT_CRITICAL(&snapshot_lock);
    return view;
}
void sensors_print(int64_t now) {
    sensor_snapshot_t s;
    portENTER_CRITICAL(&snapshot_lock);
    s=snapshot; snapshot.zero_result=0;
    // Capture time after taking the lock/snapshot, not an earlier motor-loop time
    // that can precede newer measurements (previously printed negative ages).
    now=esp_timer_get_time();
    range_guard_view_t front=range_guard_read(&forward_guard,now);
    portEXIT_CRITICAL(&snapshot_lock);
    bool bmi_fresh=s.bmi_ready&&s.bmi_us&&now>=s.bmi_us&&now-s.bmi_us<=50000&&s.bmi_error==0;
    bool range_valid=s.tof_ready&&s.tof_us&&now>=s.tof_us&&now-s.tof_us<=FRONT_FRESH_US&&s.tof_error==0&&s.range_status==0&&s.mm>0;
    // The last numeric value may be retained for diagnosis, never marked valid
    // after a bus error, stale update or invalid optical return.
    if(ROBOT_SUPERVISED_TEST) {
        ESP_LOGI(TAG,"TOF DISABLED front_guard=OFF; supervised test. BMI and encoder feedback remain active.");
    } else {
    ESP_LOGI(TAG,"TOF ready=%d distance_mm=%u valid=%d status=%u error=%d age_ms=%lld (SDA6 SCL7)",
        s.tof_ready,s.mm,range_valid,s.range_status,s.tof_error,s.tof_us?(now-s.tof_us)/1000:-1LL);
    ESP_LOGI(TAG,"FORWARD_GUARD blocked=%d cause=%s distance_mm=%u age_ms=%lld clear_samples=%u/3; W stop<=250mm release>=350mm. Rear/sides unmonitored.",
        front.blocked,front_cause_name(front.cause),front.mm,front.age_us<0?-1LL:front.age_us/1000,front.clear_samples);
    }
    static bool calibration_announced;
    if(s.calibrated && !calibration_announced) {
        calibration_announced=true;
        ESP_LOGI(TAG,"CALIBRATION_OK bias[dps]=(%.3f,%.3f,%.3f); Z zero, hand-turn chassis.",s.bias[0],s.bias[1],s.bias[2]);
    }
    if(s.zero_result) ESP_LOGI(TAG,"%s",s.zero_result>0?"ANGLE_ZERO_OK":"ANGLE_ZERO_REJECTED: wait for calibrated fresh BMI data");
    static unsigned reported_angle_faults;
    if(s.angle_fault_count!=reported_angle_faults) {
        reported_angle_faults=s.angle_fault_count;
        const char *cause=s.angle_fault_cause==ANGLE_SAMPLE_GAP?"SAMPLE_GAP_OVER_50MS":
            s.angle_fault_cause==ANGLE_I2C_ERROR?"I2C_READ_ERROR":
            s.angle_fault_cause==ANGLE_SATURATION?"GYRO_SATURATION":"CLEARED_BY_Z";
        ESP_LOGI(TAG,"ANGLE_INVALID cause=%s at_ms=%lld gap_ms=%.2f api=%d bus=%s sat_mask=0x%02X events=%u; hold still then Z to start a NEW angle measurement.",
            cause,s.angle_fault_us/1000,s.angle_fault_gap_us/1000.0,s.angle_fault_api,
            esp_err_to_name(s.angle_fault_bus),s.angle_fault_sat_mask,s.angle_fault_count);
    }
    static unsigned reported_sat_samples;
    if(s.sat_samples!=reported_sat_samples) {
        reported_sat_samples=s.sat_samples;
        ESP_LOGI(TAG,"SATURATION_SAMPLE at_ms=%lld acc_mask=0x%X gyro_mask=0x%X a_g=(%.3f,%.3f,%.3f) w_dps=(%.2f,%.2f,%.2f) samples=%u; mask bits X=1 Y=2 Z=4.",
            s.last_sat_us/1000,s.last_sat_mask&7,(s.last_sat_mask>>3)&7,
            s.last_sat_acc[0],s.last_sat_acc[1],s.last_sat_acc[2],
            s.last_sat_gyro[0],s.last_sat_gyro[1],s.last_sat_gyro[2],s.sat_samples);
    }
    ESP_LOGI(TAG,"BMI ready=%d cal=%u/250 calibrated=%d fresh=%d error=%d norm_g=%.3f roll=%.1f pitch=%.1f tilt_valid=%d wz_dps=%.2f rotZ_deg=%.1f angle_valid=%d sat_mask=0x%02X",
        s.bmi_ready,s.cal_samples,s.calibrated,bmi_fresh,s.bmi_error,s.norm,s.roll,s.pitch,s.tilt_valid&&bmi_fresh,
        s.w[2],s.angle[2],s.calibrated&&s.angle_valid&&bmi_fresh,s.current_sat_mask);
    yaw_sample_t yaw=sensors_yaw_read();
    ESP_LOGI(TAG,"BODY_YAW angle_deg=%+.1f rate_dps=%+.2f valid=%d level=%d tilt_deg=%.1f zero_pending=%d; J/L use this relative angle.",
        yaw.angle_deg,yaw.rate_dps,yaw.valid,yaw.level,yaw.tilt_deg,yaw.zero_pending);
}
