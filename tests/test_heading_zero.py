"""Recovery of an invalid gyro integral without motion or fabricated angles."""
import pytest
from robot.backends.esp32_serial import ESP32SerialBackend
from runtime.errors import RobotActionError, RobotActionTimeoutError
from webui.controller import ControlDesk
from test_esp32_serial import connected
from test_webui import CLIENT, connect, wait_job

INVALID='ATTITUDE roll=5 pitch=7 yaw=-0.6 tilt_valid=1 yaw_valid=0 calibrated=1 age_ms=5 zero_epoch=3\n'
RECOVERED=INVALID.replace('yaw=-0.6','yaw=0.0').replace('yaw_valid=0','yaw_valid=1').replace('zero_epoch=3','zero_epoch=4')

def test_zero_requires_ack_and_a_new_valid_epoch_without_moving():
    backend,port=connected(responses={'z':'ANGLE_ZERO_OK\n'+RECOVERED})
    try:
        backend._on_line(INVALID)
        result=backend.zero_heading(timeout=.3)
        assert result['zero_acknowledged'] and result['zero_epoch']==4 and result['angle_deg']==0
        assert backend.get_attitude()['yaw']==0
        assert port.sent==['t','z'] and backend.snapshot()['stopped'] is True
    finally: backend.close()

@pytest.mark.parametrize('reply',['ANGLE_ZERO_OK\n'+INVALID,
    RECOVERED,'ANGLE_ZERO_OK\n'+RECOVERED.replace('yaw_valid=1','yaw_valid=0'),
    'ANGLE_ZERO_OK\n'+RECOVERED.replace('age_ms=5','age_ms=100')])
def test_zero_never_reports_success_for_old_invalid_or_unacknowledged_measurement(reply):
    backend,port=connected(responses={'z':reply})
    try:
        backend._on_line(INVALID)
        with pytest.raises(RobotActionTimeoutError): backend.zero_heading(timeout=.05)
        assert port.sent[:2]==['t','z'] and set(port.sent)<= {'t','z'}
    finally: backend.close()

@pytest.mark.parametrize('sample',[None,INVALID.replace('calibrated=1','calibrated=0'),INVALID.replace('tilt_valid=1','tilt_valid=0')])
def test_unusable_bmi_does_not_send_zero(sample):
    backend,port=connected()
    try:
        if sample: backend._on_line(sample)
        with pytest.raises(RobotActionError): backend.zero_heading()
        assert port.sent==['t']
    finally: backend.close()

def test_zero_rejection_is_visible_and_never_retries():
    backend,port=connected(responses={'z':'ANGLE_ZERO_REJECTED: wait for calibrated fresh BMI data\n'})
    try:
        backend._on_line(INVALID)
        with pytest.raises(RobotActionError,match='拒绝'): backend.zero_heading(timeout=.3)
        assert port.sent==['t','z']
    finally: backend.close()

def test_zero_cannot_run_while_not_stopped():
    backend,port=connected()
    try:
        backend._on_line(INVALID);backend._stopped=False
        with pytest.raises(RobotActionError): backend.zero_heading()
        assert port.sent==['t']
    finally: backend.close()

def test_webui_zero_invalidates_preview_and_preserves_session_context(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        connect(desk)
        desk.plan={'id':'old'}
        desk.tasks.append({'instruction':'previous task','trace':[]})
        session=desk.session_id
        desk.zero_heading(CLIENT)
        job=wait_job(desk)
        assert job['status']=='completed' and job['result']['angle_deg']==0
        assert desk.plan is None and desk.session_id==session and len(desk.tasks)==2
        assert desk.tasks[-1]['trace'][0]['kind']=='observation'
        assert desk.backend.action_count==0
    finally: desk.close()

def test_angle_chart_does_not_connect_different_zero_references(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        desk._receive({'kind':'rx','line':INVALID})
        desk.telemetry['samples']=[{'at':1,'yaw':80}]
        desk._receive({'kind':'rx','line':RECOVERED})
        assert [point['yaw'] for point in desk.telemetry['samples']]==[0]
    finally: desk.close()
