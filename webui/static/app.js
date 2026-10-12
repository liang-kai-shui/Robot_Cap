'use strict';

const $ = (id) => document.getElementById(id);
const client = sessionStorage.getItem('rover-client') || crypto.randomUUID();
sessionStorage.setItem('rover-client', client);
let token = '', state = null, streamHealthy = false, receivedAt = performance.now();
let distance = 0.10, angle = 30, currentView = 'dashboard', paused = false;
let events = [], eventCursor = 0, clearCursor = 0, toastTimer, stopOnLost = false;
let portCount = 0, portsError = '';
const terminalHistory = []; let historyIndex = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function toast(message) {
  $('toast').textContent = message;
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
const driveReady = () => streamHealthy && owns() && state?.device?.connected && state.device.ready && state.device.stopped === true && !state.device.fault && !running();
const fmt = (value, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const seconds = (value) => value === undefined || value === null ? '—' : `${(value / 1000).toFixed(2)} s`;
function badge(node, text, color = 'neutral') { node.textContent = text; node.className = `badge ${color}`; }
function dot(node, color) { node.className = `status-dot ${color || ''}`; }

function render() {
  if (!state) return;
  const device = state.device, job = state.job;
  const active = running(), ready = driveReady(), demo = state.mode === 'demo';
  const idle = device.connected && device.stopped === true;
  badge($('mode-badge'), demo ? '离线演示' : '实车模式', demo ? 'demo' : 'neutral');
  $('demo-notice').hidden = !demo;
  $('page-subtitle').textContent = demo ? '在演示设备上体验控制、计划执行与回传。' : '连接小车，在这里查看状态、控制运动与执行任务。';
  let status = !streamHealthy ? '回传连接中断' : device.fault ? '需要重新连接' : active ? job.phase : device.connected ? (idle ? '已停稳，待命' : '等待停稳反馈') : '未连接设备';
  $('connection-state').textContent = status;
  $('sidebar-status').textContent = device.connected ? (device.fault ? '会话已中止' : demo ? '演示已连接' : '已连接') : '等待连接';
  $('connection-detail').textContent = state.port ? `${state.port} / 115200` : 'UART0 / 115200';
  dot($('connection-dot'), device.fault || !streamHealthy ? 'error' : device.connected ? 'connected' : '');
  dot($('sidebar-dot'), device.fault ? 'error' : device.connected ? 'connected' : '');
  $('connect-button').querySelector('span').textContent = device.fault ? '重新连接' : device.connected ? '断开设备' : '连接设备';
  $('connect-button').disabled = active || !streamHealthy || (!device.connected && !$('port-select').value);
  $('device-connect-title').textContent = demo ? '演示设备（不连接小车）' : '连接小车';
  $('port-hint').textContent = demo ? '此页只提供 DEMO。连接 COM5 请打开实车控制台。' : device.connected ? `已连接 ${state.port}；切换串口前先断开设备。` : portsError ? `扫描失败：${portsError}。点击刷新重试。` : portCount ? `发现 ${portCount} 个串口，请选择开发板对应的 USB 串口。` : '未发现串口：请接好开发板 USB 数据线，再点击刷新。';
  $('port-select').disabled = device.connected || active;
  $('refresh-ports').disabled = active || device.connected;
  badge($('firmware-badge'), device.firmware ? `固件 v${device.firmware} / PARAM1` : '等待固件回传', device.firmware ? 'good' : 'neutral');
  for (const id of ['move-forward', 'move-backward', 'turn-left', 'turn-right']) $(id).disabled = !ready;
  $('keyboard-enabled').disabled = !device.connected || !owns() || !!device.fault || !streamHealthy;
  $('generate-button').disabled = active || !owns() || !streamHealthy;
  $('build-nav').disabled = active || device.connected;
  document.querySelector('[data-tool="build"]').disabled = active || device.connected;
  if (device.fault) {
    $('notice').hidden = false;
    $('notice').textContent = `当前会话已中止。点击“重新连接”后继续。${device.fault}`;
  } else if (!streamHealthy) {
    $('notice').hidden = false; $('notice').textContent = '回传连接已中断，运动控制已禁用。若服务已重启，请刷新页面。';
  } else if (job?.error) {
    $('notice').hidden = false; $('notice').textContent = job.error;
  } else if (!owns()) {
    $('notice').hidden = false; $('notice').textContent = '另一页面正在控制设备。本页仍可查看回传和发送停止。';
  } else $('notice').hidden = true;

  const telemetry = state.telemetry, yaw = telemetry.yaw, bmi = telemetry.bmi;
  renderRange(telemetry, device, demo);
  renderMotionTelemetry(telemetry, device);
  const age = yaw ? yaw.age_ms + performance.now() - receivedAt : null;
  const yawValid = yaw?.valid === 1;
  const fresh = age !== null && age < 1500 && yawValid && streamHealthy;
  $('yaw-value').textContent = yawValid ? fmt(yaw.angle_deg) : '—';
  $('yaw-fresh').textContent = fresh ? '最近回传' : yaw ? '上次读数' : '等待回传';
  $('yaw-fresh').className = `fresh-label ${fresh ? 'fresh' : ''}`;
  $('sample-age').textContent = age === null ? '等待数据' : `${Math.floor(age / 1000)}s 前更新`;
  $('roll-value').textContent = bmi?.tilt_valid === 1 ? `${fmt(bmi.roll)}°` : '—';
  $('pitch-value').textContent = bmi?.tilt_valid === 1 ? `${fmt(bmi.pitch)}°` : '—';
  const bmiOk = bmi?.calibrated === 1 && bmi?.error === 0 && bmi.fresh;
  $('imu-status').textContent = !bmi ? '等待回传' : bmi.calibrated !== 1 ? '校准中' : bmiOk ? '已校准' : '检查日志';
  $('imu-dot').style.background = bmiOk ? '#249b9a' : '#b5becb';
  const result = device.last_result;
  const move = result?.target_mm !== undefined ? result : telemetry.last_move;
  function measurement(id, value, unit, digits) {
    $(id).replaceChildren(document.createTextNode(fmt(value, digits)), element('small', '', unit));
  }
  measurement('right-distance', move?.after_stop_right_mm, 'mm', 1);
  measurement('left-distance', move?.after_stop_left_mm, 'mm', 1);
  measurement('action-duration', result?.elapsed_ms !== undefined ? result.elapsed_ms / 1000 : undefined, 's', 2);
  renderChart(telemetry.samples);

  $('model-name').textContent = demo ? '离线演示策略' : state.settings.model;
  const plan = state.plan;
  $('plan-container').hidden = !plan;
  if (plan) {
    $('policy-code').textContent = plan.policy;
    $('plan-summary').textContent = `${plan.segments.length} 段 / ${plan.policy.split('\n').length - 1} 个运动`;
    $('execute-button').textContent = `执行 ${plan.segments.length} 段计划`;
    $('execute-button').disabled = !ready;
  }
  const elapsed = job ? job.elapsed_ms + (active ? performance.now() - receivedAt : 0) : 0;
  $('job-title').textContent = job?.title || '等待你的下一步';
  $('job-phase').textContent = job?.phase || '连接小车或先生成一个计划。';
  badge($('job-status'), active ? '执行中' : job?.status === 'failed' ? '失败' : job?.status === 'cancelled' ? '已中止' : job?.status === 'completed' ? '已完成' : '待命', active ? 'running' : job?.status === 'completed' ? 'good' : job?.error ? 'bad' : 'neutral');
  $('job-elapsed').textContent = (elapsed / 1000).toFixed(1);
  $('job-progress-bar').style.width = `${Math.min(100, elapsed / 1800)}%`;
  $('job-progress-bar').parentElement.title = '任务时间预算，最长180秒';
  const timings = job?.timings_ms || {};
  $('model-time').textContent = seconds(timings.model_request);
  $('execution-time').textContent = seconds(timings.policy_execution);
  $('review-time').textContent = seconds(timings.feedback_review);
  $('key-state').textContent = demo ? '演示模式不使用API密钥' : state.settings.key_configured ? '密钥已配置，保存在当前进程中' : '尚未配置密钥';
  $('footer-state').textContent = active ? `${job.phase}，可随时停止` : !streamHealthy ? '等待本地服务回传' : device.connected ? '设备回传已接入' : '本地服务在线，设备未连接';
  $('server-state').textContent = streamHealthy ? '在线' : '连接中断';
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
  $('tof-status').textContent = status;
  $('tof-dot').style.background = valid ? '#249b9a' : '#b5becb';
  $('range-value').textContent = valid ? fmt(sample.distance_mm / 10) : '—';
  $('range-mm').textContent = valid ? `${fmt(sample.distance_mm, 0)} mm${demo ? ' · 演示数据' : ''}` : sample && !disabled ? `原始读数 ${fmt(sample.distance_mm, 0)} mm（无效，不采用）` : '当前没有有效距离';
  $('range-age').textContent = age === null ? '—' : `${fmt(age / 1000, 1)} s 前`;
  $('range-optical').textContent = sample ? `status ${sample.status ?? '—'}` : '—';
  $('range-bus').textContent = sample ? sample.error === 0 ? '正常' : `error ${sample.error}` : '—';
  $('range-note').textContent = disabled ? '板上固件关闭了采样，需更新测距固件。演示页面不连接硬件。' : status === '初始化失败' || status === '总线异常' ? '检查 VL53L1X 电源、GND、SDA6 / SCL7；修复后重启开发板。' : status === '读数无效' ? '未获得有效反射，调整镜头朝向或目标距离；不会将无效读数显示为距离。' : '距离是传感器测量值；前方停车保护 OFF，当前不会按距离自动停车。';
}

function renderMotionTelemetry(telemetry, device) {
  const addedAge = performance.now() - receivedAt;
  const wheels = telemetry.wheels || {}, speeds = [];
  for (const [motor, side] of [['B', 'left'], ['A', 'right']]) {
    const wheel = wheels[motor];
    const valid = device.connected && streamHealthy && wheel?.fresh && wheel.age_ms + addedAge < 1500;
    $(`${side}-wheel-speed`).textContent = valid ? fmt(wheel.speed_mm_s / 10) : '—';
    $(`${side}-wheel-rpm`).textContent = valid ? fmt(wheel.rpm) : '—';
    $(`${side}-wheel-cps`).textContent = valid ? fmt(wheel.cps, 0) : '—';
    $(`${side}-wheel-direction`).textContent = valid ? Math.abs(wheel.speed_mm_s) < .5 ? '静止' : wheel.speed_mm_s > 0 ? '前向' : '后向' : '等待数据';
    if (valid) speeds.push(wheel.speed_mm_s);
  }
  $('vehicle-speed').textContent = speeds.length === 2 ? fmt((speeds[0] + speeds[1]) / 20) : '—';
  badge($('wheel-status'), speeds.length === 2 ? '实时反馈' : '等待有效回传', speeds.length === 2 ? 'good' : 'neutral');
  const attitude = telemetry.attitude;
  const valid = device.connected && streamHealthy && attitude?.fresh && attitude.age_ms + addedAge < 1500 && attitude.tilt_valid === 1 && attitude.yaw_valid === 1 && attitude.calibrated === 1;
  badge($('attitude-status'), valid ? '实时姿态' : attitude ? '冻结 · 数据无效或过期' : '等待回传', valid ? 'good' : 'neutral');
  for (const name of ['roll', 'pitch', 'yaw']) $('attitude-' + name).textContent = valid ? `${fmt(attitude[name])}°` : '—';
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
  $('chart-top').textContent = high.toFixed(0); $('chart-bottom').textContent = low.toFixed(0);
}

let historySignature = '';
function renderHistory() {
  const jobs = (state?.jobs || []).filter(j => ['manual', 'execute', 'plan'].includes(j.kind));
  $('history-count').textContent = jobs.length;
  const signature = JSON.stringify(jobs.map(j => [j.id, j.status]));
  if (signature === historySignature) return; historySignature = signature;
  const list = $('history-list'); list.replaceChildren();
  if (!jobs.length) { list.append(element('div', 'history-empty', '还没有任务。完成一次控制或生成计划后，记录会出现在这里。')); return; }
  for (const job of [...jobs].reverse()) {
    const row = element('div', 'history-entry');
    row.append(element('span', 'history-date', new Date(job.started_at).toLocaleTimeString('zh-CN', {hour12: false})));
    const title = element('div', 'history-title', job.title);
    title.append(element('small', '', job.kind === 'plan' ? '仅生成预览，没有运动' : job.result?.execution_success ? '接口执行完成，地面结果未独立验证' : job.error || job.phase));
    row.append(title);
    const status = element('span', 'badge');
    badge(status, job.status === 'completed' ? '完成' : job.status === 'cancelled' ? '中止' : '失败', job.status === 'completed' ? 'good' : 'bad');
    row.append(status, element('span', 'history-duration', seconds(job.elapsed_ms))); list.append(row);
  }
}

function addEvents(batch) {
  for (const item of batch) if (item.id > eventCursor) { events.push(item); eventCursor = item.id; }
  events = events.slice(-1600); renderLogs();
}
function renderLogs() {
  if (paused) return;
  const filter = $('log-filter').value, search = $('log-search').value.toLowerCase();
  const output = $('console-output');
  const nearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 50;
  const shown = events.filter(e => e.id > clearCursor && (filter === 'all' || e.channel === filter) && e.text.toLowerCase().includes(search)).slice(-350);
  const fragment = document.createDocumentFragment();
  for (const item of shown) {
    const row = element('div', `log-line ${item.level}`);
    row.append(element('span', 'log-time', new Date(item.at).toLocaleTimeString('zh-CN', {hour12: false})),
      element('span', 'log-channel', {serial: '串口', task: '任务', policy: '策略', feedback: '反馈', terminal: '终端', build: '构建', system: '系统'}[item.channel] || item.channel),
      element('span', 'log-text', item.text)); fragment.append(row);
  }
  output.replaceChildren(fragment);
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
  if (state) { $('setting-model').value = state.settings.model; $('setting-url').value = state.settings.base_url; }
  $('setting-key').value = ''; $('settings-dialog').showModal();
}
async function openDocument(name = 'webui') {
  try { const doc = await request(`/api/document?name=${encodeURIComponent(name)}`); $('document-title').textContent = doc.name; $('document-content').textContent = doc.text; if (!$('document-dialog').open) $('document-dialog').showModal(); }
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
  try { await request('/api/stop', {}, keepalive); if (!keepalive) toast('已请求停止；运动会话中止后需要重新连接。'); }
  catch (error) { if (!keepalive) toast(error.message); }
}
async function generate() {
  if (!state?.settings.key_configured) { openSettings(); toast('先配置模型服务，再生成计划。'); return; }
  const instruction = $('instruction').value.trim();
  if (!instruction) { $('instruction').focus(); toast('先输入一个任务，或点选示例。'); return; }
  await act('/api/generate', {instruction});
}

$('refresh-ports').addEventListener('click', refreshPorts);
$('connect-button').addEventListener('click', () => state?.device.connected && !state.device.fault ? act('/api/disconnect', {}) : act('/api/connect', {port: $('port-select').value}));
$('stop-top').addEventListener('click', () => stop()); $('stop-pad').addEventListener('click', () => stop());
$('move-forward').addEventListener('click', () => act('/api/manual', {action: 'move', value: distance}));
$('move-backward').addEventListener('click', () => act('/api/manual', {action: 'reverse', value: .8}));
$('turn-left').addEventListener('click', () => act('/api/manual', {action: 'turn', value: angle}));
$('turn-right').addEventListener('click', () => act('/api/manual', {action: 'turn', value: -angle}));
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
  $('view-name').textContent = currentView === 'dashboard' ? '控制工作台' : '任务记录';
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
  e.preventDefault(); const payload = {model: $('setting-model').value, base_url: $('setting-url').value};
  if ($('setting-key').value.trim()) payload.api_key = $('setting-key').value.trim();
  const result = await act('/api/settings', payload); $('setting-key').value = '';
  if (result) { $('settings-dialog').close(); toast('模型配置已保存，仅本次进程使用。'); }
});
$('shutdown-button').addEventListener('click', async () => { if (confirm('结束本地服务？正在运行的任务会中止。')) { await act('/api/shutdown', {}); $('settings-dialog').close(); } });
$('log-filter').addEventListener('change', renderLogs); $('log-search').addEventListener('input', renderLogs);
$('log-pause').addEventListener('click', () => { paused = !paused; $('log-pause').textContent = paused ? '继续' : '暂停'; if (!paused) renderLogs(); });
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
  if (e.key === 'Escape' && running()) { e.preventDefault(); stop(); return; }
  if (!$('keyboard-enabled').checked || e.target.closest('input,textarea,select,[contenteditable="true"]') || $('settings-dialog').open || $('document-dialog').open) return;
  const key = e.key.toLowerCase();
  if (key === 't') { e.preventDefault(); stop(); }
  else if (key === 's' && driveReady()) { e.preventDefault(); act('/api/manual', {action: 'reverse', value: .8}); }
  else if (driveReady() && ['w', 'a', 'd'].includes(key)) { e.preventDefault(); act('/api/manual', {action: key === 'w' ? 'move' : 'turn', value: key === 'w' ? distance : key === 'a' ? angle : -angle}); }
});
document.addEventListener('visibilitychange', () => { if (document.hidden && running()) stop(true); });
window.addEventListener('pagehide', () => { if (running()) stop(true); });
window.addEventListener('blur', () => { if (running() && $('keyboard-enabled').checked && state.job.kind === 'manual') stop(true); });

async function boot() {
  try {
    const data = await request('/api/bootstrap'); token = data.token; state = data.state; streamHealthy = true;
    receivedAt = performance.now(); render(); await refreshPorts();
    const source = new EventSource('/api/events');
    source.onmessage = event => { const data = JSON.parse(event.data); state = data.state; receivedAt = performance.now(); streamHealthy = true; stopOnLost = false; addEvents(data.events); render(); };
    source.onerror = () => { streamHealthy = false; if (running() && !stopOnLost) { stopOnLost = true; stop(true); } render(); };
    setInterval(() => { if (!document.hidden) request('/api/heartbeat', {}).catch(() => {}); }, 900);
    setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString('zh-CN', {hour12: false}); render(); }, 500);
  } catch (error) { $('notice').hidden = false; $('notice').textContent = `本地服务未就绪：${error.message}。请双击启动文件后刷新页面。`; }
}
boot();
