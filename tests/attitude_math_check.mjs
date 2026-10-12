import assert from 'node:assert/strict';
import {modelQuaternion,attitudeParts} from '../webui/static/attitude-math.js';
import {Euler,Quaternion,Vector3} from '../webui/static/vendor/three/three.module.js';
const radians=x=>x*Math.PI/180;
const q=a=>new Quaternion().setFromEuler(new Euler(radians(a.roll),radians(a.pitch),radians(a.yaw),'ZYX'));
const projected=(raw,frame)=>new Quaternion(...Object.values(modelQuaternion(raw,frame)));
for(const [frame,angle] of [['header',Math.PI/2],['header-negative',-Math.PI/2],['sensor',0]]) {
  const basis=new Quaternion().setFromAxisAngle(new Vector3(0,0,1),angle);
  for(const raw of [{roll:20,pitch:0,yaw:0},{roll:0,pitch:30,yaw:0},{roll:13,pitch:-8,yaw:47},{roll:0,pitch:0,yaw:90},{roll:-80,pitch:56,yaw:-120}]) {
    const before={...raw};
    const conjugated=basis.clone().multiply(q(raw)).multiply(basis.clone().invert());
    assert.ok(projected(raw,frame).angleTo(conjugated)<1e-6,'quarter-turn mounting including mixed tilt');
    assert.deepEqual(raw,before,'raw control telemetry remains unchanged');
  }
}
assert.ok(projected({roll:20,pitch:0,yaw:0},'header').angleTo(q({roll:0,pitch:20,yaw:0}))<1e-6,'sensor X tilt maps to model Y');
assert.ok(projected({roll:0,pitch:30,yaw:0},'header').angleTo(q({roll:-30,pitch:0,yaw:0}))<1e-6,'sensor Y tilt maps to model negative X');
assert.ok(projected({roll:0,pitch:0,yaw:90},'header').angleTo(q({roll:0,pitch:0,yaw:90}))<1e-6,'pure yaw unchanged');
assert.deepEqual(attitudeParts({roll:5,pitch:7,yaw:NaN,calibrated:1,tilt_valid:1,yaw_valid:0}),{tilt:true,yaw:false});
assert.deepEqual(attitudeParts({roll:5,pitch:7,yaw:8,calibrated:0,tilt_valid:1,yaw_valid:1}),{tilt:false,yaw:false});
assert.deepEqual(attitudeParts(null),{tilt:false,yaw:false});
console.log('PASS: +/-90deg mounting, mixed tilt, unchanged raw/yaw, partial validity');
