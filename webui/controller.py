"""Single-owner local control service. UI never owns or writes the serial port."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import ast
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlparse

from agent.physical_context import build_physical_history
from agent.physical_mission import split_mission, review_segment
from agent.physical_context import _summary
from agent.physical_policy import generate_physical_policy, validate_physical_policy
from physical_control import _physical_limits
from providers.openai_compatible import OpenAICompatibleProvider
from robot.backends.esp32_serial import ESP32SerialBackend, build_esp32_registry
from robot.hardware_limits import move_mm, turn_tenths
from robot.runtime import RobotRuntime
from robot.state import Pose
from runtime.errors import RobotEmergencyStopError, RobotActionTimeoutError
from runtime.executor import PolicyExecutor
from webui.demo import DemoBackend, DemoProvider


ROOT = Path(__file__).resolve().parents[1]
HELP = '命令：help / status / ports / connect COM5 / disconnect / move 0.10 / turn 30 / back / stop / plan 前进10厘米 / run / build。move单位米，turn单位度，back固定后退0.8秒；这是机器人命令终端。'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ControlDesk:
    def __init__(self, *, demo=False, backend_factory=None, provider_factory=None, log_dir=None,
                 lease_seconds=4, monitor=True):
        self.demo = demo
        self.backend_factory = backend_factory or (DemoBackend if demo else ESP32SerialBackend)
        self.provider_factory = provider_factory
        self.log_dir = Path(log_dir) if log_dir else ROOT/'runs'/'webui'
        self.lock = threading.RLock()
        self.backend = None
        self.owner = None
        self.epoch = self.revision = 0
        self.last_heartbeat = time.monotonic()
        self.lease_seconds = lease_seconds
        self.job = None
        self.runtime = None
        self.process = None
        self.plan = None
        self.tasks = []
        self.session_id = uuid.uuid4().hex
        self.boot_id = None
        self.holding = None
        self.range_offset_mm = 0
        self.errors = deque(maxlen=50)
        try:
            calibration=json.loads((self.log_dir/'range-calibration.json').read_text(encoding='utf-8'))
            value=calibration.get('offset_mm')
            if type(value) is int and -100<=value<=100: self.range_offset_mm=value
        except (OSError,ValueError): pass
        self.jobs = deque(maxlen=24)
        self.events = deque(maxlen=1600)
        self.event_id = 0
        self.serial_cursor = 0
        self.serial_last = None
        self.telemetry = {'bmi': None, 'yaw': None, 'range': None, 'tof_disabled': None,
                          'wheels': {}, 'samples': [], 'last_move': None, 'attitude': None}
        self.settings = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'reasoning_effort':'none', 'review_mode':'local'}
        self._key = os.getenv('LLM_API_KEY') or os.getenv('DEEPSEEK_API_KEY') or ''
        self.finished = threading.Event()
        self.thread = None
        self.emit('system', 'info', '控制台已启动。'+('演示模式：不连接硬件、不请求真实模型。' if demo else '选择串口并连接设备。'))
        if monitor:
            self.thread = threading.Thread(target=self._monitor, name='webui-monitor', daemon=True)
            self.thread.start()

    def scrub(self, value):
        text = str(value)
        if self._key:
            text = text.replace(self._key, '[密钥已隐藏]')
        return re.sub(r'(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{25,})', '[密钥已隐藏]', text)

    def emit(self, channel, level, text):
        with self.lock:
            self.event_id += 1
            self.events.append({'id': self.event_id, 'at': utc_now(), 'channel': channel,
                                'level': level, 'text': self.scrub(text)})

    def redact(self, value):
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        return value

    def heartbeat(self, owner):
        with self.lock:
            if self.owner is None or self.owner == owner:
                self.last_heartbeat = time.monotonic()

    def _claim(self, owner):
        if not isinstance(owner, str) or not 8 <= len(owner) <= 80:
            raise ValueError('页面标识无效，请刷新页面')
        if self.owner not in (None, owner):
            if self._active() or self.holding or time.monotonic()-self.last_heartbeat <= self.lease_seconds:
                raise ValueError('设备正由另一页面控制；可先停止，再由原页面断开')
        self.owner = owner
        self.last_heartbeat = time.monotonic()

    def _active(self):
        return self.job is not None and self.job['status'] == 'running'

    def _submit(self, owner, kind, title, function):
        with self.lock:
            self._claim(owner)
            if self._active() or self.holding:
                raise ValueError('当前任务尚未停稳，不接受排队动作')
            job = {'id': uuid.uuid4().hex, 'kind': kind, 'title': title, 'status': 'running',
                   'phase': '准备', 'started_at': utc_now(), 'started': time.monotonic(),
                   'cancel': threading.Event(), 'error': None, 'result': None, 'timings_ms': {},
                   'motion_attempted': False, 'policy':None, 'diagnostics':{}, 'session_id':self.session_id}
            self.job = job
            self.runtime = None
        self.emit('task', 'info', title)

        def run():
            try:
                result = function(job)
                self._check(job, hardware=False)
                with self.lock:
                    if job['cancel'].is_set():
                        raise RobotEmergencyStopError('任务已取消')
                    job.update(status='completed', phase='完成', result=result)
                self.emit('task', 'success', title+'：完成')
            except BaseException as exc:
                if job['motion_attempted'] and self.backend:
                    try:
                        self.backend.emergency_stop()
                    except Exception as stop_error:
                        self.emit('system','error','停止发送失败：'+self.scrub(stop_error))
                with self.lock:
                    job.update(status='cancelled' if job['cancel'].is_set() else 'failed',
                               phase='已中止' if job['cancel'].is_set() else '失败',
                               error=self.scrub(f'{type(exc).__name__}: {exc}'))
                diagnostics=self.redact(job.get('diagnostics') or {})
                category='model_call' if diagnostics.get('stage')=='model_call' else 'validation' if diagnostics.get('stage')=='validation' else 'execution' if job['motion_attempted'] else 'service'
                if diagnostics.get('raw_response'): diagnostics['raw_response']=diagnostics['raw_response'][:8192]
                item={'id':job['id'],'at':utc_now(),'instruction':job['title'],'category':category,
                      'type':type(exc).__name__,'message':job['error'],**diagnostics}
                with self.lock: self.errors.append(item)
                self.emit('model_error' if category in ('model_call','validation') else 'task', 'error', job['error'])
            finally:
                with self.lock:
                    job['elapsed_ms'] = (time.monotonic()-job['started'])*1000
                    self.jobs.append(self._public_job(job))
                    if job['kind']=='plan' and job['session_id']==self.session_id:
                        self.tasks.append({'number':len(self.tasks)+1,'instruction':job['title'],
                            'execution_success':False,'policy_validated':job['status']=='completed',
                            'error':job['error'],'cancelled':job['cancel'].is_set(),'trace':[]})
                self.save()
        threading.Thread(target=run, name='webui-job-'+kind, daemon=True).start()
        return {'job_id': job['id']}

    def _phase(self, job, phase):
        with self.lock:
            job['phase'] = phase
        self.emit('task', 'info', phase)

    def _check(self, job, *, hardware=True):
        if job['cancel'].is_set():
            raise RobotEmergencyStopError('任务已取消，剩余动作不执行')
        if time.monotonic()-job['started'] >= 180:
            raise RobotActionTimeoutError('整项任务超过180秒')
        if hardware:
            state = self._healthy()
            return state

    def _healthy(self):
        backend = self.backend
        if backend is None:
            raise ValueError('先连接小车')
        state = backend.snapshot()
        if state['fault'] or not state['connected'] or not state['ready'] or state['stopped'] is not True:
            raise RobotEmergencyStopError('设备未健康停稳：'+str(state['fault'] or '等待READY'))
        return state

    def _wait_model(self, job, function, *, hardware):
        result, ready = {}, threading.Event()
        def call():
            try:
                result['value'] = function()
            except BaseException as exc:
                result['error'] = exc
            finally:
                ready.set()
        threading.Thread(target=call, name='webui-model', daemon=True).start()
        while True:
            self._check(job, hardware=hardware)
            if ready.wait(.025):
                self._check(job, hardware=hardware)
                if 'error' in result:
                    raise result['error']
                return result['value']

    def ports(self):
        if self.demo:
            return [{'device': 'DEMO', 'description': '离线演示设备'}]
        from serial.tools.list_ports import comports
        return [{'device': p.device, 'description': p.description} for p in comports()]

    def connect(self, owner, port):
        if not isinstance(port, str) or not re.fullmatch(r'(?:COM[1-9]\d{0,3}|/dev/(?:tty|cu\.)[A-Za-z0-9._-]+|DEMO)', port):
            raise ValueError('请选择有效串口')
        with self.lock:
            if self.backend and self.backend.snapshot()['connected'] and not self.backend.snapshot()['fault']:
                raise ValueError('设备已连接，切换串口前先断开')
        def run(job):
            self._phase(job, '连接并等待停稳确认')
            if self.backend:
                self.backend.close()
            backend = self.backend_factory(port)
            with self.lock:
                self.backend = backend
                self.serial_cursor = 0
                self.serial_last = None
                self.plan = None
                self.epoch += 1
                self.telemetry = {'bmi': None, 'yaw': None, 'range': None, 'tof_disabled': None, 'wheels': {}, 'samples': [], 'last_move': None, 'attitude': None}
            started = time.perf_counter()
            backend.connect()
            new_boot=backend.snapshot().get('boot_id')
            with self.lock:
                if new_boot is None or new_boot!=self.boot_id:
                    self.tasks.clear(); self.session_id=uuid.uuid4().hex
                self.boot_id=new_boot
            if hasattr(backend,'configure_range_offset'): backend.configure_range_offset(self.range_offset_mm)
            job['timings_ms']['connection_handshake'] = (time.perf_counter()-started)*1000
            self._check(job)
            return backend.snapshot()
        return self._submit(owner, 'connect', '连接 '+port, run)

    def disconnect(self, owner):
        with self.lock:
            self._claim(owner)
        self.stop('主动断开')
        backend = self.backend
        if backend:
            backend.close()
        with self.lock:
            self.plan = None
            self.epoch += 1
            self.owner = None
        self.emit('system', 'info', '已断开设备；停止确认以实际回传为准')
        return {'disconnected': True}

    def stop(self, reason='停止按钮'):
        with self.lock:
            job, backend, runtime, process = self.job, self.backend, self.runtime, self.process
            holding=self.holding
            self.holding=None
            active = self._active()
            if active:
                job['cancel'].set()
                job['phase'] = '正在中止'
            self.plan = None
        self.emit('system', 'warning', reason+'：请求停止')
        if backend:
            if holding:
                backend.teleop_release()
            if active:
                (runtime.emergency_stop if runtime else backend.emergency_stop)()
            elif backend.snapshot()['connected']:
                try:
                    backend.stop()
                except Exception as exc:
                    self.emit('system', 'error', self.scrub(exc))
        if process and process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
        return {'stop_requested': True, 'session_requires_reconnect': active and backend is not None}

    def configure(self, owner, data):
        with self.lock:
            self._claim(owner)
            if self._active() or self.holding:
                raise ValueError('任务运行中不能修改模型配置')
            url = data.get('base_url', self.settings['base_url']).strip().rstrip('/')
            model = data.get('model', self.settings['model']).strip()
            parsed = urlparse(url)
            allowed_url = parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1'))
            if not allowed_url or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not model or len(model) > 120 or len(url) > 500:
                raise ValueError('模型名称或服务地址无效')
            key = data.get('api_key')
            if key is not None:
                if not isinstance(key, str) or len(key) > 2000:
                    raise ValueError('API Key格式无效')
                self._key = key.strip()
            reasoning=data.get('reasoning_effort',self.settings['reasoning_effort'])
            review=data.get('review_mode',self.settings['review_mode'])
            if reasoning not in ('none','low','high') or review not in ('local','model'):
                raise ValueError('无效的思考/分段审核配置')
            self.settings.update(base_url=url, model=model, reasoning_effort=reasoning, review_mode=review)
        self.emit('system', 'success', '模型配置已保存到本次进程内存')
        return {'configured': bool(self._key) or self.demo}

    def _provider(self):
        if self.provider_factory:
            return self.provider_factory()
        if self.demo:
            return DemoProvider()
        with self.lock:
            if not self._key:
                raise ValueError('请先在设置中填写模型API Key')
            return OpenAICompatibleProvider(api_key=self._key, base_url=self.settings['base_url'], model=self.settings['model'], reasoning_effort=self.settings['reasoning_effort'], stable_api_prefix=True)

    def generate(self, owner, instruction, auto_execute=False):
        if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 2000:
            raise ValueError('请输入不超过2000字的任务')
        if type(auto_execute) is not bool: raise ValueError('执行模式无效')
        if auto_execute: self._healthy()
        instruction = self.scrub(instruction.strip())
        provider = self._provider()
        def run(job):
            with self.lock:
                self.plan = None
                history = build_physical_history(list(self.tasks))
                stamp = (self.epoch, self.revision)
            backend = self.backend or self.backend_factory('COM5')
            registry = build_esp32_registry(backend)
            connected = backend.snapshot()['connected']
            self._phase(job, '正在生成并校验完整计划')
            timings = {}; diagnostics=job['diagnostics']
            feedback=backend.snapshot()
            sensors={}
            for name in ('get_distance','get_attitude'):
                try: sensors[name]=getattr(backend,name)()
                except Exception: sensors[name]={'valid':False,'reason':'unavailable_or_stale'}
            feedback['sensors']=sensors
            try:
                policy, response = self._wait_model(job, lambda: generate_physical_policy(provider, instruction,
                registry, feedback=feedback, history=history, timings=timings, max_motion_calls=9, diagnostics=diagnostics), hardware=connected)
            finally:
                job['timings_ms'].update(timings)
                job['policy']=self.scrub(diagnostics.get('policy',diagnostics.get('raw_response','')))[:8192]
            diagnostics['stage']='validated'
            policies = split_mission(policy, registry)
            self._check(job, hardware=connected)
            plan = {'id': uuid.uuid4().hex, 'instruction': instruction, 'policy': policy, 'segments': policies,
                    'model': response.model, 'timings_ms': dict(timings), 'epoch': stamp[0], 'revision': stamp[1]}
            with self.lock:
                self._check(job, hardware=False)
                if stamp != (self.epoch, self.revision):
                    raise ValueError('设备会话已变化，请重新生成计划')
                self.plan = plan
            job['policy']=policy
            self.emit('policy', 'success', policy)
            if auto_execute:
                self._check(job)
                with self.lock: self.plan=None
                job['kind']='execute'
                job['diagnostics']['stage']='execution'
                return self._execute(job,instruction,policies,provider)
            return {'plan_id': plan['id'], 'segment_count': len(policies), 'preview_only': True}
        return self._submit(owner, 'plan', instruction, run)

    def manual(self, owner, action, value):
        if action == 'reverse':
            if isinstance(value, bool) or value != .8:
                raise ValueError('手动后退固定0.8秒，前进步长不用于后退')
            self._healthy()
            return self._submit(owner, 'manual', '手动后退 0.8 秒', self._reverse_manual)
        if action == 'move':
            move_mm(value)
        elif action == 'turn':
            turn_tenths(value)
        else:
            raise ValueError('只支持前进或左右转弯')
        self._healthy()
        policy = f'robot.{action}({value})\nrobot.stop()'
        validate_physical_policy(policy, build_esp32_registry(self.backend))
        title = f'前进 {value*100:g} cm' if action == 'move' else f'{"左" if value > 0 else "右"}转 {abs(value):g}°'
        with self.lock:
            self.plan = None
        return self._submit(owner, 'manual', title, lambda job: self._execute(job, title, [policy], None))

    def _reverse_manual(self, job):
        backend, trace = self.backend, []
        task = {'number':len(self.tasks)+1, 'instruction':'手动后退0.8秒，停止',
                'execution_success':False, 'trace':trace, 'error':None}
        with self.lock:
            self.plan = None
        try:
            self._check(job)
            self._phase(job, '短时后退0.8秒并等待停稳')
            before_stop = time.perf_counter()
            backend.stop()
            job['timings_ms']['pre_task_standstill'] = (time.perf_counter()-before_stop)*1000
            self._check(job)
            backend.begin_task()
            self._check(job)
            job['motion_attempted'] = True
            event = {'kind':'action','phase':'started','capability_id':'manual.reverse','request':{'duration':.8}}
            trace.append(event)
            started = time.perf_counter()
            result = backend.reverse(.8, cancel_event=job['cancel'], deadline=job['started']+180)
            trace.append({**event,'phase':'completed','result':result})
            self.emit('feedback','success','manual.reverse: '+json.dumps(result,ensure_ascii=False))
            self._check(job)
            stopped = backend.stop()
            trace.append({'kind':'action','phase':'completed','capability_id':'motion.stop','request':{},'result':stopped})
            self._check(job)
            job['timings_ms']['policy_execution'] = (time.perf_counter()-started)*1000
            task['execution_success'] = True
            return {'execution_success':True,'manual_reverse':True,'duration_ms':800,'task_success':None}
        except BaseException as exc:
            task['error'] = self.scrub(exc)
            raise
        finally:
            with self.lock:
                task['cancelled'] = job['cancel'].is_set()
                if job['session_id']==self.session_id: self.tasks.append(task)
                self.revision += 1
                self.plan = None

    def execute(self, owner, plan_id):
        self._healthy()
        with self.lock:
            self._claim(owner)
            plan = deepcopy(self.plan)
            if not plan or plan['id'] != plan_id or (plan['epoch'], plan['revision']) != (self.epoch, self.revision):
                raise ValueError('计划已失效或已执行，请重新生成')
            if self._active():
                raise ValueError('已有任务在运行')
            provider = self._provider()
            self.plan = None
        def run(job):
            # Preserve the preview's actual model latency, while execution elapsed
            # remains the time since the user clicked Execute.
            job['policy']=plan['policy']
            job['diagnostics']['stage']='execution'
            job['timings_ms'].update(plan['timings_ms'])
            return self._execute(job, plan['instruction'], plan['segments'], provider)
        return self._submit(owner, 'execute', plan['instruction'], run)

    def _execute(self, job, instruction, policies, provider):
        backend, limits = self.backend, _physical_limits()
        registry = build_esp32_registry(backend)
        runtime = RobotRuntime(backend, limits, registry)
        with self.lock:
            self.runtime = runtime
        task = {'number': len(self.tasks)+1, 'instruction': instruction, 'execution_success': False,
                'policy_validated': True, 'error': None, 'trace': [], 'segments': [], 'reviews': []}
        motions = 0
        try:
            for index, policy in enumerate(policies):
                self._check(job)
                validate_physical_policy(policy, registry)
                motions += sum(isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and
                    isinstance(node.value.func,ast.Attribute) and node.value.func.attr in ('move','reverse','turn','approach')
                    for node in ast.parse(policy).body)
                if motions > 9:
                    raise ValueError('整项任务运动预算超过9次')
                self._phase(job, f'执行第 {index+1}/{len(policies)} 段')
                started = time.perf_counter()
                self._healthy()
                job['timings_ms']['pre_task_standstill'] = job['timings_ms'].get('pre_task_standstill', 0)+(time.perf_counter()-started)*1000
                self._check(job)
                backend.begin_task()
                self._check(job)
                runtime.begin_decision(4)
                job['motion_attempted'] = True
                job['policy']=job.get('policy') or '\n'.join(policies)
                try:
                    outcome = PolicyExecutor(limits).execute(policy, None, Pose(0, 0, 0), runtime=runtime)
                finally:
                    runtime.end_decision()
                task['segments'].append({'policy': policy, 'execution_success': outcome.success, 'trace': outcome.trace})
                job['timings_ms']['policy_execution'] = job['timings_ms'].get('policy_execution', 0)+outcome.execution_ms
                for event in outcome.trace:
                    if event['phase'] == 'completed' and event['kind'] == 'action':
                        self.emit('feedback', 'success', event['capability_id']+': '+json.dumps(event['result'], ensure_ascii=False))
                self._check(job)
                if not outcome.success:
                    raise RobotEmergencyStopError(outcome.error_message or '动作未完成')
                if index+1 < len(policies):
                    self._phase(job, '已停稳，检查本段反馈')
                    if self.settings['review_mode']=='local':
                        started_review=time.perf_counter()
                        summary=_summary({'execution_success':outcome.success,'trace':outcome.trace})
                        self._healthy()
                        if summary['status']!='completed':
                            raise RobotEmergencyStopError('本地反馈审核失败，不执行剩余计划')
                        task['reviews'].append({'decision':'CONTINUE','source':'local_validated_feedback'})
                        job['timings_ms']['feedback_review']=job['timings_ms'].get('feedback_review',0)+(time.perf_counter()-started_review)*1000
                        continue
                    self._phase(job,'已停稳，等待模型审核反馈')
                    segment = {'number': index+1, 'instruction': instruction, 'execution_success': True,
                               'trace': outcome.trace}
                    timings = {}
                    review = self._wait_model(job, lambda: review_segment(provider, instruction, backend,
                        segment, policies[index+1:], timings=timings), hardware=True)
                    job['timings_ms']['feedback_review'] = job['timings_ms'].get('feedback_review', 0)+timings.get('feedback_review', 0)
                    task['reviews'].append(review)
                    self.emit('task', 'info', '反馈决策：'+review['decision'])
                    if review['decision'] != 'CONTINUE':
                        raise RobotEmergencyStopError('模型中止：'+review.get('reason', ''))
            self._check(job)
            task['execution_success'] = True
            return {'completed_motion_calls': motions, 'segment_count': len(policies),
                    'execution_success': True, 'task_success': None}
        except BaseException as exc:
            task['error'] = self.scrub(exc)
            raise
        finally:
            task.update(trace=list(runtime.trace), cancelled=job['cancel'].is_set(), timings_ms=dict(job['timings_ms']))
            with self.lock:
                if job['session_id']==self.session_id: self.tasks.append(task)
                self.revision += 1
                self.plan = None

    def teleop_start(self, owner, direction):
        with self.lock:
            self._claim(owner)
            if self._active() or self.holding: raise ValueError('任务运行中，持续控制不能排队')
            self._healthy()
            result=self.backend.teleop_start(direction)
            self.plan=None;self.revision+=1
            self.holding={'direction':direction,'session':result['session'],'started_at':utc_now(),'_pulse_at':time.monotonic(),'_began':time.monotonic()}
            # Manual repositioning invalidates previous turn-back reference.
            self.tasks.append({'number':len(self.tasks)+1,'instruction':'按住手动控制 '+direction,
                'execution_success':False,'trace':[{'kind':'action','phase':'started','capability_id':'manual.teleop'}]})
            return result

    def teleop_pulse(self, owner):
        with self.lock:
            if owner!=self.owner or not self.holding: raise ValueError('当前按键已失效，请重新按下')
            if time.monotonic()-self.holding['_pulse_at']>.2 or time.monotonic()-self.holding['_began']>=30:
                self.teleop_release(owner,reason='按住超时');raise ValueError('持续控制已超时')
            result=self.backend.teleop_pulse()
            self.holding['_pulse_at']=time.monotonic()
            self.last_heartbeat=time.monotonic()
            return result

    def teleop_release(self, owner, reason='松开按键'):
        with self.lock:
            if owner!=self.owner or not self.holding: return {'released':True}
            self.holding=None
            backend=self.backend
        if backend: backend.teleop_release()
        self.emit('task','info',reason+'：驱动停止，等待停稳回传')
        return {'released':True}

    def zero_heading(self, owner):
        self._healthy()
        def run(job):
            self._check(job)
            self._phase(job,'保持小车静止，重建相对转角零点')
            with self.lock:
                self.plan=None; self.revision+=1
            result=self.backend.zero_heading(cancel_event=job['cancel'])
            # Record the new measurement reference without inventing chassis motion.
            with self.lock:
                self.tasks.append({'number':len(self.tasks)+1,'instruction':'相对航向归零，开始新的角度参考',
                    'execution_success':False,'trace':[{'kind':'observation','phase':'completed','capability_id':'state.yaw_zero','result':result}]})
            return result
        return self._submit(owner,'calibration','航向归零 / 恢复',run)

    def set_range_offset(self, owner, offset):
        if type(offset) is not int or not -100<=offset<=100:
            raise ValueError('校正值需为-100..100的整数毫米')
        with self.lock:
            self._claim(owner)
            if self._active() or self.holding: raise ValueError('先停止当前任务，再调整校正值')
            if self.backend:
                self._healthy()
                self.backend.configure_range_offset(offset)
            self.log_dir.mkdir(parents=True,exist_ok=True)
            (self.log_dir/'range-calibration.json').write_text(json.dumps({'offset_mm':offset,'source':'ruler_manual','at':utc_now()}),encoding='utf-8')
            self.range_offset_mm=offset; self.plan=None; self.revision+=1
            self.emit('calibration','info',f'测距显示和模型距离校正 {offset:+d} mm；原始读数保留。')
            return {'offset_mm':offset}

    def calibrate_range(self, owner, known_mm):
        if type(known_mm) is not int or not 100<=known_mm<=2000: raise ValueError('标定距离需为100..2000毫米；建议140毫米平整目标')
        self._healthy()
        def run(job):
            self._phase(job,'采集20个测距样本，保持目标静止')
            import statistics
            values=[];last=None
            end=time.monotonic()+7
            while len(values)<20 and time.monotonic()<end:
                self._check(job)
                value=self.backend.get_distance()
                sample=getattr(self.backend,'_range',None)
                identity=sample.get('_at') if sample else time.monotonic()
                if identity!=last:
                    values.append(value['raw_mm']);last=identity
                job['cancel'].wait(.065)
            if len(values)<20 or max(values)-min(values)>20: raise ValueError('有效测距样本不足或目标不稳定，请重新摆放')
            offset=round(known_mm-statistics.median(values))
            if not -100<=offset<=100: raise ValueError('偏差超过100毫米，先检查镜头与测量基准')
            self.backend.configure_range_offset(offset)
            with self.lock: self.range_offset_mm=offset;self.plan=None;self.revision+=1
            self.log_dir.mkdir(parents=True,exist_ok=True)
            (self.log_dir/'range-calibration.json').write_text(json.dumps({'offset_mm':offset,'known_mm':known_mm,'samples':values,'at':utc_now()}),encoding='utf-8')
            return {'offset_mm':offset,'known_mm':known_mm,'raw_median_mm':statistics.median(values)}
        return self._submit(owner,'calibration','测距偏移标定',run)

    def build(self, owner, *, flash_port=None, motor_power_disconnected=False):
        if self.backend and self.backend.snapshot()['connected']:
            raise ValueError('编译前先断开设备，避免同时占用控制资源')
        if flash_port is not None:
            if motor_power_disconnected is not True:
                raise ValueError('烧录前请拔下电机独立5V电源，只保留USB，并勾选确认')
            if not isinstance(flash_port,str) or not re.fullmatch(r'COM[1-9]\d{0,3}',flash_port):
                raise ValueError('请选择开发板对应的COM串口')
            if not self.demo and flash_port not in {p['device'] for p in self.ports()}:
                raise ValueError('选定串口不存在，请刷新串口')
        def run(job):
            self._phase(job, '编译并烧录v1.9固件' if flash_port else '编译固件（不烧录）')
            if self.demo:
                self.emit('build', 'info', '演示模式：展示构建入口，不运行工具链')
                return {'demo': True, 'flashed': False}
            idf = Path(os.getenv('IDF_PATH') or r'D:\Espressif\frameworks\esp-idf-v5.1.2')
            script = idf/'tools'/'idf.py'
            if not script.is_file():
                raise ValueError('未找到ESP-IDF5.1.2，请配置IDF_PATH后重新启动界面')
            tools = Path(os.getenv('IDF_TOOLS_PATH') or r'D:\Espressif')
            python = tools/'python_env'/'idf5.1_py3.11_env'/'Scripts'/'python.exe'
            env = os.environ.copy()
            env.update(IDF_PATH=str(idf), IDF_TOOLS_PATH=str(tools), CCACHE_DISABLE='1', PYTHONIOENCODING='utf-8')
            bins = []
            for component, filename in [('ninja', 'ninja.exe'), ('cmake', 'cmake.exe'),
                                        ('xtensa-esp32s3-elf', 'xtensa-esp32s3-elf-gcc.exe'), ('ccache', 'ccache.exe')]:
                candidate = next((tools/'tools'/component).rglob(filename), None)
                if candidate:
                    bins.append(str(candidate.parent))
            env['PATH'] = os.pathsep.join(bins+[env.get('PATH', '')])
            command = [str(python) if python.is_file() else sys.executable, '-u', str(script),
                       '-C', str(ROOT/'firmware'/'esp32s3'), 'build']
            if flash_port: command=[*command[:-1],'-p',flash_port,'flash']
            with self.lock:
                self._check(job, hardware=False)
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', env=env, cwd=ROOT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.process = process
            try:
                for line in process.stdout:
                    self.emit('build', 'info', line.rstrip())
                    self._check(job, hardware=False)
                code = process.wait(timeout=5)
                if code:
                    raise ValueError(f'构建退出码{code}，查看终端输出')
                if flash_port:
                    with self.lock:
                        self.tasks.clear();self.boot_id=None;self.session_id=uuid.uuid4().hex;self.plan=None;self.epoch+=1
                return {'build_success': True, 'flashed': bool(flash_port)}
            finally:
                if process.poll() is None:
                    process.terminate()
                with self.lock:
                    self.process = None
        return self._submit(owner, 'flash' if flash_port else 'build', '烧录ESP32固件' if flash_port else '构建ESP32固件', run)

    def terminal(self, owner, command):
        if not isinstance(command, str) or len(command) > 2100:
            raise ValueError('命令格式无效')
        parts = shlex.split(command)
        if not parts:
            return {'text': ''}
        verb = parts[0].lower()
        if verb not in {'help', 'status', 'ports', 'connect', 'disconnect', 'move', 'turn', 'back', 'stop', 't', 'plan', 'run', 'build'}:
            raise ValueError('未知机器人命令。输入help查看支持的命令；不执行系统Shell或Python代码')
        self.emit('terminal', 'info', '> '+command)
        if verb == 'help': result = {'text': HELP}
        elif verb == 'status': result = {'text': json.dumps(self.snapshot()['device'], ensure_ascii=False)}
        elif verb == 'ports': result = {'text': json.dumps(self.ports(), ensure_ascii=False)}
        elif verb in ('stop', 't'): result = self.stop('终端停止')
        elif verb == 'disconnect': result = self.disconnect(owner)
        elif verb == 'connect' and len(parts) == 2: result = self.connect(owner, parts[1])
        elif verb in ('move', 'turn') and len(parts) == 2: result = self.manual(owner, verb, float(parts[1]))
        elif verb == 'back' and len(parts) == 1: result = self.manual(owner, 'reverse', .8)
        elif verb == 'plan' and len(parts) >= 2: result = self.generate(owner, command.partition(' ')[2])
        elif verb == 'run': result = self.execute(owner, (self.plan or {}).get('id'))
        elif verb == 'build': result = self.build(owner)
        else: raise ValueError('参数不完整。'+HELP)
        if 'text' in result:
            self.emit('terminal', 'info', result['text'])
        return result

    def _public_job(self, job):
        if job is None:
            return None
        data = {key: deepcopy(job.get(key)) for key in ('id', 'kind', 'title', 'status', 'phase', 'started_at', 'error', 'result', 'timings_ms', 'policy', 'diagnostics', 'session_id')}
        data['elapsed_ms'] = job.get('elapsed_ms', (time.monotonic()-job['started'])*1000)
        return self.redact(data)

    def snapshot(self):
        with self.lock:
            backend = self.backend
            data = {'mode': 'demo' if self.demo else 'hardware', 'owner': self.owner, 'epoch': self.epoch,
                    'lease_age_ms': (time.monotonic()-self.last_heartbeat)*1000,
                    'holding':deepcopy(self.holding),'session_id':self.session_id,'context_tasks':len(self.tasks),
                    'errors':list(self.errors), 'range_offset_mm':self.range_offset_mm,
                    'job': self._public_job(self.job), 'jobs': deepcopy(list(self.jobs)), 'plan': deepcopy(self.plan),
                    'settings': {**self.settings, 'key_configured': bool(self._key) or self.demo},
                    'telemetry': deepcopy(self.telemetry), 'event_cursor': self.event_id,
                    'limits': {'forward_cm': [5, 30], 'turn_deg': [15, 90], 'max_motions': 9, 'max_seconds': 180}}
        data['device'] = backend.snapshot() if backend else {'connected': False, 'ready': False, 'stopped': None,
            'fault': None, 'firmware': None, 'front_guard': 'OFF', 'pose_available': False, 'range_available': False,
            'last_action': None, 'last_result': None, 'action_count': 0}
        if data['holding']: data['holding']={k:v for k,v in data['holding'].items() if not k.startswith('_')}
        data['port'] = backend.port_name if backend else None
        for name in ('bmi', 'yaw', 'range', 'attitude'):
            sample = data['telemetry'][name]
            if sample:
                received_age = max(0, (time.monotonic()-sample.pop('_at'))*1000)
                if name == 'range':
                    sensor_age = sample.get('age_ms', -1)
                    sample['raw_mm']=sample.get('distance_mm')
                    sample['offset_mm']=self.range_offset_mm
                    if sample.get('distance_mm') is not None: sample['distance_mm']+=self.range_offset_mm
                    sample['near_field']=sample.get('raw_mm',1000)<40
                    sample['sensor_age_ms'] = sensor_age
                    sample['received_age_ms'] = received_age
                    sample['age_ms'] = received_age + max(0, sensor_age)
                    sample['fresh'] = (received_age < 1500 and 0 <= sensor_age <= 200 and
                        sample.get('ready') == 1 and sample.get('valid') == 1 and
                        sample.get('error') == 0 and sample.get('status') == 0 and sample.get('distance_mm', 0) > 0)
                else:
                    sample['age_ms'] = received_age
                    sample['fresh'] = received_age < 1500 and sample.get('fresh', 1) == 1
        for wheel in data['telemetry']['wheels'].values():
            if '_at' in wheel:
                wheel['age_ms'] = max(0, (time.monotonic()-wheel.pop('_at'))*1000)
                wheel['fresh'] = wheel['age_ms'] < 1500 and wheel.get('valid') == 1
        return self.redact(data)

    def event_batch(self, cursor):
        with self.lock:
            return [deepcopy(event) for event in self.events if event['id'] > cursor]

    def _receive(self, entry):
        if entry['kind'] == 'rx':
            line = re.sub(r'\x1b\[[0-9;]*m', '', entry.get('line', ''))
            self.emit('serial', 'error' if 'INVALID' in line or 'BLOCKED' in line else 'info', line)
            fields = {}
            for key, value in re.findall(r'([A-Za-z_][A-Za-z_0-9]*)=([+-]?\d+(?:\.\d+)?)', line):
                number = float(value)
                if math.isfinite(number):
                    fields[key] = number
            with self.lock:
                if 'KEYBOARD_DRIVE' in line and self.boot_id is not None:
                    self.tasks.clear();self.plan=None;self.epoch+=1;self.session_id=uuid.uuid4().hex;self.boot_id=None
                    self.emit('system','warning','开发板重启：模型上下文已清空')
                if 'BMI ready=' in line:
                    self.telemetry['bmi'] = {**fields, '_at': time.monotonic()}
                if 'BODY_YAW' in line:
                    self.telemetry['yaw'] = {**fields, '_at': time.monotonic()}
                    if fields.get('valid') == 1 and 'angle_deg' in fields:
                        self.telemetry['samples'] = (self.telemetry['samples']+[{'at': time.time(), 'yaw': fields['angle_deg']}])[-80:]
                if 'ATTITUDE roll=' in line:
                    previous=self.telemetry.get('attitude')
                    if previous and fields.get('zero_epoch')!=previous.get('zero_epoch'):
                        self.telemetry['samples']=[]  # Never join two different angle references.
                    self.telemetry['attitude'] = {**fields, '_at': time.monotonic()}
                    if 'yaw' in fields:
                        self.telemetry['yaw'] = {'angle_deg': fields['yaw'], 'valid': fields.get('yaw_valid', 0), '_at': time.monotonic()}
                    if fields.get('yaw_valid') == 1 and 'yaw' in fields:
                        self.telemetry['samples'] = (self.telemetry['samples']+[{'at': time.time(), 'yaw': fields['yaw']}])[-80:]
                if 'WHEEL motor=' in line:
                    motor = re.search(r'motor=([AB])', line)
                    if motor:
                        self.telemetry['wheels'][motor[1]] = {**fields, '_at': time.monotonic()}
                if 'TOF DISABLED' in line:
                    self.telemetry['tof_disabled'] = True
                    self.telemetry['range'] = None
                if 'TOF ready=' in line:
                    self.telemetry['tof_disabled'] = False
                    self.telemetry['range'] = {**fields, '_at': time.monotonic()}
                if 'VEHICLE_READY:' in line and 'tof=' in line:
                    self.telemetry['tof_disabled'] = 'tof=ON' not in line
                if 'VEHICLE_RESULT' in line:
                    motor = re.search(r'motor=([AB])', line)
                    if motor:
                        self.telemetry['wheels'].setdefault(motor[1], fields)
        elif entry['kind'] == 'tx':
            self.emit('serial', 'info', 'TX '+repr(entry.get('command', '')))
        elif entry['kind'] in ('fault', 'emergency_stop'):
            self.emit('serial', 'error', str(entry))

    def poll(self):
        with self.lock:
            backend, previous, job = self.backend, self.serial_last, self.job
        if backend:
            if hasattr(backend, 'sample'):
                backend.sample()
            result = backend.snapshot().get('last_result')
            if isinstance(result, dict) and 'target_mm' in result:
                with self.lock:
                    if backend is self.backend:
                        self.telemetry['last_move'] = deepcopy(result)
            buffer = backend.logs
            index = next((i for i, entry in enumerate(buffer) if entry is previous), -1)
            entries = buffer[index+1:]
            if entries:
                for entry in entries:
                    self._receive(entry)
                with self.lock:
                    if backend is self.backend:
                        self.serial_cursor = entries[-1]['time_monotonic']
                        self.serial_last = entries[-1]
        if self.holding and time.monotonic()-self.holding['_pulse_at']>.2:
            self.teleop_release(self.owner,reason='控制心跳过期')
        if job and job['status'] == 'running' and not job['cancel'].is_set():
            now = time.monotonic()
            if now-job['started'] >= 180 or now-self.last_heartbeat > self.lease_seconds:
                self.stop('任务超时' if now-job['started'] >= 180 else '控制页面失联')

    def _monitor(self):
        while not self.finished.wait(.05):
            try:
                self.poll()
            except Exception as exc:
                self.emit('system', 'error', self.scrub(exc))

    def save(self):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            record = {'snapshot': self.snapshot(), 'events': self.event_batch(0), 'tasks': list(self.tasks), 'task_success': None}
            text = json.dumps(self.redact(record), ensure_ascii=False, indent=2)
            (self.log_dir/'latest-session.json').write_text(text, encoding='utf-8')
        except OSError as exc:
            self.emit('system', 'error', '日志写入失败：'+str(exc))

    def close(self):
        self.stop('控制台退出')
        self.finished.set()
        if self.thread:
            self.thread.join(.5)
        if self.backend:
            self.backend.close()
        self.save()
