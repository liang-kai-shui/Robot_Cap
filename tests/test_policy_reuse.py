"""Verified recipe learning and actual repeated execution on an offline rover."""
from copy import deepcopy
import json
import pytest
from agent.physical_policy import generate_physical_policy
from policy.episode import EpisodeRecord
from policy.reuse import PhysicalPolicyLibrary, explicit_recipe, compatibility
from providers.base import LLMResponse
from robot.backends.esp32_serial import build_esp32_registry
from webui.controller import ControlDesk
from webui.demo import DemoBackend
from test_webui import CLIENT,connect,wait_job

INSTRUCTION='前进10厘米，然后左转30度，最后停止'
POLICY='robot.move(.1)\nrobot.turn(30)\nrobot.stop()'

class CountingModel:
    def __init__(self,reply=POLICY): self.reply=reply;self.calls=0
    def generate_policy(self,*args):
        self.calls+=1
        return LLMResponse(self.reply,'test-model',40,12,3,8)

def train(tmp_path):
    model=CountingModel()
    desk=ControlDesk(demo=True,provider_factory=lambda:model,log_dir=tmp_path,monitor=False)
    connect(desk);desk.generate(CLIENT,INSTRUCTION,True)
    job=wait_job(desk)
    assert job['status']=='completed',job.get('error')
    return desk,model

@pytest.mark.parametrize('instruction',[INSTRUCTION,'向前走10cm；往左转30度；停车'])
def test_repeat_skips_model_but_executes_new_actions(tmp_path,instruction):
    desk,model=train(tmp_path)
    try:
        assert desk.policy_library.snapshot()['verified_recipes']==1
        yaw=desk.backend.yaw
        desk.generate(CLIENT,instruction,True)
        job=wait_job(desk)
        assert job['status']=='completed' and model.calls==1
        assert job['diagnostics']['policy_source']=='verified_reuse'
        assert job['timings_ms']['model_request']==0
        assert desk.backend.yaw>yaw+29  # Reused code executed again; no result replay.
        episodes=list((tmp_path/'episodes').glob('*.json'))
        assert len(episodes)==2
        data=json.loads(episodes[-1].read_text(encoding='utf-8'))
        assert data['task_success'] is None and not data['initial_state'].get('pose_available')
        assert data['used_capabilities'] and data['api_surface_signature']
    finally:desk.close()

def test_preview_cannot_train_and_force_new_model_is_obeyed(tmp_path):
    desk=ControlDesk(demo=True,provider_factory=lambda:CountingModel(),log_dir=tmp_path,monitor=False)
    try:
        connect(desk);desk.generate(CLIENT,INSTRUCTION)
        assert wait_job(desk)['status']=='completed'
        assert desk.policy_library.snapshot()['verified_recipes']==0
        preview=desk.tasks[-1]['episode']
        assert not preview['execution_success'] and not preview['trace']
        assert preview['metrics']['llm_calls']==1
    finally:desk.close()
    desk,model=train(tmp_path)
    try:
        desk.generate(CLIENT,INSTRUCTION,force_generate=True)
        assert wait_job(desk)['status']=='completed' and model.calls==2
        assert desk.snapshot()['job']['diagnostics']['policy_source']=='model'
        assert desk.policy_library.snapshot()['verified_recipes']==1  # Preview never evicts a verified run.
    finally:desk.close()

@pytest.mark.parametrize('instruction',['转回去','继续','再来一次','前进10厘米然后避开障碍',
    '前进10厘米，如果前面安全再左转30度','走到距离前面30厘米','前进100厘米','前进10厘米停下再左转30度'])
def test_context_sensor_unsupported_and_partial_requests_never_match(instruction):
    assert explicit_recipe(instruction) is None

def test_disk_reload_revalidates_and_scope_isolation(tmp_path):
    desk,model=train(tmp_path)
    try:
        path=desk.policy_library.path;registry=build_esp32_registry(desk.backend)
        library=PhysicalPolicyLibrary(path,scope='demo')
        match,_=library.lookup(INSTRUCTION,registry)
        assert match and match['policy']==POLICY
        assert PhysicalPolicyLibrary(path,scope='hardware').lookup(INSTRUCTION,registry)[0] is None
        registry.unregister('motion.turn')
        assert library.lookup(INSTRUCTION,registry)[0] is None
        registry=build_esp32_registry(desk.backend)
        value=json.loads(path.read_text(encoding='utf-8'));entry=next(iter(value['entries'].values()))
        entry['policy']='robot.move(.3)\nrobot.stop()'
        path.write_text(json.dumps(value))
        assert library.lookup(INSTRUCTION,registry)[0] is None
    finally:desk.close()

@pytest.mark.parametrize('change',['failed','bad_measurement','wrong_intent','task_failed','not_stopped','cancel_trace'])
def test_unverified_episode_never_enters_library(tmp_path,change):
    desk,_=train(tmp_path)
    try:
        data=deepcopy(desk.tasks[-1]['episode']);data['episode_id']='a'*32
        if change=='failed':data['execution_success']=False
        if change=='task_failed':data['task_success']=False
        if change=='not_stopped':data['final_state']['stopped']=False
        if change=='wrong_intent':data['instruction']='右转30度'
        if change=='cancel_trace':data['trace'].append({'phase':'cancelled'})
        if change=='bad_measurement':
            for event in data['trace']:
                if event.get('capability_id')=='motion.move' and event['phase']=='completed':event['result']['encoder_valid']=False
        lib=PhysicalPolicyLibrary(tmp_path/'rejected.json',scope='demo')
        assert lib.record(EpisodeRecord(**data),build_esp32_registry(desk.backend)) is None
        assert lib.snapshot()['verified_recipes']==0
    finally:desk.close()

