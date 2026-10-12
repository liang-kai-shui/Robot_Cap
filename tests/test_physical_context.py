"""Bounded, feedback-backed session memory; no hardware or model requests."""
import json

import pytest

from agent.physical_context import HISTORY_TASK_LIMIT, build_physical_history


def action(name, value=None):
    if name == 'move':
        request = {'distance': value}
        result = {'target_mm': value*1000, 'encoder_valid': True, 'yaw_valid': True,
                  'after_stop_mean_mm': value*1000+12, 'after_stop_yaw_error_deg': .5}
    elif name == 'turn':
        request = {'angle': value}
        result = {'target_deg': value, 'valid': True, 'after_stop_deg': value+.3, 'error_deg': .3}
    else:
        request = {}
        result = {'stop_acknowledged': True, 'wheel_standstill_confirmed': True}
    return {'kind': 'action', 'phase': 'completed', 'capability_id': 'motion.'+name,
            'request': request, 'result': result}


def completed(number=1):
    return {'number': number, 'instruction': '前进10厘米左转90再左转45最后停止',
            'policy_validated': True, 'execution_success': True, 'cancelled': False, 'error': None,
            'trace': [action('move', .1), action('turn', 90), action('turn', 45), action('stop')]}


def test_reversal_uses_commanded_turns_in_reverse_order_excluding_forward_move():
    task = completed()
    history = build_physical_history([task])
    assert history['scope'] == 'current_session_only'
    assert history['turn_back_reference']['commanded_turns_deg'] == [90, 45]
    assert history['turn_back_reference']['inverse_turns_deg'] == [-45, -90]
    assert history['last_completed_motion_task']['status'] == 'completed'
    assert history['last_completed_motion_task']['completed_actions'][1]['after_stop_deg'] == 90.3
    assert 'pose' not in history
    # Snapshots cannot acquire later changes to the journal or serial results.
    task['trace'][1]['request']['angle'] = -30
    assert history['turn_back_reference']['commanded_turns_deg'] == [90, 45]


def test_rejected_requests_do_not_erase_completed_reference_and_context_is_bounded():
    tasks = [completed()]+[{'number': n, 'instruction': 'x'*2000,
        'error': 'REFUSE: unsupported', 'trace': [], 'execution_success': False} for n in range(2, 11)]
    tasks[0]['serial_log'] = ['private full serial transcript']
    tasks[0]['generation'] = {'api_key': 'never-include-key'}
    tasks[0]['policy'] = 'never-include-raw-code'
    history = build_physical_history(tasks)
    assert len(history['recent_tasks']) == HISTORY_TASK_LIMIT
    assert history['last_completed_motion_task']['task_number'] == 1
    assert history['turn_back_reference']['task_number'] == 1
    assert all(item['status'] == 'rejected_without_motion' for item in history['recent_tasks'])
    assert all(len(item['instruction']) <= 500 for item in history['recent_tasks'])
    encoded = json.dumps(history, allow_nan=False)
    assert all(secret not in encoded for secret in ('private full', 'never-include-key', 'never-include-raw-code'))


@pytest.mark.parametrize('change', ['partial', 'cancelled', 'bad_feedback', 'missing_stop', 'error'])
def test_unconfirmed_motion_invalidates_old_reversal_reference(change):
    latest = completed(2)
    if change == 'partial':
        latest.update(execution_success=False, error='Second turn failed', trace=latest['trace'][:2])
    elif change == 'cancelled':
        latest['cancelled'] = True
    elif change == 'bad_feedback':
        latest['trace'][1]['result']['valid'] = False
    elif change == 'missing_stop':
        latest['trace'].pop()
    else:
        latest['error'] = 'READY lost'
    history = build_physical_history([completed(), latest])
    assert history['turn_back_reference'] is None and history['last_completed_motion_task'] is None
    assert history['recent_tasks'][-1]['status'] != 'completed'


def test_previews_and_new_sessions_have_no_executed_reversal_reference():
    task = {'number': 1, 'instruction': '左转90', 'policy_validated': True,
            'execution_success': False, 'trace': []}
    history = build_physical_history([task])
    assert history['recent_tasks'][0]['status'] == 'preview_not_executed'
    assert history['turn_back_reference'] is None
    assert build_physical_history([])['recent_tasks'] == []


def test_latest_forward_only_task_does_not_reverse_an_older_turn():
    task = completed(2)
    task['trace'] = [action('move', .1), action('stop')]
    history = build_physical_history([completed(), task])
    assert history['last_completed_motion_task']['task_number'] == 2
    assert history['turn_back_reference'] is None
