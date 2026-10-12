import assert from 'node:assert/strict';
import {modelAngles,attitudeParts} from '../webui/static/attitude-math.js';
import {Euler,Quaternion,Vector3} from '../webui/static/vendor/three/three.module.js';
const radians=x=>x*Math.PI/180;
const q=a=>new Quaternion().setFromEuler(new Euler(radians(a.roll),radians(a.pitch),radians(a.yaw),'ZYX'));
const basis=new Quaternion().setFromAxisAngle(new Vector3(0,0,1),Math.PI);
for(const raw of [{roll:20,pitch:0,yaw:0},{roll:0,pitch:30,yaw:0},{roll:13,pitch:-8,yaw:47},{roll:0,pitch:0,yaw:90}]) {
  const projected=q(modelAngles(raw));
  const conjugated=basis.clone().multiply(q(raw)).multiply(basis.clone().invert());
  assert.ok(projected.angleTo(conjugated)<1e-6,'model is a proper basis rotation');
  assert.equal(modelAngles(raw).yaw,raw.yaw,'control yaw does not flip');
  assert.deepEqual(modelAngles(raw,'sensor'),raw);
}
assert.ok(new Vector3(0,1,0).applyQuaternion(q(modelAngles({roll:20,pitch:0,yaw:0}))).z<0,'tilt of header edge reverses');
assert.deepEqual(attitudeParts({roll:5,pitch:7,yaw:NaN,calibrated:1,tilt_valid:1,yaw_valid:0}),{tilt:true,yaw:false});
assert.deepEqual(attitudeParts({roll:5,pitch:7,yaw:8,calibrated:0,tilt_valid:1,yaw_valid:1}),{tilt:false,yaw:false});
assert.deepEqual(attitudeParts(null),{tilt:false,yaw:false});
console.log('PASS: physical basis transform, left/right tilt, unchanged yaw, raw-axis option');