def test_disabling_reuse_and_clearing_library_are_real_controls(tmp_path):
    desk,model=train(tmp_path)
    try:
        desk.configure(CLIENT,{'policy_reuse':False})
        desk.generate(CLIENT,INSTRUCTION)
        assert wait_job(desk)['status']=='completed' and model.calls==2
        assert desk.clear_policy_library(CLIENT)['cleared']
        assert desk.policy_library.snapshot()['verified_recipes']==0
    finally:desk.close()

def test_low_budget_cannot_replay_a_longer_recipe(tmp_path):
    desk,_=train(tmp_path)
    try:
        registry=build_esp32_registry(desk.backend)
        assert desk.policy_library.lookup(INSTRUCTION,registry,max_motion_calls=1)[0] is None
    finally:desk.close()

def test_cli_uses_the_same_verified_reuse_and_episode_contract(monkeypatch,tmp_path):
    from test_physical_session import session
    code,port,model,_,record=session(monkeypatch,tmp_path,
        ['前进10厘米，停止','前进10厘米，停止','q'],['robot.move(.1)\nrobot.stop()'])
    assert code==0 and len(model.requests)==1
    assert port.sent.count('@F100\n')==2
    assert record['tasks'][1]['diagnostics']['policy_source']=='verified_reuse'
    metrics=record['tasks'][1]['episode']['metrics']
    assert metrics['llm_calls']==0 and metrics['input_tokens']==0 and metrics['task_success'] is None

def test_execution_error_invalidates_the_reused_recipe(tmp_path):
    desk,model=train(tmp_path)
    try:
        def fail(*args,**kwargs):raise ValueError('test sensor unavailable')
        desk.backend.move=fail
        desk.generate(CLIENT,INSTRUCTION,True)
        assert wait_job(desk)['status']=='failed'
        assert desk.policy_library.snapshot()['verified_recipes']==0
        assert desk.tasks[-1]['episode']['error_type']=='RobotEmergencyStopError'
    finally:desk.close()

def test_cache_hit_does_not_construct_a_provider_or_need_a_key(tmp_path):
    desk,_=train(tmp_path)
    try:
        registry=build_esp32_registry(desk.backend)
        def unavailable():raise AssertionError('provider should not be constructed')
        diagnostics={};timings={}
        policy,response=generate_physical_policy(unavailable,INSTRUCTION,registry,
            policy_library=desk.policy_library,diagnostics=diagnostics,timings=timings,max_motion_calls=9)
        assert policy==POLICY and response.input_tokens==response.output_tokens==0
        assert diagnostics['model_call_skipped'] and timings['model_request']==0
    finally:desk.close()


def test_episode_counts_planning_and_segment_review_usage(monkeypatch, tmp_path):
    from test_physical_mission import run_session, FOUR
    code, _, _, record = run_session(monkeypatch, tmp_path, [FOUR, 'CONTINUE'])
    assert code == 0
    metrics = record['tasks'][0]['episode']['metrics']
    assert metrics['planning_llm_calls'] == metrics['review_llm_calls'] == 1
    assert metrics['llm_calls'] == 2
    assert metrics['input_tokens'] == 200 and metrics['output_tokens'] == 40
    review = record['tasks'][0]['reviews'][0]
    assert metrics['llm_total_ms'] == pytest.approx(
        record['tasks'][0]['timings_ms']['model_request'] + review['elapsed_ms'])


def test_invalid_review_usage_is_still_recorded(monkeypatch, tmp_path):
    from test_physical_mission import run_session, FOUR
    code, _, _, record = run_session(monkeypatch, tmp_path, [FOUR, 'unexpected decision'])
    assert code == 1
    metrics = record['tasks'][0]['episode']['metrics']
    assert metrics['llm_calls'] == 2 and metrics['review_llm_calls'] == 1
    assert metrics['input_tokens'] == 200
    assert not metrics['execution_success']


def test_entire_plan_is_rechecked_against_connected_firmware(monkeypatch, tmp_path):
    import physical_control
    from test_esp32_serial import FakePort
    from test_physical_control import install_fake_backend
    port = FakePort()  # v1.8 supports the first action but cannot reverse.
    install_fake_backend(monkeypatch, port)
    monkeypatch.setattr(physical_control, '_model_provider',
                        lambda args: CountingModel('robot.move(.1)\nrobot.reverse(.1)\nrobot.stop()'))
    result = physical_control.main(['--instruction', '前进10厘米，然后后退10厘米',
                                    '--execute', '--log', str(tmp_path/'older.json')])
    assert result == 1 and port.sent and all(wire == 't' for wire in port.sent)
    record = json.loads((tmp_path/'older.json').read_text(encoding='utf-8'))
    assert record['error'] and not record['execution_success']
    assert not record['episode']['trace']


def test_preview_and_execution_do_not_double_count_model_requests(tmp_path):
    model=CountingModel()
    desk=ControlDesk(demo=True,provider_factory=lambda:model,log_dir=tmp_path,monitor=False)
    try:
        connect(desk)
        desk.generate(CLIENT,INSTRUCTION)
        assert wait_job(desk)['status']=='completed'
        plan=desk.snapshot()['plan']
        assert desk.tasks[-1]['episode']['metrics']['llm_calls']==1
        desk.execute(CLIENT,plan['id'])
        assert wait_job(desk)['status']=='completed' and model.calls==1
        metrics=desk.tasks[-1]['episode']['metrics']
        assert metrics['llm_calls']==0 and metrics['planning_request_inherited']
        assert metrics['input_tokens']==metrics['output_tokens']==0
        assert desk.policy_library.snapshot()['verified_recipes']==1
    finally:desk.close()
