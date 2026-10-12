import { modelAngles, attitudeParts } from './attitude-math.js';
import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';

// Sensor coordinates: X/Y in the board plane, Z up. Display coordinates: Y up.
// Model frame is selectable; header view rotates the in-plane reference by 180deg.
// Raw firmware angles remain sensor coordinates; this is not chassis localization.
const canvas = document.getElementById('attitude-canvas');
const status = document.getElementById('attitude-engine');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const mount = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
const frameSelect = document.getElementById('attitude-frame');
frameSelect.value = 'header';
try { if(localStorage.getItem('rover-attitude-frame') === 'sensor') frameSelect.value='sensor'; } catch (_) {}
frameSelect.addEventListener('change', () => {try {localStorage.setItem('rover-attitude-frame', frameSelect.value);} catch (_) {} if(input) update(input);});
let input = null, arrivedAt = 0, lastFrame = 0, lastYaw = 0, renderer, scene, camera, controls, board;
let orientation = new THREE.Quaternion(), target = new THREE.Quaternion(), initialized = false;
const wheelState = {A: {phase: 0, target: 0, received: false}, B: {phase: 0, target: 0, received: false}};

function mesh(geometry, color, x, y, z, metallic = 0) {
  const material = new THREE.MeshStandardMaterial({color, roughness: .54, metalness: metallic});
  const object = new THREE.Mesh(geometry, material); object.position.set(x, y, z);
  board.add(object); return object;
}
function setup() {
  renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: false});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor('#f0f5fa');
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(38, 1, .1, 50);
  resetView();
  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = .08;
  controls.enablePan = false; controls.minDistance = 3; controls.maxDistance = 12;
  controls.target.set(0, .25, 0); controls.update();
  scene.add(new THREE.HemisphereLight('#ffffff', '#8aa0bb', 2.8));
  const light = new THREE.DirectionalLight('#ffffff', 3.2); light.position.set(4, 6, 3); scene.add(light);
  const grid = new THREE.GridHelper(9, 18, '#bbcbdc', '#dce5ef'); grid.position.y = -1.05; scene.add(grid);
  board = new THREE.Group(); scene.add(board);
  mesh(new THREE.BoxGeometry(2.55, 1.7, .09), '#243d43', 0, 0, 0);
  mesh(new THREE.BoxGeometry(.7, .7, .16), '#263347', .25, .08, .12);
  mesh(new THREE.BoxGeometry(.52, .5, .045), '#778da3', .25, .08, .22, .5);
  mesh(new THREE.BoxGeometry(.74, .32, .27), '#f4f4e7', -.4, .65, .15);
  for (let i = 0; i < 6; i++) {
    mesh(new THREE.BoxGeometry(.08, .08, .56), '#d9b16a', -.94 + i * .32, -.7, .28, .65);
    mesh(new THREE.BoxGeometry(.17, .18, .08), '#cba66b', -.94 + i * .32, -.7, .085, .5);
  }
  for (const [x, y] of [[-1.08, .55], [1.08, .55]]) {
    mesh(new THREE.CylinderGeometry(.1, .1, .02, 24), '#d6b678', x, y, .08, .65).rotation.x = Math.PI / 2;
  }
  const axes = new THREE.AxesHelper(1.45); axes.position.set(-.8, -.3, .12); board.add(axes);
  status.textContent = 'X 红 · Y 绿 · Z 蓝';
  document.getElementById('view-reset').addEventListener('click', resetView);
  new ResizeObserver(resize).observe(canvas.parentElement); resize();
}
function resize() {
  const box = canvas.parentElement.getBoundingClientRect();
  if (!box.width || !box.height || !renderer) return;
  renderer.setSize(box.width, box.height, false); camera.aspect = box.width / box.height;
  camera.updateProjectionMatrix();
}
function resetView() {
  camera?.position.set(3.7, 3.2, 4.7);
  controls?.target.set(0, .25, 0); controls?.update();
}
function valid(sample, keys = []) {
  return input?.enabled && sample?.fresh === true && sample.age_ms + performance.now() - arrivedAt < 1500 && keys.every(key => Number.isFinite(sample[key]));
}
function update(data) {
  input = data; arrivedAt = data.receivedAt;
  const attitude = data.attitude;
  if (valid(attitude, ['roll', 'pitch']) && attitudeParts(attitude).tilt) {
    if(attitudeParts(attitude).yaw) lastYaw=attitude.yaw;
    const angles = modelAngles({...attitude,yaw:lastYaw}, frameSelect.value);
    target.setFromEuler(new THREE.Euler(THREE.MathUtils.degToRad(angles.roll), THREE.MathUtils.degToRad(angles.pitch), THREE.MathUtils.degToRad(angles.yaw), 'ZYX'));
    status.textContent = (frameSelect.value === 'header' ? '排针视角 · X/Y 参考轴转180°' : '芯片原始轴 · X红 Y绿 Z蓝') + (attitudeParts(attitude).yaw ? '' : ' · 航向保持不更新');
    if (!initialized || reduced.matches) { orientation.copy(target); initialized = true; }
  }
  for (const [motor, countsPerMm] of [['A', 19.190], ['B', 19.294]]) {
    const sample = data.wheels?.[motor], wheel = wheelState[motor];
    if (valid(sample, ['forward_count']) && sample.valid === 1) {
      wheel.target = sample.forward_count / countsPerMm / 21.5;
      if (!wheel.received || reduced.matches) { wheel.phase = wheel.target; wheel.received = true; }
    }
  }
}
function animate(now) {
  requestAnimationFrame(animate);
  if (document.hidden) { lastFrame = now; return; }
  const dt = Math.min(.05, Math.max(0, (now - lastFrame) / 1000)); lastFrame = now;
  const attitude = input?.attitude;
  if (valid(attitude, ['roll', 'pitch']) && attitudeParts(attitude).tilt) orientation.slerp(target, reduced.matches ? 1 : 1 - Math.exp(-14 * dt));
  for (const [motor, side] of [['B', 'left'], ['A', 'right']]) {
    const sample = input?.wheels?.[motor], wheel = wheelState[motor];
    if (valid(sample, ['forward_count']) && sample.valid === 1) wheel.phase += (wheel.target - wheel.phase) * (reduced.matches ? 1 : 1 - Math.exp(-18 * dt));
    document.getElementById(`wheel-${side}-spokes`).setAttribute('transform', `rotate(${wheel.phase * 180 / Math.PI},50,50)`);
  }
  if (renderer) { board.quaternion.copy(mount).multiply(orientation); controls.update(); renderer.render(scene, camera); }
}
try { setup(); }
catch (error) {
  renderer?.dispose(); renderer = null;
  status.textContent = '当前浏览器无法使用 WebGL；姿态数字仍可查看';
  canvas.setAttribute('aria-label', '3D 引擎不可用，可查看姿态数值');
  console.warn('Rover 3D rendering unavailable', error.message);
}
window.RoverVisuals = {update};
document.dispatchEvent(new Event('rover-visual-ready'));
requestAnimationFrame(animate);
