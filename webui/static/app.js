'use strict';

const $ = (id) => document.getElementById(id);
const client = sessionStorage.getItem('rover-client') || crypto.randomUUID();
sessionStorage.setItem('rover-client', client);
let token = '', state = null, streamHealthy = false, receivedAt = performance.now();
let distance = 0.10, angle = 30, currentView = 'dashboard', paused = false;
let events = [], eventCursor = 0, clearCursor = 0, toastTimer, stopOnLost = false;
let historyPage = 0;
let manualMode = 'four', fourAxisHold = true;
let rangeOffsetDirty = false;
let portCount = 0, portsError = '';
const terminalHistory = []; let historyIndex = 0;

// Reassigning textContent, even to the same string, destroys browser selections.
function setText(node, value) {
  const text = String(value ?? '');
  if (node.textContent === text) return;
  if (node.childNodes.length === 1 && node.firstChild.nodeType === 3) node.firstChild.data = text;
  else node.textContent = text;
}
function hasSelectedText(node) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) return false;
  for (let i = 0; i < selection.rangeCount; i++) {
    if (selection.getRangeAt(i).intersectsNode(node)) return true;
  }
  return false;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) setText(node, text);
  return node;
}
function toast(message) {
  setText($('toast'), message);
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}
async function request(path, payload, keepalive = false) {
  const options = payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Rover-Token': token},
    body: JSON.stringify({...payload, client}), keepalive,
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
async function act(path, payload) {
  try { return await request(path, payload); }
  catch (error) { toast(error.message); return null; }
}
const running = () => state?.job?.status === 'running';
const owns = () => !state?.owner || state.owner === client || (!running() && state.lease_age_ms > 4000);
const driveReady = () => streamHealthy && owns() && state?.device?.connected && state.device.ready && state.device.stopped === true && !state.device.fault && !running() && !state.holding;
const manualReady = () => streamHealthy && owns() && state?.device?.connected && !state.device.fault && !running() &&
  (controlIdle!==null && performance.now()-ackAt<180 ? controlIdle : driveReady());
function directionAvailable(direction) {
  const hold = $('hold-mode').checked;
  if(hold && state.device.pwm_manual_available && manualReady()) return true;
  const action = hold ? state.device.pwm_manual_available ? 'manual_pwm' :
    direction === 'forward' ? 'manual_hold_forward' : 'manual_hold_other' :
    direction === 'backward' ? 'manual_reverse_timed' : direction === 'forward' ? 'move' : 'turn';
  return state.action_availability?.[action]?.available !== false;
}
const fmt = (value, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const seconds = (value) => value === undefined || value === null ? '—' : `${(value / 1000).toFixed(2)} s`;
function badge(node, text, color = 'neutral') { setText(node, text); node.className = `badge ${color}`; }
function dot(node, color) { node.className = `status-dot ${color || ''}`; }

function render() {
  if (!state) return;
  const device = state.device, job = state.job;
  const active = running(), ready = driveReady(), demo = state.mode === 'demo';
  if (streamHealthy && device.connected && !device.fault && teleopSocket?.readyState === WebSocket.CLOSED) openTeleop();
  const idle = device.connected && device.stopped === true;
  badge($('mode-badge'), demo ? '离线演示' : '实车模式', demo ? 'demo' : 'neutral');
  $('demo-notice').hidden = !demo;
  setText($('page-subtitle'), demo ? '在演示设备上体验控制、计划执行与回传。' : '连接小车，在这里查看状态、控制运动与执行任务。');
  let status = !streamHealthy ? '回传连接中断' : device.fault ? '需要重新连接' : active ? job.phase : device.connected ? (idle ? '已停稳，待命' : '等待停稳反馈') : '未连接设备';
  setText($('connection-state'), status);
  setText($('sidebar-status'), device.connected ? (device.fault ? '会话已中止' : demo ? '演示已连接' : '已连接') : '等待连接');
  setText($('connection-detail'), state.port ? `${state.port} / 115200` : 'UART0 / 115200');
  dot($('connection-dot'), device.fault || !streamHealthy ? 'error' : device.connected ? 'connected' : '');
  dot($('sidebar-dot'), device.fault ? 'error' : device.connected ? 'connected' : '');
  setText($('connect-button').querySelector('span'), device.fault ? '重新连接' : device.connected ? '断开设备' : '连接设备');
  $('connect-button').disabled = active || !streamHealthy || (!device.connected && !$('port-select').value);
  setText($('device-connect-title'), demo ? '演示设备（不连接小车）' : '连接小车');
  setText($('port-hint'), demo ? '此页只提供 DEMO。连接 COM5 请打开实车控制台。' : device.connected ? `已连接 ${state.port}；切换串口前先断开设备。` : portsError ? `扫描失败：${portsError}。点击刷新重试。` : portCount ? `发现 ${portCount} 个串口，请选择开发板对应的 USB 串口。` : '未发现串口：请接好开发板 USB 数据线，再点击刷新。');
  $('port-select').disabled = device.connected || active;
  $('refresh-ports').disabled = active || device.connected;
  const pwmRevision = device.firmware_build?.match(/^pwm-stream-v(\d+)$/)?.[1];
  badge($('firmware-badge'), device.firmware ? `固件 v${device.firmware} / ${device.firmware === '1.9' ? 'PARAM2' : 'PARAM1'}${pwmRevision ? ' · PWM v'+pwmRevision : ''}` : '等待固件回传', device.firmware ? 'good' : 'neutral');
  const pwmZero = device.pwm_manual_available && $('hold-mode').checked && Number($('manual-pwm').value) === 0;
  refreshManualInput();
  const availability = state.action_availability || {};
  const reasons = {heading_unavailable:'等待有效航向', range_unavailable:'等待有效测距', unsupported_firmware:'固件不支持', disconnected:'未连接', not_idle:'等待停稳', session_fault:'会话中止'};
  const statusOf = name => availability[name]?.available ? '可用' : reasons[availability[name]?.reason] || '等待设备恢复';
  setText($('action-preflight'), `闭环移动/转弯：${statusOf('move')} · 持续 PWM：${statusOf('manual_pwm')} · 测距接近：${statusOf('approach')}`);
  const timing = device.teleop_timing;
  setText($('firmware-ack-timing'), timing ? `本次按住→固件确认 ${fmt(timing.first_firmware_ack_ms, 0)} ms · 最近确认 ${fmt(timing.firmware_ack_age_ms, 0)} ms 前` : '按住→固件确认：等待本次遥控 · 不代表车轮开始运动');
  $('manual-pwm').disabled = !device.pwm_manual_available || !$('hold-mode').checked || active || !owns() || !streamHealthy || !!device.fault;
  setText($('manual-pwm-hint'), !device.connected ? '连接后检查固件是否支持 PWM' : !device.pwm_manual_available ? '需要烧录 PWM 新固件；当前持续模式沿用原速度' : !$('hold-mode').checked ? '仅用于持续按住模式；步进与自然语言速度不变' : '实际占空比 · 0 停止 · 手动不做航向保持');
  const applied = device.manual_pwm_actual;
  setText($('manual-pwm-actual'), applied ? `右轮 ${fmt(applied.A, 0)}% · 左轮 ${fmt(applied.B, 0)}%（固件回传）` : '右轮 — · 左轮 —（等待输出回传）');
  $('keyboard-enabled').disabled = !device.connected || !owns() || !!device.fault || !streamHealthy;
  $('generate-button').disabled = !!state.holding || active || !owns() || !streamHealthy;
  $('build-nav').disabled = active || device.connected;
  document.querySelector('[data-tool="build"]').disabled = active || device.connected;
  if (device.fault) {
    $('notice').hidden = false;
    setText($('notice'), `${state.diagnosis?.message || '当前会话已中止'}。${state.diagnosis?.recovery || '确认静止后重新连接。'} 原始故障：${device.fault}`);
  } else if (!streamHealthy) {
    $('notice').hidden = false; setText($('notice'), '回传连接已中断，运动控制已禁用。若服务已重启，请刷新页面。');
  } else if (device.control_stop?.code === 'LOW_PWM_NO_ENCODER_EDGES') {
    $('notice').hidden = false;
    setText($('notice'), '当前 PWM 下未检测到编码器运动，两轮已停止。可能无法起转，也可能是供电或编码器问题；检查后调整 PWM，再重新按下。不会自动提高 PWM，无需因本次低 PWM 停止而复位。');
  } else if (device.control_stop?.code === 'MANUAL_ENCODER_VARIATION') {
    $('notice').hidden = false;
    setText($('notice'), '手动控制检测到小幅编码器计数偏差，两轮已停止，固件未锁定。可能有回弹或信号干扰；检查轮子和接线，等停稳后松开并重新按下。不会自动继续；反复出现时应排查硬件。');
  } else if (job?.error) {
    $('notice').hidden = false; setText($('notice'), job.error);
  } else if (!owns()) {
    $('notice').hidden = false; setText($('notice'), '另一页面正在控制设备。本页仍可查看回传和发送停止。');
  } else $('notice').hidden = true;

  const telemetry = state.telemetry, yaw = telemetry.yaw, bmi = telemetry.bmi;
  renderRange(telemetry, device, demo);
  renderMotionTelemetry(telemetry, device);
  const age = yaw ? yaw.age_ms + performance.now() - receivedAt : null;
  const yawValid = yaw?.valid === 1;
  const fresh = age !== null && age < 1500 && yawValid && streamHealthy;
  setText($('yaw-value'), yawValid ? fmt(yaw.angle_deg) : '—');
  setText($('yaw-fresh'), fresh ? '最近回传' : yaw && !yawValid ? '航向失效 · 请静止归零' : yaw ? '回传过期' : '等待回传');
  $('yaw-fresh').className = `fresh-label ${fresh ? 'fresh' : ''}`;
  setText($('sample-age'), age === null ? '等待数据' : `${Math.floor(age / 1000)}s 前更新`);
  setText($('roll-value'), bmi?.tilt_valid === 1 ? `${fmt(bmi.roll)}°` : '—');
  setText($('pitch-value'), bmi?.tilt_valid === 1 ? `${fmt(bmi.pitch)}°` : '—');
  const bmiOk = bmi?.calibrated === 1 && bmi?.error === 0 && bmi.fresh;
  setText($('imu-status'), !bmi ? '等待回传' : bmi.calibrated !== 1 ? '校准中' : bmiOk ? '已校准' : '检查日志');
  $('imu-dot').style.background = bmiOk ? '#249b9a' : '#b5becb';
  const result = device.last_result;
  const move = result?.target_mm !== undefined ? result : telemetry.last_move;
  function measurement(id, value, unit, digits) {
    const node = $(id), text = fmt(value, digits);
    if (node.childNodes.length !== 2 || node.firstChild.nodeType !== 3 || node.lastChild.tagName !== 'SMALL') {
      node.replaceChildren(document.createTextNode(text), element('small', '', unit));
    } else {
      if (node.firstChild.data !== text) node.firstChild.data = text;
      setText(node.lastChild, unit);
    }
  }
  measurement('right-distance', move?.after_stop_right_mm, 'mm', 1);
  measurement('left-distance', move?.after_stop_left_mm, 'mm', 1);
  measurement('action-duration', result?.elapsed_ms !== undefined ? result.elapsed_ms / 1000 : undefined, 's', 2);
  renderChart(telemetry.samples);

  setText($('model-name'), demo ? '离线演示策略' : state.settings.model);
  const source = job?.diagnostics?.policy_source || state.plan?.diagnostics?.policy_source;
  badge($('policy-source'), source === 'verified_reuse' ? '已验证策略复用' : source === 'model' ? '模型生成' : '等待计划', source === 'verified_reuse' ? 'good' : 'neutral');
  const library=state.policy_library || {};
  setText($('reuse-summary'), `${state.settings.policy_reuse === false ? '复用已关闭' : '已验证策略库'} · ${library.verified_recipes || 0} 条 · 本会话命中 ${library.hits || 0} 次`);
  $('policy-library-clear').disabled=active || !!state.holding || !owns();
  const plan = state.plan;
  $('plan-container').hidden = !plan;
  if (plan) {
    setText($('policy-code'), plan.policy);
    setText($('plan-summary'), `${plan.segments.length} 段 / ${plan.policy.split('\n').length - 1} 个运动`);
    setText($('execute-button'), `执行 ${plan.segments.length} 段计划`);
    $('execute-button').disabled = !ready;
  }
  const elapsed = job ? job.elapsed_ms + (active ? performance.now() - receivedAt : 0) : 0;
  setText($('job-title'), job?.title || '等待你的下一步');
  setText($('job-phase'), job?.phase || '连接小车或先生成一个计划。');
  badge($('job-status'), active ? '执行中' : job?.status === 'failed' ? '失败' : job?.status === 'cancelled' ? '已中止' : job?.status === 'completed' ? '已完成' : '待命', active ? 'running' : job?.status === 'completed' ? 'good' : job?.error ? 'bad' : 'neutral');
  setText($('job-elapsed'), (elapsed / 1000).toFixed(1));
  $('job-progress-bar').style.width = `${Math.min(100, elapsed / 1800)}%`;
  $('job-progress-bar').parentElement.title = '任务时间预算，最长180秒';
  const timings = job?.timings_ms || {};
  setText($('model-time'), seconds(timings.model_request));
  setText($('execution-time'), seconds(timings.policy_execution));
  setText($('review-time'), seconds(timings.feedback_review));
  setText($('key-state'), demo ? '演示模式不使用API密钥' : state.settings.key_configured ? '密钥已配置，保存在当前进程中' : '尚未配置密钥');
  setText($('footer-state'), active ? `${job.phase}，可随时停止` : !streamHealthy ? '等待本地服务回传' : device.connected ? '设备回传已接入' : '本地服务在线，设备未连接');
  setText($('server-state'), streamHealthy ? '在线' : '连接中断');
  setText($('context-info'), '当前开机上下文 · ' + (state.context_tasks || 0) + ' 条执行任务');
  $('yaw-zero').disabled = !ready || !bmi?.fresh || bmi.calibrated !== 1 || bmi.error !== 0;
  $('range-calibrate').disabled = !idle || active || !owns() || !streamHealthy || !!device.fault;
  $('range-offset-save').disabled = active || !!state.holding || !owns() || (device.connected && !idle);
  if(!rangeOffsetDirty && document.activeElement !== $('range-offset-mm')) $('range-offset-mm').value = state.range_offset_mm || 0;
  setText($('calibration-state'), '校正 ' + (state.range_offset_mm || 0) + ' mm · 原始值保留');
  $('hold-mode').disabled = manualMode === 'joystick' || active || !!state.holding;
  for (const button of document.querySelectorAll('[data-control-mode]'))
    button.disabled = active && job.kind !== 'manual';
  setText($('reverse-label'), $('hold-mode').checked ? '按住' : '0.8秒');
  badge($('teleop-status'), state.holding ? '持续控制中' : $('hold-mode').checked ? '按住行走 · 松开停止' : '按一次走一步', state.holding ? 'running' : 'neutral');
  const error = (state.errors || []).at(-1);
  $('error-panel').hidden = !error;
  if (error) {
    setText($('error-category'), {model_call:'模型调用',validation:'代码 / API 校验',execution:'执行反馈',service:'服务'}[error.category] || error.category);
    setText($('error-message'), error.type + '：' + error.message);
    setText($('error-source'), error.policy || error.raw_response || '此环节没有返回模型代码。');
  }
  renderHistory();
}

function renderRange(telemetry, device, demo) {
  const sample = telemetry.range;
  const sinceReceive = performance.now() - receivedAt;
  const age = sample ? sample.age_ms + sinceReceive : null;
  const receiveAge = sample ? sample.received_age_ms + sinceReceive : null;
  const disabled = telemetry.tof_disabled === true;
  const valid = device.connected && streamHealthy && !disabled && sample?.fresh === true && age < 1500;
  let status = '等待回传', color = 'neutral';
  if (!device.connected) status = '未连接';
  else if (disabled) status = '采集关闭';
  else if (sample?.ready === 0) { status = '初始化失败'; color = 'bad'; }
  else if (sample?.error !== undefined && sample.error !== 0) { status = '总线异常'; color = 'bad'; }
  else if (!streamHealthy || (receiveAge !== null && receiveAge >= 1500)) { status = '回传过期'; color = 'bad'; }
  else if (sample && !valid) { status = '读数无效'; color = 'bad'; }
  else if (valid) { status = '正常'; color = 'good'; }
  badge($('range-status'), status, color);
  setText($('tof-status'), status);
  $('tof-dot').style.background = valid ? '#249b9a' : '#b5becb';
  setText($('range-value'), valid ? fmt(sample.distance_mm / 10) : '—');
  setText($('range-mm'), valid ? `${fmt(sample.distance_mm, 0)} mm${demo ? ' · 演示数据' : ''} · 原始 ${fmt(sample.raw_mm,0)} mm` : sample && !disabled ? `原始读数 ${fmt(sample.raw_mm, 0)} mm（无效，不采用）` : '当前没有有效距离');
  setText($('range-age'), age === null ? '—' : `${fmt(age / 1000, 1)} s 前`);
  setText($('range-optical'), sample ? `status ${sample.status ?? '—'}` : '—');
  setText($('range-bus'), sample ? sample.error === 0 ? '正常' : `error ${sample.error}` : '—');
  setText($('range-note'), disabled ? '板上固件关闭了采样，需更新测距固件。演示页面不连接硬件。' : status === '初始化失败' || status === '总线异常' ? '检查 VL53L1X 电源、GND、SDA6 / SCL7；修复后重启开发板。' : status === '读数无效' ? '未获得有效反射，调整镜头朝向或目标距离；不会将无效读数显示为距离。' : sample?.near_field ? '目标小于约4cm，已进入近场；贴住读数不能用于偏移标定。请用140mm平整目标标定。' : '以镜头平面为测量基准；普通前进保护 OFF，approach 动作单独按测距闭环停车。');
}

function renderMotionTelemetry(telemetry, device) {
  const addedAge = performance.now() - receivedAt;
  const wheels = telemetry.wheels || {}, speeds = [];
  for (const [motor, side] of [['B', 'left'], ['A', 'right']]) {
    const wheel = wheels[motor];
    const valid = device.connected && streamHealthy && wheel?.fresh && wheel.age_ms + addedAge < 1500;
    setText($(`${side}-wheel-speed`), valid ? fmt(wheel.speed_mm_s / 10) : '—');
    setText($(`${side}-wheel-rpm`), valid ? fmt(wheel.rpm) : '—');
    setText($(`${side}-wheel-cps`), valid ? fmt(wheel.cps, 0) : '—');
    setText($(`${side}-wheel-direction`), valid ? Math.abs(wheel.speed_mm_s) < .5 ? '静止' : wheel.speed_mm_s > 0 ? '前向' : '后向' : '等待数据');
    if (valid) speeds.push(wheel.speed_mm_s);
  }
  setText($('vehicle-speed'), speeds.length === 2 ? fmt((speeds[0] + speeds[1]) / 20) : '—');
  badge($('wheel-status'), speeds.length === 2 ? '实时反馈' : '等待有效回传', speeds.length === 2 ? 'good' : 'neutral');
  const attitude = telemetry.attitude;
  const attitudeFresh = device.connected && streamHealthy && attitude?.fresh && attitude.age_ms + addedAge < 1500 && attitude.calibrated === 1;
  const tiltValid = attitudeFresh && attitude.tilt_valid === 1 && Number.isFinite(attitude.roll) && Number.isFinite(attitude.pitch);
  const yawValid = attitudeFresh && attitude.yaw_valid === 1 && Number.isFinite(attitude.yaw);
  badge($('attitude-status'), tiltValid ? yawValid ? '实时姿态' : '倾斜实时 · 航向失效' : attitude ? '冻结 · 数据无效或过期' : '等待回传', tiltValid && yawValid ? 'good' : 'neutral');
  for (const name of ['roll', 'pitch', 'yaw']) setText($('attitude-' + name), (name === 'yaw' ? yawValid : tiltValid) ? `${fmt(attitude[name])}°` : '—');

  window.RoverVisuals?.update({attitude, wheels, receivedAt, enabled: device.connected && streamHealthy});
}
document.addEventListener('rover-visual-ready', () => { if (state) render(); });

function renderChart(samples) {
  const points = (samples || []).filter(p => Number.isFinite(p.yaw) && Number.isFinite(p.at));
  $('chart-empty').hidden = points.length >= 2;
  if (points.length < 2) { $('chart-line').setAttribute('d', ''); $('chart-area').setAttribute('d', ''); return; }
  let low = Math.min(...points.map(p => p.yaw)), high = Math.max(...points.map(p => p.yaw));
  const padding = Math.max(1, (high - low) * .2); low -= padding; high += padding;
  const begin = points[0].at, period = Math.max(.1, points.at(-1).at - begin);
  const positions = points.map(p => [30 + (p.at - begin) / period * 660, 86 - (p.yaw - low) / (high - low) * 64]);
  const line = positions.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  $('chart-line').setAttribute('d', line);
  $('chart-area').setAttribute('d', `${line} L690,88 L30,88 Z`);
  setText($('chart-top'), high.toFixed(0)); setText($('chart-bottom'), low.toFixed(0));
}

let historySignature = '';
function renderHistory() {
  let jobs = (state?.jobs || []).filter(j => ['manual', 'execute', 'plan'].includes(j.kind));
  setText($('history-count'), jobs.length);
  const filter = $('history-filter').value, search = $('history-search').value.trim().toLowerCase();
  jobs = jobs.filter(j => (filter === 'all' || filter === 'plan' && j.kind === 'plan' || filter === 'completed' && j.status === 'completed' || filter === 'failed' && ['failed','cancelled'].includes(j.status)) && (j.title+' '+j.policy+' '+j.error).toLowerCase().includes(search)).reverse();
  const pages = Math.max(1,Math.ceil(jobs.length/6));historyPage = Math.min(historyPage,pages-1);
  setText($('history-page'), (historyPage+1)+' / '+pages);
  $('history-prev').disabled = historyPage===0; $('history-next').disabled = historyPage>=pages-1;
  const visible=jobs.slice(historyPage*6,historyPage*6+6),signature=JSON.stringify([historyPage,filter,search,visible]);
  if (signature === historySignature) return; historySignature = signature;
  const list = $('history-list'); list.replaceChildren();
  if (!visible.length) { list.append(element('div', 'history-empty', '没有匹配任务。生成的计划和执行结果会显示在这里。')); return; }
  for (const job of visible) {
    const card=element('article','history-card'),head=element('div','history-card-head'),status=element('span','badge');
    badge(status,job.status==='completed' ? job.kind==='plan' ? '已校验 · 未执行' : '已执行' : job.status==='cancelled' ? '中止' : '失败',job.status==='completed'?'good':'bad');
    if(job.diagnostics?.policy_source==='verified_reuse')head.append(element('span','badge good','策略复用'));
    head.append(status,element('time','muted',new Date(job.started_at).toLocaleTimeString('zh-CN',{hour12:false})));
    card.append(head,element('h3','',job.title));
    const steps=element('div','history-steps');
    for (const line of (job.policy||'').split('\n').filter(Boolean)) {
      const match=line.match(/robot\.(\w+)\(([^)]*)\)/);if (!match) continue;
      const v=Number(match[2]),name=match[1];
      const label=name==='move'?'↑ '+(v*100).toFixed(0)+'cm':name==='reverse'?'↓ '+(v*100).toFixed(0)+'cm':name==='turn'?(v>0?'↶ ':'↷ ')+Math.abs(v)+'°':name==='approach'?'距前方 '+(v*100)+'cm':name==='stop'?'■ 停止':name;
      steps.append(element('span','action-chip',label));
    }
    card.append(steps);
    if(job.error) card.append(element('p','history-error',job.error));
    const details=element('details','history-code'),summary=element('summary','','查看生成代码与耗时');
    details.append(summary,element('pre','',job.policy||'没有生成可校验代码。'),element('small','muted','模型 '+seconds(job.timings_ms?.model_request)+' · 执行 '+seconds(job.timings_ms?.policy_execution)+' · 总计 '+seconds(job.elapsed_ms)));
    card.append(details);list.append(card);
  }
}

function addEvents(batch) {
  let changed=false;
  for (const item of batch) if (item.id > eventCursor) { events.push(item); eventCursor = item.id; changed=true; }
  events = events.slice(-1600); if(changed) renderLogs();
}
let logSignature='', renderedLogId=0;
function renderLogs() {
  if (paused) return;
  const filter = $('log-filter').value, search = $('log-search').value.toLowerCase();
  const output = $('console-output');
  const signature=JSON.stringify([filter,search,clearCursor]);
  if(signature!==logSignature) { output.replaceChildren();renderedLogId=clearCursor;logSignature=signature; }
  const selecting = hasSelectedText(output);
  const nearBottom = !selecting && output.scrollHeight - output.scrollTop - output.clientHeight < 50;
  const shown = events.filter(e => e.id > renderedLogId && (filter === 'all' || e.channel === filter) && e.text.toLowerCase().includes(search)).slice(-350);
  const fragment = document.createDocumentFragment();
  for (const item of shown) {
    const row = element('div', `log-line ${item.level}`);
    row.append(element('span', 'log-time', new Date(item.at).toLocaleTimeString('zh-CN', {hour12: false})),
      element('span', 'log-channel', {serial: '串口', task: '任务', policy: '策略', feedback: '反馈', terminal: '终端', build: '构建', system: '系统'}[item.channel] || item.channel),
      element('span', 'log-text', item.text)); fragment.append(row);
  }
  output.append(fragment); renderedLogId=eventCursor;
  for (const row of [...output.children]) {
    if (output.childElementCount <= 350) break;
    if (!hasSelectedText(row)) row.remove();
  }
  if (nearBottom) output.scrollTop = output.scrollHeight;
}

async function refreshPorts() {
  try {
    const result = await request('/api/ports'), select = $('port-select'), previous = select.value;
    select.replaceChildren();
    portCount = result.ports.length; portsError = '';
    for (const port of result.ports) { const option = element('option', '', `${port.device} — ${port.description}`); option.value = port.device; option.title = port.description; select.append(option); }
    if (!result.ports.length) { const option = element('option', '', '未发现串口'); option.value = ''; select.append(option); }
    if ([...select.options].some(o => o.value === previous)) select.value = previous;
    render();
  } catch (error) { portsError = error.message; render(); toast(error.message); }
}
$('port-select').addEventListener('change', render);
function openSettings() {
  if (state) { $('setting-reuse').checked=state.settings.policy_reuse !== false; $('setting-reasoning').value=state.settings.reasoning_effort || 'none'; $('setting-review').value=state.settings.review_mode || 'local'; $('setting-model').value = state.settings.model; $('setting-url').value = state.settings.base_url; }
  $('setting-key').value = ''; $('settings-dialog').showModal();
}
async function openDocument(name = 'webui') {
  try { const doc = await request(`/api/document?name=${encodeURIComponent(name)}`); setText($('document-title'), doc.name); setText($('document-content'), doc.text); if (!$('document-dialog').open) $('document-dialog').showModal(); }
  catch (error) { toast(error.message); }
}
async function downloadLogs() {
  try {
    const data = await request('/api/logs'), file = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const link = element('a'); link.href = URL.createObjectURL(file); link.download = `robot-cap-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) { toast(error.message); }
}
async function stop(keepalive = false) {
  releaseHold();
  try { await request('/api/stop', {}, keepalive); if (!keepalive) toast('已请求停止；运动会话中止后需要重新连接。'); }
  catch (error) { if (!keepalive) toast(error.message); }
}
async function generate() {
  if (!state?.settings.key_configured) { openSettings(); toast('先配置模型服务，再生成计划。'); return; }
  const instruction = $('instruction').value.trim();
  if (!instruction) { $('instruction').focus(); toast('先输入一个任务，或点选示例。'); return; }
  await act('/api/generate', {instruction, auto_execute:$('auto-execute').checked,force_generate:$('force-generate').checked});
}

$('refresh-ports').addEventListener('click', refreshPorts);
$('connect-button').addEventListener('click', () => { releaseHold(); return state?.device.connected && !state.device.fault ? act('/api/disconnect', {}) : act('/api/connect', {port: $('port-select').value}); });
$('stop-top').addEventListener('click', () => stop()); $('stop-pad').addEventListener('click', () => stop());
$('joystick-stop').addEventListener('click', () => stop());
$('move-forward').addEventListener('click', () => { if (!$('hold-mode').checked) act('/api/manual', {action: 'move', value: distance}); });
$('move-backward').addEventListener('click', () => { if (!$('hold-mode').checked) act('/api/manual', {action: 'reverse', value: .8}); });
$('turn-left').addEventListener('click', () => { if (!$('hold-mode').checked) act('/api/manual', {action: 'turn', value: angle}); });
$('turn-right').addEventListener('click', () => { if (!$('hold-mode').checked) act('/api/manual', {action: 'turn', value: -angle}); });
for (const [id, setter] of [['distance-options', v => { distance = v; }], ['angle-options', v => { angle = v; }]]) {
  $(id).addEventListener('click', e => { const button = e.target.closest('button[data-value]'); if (!button) return; $(id).querySelectorAll('button').forEach(b => b.classList.toggle('selected', b === button)); setter(Number(button.dataset.value)); });
}
$('generate-button').addEventListener('click', generate);
$('execute-button').addEventListener('click', () => { if (state?.plan && driveReady()) act('/api/execute', {plan_id: state.plan.id}); });
document.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => { $('instruction').value = button.dataset.example; $('instruction').focus(); }));
$('instruction').addEventListener('keydown', e => { if (e.ctrlKey && e.key === 'Enter' && !running()) { e.preventDefault(); generate(); } });
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
  currentView = button.dataset.view;
  document.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('active', b === button));
  $('dashboard-view').hidden = currentView !== 'dashboard'; $('history-view').hidden = currentView !== 'history';
  setText($('view-name'), currentView === 'dashboard' ? '控制工作台' : '任务记录');
}));
$('nav-terminal').addEventListener('click', () => { $('console-panel').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}); $('terminal-command').focus(); });
$('settings-open').addEventListener('click', openSettings);
$('help-open').addEventListener('click', () => openDocument());
$('build-nav').addEventListener('click', () => act('/api/build', {}));
document.querySelectorAll('[data-tool]').forEach(button => button.addEventListener('click', () => {
  const tool = button.dataset.tool;
  if (tool === 'settings') openSettings();
  else if (tool === 'help') openDocument();
  else if (tool === 'build') act('/api/build', {});
  else if (tool === 'terminal') { $('console-panel').scrollIntoView(); $('terminal-command').focus(); }
}));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
document.querySelectorAll('[data-document]').forEach(button => button.addEventListener('click', () => openDocument(button.dataset.document)));
$('settings-form').addEventListener('submit', async e => {
  e.preventDefault(); const payload = {model: $('setting-model').value, base_url: $('setting-url').value, reasoning_effort:$('setting-reasoning').value, review_mode:$('setting-review').value,policy_reuse:$('setting-reuse').checked};
  if ($('setting-key').value.trim()) payload.api_key = $('setting-key').value.trim();
  const result = await act('/api/settings', payload); $('setting-key').value = '';
  if (result) { $('settings-dialog').close(); toast('模型配置已保存，仅本次进程使用。'); }
});
$('shutdown-button').addEventListener('click', async () => { if (confirm('结束本地服务？正在运行的任务会中止。')) { await act('/api/shutdown', {}); $('settings-dialog').close(); } });
$('log-filter').addEventListener('change', renderLogs); $('log-search').addEventListener('input', renderLogs);
$('log-pause').addEventListener('click', () => { paused = !paused; setText($('log-pause'), paused ? '继续' : '暂停'); if (!paused) renderLogs(); });
$('log-clear').addEventListener('click', () => { clearCursor = eventCursor; $('console-output').replaceChildren(); });
$('log-download').addEventListener('click', downloadLogs); $('history-download').addEventListener('click', downloadLogs);
$('terminal-form').addEventListener('submit', async e => { e.preventDefault(); const command = $('terminal-command').value.trim(); if (!command) return; terminalHistory.push(command); historyIndex = terminalHistory.length; $('terminal-command').value = ''; await act('/api/terminal', {command}); });
$('terminal-command').addEventListener('keydown', e => {
  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); historyIndex = Math.max(0, Math.min(terminalHistory.length, historyIndex + (e.key === 'ArrowUp' ? -1 : 1))); e.target.value = terminalHistory[historyIndex] || ''; }
  if (e.ctrlKey && e.key.toLowerCase() === 'c') { e.preventDefault(); stop(); }
});
$('keyboard-enabled').addEventListener('change', () => { if (!$('keyboard-enabled').checked && running() && state.job.kind === 'manual') stop(); });
document.addEventListener('keydown', e => {
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Escape' && (running() || heldButton)) { e.preventDefault(); stop(); return; }
  if (!$('keyboard-enabled').checked || e.target.closest('textarea,select,[contenteditable="true"],input:not([type="checkbox"]):not([type="radio"])') || $('settings-dialog').open || $('document-dialog').open) return;
  const key = e.key.toLowerCase();
  if (key === 't') { e.preventDefault(); stop(); }
  else if (manualMode === 'four' && ['w','a','s','d'].includes(key) && $('hold-mode').checked && driveReady()) { e.preventDefault(); beginHold({w:'forward',s:'backward',a:'left',d:'right'}[key], {w:'move-forward',s:'move-backward',a:'turn-left',d:'turn-right'}[key],key); }
  else if (manualMode === 'four' && key === 's' && driveReady() && directionAvailable('backward')) { e.preventDefault(); act('/api/manual', {action: 'reverse', value: .8}); }
  else if (manualMode === 'four' && driveReady() && ['w', 'a', 'd'].includes(key) && directionAvailable(key === 'w' ? 'forward' : key === 'a' ? 'left' : 'right')) { e.preventDefault(); act('/api/manual', {action: key === 'w' ? 'move' : 'turn', value: key === 'w' ? distance : key === 'a' ? angle : -angle}); }
});
document.addEventListener('visibilitychange', () => { if (document.hidden) { if(running()||heldButton)stop(true);else releaseHold(); } });
window.addEventListener('pagehide', () => { if (running() || heldButton) stop(true); });
window.addEventListener('blur', () => { releaseHold(); if (running() && $('keyboard-enabled').checked && state.job.kind === 'manual') stop(true); });

async function boot() {
  try {
    const data = await request('/api/bootstrap'); token = data.token; state = data.state; streamHealthy = true;
    receivedAt = performance.now(); render(); openTeleop(); await refreshPorts();
    const source = new EventSource('/api/events');
    source.onmessage = event => { const data = JSON.parse(event.data); state = data.state; receivedAt = performance.now(); streamHealthy = true; stopOnLost = false; addEvents(data.events); render(); };
    source.onerror = () => { releaseHold(); streamHealthy = false; if (running() && !stopOnLost) { stopOnLost = true; stop(true); } render(); };
    setInterval(() => { if (!document.hidden) request('/api/heartbeat', {}).catch(() => {}); }, 900);
    setInterval(() => { setText($('clock'), new Date().toLocaleTimeString('zh-CN', {hour12: false})); render(); }, 200);
  } catch (error) { $('notice').hidden = false; setText($('notice'), `本地服务未就绪：${error.message}。请双击启动文件后刷新页面。`); }
}
boot();

// Hold channel: one in-flight heartbeat, no motion queue, expiring server challenge.
let teleopSocket=null, challenge=null, wsSeq=0, inFlight=false, sentAt=0, ackAt=0, controlIdle=null;
let heldButton=null, heldKey=null, heldDirection=null, holdBegan=0, startWanted=false, startedHold=false, heldPwm=false;
let joyPointer=null, joyX=0, joyY=0;
function vectorPayload() { return {x:joyX,y:joyY,limit:Number($('manual-pwm').value)}; }
function currentPayload() { return heldDirection==='joystick' ? vectorPayload() : pwmPayload(); }
function startControl() { return sendControl(heldDirection==='joystick'?'vector_start':'start',heldDirection==='joystick'?vectorPayload():{direction:heldDirection,...pwmPayload()}); }
function refreshManualInput() {
  if(!state)return;
  const ready=$('hold-mode').checked?manualReady():driveReady();
  const zero=state.device.pwm_manual_available && $('hold-mode').checked && Number($('manual-pwm').value)===0;
  for(const [id,direction] of [['move-forward','forward'],['move-backward','backward'],['turn-left','left'],['turn-right','right']])
    $(id).disabled=zero || !((ready&&directionAvailable(direction))||heldButton===id);
  const supported=state.device.vector_manual_available;
  const enabled=manualMode==='joystick'&&supported&&$('hold-mode').checked&&!zero&&(manualReady()||heldDirection==='joystick');
  $('joystick-pad').classList.toggle('disabled',!enabled);
  $('joystick-pad').setAttribute('aria-disabled',String(!enabled));
  setText($('joystick-status'),!supported?'需要 PWM v6':heldDirection==='joystick'?'连续差速控制':enabled?'拖动控制 · 松开停止':'等待停稳');
}
function pwmPayload() { return heldPwm ? {pwm_percent:Number($('manual-pwm').value)} : {}; }
function sendControl(action,extra={}) {
  if (!teleopSocket || teleopSocket.readyState!==WebSocket.OPEN || !challenge) return false;
  if (teleopSocket.bufferedAmount>1024) { teleopSocket.close();return false; }
  if (inFlight && action!=='release') return false;
  inFlight=true; sentAt=performance.now();
  teleopSocket.send(JSON.stringify({action,seq:++wsSeq,challenge,...extra}));
  return true;
}
function openTeleop() {
  if (teleopSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(teleopSocket.readyState)) return;
  // A fresh channel starts idle with a fresh sequence; never replay a held key.
  releaseHold();challenge=null;wsSeq=0;inFlight=false;ackAt=0;controlIdle=null;
  const socket=new WebSocket('ws://'+location.host+'/api/teleop');
  teleopSocket=socket;
  socket.onopen=()=>socket.send(JSON.stringify({token,client}));
  socket.onmessage=e=>{
    if (teleopSocket !== socket) return;
    const msg=JSON.parse(e.data);
    if(msg.error) { toast(msg.error); releaseHold(); teleopSocket.close(); return; }
    if (msg.seq<wsSeq) return;
    const now=performance.now();
    if(inFlight) setText($('control-latency'), `网页往返 ${Math.round(now-sentAt)} ms · 服务处理 ${fmt(msg.server_processing_ms, 1)} ms`);
    challenge=msg.challenge;inFlight=false;ackAt=now;controlIdle=typeof msg.ready==='boolean'?msg.ready&&msg.stopped===true&&!msg.holding:null;
    if (msg.control_stop) {
      startedHold=false;releaseHold();
      if(msg.control_stop.code!=='JOYSTICK_NEUTRAL') toast(msg.control_stop.code === 'LOW_PWM_NO_ENCODER_EDGES' ? '当前 PWM 未检测到运动，已停止。检查供电和编码器后调整 PWM，重新按下。' : msg.control_stop.code === 'MANUAL_ENCODER_VARIATION' ? '小幅编码器偏差，两轮已停止且未锁定。检查后等停稳，再重新按下。' : '本次按住已停止，请松开后重新按下。');
    }
    if(startWanted && heldButton && now-holdBegan<180) {
      startWanted=false;startedHold=startControl();
    }
    refreshManualInput();
  };
  socket.onclose=()=>{ if(teleopSocket !== socket) return; releaseHold(); challenge=null;inFlight=false; setText($('control-latency'), '遥控通道断开 · 设备恢复后重建'); };
  socket.onerror=()=>{ if(teleopSocket === socket) releaseHold(); };
}
function beginHold(direction,id,key=null) {
  if(!manualReady() || heldButton || !directionAvailable(direction)) return;
  if(state.device.pwm_manual_available && Number($('manual-pwm').value)===0) { toast('PWM 为 0，请先调高再按方向。');return; }
  if(state.device.firmware!=='1.9') { toast('持续控制需要烧录v1.9固件；可取消持续模式使用步进。');return; }
  if(!challenge || performance.now()-ackAt>180) { toast('遥控通道尚未就绪，请稍后再按。');return; }
  heldButton=id;heldKey=key;heldDirection=direction;holdBegan=performance.now();heldPwm=!!state.device.pwm_manual_available;
  $(id).classList.add('held'); startWanted=true;
  if(!inFlight) { startWanted=false;controlIdle=false;startedHold=startControl(); }
}
function releaseHold() {
  joyPointer=null;joyX=joyY=0;
  $('joystick-thumb').style.transform='translate(0px,0px)';
  setText($('joystick-x'),'0%');setText($('joystick-y'),'0%');
  startWanted=false;
  if(!heldButton) return;
  $(heldButton).classList.remove('held');
  heldButton=heldKey=heldDirection=null;
  if(startedHold) sendControl('release');
  startedHold=false;heldPwm=false;
}
setInterval(()=>{
  if(!challenge || document.hidden) return;
  if(heldButton && (performance.now()-holdBegan>=30000 || performance.now()-ackAt>180)) { releaseHold();return; }
  if(!inFlight) sendControl(startedHold?(heldDirection==='joystick'?'vector_pulse':'pulse'):'ping',startedHold?currentPayload():{});
},35);

function moveJoystick(e) {
  if(manualMode!=='joystick'||e.pointerId!==joyPointer)return;
  const box=$('joystick-pad').getBoundingClientRect(),radius=box.width/2-30;
  let x=(e.clientX-box.left-box.width/2)/radius,y=(e.clientY-box.top-box.height/2)/radius;
  const length=Math.hypot(x,y),scale=length>1?1/length:1;x*=scale;y*=scale;
  $('joystick-thumb').style.transform=`translate(${x*radius}px,${y*radius}px)`;
  const magnitude=Math.hypot(x,y),gain=magnitude<=.08?0:(magnitude-.08)/(.92*magnitude);
  joyX=Number((x*gain).toFixed(4));joyY=Number((y*gain).toFixed(4));
  const r=-joyY-joyX,l=-joyY+joyX,divisor=Math.max(1,Math.abs(r),Math.abs(l)),limit=Number($('manual-pwm').value);
  if(!Math.round(Math.abs(r)*limit/divisor)&&!Math.round(Math.abs(l)*limit/divisor))joyX=joyY=0;
  setText($('joystick-x'),`${Math.round(joyX*100)}%`);setText($('joystick-y'),`${Math.round(-joyY*100)}%`);
  if(!joyX&&!joyY) {
    const pointer=joyPointer;if(heldDirection==='joystick')releaseHold();joyPointer=pointer;return;
  }
  if(!heldButton) {
    if(!manualReady()||!challenge||performance.now()-ackAt>180)return; // No delayed movement queue.
    heldButton='joystick-pad';heldDirection='joystick';heldPwm=true;holdBegan=performance.now();startWanted=true;
    $('joystick-pad').classList.add('held');
    if(!inFlight){startWanted=false;controlIdle=false;startedHold=startControl();}
  } else if(heldDirection==='joystick'&&!inFlight&&performance.now()-sentAt>=35)sendControl('vector_pulse',vectorPayload());
}
$('joystick-pad').addEventListener('pointerdown',e=>{
  if(manualMode!=='joystick'||e.button!==0||!state?.device.vector_manual_available||!$('hold-mode').checked||!manualReady()||heldButton||Number($('manual-pwm').value)===0)return;
  e.preventDefault();joyPointer=e.pointerId;$('joystick-pad').setPointerCapture(e.pointerId);moveJoystick(e);
});
$('joystick-pad').addEventListener('pointermove',moveJoystick);
for(const type of ['pointerup','pointercancel','lostpointercapture'])$('joystick-pad').addEventListener(type,e=>{if(e.pointerId===joyPointer)releaseHold();});
for(const [id,direction] of [['move-forward','forward'],['move-backward','backward'],['turn-left','left'],['turn-right','right']]) {
  const button=$(id);
  button.addEventListener('pointerdown',e=>{
    if(manualMode!=='four' || !$('hold-mode').checked || e.button!==0) return;
    e.preventDefault();button.setPointerCapture(e.pointerId);beginHold(direction,id);
  });
  for(const type of ['pointerup','pointercancel','lostpointercapture']) button.addEventListener(type,releaseHold);
}
document.addEventListener('keyup',e=>{ if(e.key.toLowerCase()===heldKey) {e.preventDefault();releaseHold();} });
// Mode changes end the old gesture; displaying a control never starts motion.
for (const button of document.querySelectorAll('[data-control-mode]')) button.addEventListener('click', () => {
  const next = button.dataset.controlMode;
  if (next === manualMode) return;
  const localHold = !!heldButton;
  releaseHold();
  if (!localHold && owns() && (state?.holding || (running() && state.job.kind === 'manual'))) stop();
  if (manualMode === 'four') fourAxisHold = $('hold-mode').checked;
  manualMode = next;
  $('hold-mode').checked = next === 'joystick' ? true : fourAxisHold;
  $('keyboard-enabled').checked = false;
  $('four-axis-controls').hidden = next !== 'four';
  $('joystick-controls').hidden = next !== 'joystick';
  for (const option of document.querySelectorAll('[data-control-mode]')) {
    const selected = option.dataset.controlMode === next;
    option.classList.toggle('selected', selected);
    option.setAttribute('aria-pressed', String(selected));
  }
  render();
});
$('hold-mode').addEventListener('change',releaseHold);
$('hold-mode').addEventListener('change',render);
$('manual-pwm').addEventListener('input',()=>{
  setText($('manual-pwm-value'), $('manual-pwm').value+'%');
  if(Number($('manual-pwm').value)===0) releaseHold();
  render();
});
$('keyboard-enabled').addEventListener('change',()=>{if(!$('keyboard-enabled').checked)releaseHold();});
$('range-calibrate').addEventListener('click',()=>act('/api/range-calibrate',{known_mm:Number($('calibration-mm').value)}));
for(const id of ['history-search','history-filter']) $(id).addEventListener('input',()=>{historyPage=0;renderHistory();});
$('history-prev').addEventListener('click',()=>{historyPage--;renderHistory();});
$('history-next').addEventListener('click',()=>{historyPage++;renderHistory();});

$('firmware-flash').addEventListener('click',async()=>{
  if(!$('motor-power-off').checked) { toast('先拔电机5V，只保留USB，再勾选确认。');return; }
  if(state?.device?.connected) { toast('先关闭设置并断开设备连接，再烧录。');return; }
  const result=await act('/api/flash',{port:$('port-select').value,motor_power_disconnected:true});
  if(result) {$('settings-dialog').close();$('motor-power-off').checked=false;}
});

$('range-offset-save').addEventListener('click', async () => { const result=await act('/api/range-offset',{offset_mm:Number($('range-offset-mm').value)}); if(result) {rangeOffsetDirty=false;toast(`测距已校正 ${result.offset_mm} mm`);} });

$('range-offset-mm').addEventListener('input', () => {rangeOffsetDirty=true;});

$('yaw-zero').addEventListener('click', async () => { const result=await act('/api/yaw-zero',{}); if(result) toast('保持小车静止，正在等待新的有效转角零点。'); });

$('policy-library-clear').addEventListener('click',async()=>{ const result=await act('/api/policy-library-clear',{}); if(result?.cleared) toast('已清空可复用策略；会话历史保留。'); });
