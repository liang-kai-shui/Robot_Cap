"""Shared host preflight facts. Firmware remains the final motion authority."""


def execution_profile(state, range_offset_mm):
    """Stable reported configuration; never include boot, pose or sensor health."""
    return {'schema': 1, 'firmware': state.get('firmware'),
            'firmware_build': state.get('firmware_build'),
            'front_guard': state.get('front_guard'),
            'direct_pwm': bool(state.get('pwm_manual_available')),
            'range_offset_mm': range_offset_mm,
            'wheel_calibration': 'not_reported_by_firmware'}


def diagnose(state):
    fault = str(state.get('fault') or '')
    if state.get('firmware_fault_latched'):
        return {'code':'firmware_latched','message':'固件故障已锁定，需要复位',
                'recovery':'拔下电机独立5V、保留USB，按开发板 EN/RST 后再连接；重新打开串口不会清除固件故障。'}
    if any(marker in fault for marker in ('ClearCommError', 'Serial port is closed', 'ESP32 reset')):
        return {'code': 'serial_lost', 'message': '串口连接失效或开发板已复位',
                'recovery': '检查 USB 数据线和串口占用，确认小车静止后重新选择串口连接。'}
    if 'BMI_INVALID_OR_STALE' in fault or 'ANGLE_INVALID' in fault:
        return {'code': 'heading_unavailable', 'message': '航向失效，当前会话已停止',
                'recovery': '让小车静止，重新连接后检查 BMI 回传并执行航向归零；恢复后重新提交任务。'}
    if 'SPEED_OVER_2X_TARGET' in fault:
        return {'code': 'speed_guard', 'message': '闭环速度保护触发',
                'recovery': '停止并检查轮子、编码器接线和固件版本，排除问题后重新连接。'}
    if fault:
        return {'code': 'session_fault', 'message': '设备会话已中止',
                'recovery': '先查看原始故障并检查设备，确认小车静止后重新连接；不会自动重试运动。'}
    if not state.get('connected'):
        return {'code': 'disconnected', 'message': '串口未连接', 'recovery': '选择开发板串口并连接。'}
    if not state.get('ready') or state.get('stopped') is not True:
        return {'code': 'not_idle', 'message': '设备正在运动或等待停稳回传',
                'recovery': '等待停稳 READY；需要中止时使用停止按钮。'}
    return {'code': 'healthy_idle', 'message': '设备已连接并停稳', 'recovery': ''}


def action_availability(state, sensors):
    """Use the same fresh typed observations as the public sensor methods.

    This is an idle preflight, not a claim about 5V presence, ground clearance
    or future sensor health. Unknown/offline state does not forbid planning.
    """
    diagnosis = diagnose(state)
    idle = diagnosis['code'] == 'healthy_idle'
    heading = sensors.get('get_attitude', {}).get('valid') is True
    distance = sensors.get('get_distance', {}).get('valid') is True

    def item(needs_heading=False, needs_range=False, supported=True):
        reason = diagnosis['code'] if not idle else 'unsupported_firmware' if not supported else (
            'heading_unavailable' if needs_heading and not heading else
            'range_unavailable' if needs_range and not distance else 'available')
        return {'available': reason == 'available', 'reason': reason}

    param2 = state.get('firmware') == '1.9'
    return {'basis': 'host_preflight_firmware_rechecks',
            'move': item(True), 'reverse': item(True, supported=param2),
            'turn': item(True), 'approach': item(True, True, param2),
            'manual_pwm': item(supported=param2 and state.get('pwm_manual_available')),
            'manual_hold_forward': item(True, supported=param2),
            'manual_hold_other': item(supported=param2),
            'manual_reverse_timed': item(),
            'stop': {'available': bool(state.get('connected')), 'reason': 'stop_always_allowed'}}
